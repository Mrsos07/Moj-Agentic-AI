"""
الهوية المعتمدة للموظف على القرص — «ما يعتمده المالك هو ما يُطبَّق على X».

كانت المعاينة تعرض صورة/هيدر من سيرفر الهويات (ملفات مؤقتة تُحذف بعد ساعة)، والاعتماد
يمرّر روابطها إلى خيط خلفي لا يُحفظ في أي مكان: إعادة تشغيل الخادم (نشر يسحب الكود
فيعيد uvicorn التحميل) قتلت التطبيق بصمت، فبقي على الحساب ما وضعه مسار الإنشاء
التلقائي — صورة لم يرها المالك قط.

- ``create_pending``: لحظة الاعتماد تُنسخ بايتات الصور نفسها من مجلد سيرفر الهويات
  (روابط المعاينة فقط — لا جلب شبكي ولا روابط خارجية) إلى مجلد الموظف مع sha256،
  ويُحفظ الاعتماد في ``pending.json`` حتى يكتمل التطبيق (يُستأنف بعد إعادة التشغيل).
- ``applied.json``: آخر هوية طُبّقت فعلاً. وجودها أو وجود اعتماد معلّق يمنع مسار
  الإنشاء التلقائي من الكتابة فوق ما اعتمده المالك.
- ``preview.json``: آخر هوية عُرضت في الشات، فيطبّق «طبّق الهوية» ما رآه المالك لا
  هوية جديدة تُولَّد لحظة التطبيق.
- ``x_profile.json``: هوية الحساب كما قُرئت من X فعلاً (اسم/نبذة/صورة/هيدر) — تعرضها
  بطاقة الموظف. تتحدّث مع كل قراءة أداء، وبعد كل تطبيق معتمد، وبزر التحديث في البطاقة.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(os.getenv("IDENTITY_STORE_DIR")
             or Path(__file__).resolve().parents[3] / "data" / "employees")
# صور سيرفر الهويات: /static/generated/<uuid hex>.<ext> (gemini_service._save_image_bytes)
_GENERATED = re.compile(r"/static/generated/([0-9a-f]{32})\.(png|jpe?g|webp)")
_ID = re.compile(r"[0-9A-Za-z_-]{1,64}")
KINDS = ("avatar", "banner")
PREVIEW_TTL = 3600           # = IMAGE_MAX_AGE_SECONDS في سيرفر الهويات: بعدها تُحذف صور المعاينة
MAX_ATTEMPTS = 3             # تطبيقات قطعتها إعادة التشغيل قبل التخلي وإبلاغ المالك
PENDING_MAX_AGE = 24 * 3600  # لا يُطبَّق بعد إعادة التشغيل اعتماد عمره أكثر من يوم
_LOCK = threading.RLock()


class IdentityError(RuntimeError):
    """خطأ برسالة عربية تُعرض للمالك كما هي."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dir(employee_id: str) -> Path:
    if not _ID.fullmatch(str(employee_id or "")):
        raise IdentityError("معرّف موظف غير صالح.")
    return _ROOT / employee_id / "identity"


def _read(employee_id: str, name: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads((_dir(employee_id) / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(employee_id: str, name: str, data: Dict[str, Any]) -> None:
    d = _dir(employee_id)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{name}.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, d / name)


def _remove(employee_id: str, name: str) -> None:
    (_dir(employee_id) / name).unlink(missing_ok=True)


def _is_image(data: bytes) -> bool:
    return (data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff")
            or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"))


def generated_dir() -> Path:
    from app.services import identity_bridge

    return identity_bridge.IDENTITY_DIR / "static" / "generated"


def source_file(url: str) -> Path:
    """رابط صورة المعاينة ← ملفها في مجلد سيرفر الهويات (غير ذلك مرفوض)."""
    from app.services import identity_bridge

    url = str(url or "").strip()
    base = identity_bridge.IDENTITY_BASE_URL
    m = _GENERATED.fullmatch(url[len(base):] if url.startswith(base + "/") else url)
    if not m:
        raise IdentityError("رابط صورة غير معروف — تُقبل صور معاينة الهوية فقط. أعد توليد المعاينة.")
    f = generated_dir() / f"{m.group(1)}.{m.group(2)}"
    if not f.is_file():
        raise IdentityError("صور المعاينة لم تعد متاحة (يحذفها مولّد الهويات بعد ساعة من توليدها) — "
                            "أعد توليد المعاينة ثم اعتمدها.")
    return f


# ─────────────────────────── الاعتماد والتطبيق ───────────────────────────
def create_pending(cfg, user_id: Optional[int], identity: Dict[str, Any]) -> Dict[str, Any]:
    """يثبّت الهوية المعتمدة: نسخة من بايتات صورها نفسها + اعتماد معلّق حتى يكتمل التطبيق."""
    approval_id = uuid.uuid4().hex[:12]
    d = _dir(cfg.id)
    media: Dict[str, Dict[str, str]] = {}
    for kind in KINDS:
        url = str(identity.get(f"{kind}_url") or "").strip()
        if not url:
            continue
        src = source_file(url)
        data = src.read_bytes()
        if not _is_image(data):
            raise IdentityError("ملف صورة المعاينة تالف — أعد توليد المعاينة.")
        d.mkdir(parents=True, exist_ok=True)
        name = f"{approval_id}-{kind}{src.suffix.lower()}"
        (d / name).write_bytes(data)
        media[kind] = {"file": name, "sha256": hashlib.sha256(data).hexdigest(), "source": url}
    record = {
        "approval_id": approval_id, "employee_id": cfg.id,
        "account": cfg.account_username, "user_id": user_id,
        "name": str(identity.get("name") or "").strip(),
        "bio": str(identity.get("bio") or "").strip(),
        "media": media, "approved_at": _now().isoformat(), "attempts": 0,
    }
    if not (record["name"] or record["bio"] or media):
        raise IdentityError("لا توجد حقول قابلة للتطبيق في الهوية.")
    with _LOCK:
        _write(cfg.id, "pending.json", record)
        _prune(cfg.id)
    return record


def media_path(record: Dict[str, Any], kind: str) -> str:
    m = (record.get("media") or {}).get(kind)
    return str(_dir(record["employee_id"]) / m["file"]) if m else ""


def is_current(record: Dict[str, Any]) -> bool:
    """الاعتماد ما زال هو المعلّق (لم يحلّ محله اعتماد أحدث ولم يُنهَ)."""
    cur = _read(record["employee_id"], "pending.json")
    return bool(cur and cur.get("approval_id") == record.get("approval_id"))


def start_attempt(record: Dict[str, Any]) -> None:
    with _LOCK:
        if is_current(record):
            record["attempts"] = int(record.get("attempts") or 0) + 1
            _write(record["employee_id"], "pending.json", record)


def finish(record: Dict[str, Any], ok: bool, confirmed: Optional[Dict[str, str]] = None) -> None:
    eid = record["employee_id"]
    with _LOCK:
        if ok:
            _write(eid, "applied.json", {**record, "applied_at": _now().isoformat(),
                                         "confirmed": confirmed or {}})
        if is_current(record):
            _remove(eid, "pending.json")
        _prune(eid)


def expired(record: Dict[str, Any]) -> bool:
    try:
        approved = datetime.fromisoformat(record["approved_at"])
    except (KeyError, TypeError, ValueError):
        return True
    return (_now() - approved).total_seconds() > PENDING_MAX_AGE


def all_pending() -> List[Dict[str, Any]]:
    out = []
    for p in (_ROOT.glob("*/identity/pending.json") if _ROOT.is_dir() else []):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if rec.get("employee_id") == p.parent.parent.name:
            out.append(rec)
    return out


def has_owner_identity(employee_id: str) -> bool:
    """للمالك هوية معتمدة (معلّقة أو مطبّقة) ⇒ لا يكتب فوقها أي مسار تلقائي."""
    d = _dir(employee_id)
    return (d / "pending.json").exists() or (d / "applied.json").exists()


def _prune(employee_id: str) -> None:
    """يحذف الصور المثبّتة التي لا يشير إليها اعتماد معلّق ولا آخر هوية طُبّقت."""
    keep = set()
    for name in ("pending.json", "applied.json"):
        keep.update(m.get("file") for m in ((_read(employee_id, name) or {}).get("media") or {}).values())
    for f in _dir(employee_id).glob("*-*.*"):
        if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") and f.name not in keep:
            f.unlink(missing_ok=True)


# ─────────────────────────── معاينة الشات ───────────────────────────
def remember_preview(employee_id: str, user_id: Optional[int], identity: Dict[str, Any]) -> None:
    with _LOCK:
        _write(employee_id, "preview.json", {
            "user_id": user_id, "at": _now().isoformat(),
            "identity": {k: identity.get(k) or "" for k in ("name", "bio", "avatar_url", "banner_url")},
        })


def recent_preview(employee_id: str, user_id: Optional[int],
                   max_age: int = PREVIEW_TTL) -> Optional[Dict[str, Any]]:
    """آخر هوية عُرضت على هذا المالك خلال صلاحية صورها، وإلا None."""
    rec = _read(employee_id, "preview.json")
    if not rec or rec.get("user_id") != user_id:
        return None
    try:
        age = (_now() - datetime.fromisoformat(rec["at"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        return None
    return rec if age <= max_age else None


def forget_preview(employee_id: str) -> None:
    with _LOCK:
        _remove(employee_id, "preview.json")


# ─────────────────────────── هوية الحساب كما على X ───────────────────────────
def save_x_profile(employee_id: str, handle: str, header: Dict[str, Any],
                   source: str) -> Optional[Dict[str, Any]]:
    """يحفظ رأس الحساب كما قُرئ من X. قراءة بلا اسم ولا صورة لا تمحو لقطة سابقة صالحة."""
    if not (header and (header.get("name") or header.get("avatar_url"))):
        return None
    snap = {k: str(header.get(k) or "") for k in ("name", "bio", "avatar_url", "banner_url")}
    snap.update(handle=str(handle or "").lstrip("@"), fetched_at=_now().isoformat(), source=source)
    with _LOCK:
        _write(employee_id, "x_profile.json", snap)
    return snap


def load_x_profile(employee_id: str) -> Optional[Dict[str, Any]]:
    try:
        return _read(employee_id, "x_profile.json")
    except IdentityError:
        return None


def purge(employee_id: str) -> None:
    shutil.rmtree(_dir(employee_id), ignore_errors=True)
