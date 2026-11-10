"""
مهارات النظام (SKILL.md) — مهارات المنسّق العام، مدمجة لكل مستخدم تلقائياً.

الفرق عن مهارات الموظفين (``custom.py``):
- **مهارة موظف**: قاعدة سلوك يضيفها المالك لموظف بعينه (هدف + أفعال على X + حدود).
- **مهارة نظام**: معرفة إجرائية للمنسّق (كيف يُعدّ تقريراً أسبوعياً، كيف يخطط حملة…)،
  يضيفها **المطوّر** للنظام كله فتصل لمنسّق كل مستخدم دون أي إعداد.

الصيغة: المعيار المفتوح Agent Skills — مجلد لكل مهارة فيه ``SKILL.md``:

    ---
    name: team-weekly-report
    description: متى تُستخدم وماذا تفعل (هذا ما يراه المنسّق دائماً)
    allowed-tools: employee_report, list_employees      # اختياري
    ---
    التعليمات الكاملة (تُحمَّل فقط حين يقرّر المنسّق استخدامها)

الكشف التدريجي (progressive disclosure): المنسّق يرى الاسم والوصف فقط في موجّهه، ويطلب
النص الكامل بأداة ``use_system_skill`` عند الحاجة — فلا تتضخّم تعليماته بكل المهارات.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[3]
SKILLS_DIR = Path(os.getenv("SYSTEM_SKILLS_DIR") or _ROOT / "system" / "skills")
STATE_PATH = Path(os.getenv("SYSTEM_STATE_PATH") or _ROOT / "data" / "system_state.json")
FILE = "SKILL.md"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
#: نوعا المهارة في المجلد الواحد:
#: coordinator — تعليمات SKILL.md للمنسّق العام (مرفوعة أو مدمجة)
#: employee    — مهارة موظف بكود (skill.py) يقترح بها أفعاله في كل دورة
KINDS = ("coordinator", "employee")
_MAX_BODY = 100_000
_MAX_ZIP = 5 * 1024 * 1024
_lock = threading.Lock()


# ─────────────────────────── parsing ───────────────────────────
def parse(text: str) -> Dict[str, Any]:
    """SKILL.md ← {name, description, allowed_tools, version, body}. يرفع ValueError عند الخلل."""
    import yaml

    text = (text or "").lstrip("\ufeff")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        raise ValueError("SKILL.md يجب أن يبدأ بترويسة YAML بين سطرَي ---")
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"ترويسة YAML غير صالحة: {exc}") from exc
    if not isinstance(meta, dict):
        raise ValueError("الترويسة يجب أن تكون مفاتيح وقيماً")
    name = str(meta.get("name") or "").strip().lower()
    if not _NAME_RE.match(name):
        raise ValueError("name: حروف إنجليزية صغيرة وأرقام وشرطات فقط، حتى 64 حرفاً")
    desc = re.sub(r"\s+", " ", str(meta.get("description") or "")).strip()
    if not desc:
        raise ValueError("description مطلوب: هو ما يقرأه المنسّق ليقرّر متى يستخدم المهارة")
    body = m.group(2).strip()
    if not body:
        raise ValueError("نص التعليمات بعد الترويسة فارغ")
    if len(body) > _MAX_BODY:
        raise ValueError("التعليمات أطول من 100 ألف حرف")
    tools = meta.get("allowed-tools", meta.get("allowed_tools"))
    if isinstance(tools, str):
        tools = [t.strip() for t in re.split(r"[,\s]+", tools) if t.strip()]
    kind = str(meta.get("kind") or "coordinator").strip().lower()
    if kind not in KINDS:
        raise ValueError(f"kind: أحد {', '.join(KINDS)}")
    out = {"name": name, "description": desc[:1024], "kind": kind,
           "allowed_tools": [str(t) for t in tools] if isinstance(tools, list) else None,
           "version": str(meta.get("version") or ""), "body": body}
    if kind == "employee":
        cls = str(meta.get("class") or "").strip()
        if not re.match(r"^[A-Za-z_]\w*$", cls):
            raise ValueError("مهارة الموظف تحتاج class: اسم فئة Python في skill.py")
        caps = meta.get("uses-capabilities") or []
        out.update(entry=str(meta.get("entry") or "skill.py"), cls=cls,
                   order=int(meta.get("order") or 100),
                   uses_capabilities=[str(c) for c in caps] if isinstance(caps, list) else [])
    return out


def _state() -> Dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _save_state(st: Dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def _meta(slug: str) -> Dict[str, Any]:
    return (_state().get("skills") or {}).get(slug) or {}


# ─────────────────────────── read ───────────────────────────
def list_skills(enabled_only: bool = False, with_body: bool = False) -> List[Dict[str, Any]]:
    out = []
    for path in sorted(SKILLS_DIR.glob(f"*/{FILE}")):
        try:
            sk = parse(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            out.append({"slug": path.parent.name, "name": path.parent.name, "error": str(exc),
                        "enabled": False, "description": ""})
            continue
        meta = _meta(path.parent.name)
        enabled = bool(meta.get("enabled", True))
        if enabled_only and not enabled:
            continue
        resources = sorted(str(p.relative_to(path.parent)).replace("\\", "/")
                           for p in path.parent.rglob("*") if p.is_file() and p.name != FILE)
        item = {"slug": path.parent.name, **{k: v for k, v in sk.items() if k != "body"},
                "enabled": enabled, "builtin": bool(meta.get("builtin", not meta.get("added_by"))),
                "added_by": meta.get("added_by", ""), "added_at": meta.get("added_at", ""),
                "resources": resources[:30], "chars": len(sk["body"])}
        if with_body:
            item["body"] = sk["body"]
        out.append(item)
    return out


def get(slug: str) -> Optional[Dict[str, Any]]:
    slug = (slug or "").strip().lower()
    return next((s for s in list_skills(with_body=True) if s["slug"] == slug or s["name"] == slug), None)


def raw(slug: str) -> str:
    sk = get(slug)
    return (SKILLS_DIR / sk["slug"] / FILE).read_text(encoding="utf-8") if sk else ""


_RESOURCE_EXT = {".md", ".markdown", ".txt", ".json", ".yaml", ".yml"}
_MAX_RESOURCE = 60_000


def read_resource(slug: str, rel: str) -> str:
    """ملف نصي مرجعي داخل مجلد المهارة (SKILL.md يحيل إليه: references/…). لا خروج من المجلد
    ولا كود: المنسّق يقرأ المعرفة لا السكربتات، وطول محدود حتى لا يُغرق سياقه."""
    base = (SKILLS_DIR / slug).resolve()
    rel = (rel or "").strip().replace("\\", "/").split("#", 1)[0].lstrip("/")
    path = (base / rel).resolve()
    if base not in path.parents or not path.is_file():
        raise LookupError(f"ملف غير موجود في المهارة {slug}: {rel}")
    if path.suffix.lower() not in _RESOURCE_EXT:
        raise ValueError("يُقرأ من المهارة ملفات نصية مرجعية فقط (md/txt/json/yaml)")
    text = path.read_text(encoding="utf-8", errors="replace")
    return text if len(text) <= _MAX_RESOURCE else text[:_MAX_RESOURCE] + "\n…(مقتطع)"


def is_enabled(slug: str) -> bool:
    """تفعيل مهارة نظام من اللوحة — قراءة حالة فقط بلا تحليل كل SKILL.md (يُستدعى عند كل كتابة)."""
    return bool(_meta(slug).get("enabled", True))


def coordinator_skills() -> List[Dict[str, Any]]:
    return [s for s in list_skills(enabled_only=True)
            if not s.get("error") and s.get("kind", "coordinator") == "coordinator"]


# ─────────────────────────── employee skills (code) ───────────────────────────
def _employee_specs() -> List[Dict[str, Any]]:
    specs = [s for s in list_skills() if s.get("kind") == "employee" and not s.get("error")]
    specs.sort(key=lambda s: (s.get("order", 100), s["slug"]))
    return specs


def employee_skill_class(class_name: str):
    """فئة مهارة موظف باسمها (للأسماء القديمة في app.aeos.skills.builtin)."""
    from app.core.system_loader import load_module

    for s in _employee_specs():
        if s.get("cls") == class_name:
            mod = load_module(SKILLS_DIR / s["slug"] / s["entry"],
                              "moj_system.skills." + re.sub(r"\W", "_", s["slug"]))
            return getattr(mod, class_name)
    return None


def employee_skill_instances() -> List[Any]:
    """كل مهارات الموظفين من المجلد الرئيسي بترتيبها — تُسجَّل في SkillRegistry عند الإقلاع."""
    out = []
    for s in _employee_specs():
        if not s.get("enabled", True):
            continue
        cls = employee_skill_class(s["cls"])
        if cls is None:
            raise RuntimeError(f"employee skill {s['slug']}: class {s['cls']} not found")
        out.append(cls())
    return out


def catalog_brief(limit: int = 40) -> str:
    """ما يراه المنسّق دائماً: الاسم والوصف فقط (الكشف التدريجي)."""
    items = coordinator_skills()[:limit]
    if not items:
        return ""
    return ("مهارات النظام المتاحة لك (حمّل تعليمات أي منها بأداة use_system_skill عندما يطابق "
            "الطلب وصفها، ثم اتبعها):\n" + "\n".join(f"- {s['name']}: {s['description']}" for s in items))


# ─────────────────────────── write (developer) ───────────────────────────
def _dir_for(name: str) -> Path:
    path = (SKILLS_DIR / name).resolve()
    if path.parent != SKILLS_DIR.resolve():
        raise ValueError("اسم مهارة غير صالح")
    return path


def save_upload(filename: str, data: bytes, added_by: str = "", overwrite: bool = False) -> Dict[str, Any]:
    """SKILL.md مفرد أو .zip (مجلد مهارة: SKILL.md + ملفات مرجعية)."""
    ext = os.path.splitext(Path(filename or "").name)[1].lower()
    files: Dict[str, bytes] = {}
    if ext in (".md", ".markdown", ""):
        files[FILE] = data
    elif ext == ".zip":
        if len(data) > _MAX_ZIP:
            raise ValueError("الحزمة أكبر من 5MB")
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            if len(infos) > 200:
                raise ValueError("ملفات كثيرة جداً")
            names = [i.filename.replace("\\", "/") for i in infos]
            skill_md = [n for n in names if n.split("/")[-1] == FILE]
            if len(skill_md) != 1:
                raise ValueError("الحزمة يجب أن تحوي SKILL.md واحداً")
            prefix = skill_md[0][: -len(FILE)]
            for info, n in zip(infos, names):
                if not n.startswith(prefix):
                    continue
                rel = n[len(prefix):]
                if not rel or rel.startswith("/") or ".." in Path(rel).parts:
                    raise ValueError(f"مسار غير آمن: {n}")
                files[rel] = zf.read(info)
    else:
        raise ValueError("ارفع SKILL.md أو حزمة .zip")
    try:
        text = files[FILE].decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("SKILL.md يجب أن يكون UTF-8") from exc
    sk = parse(text)
    # المرفوع تعليمات للمنسّق فقط: مهارات الموظفين كودٌ جزء من النظام لا يُرفع من اللوحة
    if sk["kind"] != "coordinator":
        raise ValueError("المهارات المرفوعة للمنسّق العام فقط (kind: coordinator)")
    with _lock:
        target = _dir_for(sk["name"])
        if target.exists() and _is_employee_dir(target):
            raise ValueError(f"'{sk['name']}' اسم مهارة موظف مدمجة — اختر اسماً آخر")
        if target.exists() and not overwrite:
            raise ValueError(f"مهارة باسم '{sk['name']}' موجودة — عدّلها أو احذفها أولاً")
        tmp = SKILLS_DIR / f".upload_{sk['name']}"
        shutil.rmtree(tmp, ignore_errors=True)
        for rel, content in files.items():
            dest = (tmp / rel).resolve()
            if tmp.resolve() not in dest.parents:
                raise ValueError(f"مسار غير آمن: {rel}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
        if target.exists():
            shutil.rmtree(target)
        tmp.replace(target)
        st = _state()
        st.setdefault("skills", {})[sk["name"]] = {
            "enabled": True, "builtin": False, "added_by": added_by,
            "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        _save_state(st)
    return get(sk["name"])


def _is_employee_dir(path: Path) -> bool:
    try:
        return parse((path / FILE).read_text(encoding="utf-8")).get("kind") == "employee"
    except (OSError, ValueError):
        return False


def update(slug: str, *, enabled: Optional[bool] = None, content: Optional[str] = None) -> Dict[str, Any]:
    sk = get(slug)
    if sk is None:
        raise KeyError(slug)
    if sk.get("kind") == "employee":
        raise ValueError("مهارات الموظفين المدمجة جزء من كود النظام: تُعدَّل في system/skills/"
                         f"{sk['slug']}/skill.py لا من اللوحة")
    with _lock:
        if content is not None:
            new = parse(content)
            if new["name"] != sk["slug"]:
                raise ValueError("تغيير اسم المهارة غير مسموح من التعديل — ارفعها باسم جديد")
            (SKILLS_DIR / sk["slug"] / FILE).write_text(content, encoding="utf-8")
        if enabled is not None:
            st = _state()
            st.setdefault("skills", {}).setdefault(sk["slug"], {})["enabled"] = bool(enabled)
            _save_state(st)
    return get(sk["slug"])


def delete(slug: str) -> bool:
    sk = get(slug)
    if sk is None:
        return False
    if sk.get("kind") == "employee":
        raise ValueError("مهارات الموظفين المدمجة لا تُحذف من اللوحة")
    with _lock:
        shutil.rmtree(_dir_for(sk["slug"]), ignore_errors=True)
        st = _state()
        (st.get("skills") or {}).pop(sk["slug"], None)
        _save_state(st)
    return True
