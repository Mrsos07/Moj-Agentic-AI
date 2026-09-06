"""
GraphitiStore — Zep/Graphiti temporal knowledge-graph memory (self-hosted).

يمنح الموظف الذكي "ذاكرة معرفية زمنية" على غرار Zep: كل دورة تُغذّى كـ Episode،
و Graphiti يستخرج تلقائياً الكيانات والحقائق والعلاقات (بما فيها المشاعر
والسلوك) في رسم معرفي مخزَّن محلياً عبر Kuzu (قاعدة مضمّنة، بلا سيرفر خارجي).

- كل موظف معزول بـ ``group_id = employee_id``.
- الاستخراج والـ embeddings يمرّان بنفس مزوّد المنصة (OpenRouter افتراضياً،
  OpenAI احتياطاً) عبر ``app.core.llm_provider.resolve_llm``. **لا نكتفي
  بتغيير المفتاح**: ``Graphiti()`` بلا وسائط يبني عملاءه الداخليين الثلاثة
  (LLM + Embedder + Reranker) على ``https://api.openai.com`` ويقرأ
  ``OPENAI_API_KEY`` من البيئة — فمفتاح OpenRouter وحده يعني 401 صامتاً
  وذاكرة رسم بياني فارغة. لذلك نبنيهم صريحين في ``_build_ai_clients``.
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
- ``GRAPHITI_KUZU_DB``    — مسار قاعدة Kuzu (الافتراضي data/graphiti_kuzu،
  وداخل OneDrive يصبح ``%LOCALAPPDATA%/Moj-Agentic-AI/graphiti_kuzu``
  لأن مزامنة OneDrive تُتلف قاعدة Kuzu الحيّة — انظر ``_default_db_path``).
- ``GRAPHITI_ISOLATED``   — "0" لتشغيل Kuzu داخل العملية (الافتراضي: عملية
  فرعية معزولة عبر ``GraphitiStoreProxy`` لأن انهيار Kuzu الأصلي كان يقتل
  الباك إند كاملاً).
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
import sys
import threading
import time
import warnings
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

logger = logging.getLogger("aeos.memory.graphiti")

def _default_db_path() -> Path:
    """المسار الافتراضي للقاعدة — **خارج أي مجلد يزامنه OneDrive**.

    السبب مُشاهَد لا نظري: المشروع يعيش داخل ``OneDrive\\Desktop`` والقاعدة
    كانت في ``data/graphiti_kuzu``. OneDrive يفتح الملف ويقرؤه (بصمة/رفع)
    أثناء كتابة Kuzu فيه — وKuzu محرّك أصلي يفترض ملفاً لا يلمسه غيره، فتنتج
    قاعدة نصف سليمة تُسقط العملية (0xC0000005) عند كتابة لاحقة، حتى بعد
    إعادة بناء نظيفة (قاعدة 8/3 الجديدة انهارت في 8/5 رغم قفل التسلسل).
    لذلك على ويندوز نضع القاعدة في ``%LOCALAPPDATA%`` إن كان جذر المشروع
    داخل OneDrive. ``GRAPHITI_KUZU_DB`` يتجاوز كل هذا صراحةً.
    """
    repo_default = Path(__file__).resolve().parents[3] / "data" / "graphiti_kuzu"
    in_onedrive = any(p.lower().startswith("onedrive") for p in repo_default.parts)
    local_app = os.getenv("LOCALAPPDATA", "")
    if in_onedrive and local_app:
        return Path(local_app) / "Moj-Agentic-AI" / "graphiti_kuzu"
    return repo_default


_DEFAULT_DB = _default_db_path()
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
                 auto_repair: Optional[bool] = None,
                 provider: Optional[str] = None) -> None:
        # neo4j هو الافتراضي بعد هجرة 2026-08-05: محرك Kuzu المدمج ينهار
        # أصلياً (0xC0000005) أثناء كتابات Graphiti وهو مؤرشف بلا إصلاح قادم.
        # ``GRAPHITI_DRIVER=kuzu`` يعيد المحرك القديم (تشخيص/بيانات قديمة).
        self._provider = (provider or os.getenv("GRAPHITI_DRIVER", "neo4j")).strip().lower()
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
        self._last_digest: Dict[str, str] = self._load_digests()
        # كاش البحث: المفتاح → (الوقت، حقبة التغذية، الحقائق). يُبطَل فوراً عند
        # أي كتابة (``_ingest_epoch``) وبعد TTL قصير، فلا يعيد أبداً صورة قديمة.
        self._search_cache: Dict[str, Any] = {}
        self._ingest_epoch = 0
        try:
            self._cache_ttl = float(os.getenv("GRAPHITI_SEARCH_TTL", "300"))
        except (TypeError, ValueError):
            self._cache_ttl = 300.0
        self.counters: Dict[str, int] = {
            "ingested": 0, "ingest_failed": 0, "ingest_duplicate": 0,
            "searches": 0, "search_empty": 0, "recent_fallbacks": 0,
            "search_cache_hits": 0,
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

    @staticmethod
    def _build_ai_clients(endpoint: Any) -> Dict[str, Any]:
        """عملاء Graphiti الثلاثة موجَّهين إلى مزوّدنا لا إلى OpenAI مباشرة.

        الثلاثة إلزامية وليست تزيّناً: ``Graphiti()`` يبني أي عميل غير ممرَّر
        بنفسه على ``api.openai.com`` قارئاً ``OPENAI_API_KEY`` من البيئة. أي
        واحد منسي يعني إما 401 أو — أسوأ — فاتورة على حساب OpenAI بينما بقية
        النظام على OpenRouter.

        * ``llm_client``   — استخراج الكيانات/الحقائق. نُبقي ``OpenAIClient``
          (واجهة ``/responses`` عبر ``responses.parse``) لأن OpenRouter تدعمها
          كواجهة موحّدة، والـ SDK هو من يبني المخطط الصارم فيصحّ.
          **لا تستخدم ``OpenAIGenericClient`` بوضعه الافتراضي هنا**: هو يبني
          ``response_format=json_schema`` من ``model_json_schema()`` الخام،
          وهذا المخطط يفتقد ``additionalProperties: false`` فترفضه واجهة
          OpenAI الصارمة — مُقاس فعلياً، 400 ‏``invalid_json_schema`` على
          ``ExtractedEntities`` من OpenAI و Azure كليهما عبر OpenRouter،
          فتفشل كل عملية تغذية (``ingest_failed``) ويبقى الرسم فارغاً بلا أي
          خطأ ظاهر في الواجهة. ``GRAPHITI_STRUCTURED_OUTPUT=json_object``
          يحوّل إلى العميل العام بوضع يضع المخطط في الـ prompt بدل فرضه
          (مُختبَر وناجح أيضاً) — لموديل لا تعمل معه ``/responses``.
        * ``embedder``     — نُبقي ``text-embedding-3-small`` بعينه: أي موديل
          آخر يغيّر أبعاد المتجهات، فتصبح كل عقد الرسم البياني المخزّنة غير
          قابلة للبحث الدلالي وتحتاج إعادة بناء كاملة.
        * ``cross_encoder`` — لا يُستدعى فعلياً (مسار البحث عندنا
          ``COMBINED_HYBRID_SEARCH_RRF``، وهو دمج مرتّب بلا reranker) لكن
          المُنشئ يبنيه على أي حال، فيجب أن يحمل مفتاحنا لا مفتاح OpenAI.
        """
        from openai import AsyncOpenAI

        from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
        from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
        from graphiti_core.llm_client.config import LLMConfig
        from graphiti_core.llm_client.openai_client import OpenAIClient
        from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient

        config = LLMConfig(
            api_key=endpoint.api_key,
            base_url=endpoint.base_url,
            model=endpoint.model,
            small_model=endpoint.model,
        )
        # عميل واحد مشترك: ``client_kwargs`` يحمل أيضاً ترويسات OpenRouter
        # (HTTP-Referer / X-Title) التي لا يعرفها ``LLMConfig``، فبناء العملاء
        # من الـ config وحده يفقدها ويضيع تمييز الاستخدام في لوحة المزوّد.
        http_client = AsyncOpenAI(**endpoint.client_kwargs())
        mode = (os.getenv("GRAPHITI_STRUCTURED_OUTPUT") or "responses").strip()
        if mode in ("json_object", "json_schema"):
            llm_client: Any = OpenAIGenericClient(
                config=config, client=http_client, structured_output_mode=mode
            )
        else:
            llm_client = OpenAIClient(config=config, client=http_client)
        return {
            "llm_client": llm_client,
            "embedder": OpenAIEmbedder(
                config=OpenAIEmbedderConfig(
                    api_key=endpoint.api_key,
                    base_url=endpoint.base_url,
                    embedding_model=endpoint.embedding_model,
                ),
                client=http_client,
            ),
            "cross_encoder": OpenAIRerankerClient(config=config, client=http_client),
        }

    def _initialize(self) -> bool:
        with self._init_lock:
            if self._graphiti is not None:
                return True
            if self._failed or time.monotonic() < self._retry_at:
                return False
            from app.core.llm_provider import resolve_llm

            endpoint = resolve_llm()
            if not endpoint.available:
                logger.info("GraphitiStore disabled: no OPENROUTER_API_KEY / OPENAI_API_KEY")
                self._failed = True
                return False
            try:
                from graphiti_core import Graphiti
            except Exception as exc:  # noqa: BLE001
                logger.info("GraphitiStore disabled: graphiti-core not importable (%s)", exc)
                self._failed = True
                return False
            try:
                ai_clients = self._build_ai_clients(endpoint)
            except Exception as exc:  # noqa: BLE001
                logger.warning("GraphitiStore disabled: cannot build %s clients (%s)",
                               endpoint.provider, exc)
                self._failed = True
                return False
            try:
                if self._provider == "neo4j":
                    # إشعارات «الخاصية غير موجودة» على قاعدة فارغة ضوضاء
                    # متوقعة من استعلامات graphiti — لا تستحق سطراً في اللوق
                    logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
                    driver = self._open_neo4j_driver()
                else:
                    Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
                    self._recover_from_hard_crash()
                    driver = self._open_driver()
                    self._setup_fts_indexes(driver)
                self._loop = asyncio.new_event_loop()
                self._thread = threading.Thread(
                    target=self._loop.run_forever, name="graphiti-loop", daemon=True
                )
                self._thread.start()
                self._graphiti = Graphiti(graph_driver=driver, **ai_clients)
                if self._provider == "neo4j":
                    # على Neo4j هذه تنشئ الفهارس (النصية والدلالية) فعلياً —
                    # بعكس سائق Kuzu حيث كانت no-op واضطررنا لإنشائها يدوياً.
                    asyncio.run_coroutine_threadsafe(
                        self._graphiti.build_indices_and_constraints(), self._loop
                    ).result(timeout=120)
                logger.info("GraphitiStore ready (driver=%s, db=%s, min_score=%.2f, llm=%s/%s)",
                            self._provider,
                            "bolt" if self._provider == "neo4j" else self._db_path,
                            self._min_score, endpoint.provider, endpoint.model)
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning("GraphitiStore init failed (retry in %ds): %s",
                               int(_RETRY_COOLDOWN), exc)
                self._retry_at = time.monotonic() + _RETRY_COOLDOWN
                self._shutdown_loop()
                return False

    @property
    def _writing_marker(self) -> Path:
        return Path(self._db_path + ".writing")

    def _recover_from_hard_crash(self) -> None:
        """اكشف موتاً مفاجئاً وسط كتابة، واحجر القاعدة المتضرّرة تلقائياً.

        Kuzu امتداد أصلي: انهياره (0xC0000005) يقتل العملية بلا استثناء ولا
        تنظيف، فتبقى القاعدة نصف مكتوبة. وقد رأينا أن مثل هذه القاعدة تُسقط
        العملية مرةً أخرى عند أول كتابة تالية — أي حلقة موت لا تنتهي: التطبيق
        يعمل ~20 دقيقة ثم يختفي، وهكذا (15 مرة في يوم واحد).

        العلامة تُكتب قبل كل كتابة وتُمحى بعدها. وجودها عند البدء يعني أن
        العملية السابقة ماتت وسط الكتابة ⇒ نحجر الملف (نقل لا حذف، قابل
        للاستعادة) ونبني قاعدة نظيفة، فتُشفى الحلقة من نفسها.
        """
        marker = self._writing_marker
        if not marker.exists():
            return
        try:
            detail = marker.read_text(encoding="utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            detail = ""
        logger.warning(
            "Graph DB was mid-write when the process died (%s) - quarantining it; "
            "a half-written Kuzu file crashes the process again on the next write",
            detail or "no detail",
        )
        try:
            marker.unlink()
        except Exception:  # noqa: BLE001
            pass
        if os.getenv("GRAPHITI_AUTO_REPAIR", "1").strip().lower() in ("0", "false", "no"):
            logger.warning("auto-repair disabled - keeping the damaged db as-is")
            return
        self._quarantine_db(reason="crash")

    def _mark_write_start(self, what: str) -> None:
        try:
            self._writing_marker.write_text(
                f"{datetime.now(timezone.utc).isoformat()} {what}", encoding="utf-8"
            )
        except Exception:  # noqa: BLE001
            pass

    def _mark_write_end(self) -> None:
        try:
            self._writing_marker.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    def _open_neo4j_driver(self):
        """شغّل خادم Neo4j المحلي إن لزم وافتح سائق graphiti الأساسي."""
        from graphiti_core.driver.neo4j_driver import Neo4jDriver

        from .neo4j_service import (
            DEFAULT_PASSWORD, DEFAULT_URI, DEFAULT_USER, ensure_neo4j,
        )

        if not ensure_neo4j():
            raise RuntimeError(
                f"Neo4j not reachable on {DEFAULT_URI} and could not be started")
        return Neo4jDriver(uri=DEFAULT_URI, user=DEFAULT_USER, password=DEFAULT_PASSWORD)

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
                # 1 لا 2: Graphiti ينفّذ استعلامات البحث الهجين متوازيةً عبر
                # asyncio.gather، وأي تواز على اتصال Kuzu واحد يُسقط العملية
                # بـAccess Violation. القفل الخارجي لا يمنع هذا التواز الداخلي.
                drv = KuzuDriver(db=self._db_path, max_concurrent_queries=1)
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

    def _quarantine_db(self, reason: str = "corrupt") -> None:
        """انقل ملفات القاعدة جانباً (نقل لا حذف — قابل للاستعادة يدوياً)."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        for suffix in ("", ".wal", ".tmp", ".shadow"):
            src = Path(self._db_path + suffix)
            if not src.exists():
                continue
            try:
                src.rename(f"{src}.{reason}-{stamp}")
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
        """قفل يُسلسل **كل** وصول إلى Kuzu — قراءةً وكتابةً بلا استثناء.

        سبب حاسم مُثبَت: Kuzu (امتداد أصلي) ينهار بـAccess Violation
        (0xC0000005) إذا تزامن استعلام قراءة مع معاملة كتابة، فيقتل عملية
        الباك-إند بالكامل بلا أي استثناء بايثون — سُجّل 15 انهياراً في يوم واحد،
        وكان المستخدم يرى «الموظف الذكي والمحادثة يختفيان بعد كل دورة».
        النمط الذي يُفجّره: ``ingest`` لا يُنتظر (استخراج الكيانات عبر LLM يطول
        دقيقة)، وبعد 30 ثانية تبدأ الدورة التالية فتستدعي ``search_facts``.
        كان القفل يحمي الكتابة فقط، فبقيت القراءة تتزامن معها.
        """
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

    @property
    def _digest_file(self) -> Path:
        """ملف بصمات آخر حلقة لكل موظف — يعيش بجوار القاعدة/بيانات التطبيق."""
        return Path(self._db_path).parent / "graphiti_last_digests.json"

    def _load_digests(self) -> Dict[str, str]:
        """بصمات آخر تغذية — **دائمة عبر إعادة التشغيل**.

        كانت في الذاكرة فقط، فكل إقلاع (أو إعادة تشغيل عامل الرسم بعد انهيار)
        يفقدها فتُعاد تغذية الحلقة نفسها ويستخرج Graphiti منها الحقائق مرة
        أخرى — تضخّم في الرسم وكلفة LLM بلا أي معلومة جديدة.
        """
        try:
            raw = self._digest_file.read_text(encoding="utf-8")
            data = json.loads(raw)
            return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001 - ملف مساعد: غيابه/تلفه لا يعني شيئاً
            return {}

    def _save_digests(self) -> None:
        try:
            self._digest_file.parent.mkdir(parents=True, exist_ok=True)
            self._digest_file.write_text(
                json.dumps(self._last_digest, ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

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
        self._save_digests()
        # الرسم على وشك التغيّر ⇒ كل نتيجة بحث محفوظة صارت قديمة
        self._ingest_epoch += 1
        self._search_cache.clear()

        kuzu = self._provider != "neo4j"

        async def _add() -> None:
            async with self._lock():
                # علامة «كتابة جارية» (Kuzu فقط): إن مات المحرك أصلياً في
                # المنتصف نكتشفه عند البدء التالي فنحجر الملف بدل حلقة موت
                if kuzu:
                    self._mark_write_start(f"{name}/{employee_id}")
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
                finally:
                    # الوصول إلى هنا يعني أن العملية نجت من الكتابة
                    if kuzu:
                        self._mark_write_end()

        return self._submit(_add())

    def search(self, employee_id: str, query: str, limit: int = 10,
               timeout: float = 25.0) -> List[str]:
        """بحث هجين في حقائق الموظف؛ يعيد قائمة نصوص الحقائق (قد تكون فارغة)."""
        return self.search_facts(employee_id, [query], limit=limit, timeout=timeout)

    # ---- كاش البحث ----
    def _cache_get(self, key: str):
        """نتيجة بحث سابقة ما دامت الحقائق لم تتغيّر (لا تغذية جديدة).

        كل دورة كانت تُطلق 5 استعلامات هجينة تحت قفل عام بمهلة 30 ثانية بينما
        الرسم لا يتغيّر إلا عند التغذية (مرّة في نهاية الدورة). النتيجة: انتظار
        متكرّر بلا أي معلومة جديدة. الكاش يُبطَل فوراً عند أي ``ingest``.
        """
        hit = self._search_cache.get(key)
        if not hit:
            return None
        ts, epoch, facts = hit
        if epoch != self._ingest_epoch:
            return None
        if time.monotonic() - ts > self._cache_ttl:
            return None
        self.counters["search_cache_hits"] += 1
        return list(facts)

    def _cache_put(self, key: str, facts: List[str]) -> None:
        if len(self._search_cache) > 64:
            self._search_cache.clear()
        self._search_cache[key] = (time.monotonic(), self._ingest_epoch, list(facts))

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

        cache_key = f"f|{employee_id}|{limit}|{int(include_recent)}|" + "|".join(cleaned)
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        async def _run() -> List[str]:
            # القفل إلزامي هنا: بحثٌ يتزامن مع كتابة يُسقط العملية كلها
            async with self._lock():
                facts = await self._collect_facts(employee_id, cleaned, limit)
                if facts or not include_recent:
                    return facts
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
        self._cache_put(cache_key, facts)
        return facts

    def facts_about(self, employee_id: str, entities: Sequence[str],
                    limit: int = 3, timeout: float = 30.0) -> Dict[str, List[str]]:
        """ما يعرفه الرسم المعرفي عن **كيانات محدّدة** (حسابات/مواضيع).

        هذه هي الواجهة التي كانت غائبة تماماً: الرسم كان يُستجوَب باستعلامات
        عامة («تفاعل الجمهور...») وتُستخدم النتيجة لتوليد «مزاج» فقط، فلم
        تُسأل الذاكرة قطّ عن الهدف الذي نوشك على التفاعل معه. الآن يمكن لقرار
        التفاعل أن يستند إلى تاريخ العلاقة مع الحساب نفسه.

        كل الاستعلامات تجري داخل **قفل واحد** (لا قفل لكل كيان) فلا تتضاعف
        كلفة التسلسل، وتُنسب الحقيقة لكيان بذكره الصريح فيها.
        """
        names = [str(e).strip() for e in (entities or []) if str(e).strip()][:5]
        if not names or not self.available:
            return {}

        cache_key = f"e|{employee_id}|{limit}|" + "|".join(sorted(n.lower() for n in names))
        cached = self._cache_get(cache_key)
        if cached is not None:
            return json.loads(cached[0]) if cached else {}

        async def _run() -> Dict[str, List[str]]:
            out: Dict[str, List[str]] = {}
            async with self._lock():
                for name in names:
                    query = self._clean_query(f"{name} سلوك وتفاعل وآراء")
                    facts = await self._collect_facts(
                        employee_id, [query], max(limit * 2, 4))
                    key = name.lstrip("@").strip().lower()
                    # لا تنسب لكيان حقيقةً لا تذكره: البحث الهجين يعيد جيراناً
                    # دلاليين، ونسبتُهم إليه تصنع «تاريخ علاقة» لم يحدث.
                    owned = [f for f in facts if key in f.lower()]
                    if owned:
                        out[key] = owned[:limit]
            return out

        fut = self._submit(_run())
        if fut is None:
            return {}
        try:
            result = fut.result(timeout=timeout) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Graphiti facts_about failed: %s", exc)
            return {}
        self._cache_put(cache_key, [json.dumps(result, ensure_ascii=False)])
        return result

    async def _collect_facts(self, employee_id: str, queries: Sequence[str],
                             limit: int) -> List[str]:
        """نفّذ الاستعلامات واجمع حقائقها — **يُستدعى داخل القفل دائماً**."""
        from graphiti_core.search.search_config_recipes import COMBINED_HYBRID_SEARCH_RRF

        config = COMBINED_HYBRID_SEARCH_RRF.model_copy(deep=True)
        config.limit = max(1, min(int(limit), 25))
        config.edge_config.sim_min_score = self._min_score
        config.node_config.sim_min_score = self._min_score

        facts: List[str] = []
        for q in queries:
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
        return _drop_near_duplicates(_dedup(facts))[:limit]

    async def _recent_facts_async(self, employee_id: str, limit: int) -> List[str]:
        """أحدث الحقائق الصالحة زمنياً — استعلام مباشر بلا embeddings.

        مخطط الحقائق يختلف بين المزوّدَين: Kuzu لا يدعم فهرسة خصائص العلاقات
        فيمثّلها graphiti كعقد وسيطة ``RelatesToNode_``، بينما في Neo4j هي
        علاقات ``RELATES_TO`` حقيقية.
        """
        if self._provider == "neo4j":
            query = (
                "MATCH ()-[e:RELATES_TO]->() WHERE e.group_id = $group_id "
                "AND e.expired_at IS NULL AND e.invalid_at IS NULL "
                "RETURN e.fact AS fact ORDER BY e.created_at DESC LIMIT $limit"
            )
        else:
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

        async def _guarded() -> List[str]:
            async with self._lock():
                return await self._recent_facts_async(employee_id, limit)

        fut = self._submit(_guarded())
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
            "driver": self._provider,
            "db_path": self._db_path if self._provider != "neo4j" else None,
            "min_score": self._min_score,
            "counters": dict(self.counters),
        }
        if not info["available"]:
            return info

        async def _counts() -> Dict[str, int]:
            out: Dict[str, int] = {}
            where = " WHERE n.group_id = $group_id" if employee_id else ""
            # في Neo4j الحقائق علاقات RELATES_TO؛ في Kuzu عقد RelatesToNode_
            fact_match = ("MATCH ()-[n:RELATES_TO]->()"
                          if self._provider == "neo4j" else "MATCH (n:RelatesToNode_)")
            queries = (("MATCH (n:Episodic)", "episodes"),
                       ("MATCH (n:Entity)", "entities"),
                       (fact_match, "facts"))
            async with self._lock():  # عدّ متزامن مع كتابة = انهيار العملية (Kuzu)
                for match, key in queries:
                    try:
                        records, _, _ = await self._graphiti.driver.execute_query(
                            f"{match}{where} RETURN count(n) AS c",
                            **({"group_id": employee_id} if employee_id else {}),
                        )
                        out[key] = int((records or [{}])[0].get("c") or 0)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("Graphiti stats %s failed: %s", key, exc)
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


class GraphitiStoreProxy:
    """واجهة GraphitiStore نفسها، لكن العمل كله في **عملية فرعية معزولة**.

    لماذا: Kuzu ينهار أصلياً (0xC0000005) أثناء كتابات Graphiti حتى على قاعدة
    جديدة خارج OneDrive وبوصولٍ متسلسل بالكامل — أي علة داخلية في محرك ميت
    (المشروع مؤرشف، 0.11.3 آخر إصدار، وgraphiti تعلن سائقه مهجوراً). داخل
    العملية الأم كان كل انهيار يقتل الباك إند كاملاً («يختفي الموظف وسجل
    المحادثة»). هنا الانهيار يقتل العامل وحده: النداء الجاري يعيد قيمة آمنة،
    والعامل يُعاد تشغيله عند النداء التالي، والمنصة لا تشعر بشيء.

    البروتوكول أسطر JSON متزامنة (طلب واحد في كل لحظة — الوصول إلى Kuzu
    متسلسل أصلاً بالتصميم، فلا نخسر تفاوتاً حقيقياً). ``GRAPHITI_ISOLATED=0``
    يعيد الوضع القديم داخل العملية (للتشخيص).
    """

    _SPAWN_COOLDOWN = 15.0  # لا نعيد إطلاق عامل ينهار عند إقلاعه في حلقة محمومة

    def __init__(self) -> None:
        self._proc = None
        self._io_lock = threading.Lock()
        self._next_id = 0
        self._spawn_failed_at = 0.0

    # ---- lifecycle ----
    def _spawn(self) -> bool:
        import subprocess

        if self._proc is not None and self._proc.poll() is None:
            return True
        if time.monotonic() - self._spawn_failed_at < self._SPAWN_COOLDOWN:
            return False
        root = Path(__file__).resolve().parents[3]
        try:
            self._proc = subprocess.Popen(
                [sys.executable, "-m", "app.aeos.memory.graphiti_worker"],
                cwd=str(root),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("graphiti worker spawn failed: %s", exc)
            self._spawn_failed_at = time.monotonic()
            return False
        logger.info("graphiti worker started (pid=%s)", self._proc.pid)
        return True

    def _call(self, op: str, rpc_timeout: float = 30.0, default: Any = None,
              **kwargs: Any) -> Any:
        """طلب/ردّ واحد. موت العامل (انهيار Kuzu) يعيد القيمة الآمنة فوراً."""
        with self._io_lock:
            if not self._spawn():
                return default
            proc = self._proc
            self._next_id += 1
            rid = self._next_id
            try:
                proc.stdin.write(json.dumps(
                    {"id": rid, "op": op, "kwargs": kwargs}, ensure_ascii=False
                ) + "\n")
                proc.stdin.flush()
                # readline يعود بسطر فارغ إذا مات العامل (أنبوب مقطوع) — وهذا
                # بالضبط ما يحدث عند انهيار Kuzu الأصلي: نسجّل ونكمل حيّين.
                line = self._read_line(proc, rpc_timeout)
            except Exception as exc:  # noqa: BLE001
                logger.error("graphiti worker died mid-call (%s op=%s) — "
                             "will respawn on next use", exc, op)
                self._reap()
                return default
            if not line:
                code = proc.poll()
                logger.error(
                    "graphiti worker exited (code=%s) during op=%s — the native "
                    "kuzu crash is contained; respawning on next use", code, op)
                self._reap()
                return default
            try:
                resp = json.loads(line)
            except ValueError:
                logger.warning("graphiti worker garbled reply for op=%s", op)
                return default
            if not resp.get("ok"):
                logger.warning("graphiti worker op=%s failed: %s", op, resp.get("error"))
                return default
            return resp.get("result", default)

    def _read_line(self, proc, timeout: float) -> str:
        """اقرأ سطر الردّ بمهلة (القراءة المقفلة إلى الأبد تجمّد الدورة كلها)."""
        result: List[str] = []

        def _read() -> None:
            try:
                result.append(proc.stdout.readline())
            except Exception:  # noqa: BLE001
                result.append("")

        t = threading.Thread(target=_read, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            raise TimeoutError(f"no reply within {timeout:.0f}s")
        return result[0] if result else ""

    def _reap(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    # ---- نفس الواجهة العامة للمخزن ----
    @property
    def available(self) -> bool:
        return bool(self._call("available", rpc_timeout=120.0, default=False))

    def ingest(self, employee_id: str, payload: Union[str, Dict[str, Any]],
               name: str = "cycle",
               source_description: str = "aeos cognitive cycle") -> None:
        self._call("ingest", rpc_timeout=30.0, employee_id=employee_id,
                   payload=payload, name=name, source_description=source_description)

    def search(self, employee_id: str, query: str, limit: int = 10,
               timeout: float = 25.0) -> List[str]:
        return self._call("search", rpc_timeout=timeout + 10.0, default=[],
                          employee_id=employee_id, query=query,
                          limit=limit, timeout=timeout) or []

    def search_facts(self, employee_id: str, queries: Sequence[str], limit: int = 10,
                     timeout: float = 30.0, include_recent: bool = True) -> List[str]:
        return self._call("search_facts", rpc_timeout=timeout + 10.0, default=[],
                          employee_id=employee_id, queries=list(queries), limit=limit,
                          timeout=timeout, include_recent=include_recent) or []

    def facts_about(self, employee_id: str, entities: Sequence[str],
                    limit: int = 3, timeout: float = 30.0) -> Dict[str, List[str]]:
        return self._call("facts_about", rpc_timeout=timeout + 10.0, default={},
                          employee_id=employee_id, entities=list(entities),
                          limit=limit, timeout=timeout) or {}

    def recent_facts(self, employee_id: str, limit: int = 10,
                     timeout: float = 15.0) -> List[str]:
        return self._call("recent_facts", rpc_timeout=timeout + 10.0, default=[],
                          employee_id=employee_id, limit=limit, timeout=timeout) or []

    def stats(self, employee_id: Optional[str] = None,
              timeout: float = 15.0) -> Dict[str, Any]:
        return self._call("stats", rpc_timeout=timeout + 10.0,
                          default={"enabled": True, "available": False,
                                   "error": "graph worker unavailable"},
                          employee_id=employee_id, timeout=timeout)

    def clear(self, employee_id: str) -> None:
        self._call("clear", rpc_timeout=60.0, employee_id=employee_id)

    def shutdown(self) -> None:
        """أغلق العامل بلطف (EOF يمهله لإكمال كتابة جارية ثم يخرج)."""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.stdin.close()
            proc.wait(timeout=100)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass


# ---- process-wide singleton ---------------------------------------------
_store: Optional[Union[GraphitiStore, GraphitiStoreProxy]] = None
_store_lock = threading.Lock()


def get_graphiti_store() -> Union[GraphitiStore, GraphitiStoreProxy]:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                isolated = os.getenv("GRAPHITI_ISOLATED", "1").strip().lower() \
                    not in ("0", "false", "no")
                _store = GraphitiStoreProxy() if isolated else GraphitiStore()
    return _store
