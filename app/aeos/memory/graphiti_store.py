"""
GraphitiStore — Zep/Graphiti temporal knowledge-graph memory (self-hosted).

يمنح الموظف الذكي "ذاكرة معرفية زمنية" على غرار Zep: كل دورة تُغذّى كـ Episode،
و Graphiti يستخرج تلقائياً الكيانات والحقائق والعلاقات (بما فيها المشاعر
والسلوك) في رسم معرفي مخزَّن محلياً عبر Kuzu (قاعدة مضمّنة، بلا سيرفر خارجي).

- كل موظف معزول بـ ``group_id = employee_id``.
- الاستخراج يستخدم OpenAI (نفس ``OPENAI_API_KEY`` المستعمل في المنصة).
- Graphiti غير متزامن (asyncio) بينما الدورة المعرفية متزامنة، لذا نشغّل
  حلقة asyncio في خيط خلفي واحد ونمرّر إليها المهام:
  * ``ingest``  — إرسال حلقة (episode) بدون انتظار (fire-and-forget)، نصاً أو
    قاموساً مهيكلاً (JSON) — المهيكل يعطي استخراجاً أدق للحقائق.
  * ``search`` / ``search_facts`` — بحث هجين (دلالي + نصي) على الحقائق
    والكيانات، مع استعلامات متعددة ودمج مرتّب ومهلة واحدة.
  * ``recent_facts`` — احتياطي زمني (أحدث الحقائق) عندما لا يطابق البحث الهجين
    شيئاً، فلا تعود الذاكرة فارغة أبداً بعد أول تغذية.
  * ``stats`` — عدّادات تحقّق (حلقات/كيانات/حقائق) للتشخيص.
- التعطّل الآمن: إن غابت المكتبة أو المفتاح أو فشل التهيئة، يصبح المخزن غير
  متاح ويستمر النظام على طبقات SQL الحالية دون أي كسر. أخطاء التهيئة العابرة
  تُعاد المحاولة بعد فترة تهدئة بدل التعطيل الأبدي، وقاعدة Kuzu التالفة
  (نسخة قديمة/WAL مقطوع) تُنقل إلى الحجر الصحي وتُبنى قاعدة جديدة تلقائياً.

متغيرات البيئة (اختيارية):
- ``GRAPHITI_ENABLED``    — "0" لتعطيل الميزة (الافتراضي مفعّلة).
- ``GRAPHITI_KUZU_DB``    — مسار قاعدة Kuzu (الافتراضي data/graphiti_kuzu).
- ``GRAPHITI_MIN_SCORE``  — أدنى تشابه دلالي مقبول (الافتراضي 0.3؛ افتراضي
  Graphiti 0.6 صارم جداً على الاستعلامات العربية الفضفاضة فيعيد صفر نتائج).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import time
import warnings
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

logger = logging.getLogger("aeos.memory.graphiti")

_DEFAULT_DB = Path(__file__).resolve().parents[3] / "data" / "graphiti_kuzu"
_RETRY_COOLDOWN = 300.0          # ثانية قبل إعادة محاولة تهيئة فشلت عابراً

#: بصمات أخطاء **التزاحم** (عملية أخرى تحتجز القاعدة) — ليست تلفاً أبداً.
#: Kuzu قاعدة أحادية العملية: أي سكربت جانبي يفتح نفس الملف أثناء عمل التطبيق
#: يفشل بهذا الخطأ. اعتبارُه تلفاً يعني إزاحة قاعدة سليمة يعمل عليها التطبيق —
#: وهذا ما حدث فعلاً مرة، فصار التمييز إلزامياً.
_LOCK_ERROR_MARKERS = (
    "could not set lock", "lock on file", "concurrency", "already open",
    "being used by another process", "resource busy", "permission denied",
    "access is denied", "another process",
)
_DEFAULT_MIN_SCORE = 0.3
_MAX_QUERY_WORDS = 60            # Graphiti يقصّ عند 128 كلمة؛ نبقى أضيق وأدقّ
_MAX_EPISODE_CHARS = 6000
_WS_RE = re.compile(r"\s+")
#: نسبة احتواء كلمات تعني «نفس الحقيقة بصياغة أخرى» فتُسقَط. 0.65 تمسك أيضاً
#: عائلة «نفس الجملة برقم مختلف» (دورة 144/145/150) وهي زائدة تماماً للاستنتاج.
_NEAR_DUP_THRESHOLD = 0.65
#: أقصى عدد حقائق لحسابٍ واحد مذكور — يمنع احتلال حدثٍ واحد لكل النتائج
_MAX_PER_SUBJECT = 3


class GraphitiStore:
    """جسر متزامن حول Graphiti + Kuzu، معزول لكل موظف عبر group_id."""

    def __init__(self, db_path: Optional[str] = None,
                 min_score: Optional[float] = None,
                 auto_repair: Optional[bool] = None) -> None:
        self._db_path = str(db_path or os.getenv("GRAPHITI_KUZU_DB") or _DEFAULT_DB)
        self._enabled = os.getenv("GRAPHITI_ENABLED", "1").strip() not in ("0", "false", "no")
        # التعافي الذاتي (نقل قاعدة تالفة وبناء جديدة) — تُعطّله الأدوات التي
        # تفحص قاعدة حيّة كي لا تُزيح بيانات عاملة أبداً.
        if auto_repair is None:
            auto_repair = os.getenv("GRAPHITI_AUTO_REPAIR", "1").strip() \
                not in ("0", "false", "no")
        self._auto_repair = bool(auto_repair)
        try:
            self._min_score = float(
                os.getenv("GRAPHITI_MIN_SCORE") or min_score or _DEFAULT_MIN_SCORE
            )
        except (TypeError, ValueError):
            self._min_score = _DEFAULT_MIN_SCORE
        self._graphiti = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._init_lock = threading.Lock()
        self._failed = False          # تعطيل نهائي (مكتبة/مفتاح مفقود)
        self._retry_at = 0.0          # تهدئة بعد فشل عابر
        self._ingest_lock: Optional[asyncio.Lock] = None
        self._last_digest: Dict[str, str] = {}
        self.counters: Dict[str, int] = {
            "ingested": 0, "ingest_failed": 0, "ingest_duplicate": 0,
            "searches": 0, "search_empty": 0, "recent_fallbacks": 0,
        }

    # ---- lifecycle -------------------------------------------------------
    @property
    def available(self) -> bool:
        """جاهز للاستخدام؟ يهيّئ عند أول نداء (lazy) ولا يرفع استثناء أبداً."""
        if not self._enabled or self._failed:
            return False
        if self._graphiti is not None:
            return True
        if time.monotonic() < self._retry_at:
            return False
        return self._initialize()

    def _initialize(self) -> bool:
        with self._init_lock:
            if self._graphiti is not None:
                return True
            if self._failed or time.monotonic() < self._retry_at:
                return False
            if not os.getenv("OPENAI_API_KEY"):
                # المفتاح يُقرأ عادة عبر app.core.config.settings (pydantic .env)
                # دون تصديره إلى os.environ، بينما عملاء Graphiti الداخليون
                # (LLM + Embedder) يقرؤونه من متغير البيئة — فنجسّره هنا.
                try:
                    from app.core.config import settings
                    if getattr(settings, "OPENAI_API_KEY", ""):
                        os.environ["OPENAI_API_KEY"] = settings.OPENAI_API_KEY
                except Exception:  # noqa: BLE001
                    pass
            if not os.getenv("OPENAI_API_KEY"):
                logger.info("GraphitiStore disabled: OPENAI_API_KEY not set")
                self._failed = True
                return False
            try:
                from graphiti_core import Graphiti
            except Exception as exc:  # noqa: BLE001
                logger.info("GraphitiStore disabled: graphiti-core not importable (%s)", exc)
                self._failed = True
                return False
            try:
                Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
                driver = self._open_driver()
                self._setup_fts_indexes(driver)
                self._loop = asyncio.new_event_loop()
                self._thread = threading.Thread(
                    target=self._loop.run_forever, name="graphiti-loop", daemon=True
                )
                self._thread.start()
                self._graphiti = Graphiti(graph_driver=driver)
                logger.info("GraphitiStore ready (kuzu db=%s, min_score=%.2f)",
                            self._db_path, self._min_score)
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning("GraphitiStore init failed (retry in %ds): %s",
                               int(_RETRY_COOLDOWN), exc)
                self._retry_at = time.monotonic() + _RETRY_COOLDOWN
                self._shutdown_loop()
                return False

    def _open_driver(self):
        """افتح سائق Kuzu؛ وإن كانت القاعدة غير قابلة للفتح انقلها للحجر وأعد البناء.

        ملف Kuzu يصبح غير قابل للفتح إذا أنشأته نسخة أقدم من المكتبة أو انقطع
        WAL أثناء الكتابة (شائع على مجلدات المزامنة). بدل تعطيل الذاكرة
        المعرفية للأبد، نحفظ الملف التالف جانباً ونبدأ رسماً جديداً.
        """
        from graphiti_core.driver.kuzu_driver import KuzuDriver

        def _build():
            with warnings.catch_warnings():
                # تحذير إيقاف دعم Kuzu في graphiti-core — مقصود ومُدار هنا
                warnings.simplefilter("ignore", DeprecationWarning)
                drv = KuzuDriver(db=self._db_path, max_concurrent_queries=2)
            # ثغرة في graphiti-core 0.29.2: سائق Kuzu لا يهيّئ ``_database``
            # بينما ``add_episode`` يقارن به عند تمرير group_id — و clone()
            # في Kuzu تعيد نفس الكائن، لذا يكفي وجود السمة.
            if not hasattr(drv, "_database"):
                drv._database = ""  # noqa: SLF001
            return drv

        try:
            return _build()
        except Exception as exc:  # noqa: BLE001
            message = str(exc).lower()
            if any(marker in message for marker in _LOCK_ERROR_MARKERS):
                # تزاحم لا تلف: عملية أخرى (التطبيق نفسه عادةً) تحتجز القاعدة.
                # نرفع الخطأ ليُعاد المحاولة لاحقاً، ولا نلمس الملف إطلاقاً.
                logger.warning(
                    "Kuzu db is locked by another process — NOT quarantining (%s)", exc)
                raise
            if not Path(self._db_path).exists():
                raise
            if not self._auto_repair:
                logger.warning("Kuzu db unreadable and auto-repair disabled: %s", exc)
                raise
            logger.warning("Kuzu db unreadable (%s) — quarantining and rebuilding", exc)
            self._quarantine_db()
            return _build()

    def _quarantine_db(self) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        for suffix in ("", ".wal", ".tmp", ".shadow"):
            src = Path(self._db_path + suffix)
            if not src.exists():
                continue
            try:
                src.rename(f"{src}.corrupt-{stamp}")
            except OSError as exc:
                logger.warning("could not quarantine %s: %s", src, exc)

    @staticmethod
    def _setup_fts_indexes(driver) -> None:
        """أنشئ فهارس البحث النصي الكامل التي يتطلبها بحث Graphiti.

        ``build_indices_and_constraints`` في سائق Kuzu لا يفعل شيئاً (no-op)
        بينما استعلامات البحث تستدعي ``QUERY_FTS_INDEX`` — لذا ننشئها يدوياً
        عبر امتداد FTS في Kuzu. الأخطاء (فهرس موجود مسبقاً) تُتجاهل.
        """
        import kuzu

        from graphiti_core.graph_queries import get_fulltext_indices
        from graphiti_core.driver.driver import GraphProvider

        conn = kuzu.Connection(driver.db)
        try:
            for stmt in ("INSTALL FTS;", "LOAD EXTENSION FTS;", "LOAD FTS;"):
                try:
                    conn.execute(stmt)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Kuzu FTS ext: %s -> %s", stmt, exc)
            for stmt in get_fulltext_indices(GraphProvider.KUZU):
                try:
                    conn.execute(stmt)
                except Exception as exc:  # noqa: BLE001
                    # "index already exists" متوقع بعد أول تشغيل
                    logger.debug("Kuzu FTS index: %s", exc)
        finally:
            conn.close()

    def _shutdown_loop(self) -> None:
        loop, self._loop = self._loop, None
        self._ingest_lock = None
        if loop is not None:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        if self._graphiti is not None and self._loop is not None:
            try:
                fut = asyncio.run_coroutine_threadsafe(self._graphiti.close(), self._loop)
                fut.result(timeout=10)
            except Exception:  # noqa: BLE001
                pass
        self._shutdown_loop()
        self._graphiti = None

    # ---- helpers ---------------------------------------------------------
    def _submit(self, coro) -> Optional[Future]:
        if self._loop is None:
            coro.close()
            return None
        try:
            return asyncio.run_coroutine_threadsafe(coro, self._loop)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Graphiti submit failed: %s", exc)
            return None

    def _lock(self) -> asyncio.Lock:
        """قفل يُسلسل تغذية الحلقات — Kuzu لا يتحمّل كتابات متزامنة."""
        with self._init_lock:
            if self._ingest_lock is None:
                self._ingest_lock = asyncio.Lock()
            return self._ingest_lock

    @staticmethod
    def _clean_query(query: str) -> str:
        """نظّف الاستعلام: مسافات موحّدة، بلا رموز JSON، وبطول معقول."""
        text = _WS_RE.sub(" ", str(query or "")).strip()
        text = text.replace("{", " ").replace("}", " ").replace("'", " ").replace('"', " ")
        words = _WS_RE.sub(" ", text).strip().split(" ")
        return " ".join(w for w in words if w)[:400] if words else ""

    @staticmethod
    def _episode_body(payload: Union[str, Dict[str, Any]]):
        """حوّل الحمولة إلى (نص، نوع الحلقة)؛ القواميس تُرسل كـ JSON."""
        from graphiti_core.nodes import EpisodeType

        if isinstance(payload, dict):
            body = json.dumps(payload, ensure_ascii=False, default=str)
            return body[:_MAX_EPISODE_CHARS], EpisodeType.json
        return str(payload or "").strip()[:_MAX_EPISODE_CHARS], EpisodeType.text

    # ---- public API ------------------------------------------------------
    def ingest(self, employee_id: str, payload: Union[str, Dict[str, Any]],
               name: str = "cycle",
               source_description: str = "aeos cognitive cycle") -> Optional[Future]:
        """أرسل حلقة إلى الرسم المعرفي بلا انتظار (الاستخراج يجري بالخلفية).

        ``payload`` نص أو قاموس مهيكل (يُرسل كـ JSON فيستخرج Graphiti منه
        حقائق أدقّ). الحلقة المطابقة حرفياً لسابقتها تُتجاهل لتفادي التضخّم.
        يعيد ``Future`` اختيارياً لمن يريد الانتظار (اختبارات) — الدورة لا تنتظره.
        """
        if not self.available:
            return None
        body, episode_type = self._episode_body(payload)
        if not body:
            return None
        digest = hashlib.sha1(body.encode("utf-8", "ignore")).hexdigest()
        if self._last_digest.get(employee_id) == digest:
            self.counters["ingest_duplicate"] += 1
            logger.debug("Graphiti ingest skipped (duplicate) for %s", employee_id)
            return None
        self._last_digest[employee_id] = digest

        async def _add() -> None:
            async with self._lock():
                try:
                    await self._graphiti.add_episode(
                        name=name,
                        episode_body=body,
                        source=episode_type,
                        source_description=source_description,
                        reference_time=datetime.now(timezone.utc),
                        group_id=employee_id,
                    )
                    self.counters["ingested"] += 1
                except Exception as exc:  # noqa: BLE001
                    self.counters["ingest_failed"] += 1
                    logger.warning("Graphiti ingest failed: %s", exc)

        return self._submit(_add())

    def search(self, employee_id: str, query: str, limit: int = 10,
               timeout: float = 25.0) -> List[str]:
        """بحث هجين في حقائق الموظف؛ يعيد قائمة نصوص الحقائق (قد تكون فارغة)."""
        return self.search_facts(employee_id, [query], limit=limit, timeout=timeout)

    def search_facts(self, employee_id: str, queries: Sequence[str], limit: int = 10,
                     timeout: float = 30.0, include_recent: bool = True) -> List[str]:
        """بحث بعدّة استعلامات مركّزة ودمج مرتّب بلا تكرار.

        استعلام واحد طويل يخلط كل شيء يُفشل البحث الهجين (BM25 + تشابه دلالي)؛
        عدة استعلامات قصيرة مركّزة تعطي استرجاعاً أفضل بكثير. تُضاف ملخّصات
        الكيانات إلى حقائق العلاقات لأنها تحمل معلومات السلوك والمشاعر.
        ``include_recent``: عند خلوّ النتائج نرجع لأحدث الحقائق زمنياً.
        """
        cleaned = [q for q in (self._clean_query(q) for q in (queries or [])) if q][:6]
        if not cleaned or not self.available:
            return []

        from graphiti_core.search.search_config_recipes import COMBINED_HYBRID_SEARCH_RRF

        config = COMBINED_HYBRID_SEARCH_RRF.model_copy(deep=True)
        config.limit = max(1, min(int(limit), 25))
        config.edge_config.sim_min_score = self._min_score
        config.node_config.sim_min_score = self._min_score

        async def _run() -> List[str]:
            facts: List[str] = []
            for q in cleaned:
                words = q.split(" ")
                if len(words) > _MAX_QUERY_WORDS:
                    q = " ".join(words[:_MAX_QUERY_WORDS])
                try:
                    res = await self._graphiti.search_(
                        query=q, config=config, group_ids=[employee_id]
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Graphiti search failed (%s): %s", q[:60], exc)
                    continue
                for edge in getattr(res, "edges", []) or []:
                    fact = (getattr(edge, "fact", "") or "").strip()
                    if fact:
                        facts.append(fact)
                for node in getattr(res, "nodes", []) or []:
                    nm = (getattr(node, "name", "") or "").strip()
                    summary = (getattr(node, "summary", "") or "").strip()
                    if nm and summary:
                        facts.append(f"{nm}: {summary}")
                if len(_drop_near_duplicates(_dedup(facts))) >= limit:
                    break
            # إزالة التكرار الحرفي ثم المتقارب معنى — وإلا احتلّ حدثٌ واحد
            # مكرّر الصياغة كل النتائج وأُقصيت بقية التجارب.
            merged = _drop_near_duplicates(_dedup(facts))[:limit]
            if merged or not include_recent:
                return merged
            recent = await self._recent_facts_async(employee_id, limit)
            if recent:
                self.counters["recent_fallbacks"] += 1
            return recent

        self.counters["searches"] += 1
        fut = self._submit(_run())
        if fut is None:
            return []
        try:
            facts = fut.result(timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Graphiti search timed out/failed: %s", exc)
            return []
        if not facts:
            self.counters["search_empty"] += 1
        return facts

    async def _recent_facts_async(self, employee_id: str, limit: int) -> List[str]:
        """أحدث الحقائق الصالحة زمنياً — استعلام مباشر بلا embeddings."""
        query = (
            "MATCH (e:RelatesToNode_) WHERE e.group_id = $group_id "
            "AND e.expired_at IS NULL AND e.invalid_at IS NULL "
            "RETURN e.fact AS fact ORDER BY e.created_at DESC LIMIT $limit"
        )
        try:
            records, _, _ = await self._graphiti.driver.execute_query(
                query, group_id=employee_id, limit=max(1, min(int(limit), 50))
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Graphiti recent_facts failed: %s", exc)
            return []
        facts = [str(r.get("fact") or "").strip() for r in (records or [])]
        return _drop_near_duplicates(_dedup([f for f in facts if f]))[:limit]

    def recent_facts(self, employee_id: str, limit: int = 10,
                     timeout: float = 15.0) -> List[str]:
        """نسخة متزامنة من الاحتياطي الزمني (تشخيص/عرض)."""
        if not self.available:
            return []
        fut = self._submit(self._recent_facts_async(employee_id, limit))
        if fut is None:
            return []
        try:
            return fut.result(timeout=timeout)
        except Exception:  # noqa: BLE001
            return []

    def stats(self, employee_id: Optional[str] = None,
              timeout: float = 15.0) -> Dict[str, Any]:
        """عدّادات تحقّق: هل تُغذّى الذاكرة فعلاً ويُستخرج منها حقائق؟"""
        info: Dict[str, Any] = {
            "enabled": self._enabled,
            "available": self.available,
            "db_path": self._db_path,
            "min_score": self._min_score,
            "counters": dict(self.counters),
        }
        if not info["available"]:
            return info

        async def _counts() -> Dict[str, int]:
            out: Dict[str, int] = {}
            where = " WHERE n.group_id = $group_id" if employee_id else ""
            for label, key in (("Episodic", "episodes"), ("Entity", "entities"),
                               ("RelatesToNode_", "facts")):
                try:
                    records, _, _ = await self._graphiti.driver.execute_query(
                        f"MATCH (n:{label}){where} RETURN count(n) AS c",
                        **({"group_id": employee_id} if employee_id else {}),
                    )
                    out[key] = int((records or [{}])[0].get("c") or 0)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Graphiti stats %s failed: %s", label, exc)
                    out[key] = -1
            return out

        fut = self._submit(_counts())
        if fut is not None:
            try:
                info["graph"] = fut.result(timeout=timeout)
            except Exception as exc:  # noqa: BLE001
                info["graph_error"] = str(exc)[:200]
        if employee_id:
            info["employee_id"] = employee_id
        return info

    def clear(self, employee_id: str) -> None:
        """احذف كل حقائق موظف واحد (عند حذف الموظف)."""
        self._last_digest.pop(employee_id, None)
        if not self.available:
            return

        async def _clear() -> None:
            async with self._lock():
                try:
                    from graphiti_core.utils.maintenance.graph_data_operations import (
                        clear_data,
                    )
                    await clear_data(self._graphiti.driver, group_ids=[employee_id])
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Graphiti clear failed: %s", exc)

        self._submit(_clear())


def _dedup(items: Sequence[str]) -> List[str]:
    """أزل التكرار مع الحفاظ على الترتيب (المطابقة بلا حساسية للمسافات/الحالة)."""
    seen = set()
    out: List[str] = []
    for item in items:
        key = _WS_RE.sub(" ", item).strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out


#: توكنة تُبقي المعرّفات والأرقام (أقوى إشارات التمييز بين الحقائق): معرّف مثل
#: ``@ml17ll_`` أو تاريخ مثل ``9/30`` كان يُسقَط بالكامل لو استثنينا الأرقام،
#: فتضيع أهم كلمة مشتركة بين صياغتين لنفس الحقيقة.
_TOKEN_RE = re.compile(r"[^\W_]{2,}", re.UNICODE)

#: كلمات وظيفية لا تحمل معنى مميّزاً؛ وجودها يخفض نسبة التشابه زيفاً فتهرب
#: إعادة الصياغة من الفلتر.
_STOP_WORDS = {
    "من", "إلى", "الى", "في", "فيها", "فيه", "على", "عن", "أن", "ان", "إن",
    "مع", "لها", "له", "هذا", "هذه", "التي", "الذي", "قال", "قالت", "وقال",
    "وقالت", "كان", "كانت", "قد", "ثم", "أو", "او", "كما", "بأن", "بأنه",
    "the", "and", "for", "that", "this", "with", "has", "have", "was", "were",
    "said", "about", "their", "there", "from", "into", "them", "they",
}


#: سوابق التعريف والجرّ العربية — «بالمنصة» و«منصة» كلمة واحدة معنىً، وبلا
#: تطبيعها تهرب إعادة الصياغة من فلتر التكرار (رأيناه فعلياً).
_AR_PREFIXES = ("وبال", "فبال", "بال", "وال", "فال", "كال", "لل", "ال")


def _norm_token(token: str) -> str:
    for prefix in _AR_PREFIXES:
        # لا نقتطع إن كان الباقي أقصر من ثلاثة أحرف (مثل «بالغ» ← «غ»)
        if token.startswith(prefix) and len(token) - len(prefix) >= 3:
            return token[len(prefix):]
    return token


def _words(text: str) -> set:
    """مجموعة الكلمات المميّزة في الحقيقة (بلا كلمات وظيفية، مع المعرّفات)."""
    out = set()
    for token in _TOKEN_RE.findall(text or ""):
        low = _norm_token(token.lower())
        if len(low) >= 3 and low not in _STOP_WORDS:
            out.add(low)
    return out


_HANDLE_RE = re.compile(r"@[A-Za-z0-9_]{2,}")


def _drop_near_duplicates(items: Sequence[str],
                          threshold: float = _NEAR_DUP_THRESHOLD,
                          per_subject: int = _MAX_PER_SUBJECT) -> List[str]:
    """أسقط الحقائق المكرّرة، ووزّع النتائج على مصادر مختلفة.

    Graphiti يستخرج حقيقة من كل حلقة، فالحدث الواحد المذكور في عدة دورات ينتج
    عدة صياغات لنفس المعلومة. مشكلتان لا واحدة:

    1. **تكرار معنى**: نقيسه بالاحتواء ``|A∩B| / min(|A|,|B|)`` لا Jaccard،
       لأن إحدى الصياغتين غالباً أطول من الأخرى فيهبط Jaccard ويهرب التكرار.
    2. **احتلال مصدر واحد للنتائج**: رأينا حالةً عاطفية كاملة مبنية على منشن
       حساب واحد مكرّر عشر مرات، وأُقصيت بقية التجارب. لذا نسقّف عدد الحقائق
       لكل حساب مذكور (``per_subject``) فتبقى الصورة متعدّدة المصادر.
    """
    kept: List[str] = []
    kept_words: List[set] = []
    per_key: Dict[str, int] = {}
    for item in items:
        words = _words(item)
        if not words:
            continue
        duplicate_at = -1
        for i, prev in enumerate(kept_words):
            smaller = min(len(words), len(prev))
            if smaller and len(words & prev) / smaller >= threshold:
                duplicate_at = i
                break
        if duplicate_at >= 0:
            # عند التكرار احتفظ بالأغنى معلومةً لا بالأسبق ترتيباً: جملة عامة
            # قصيرة («يعمل على منصة X في الدورة 150») كانت تُقصي ملخّصاً غنياً
            # يحمل إشارة حقيقية («تفاعل محدود من الجمهور»).
            if len(words) > len(kept_words[duplicate_at]) * 1.3:
                kept[duplicate_at] = item
                kept_words[duplicate_at] = words
            continue
        handles = _HANDLE_RE.findall(item)
        key = handles[0].lower() if handles else "_no_handle"
        if per_key.get(key, 0) >= per_subject:
            continue
        per_key[key] = per_key.get(key, 0) + 1
        kept.append(item)
        kept_words.append(words)
    return kept


# ---- process-wide singleton ---------------------------------------------
_store: Optional[GraphitiStore] = None
_store_lock = threading.Lock()


def get_graphiti_store() -> GraphitiStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = GraphitiStore()
    return _store
