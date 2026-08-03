"""
ذاكرة رصد الإشارات (Signal Cache) — تفصل "جمع البيانات" عن "اتخاذ القرار".

بدل أن تمسح كل دورة التايم لاين والترندات وأهم البوستات والأخبار من جديد
(عملية بطيئة عبر المتصفح)، تُحفَظ نتيجة المسح كلقطة (snapshot) في مساحة عمل
الموظف، وتعاد قراءتها في الدورات التالية ما دامت حديثة (الافتراضي: 60 دقيقة).

- الصلاحية قابلة للضبط لكل موظف عبر ``business_rules["observation_cache_minutes"]``
  (0 = تعطيل الكاش والمسح حيّاً في كل دورة).
- يمكن فرض مسح حي لمرة واحدة عبر ``event.payload["force_fresh"] = True``.
- آمنة ضد التكرار: منطق منع إعادة التفاعل/تكرار الموضوع الموجود في المهارات
  يضمن ألا يتفاعل الموظف مع نفس التغريدة أو الموضوع مرتين حتى مع نفس اللقطة.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

if TYPE_CHECKING:
    from ..workspace.manager import Workspace

logger = logging.getLogger("aeos.cognition.signal_cache")

_FILE = "files/signals_snapshot.json"

# مفاتيح الـ scratch التي تملؤها قدرات المسح وتستهلكها المهارات
SNAPSHOT_SCRATCH_KEYS = (
    "timeline", "x_trends", "trend_tweets", "trend_hashtags",
    "matched_hashtag", "hashtag_tweets", "top_posts",
    "specialty_news", "news", "mentions", "trends",
)

# حقول الملاحظات الخاصة بالمصادر (تُستعاد مع اللقطة؛ أخطاء المسح لا تُخزَّن)
_OBS_PREFIXES = (
    "timeline_", "x_trends_", "x_top_trend", "trend_", "matched_hashtag",
    "hashtag_", "top_posts_", "specialty_news_", "mentions_", "trends_",
    "news_",
)


def load_snapshot(workspace: "Workspace") -> Tuple[Optional[Dict[str, Any]], float]:
    """اقرأ اللقطة المخزنة؛ يعيد (اللقطة أو None، عمرها بالدقائق)."""
    try:
        raw = workspace.read_text(_FILE)
        if not raw:
            return None, 0.0
        snap = json.loads(raw)
        fetched_at = datetime.fromisoformat(snap["fetched_at"])
        age_min = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 60.0
        return snap, age_min
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to load signals snapshot: %s", exc)
        return None, 0.0


def save_snapshot(workspace: "Workspace", scratch: Dict[str, Any],
                  observations: Dict[str, Any]) -> None:
    """احفظ نتيجة المسح الحي كلقطة قابلة لإعادة الاستخدام في الدورات القادمة."""
    try:
        obs = {
            k: v for k, v in observations.items()
            if any(k.startswith(p) for p in _OBS_PREFIXES) and not k.endswith("_error")
        }
        snap = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "scratch": {
                k: scratch.get(k) for k in SNAPSHOT_SCRATCH_KEYS
                if scratch.get(k) is not None
            },
            "observations": obs,
        }
        workspace.write_text(_FILE, json.dumps(snap, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to save signals snapshot: %s", exc)


def restore_snapshot(snap: Dict[str, Any], scratch: Dict[str, Any],
                     observations: Dict[str, Any]) -> None:
    """استعد اللقطة إلى scratch/observations الدورة الحالية."""
    for k, v in (snap.get("scratch") or {}).items():
        scratch[k] = v
    observations.update(snap.get("observations") or {})


def snapshot_has_signals(snap: Dict[str, Any]) -> bool:
    """هل تحتوي اللقطة على بيانات فعلية تستحق إعادة الاستخدام؟"""
    sc = snap.get("scratch") or {}
    return any(sc.get(k) for k in SNAPSHOT_SCRATCH_KEYS)
