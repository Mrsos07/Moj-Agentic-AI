"""
Persona-driven X identity for AI employees.

Turns the employee's persona into a full visual identity (display name, bio,
profile picture, header image) via the app/identity Gemini service, then
applies it to the connected X account through app/x (`/api/profile/update`,
Playwright with the account cookies).

Generation (~30-60s for two images) plus the browser profile update (~60s+)
take minutes, so the apply runs in a **background thread** and the result is
pushed to the user's chat when done — same pattern as employee_data.

What the owner approved is exactly what lands on X: the approved images are
pinned (byte copies, see identity_store) at approval time, uploaded as files,
and an approval interrupted by a server restart is resumed on startup.
"""
from __future__ import annotations

import logging
import threading
from ..security.gateway import carry
from typing import Any, Dict, Optional

logger = logging.getLogger("aeos.identity_service")

# كيانات المستخدم الصريحة → مفاتيح معايير التوليد (تتقدّم على المستنتَج)
_ENTITY_TO_PARAM = (
    ("nationality", "nationality"),
    ("orientation", "orientation"),
    ("gender", "gender"),
    ("bio_length", "bioLength"),
    ("skin_tone", "skinTone"),
)


def persona_identity_params(persona) -> Dict[str, Any]:
    """يحوّل برسونا الموظف إلى معايير توليد هوية — الوصف هو المُدخل الأهم."""
    # الجنسية من اللهجة/التعليمات
    blob = f"{persona.dialect} {persona.instructions} {persona.bio}".lower()
    nationality = "سعودي"
    for stem, nat in [("سعود", "سعودي"), ("مصر", "مصري"), ("إمارات", "إماراتي"),
                      ("امارات", "إماراتي"), ("كويت", "كويتي"), ("قطر", "قطري"),
                      ("بحرين", "بحريني"), ("عمان", "عماني"), ("أردن", "أردني"),
                      ("اردن", "أردني"), ("لبنان", "لبناني"), ("عراق", "عراقي"),
                      ("سوري", "سوري"), ("فلسطين", "فلسطيني"), ("مغرب", "مغربي"),
                      ("جزائر", "جزائري"), ("تونس", "تونسي")]:
        if stem in blob:
            nationality = nat
            break

    # التوجه العام من الاهتمامات/الدور (قائمة ORIENTATIONS في app/identity)
    topics = " ".join(persona.interests) + " " + persona.role
    orientation = "عام"
    for stem, orient in [("سياس", "سياسي"), ("اقتصاد", "اقتصادي"), ("تقن", "تقني"),
                         ("برمج", "تقني"), ("رياض", "رياضي"), ("دين", "ديني"),
                         ("تعليم", "تعليمي"), ("ثقاف", "ثقافي"), ("فن", "فني"),
                         ("فكاه", "فكاهي"), ("ساخر", "ساخر"), ("اجتماع", "اجتماعي")]:
        if stem in topics:
            orientation = orient
            break

    # أسلوب البايو من اللغة/اللهجة
    if (persona.language or "ar").startswith("en"):
        bio_style = "إنجليزي"
    elif persona.dialect:
        bio_style = "عامية"
    else:
        bio_style = "فصحى"

    # الوصف: هو ما يبني عليه Gemini الاسم واليوزر والبايو والصور بالكامل
    parts = []
    if persona.display_name:
        parts.append(f"الاسم: {persona.display_name}.")
    if persona.role:
        parts.append(f"يعمل كـ{persona.role}.")
    if persona.bio:
        parts.append(persona.bio)
    if persona.interests:
        parts.append("مهتم بـ: " + "، ".join(persona.interests[:5]) + ".")
    if persona.tone:
        parts.append(f"أسلوبه {persona.tone}.")
    if persona.instructions:
        parts.append(persona.instructions)
    description = " ".join(parts)[:480] or f"شخصية {orientation} من {nationality}."

    return {
        "description": description,
        "nationality": nationality,
        "orientation": orientation,
        "bioLength": "متوسط",
        "imageType": "شخص",
        "headerImageType": "طبيعة",
        "gender": "رجل",
        "skinTone": "حنطي",
        "headerText": "",
        "bioStyle": bio_style,
        "useCustomPrompts": False,
        "customProfilePicPrompt": "",
        "customHeaderImagePrompt": "",
    }


# ─────────────────────────── public API ───────────────────────────
def generate_preview(cfg, entities: Optional[Dict[str, Any]] = None,
                     with_images: bool = True) -> Dict[str, Any]:
    """يولّد هوية كاملة من البرسونا (بلا تطبيق) — للمعاينة في الواجهة.

    Returns ``{name, username, bio, avatar_url, banner_url}`` (الصور مسارات نسبية، انظر _preview_path).
    Raises ``RuntimeError`` with a user-facing Arabic message on failure.
    """
    from app.services import identity_bridge
    from . import identity_store

    entities = entities or {}
    if not identity_bridge.start_identity_server():
        raise RuntimeError("تعذّر تشغيل سيرفر توليد الهويات — "
                           "تأكد أن OPENROUTER_API_KEY موجود في ملف .env الرئيسي.")

    params = persona_identity_params(cfg.persona)
    for src, dst in _ENTITY_TO_PARAM:
        if entities.get(src):
            params[dst] = entities[src]

    result = identity_bridge.generate_profile(params, with_images=with_images)
    if not result.get("success"):
        raise RuntimeError(f"فشل توليد الهوية: {result.get('error', 'خطأ غير معروف')}")
    data = result.get("data", {})

    identity = {
        "name": (data.get("name") or "").strip(),
        "username": (data.get("username") or "").strip(),
        "bio": (data.get("bio") or "").strip(),
        "avatar_url": identity_store.preview_url(cfg, data.get("profilePictureUrl")),
        "banner_url": identity_store.preview_url(cfg, data.get("headerImageUrl")),
    }

    # الهوية من التعليمات والبرسونا فقط: ما كتبه المستخدم في البرسونا
    # (الاسم الظاهر/النبذة) يتقدّم دائماً على اختراعات الـLLM — التوليد يكمّل
    # الناقص فقط (الصور، وأي حقل تركه المستخدم فارغاً).
    persona = getattr(cfg, "persona", None)
    if persona is not None:
        if (getattr(persona, "display_name", "") or "").strip():
            identity["name"] = persona.display_name.strip()
        if (getattr(persona, "bio", "") or "").strip():
            identity["bio"] = persona.bio.strip()

    if not any([identity["name"], identity["bio"],
                identity["avatar_url"], identity["banner_url"]]):
        raise RuntimeError("التوليد لم يُرجع أي حقول قابلة للتطبيق.")
    return identity


# الحقول القابلة للتوليد المنفصل → مفتاحها في dict الهوية
IDENTITY_FIELDS = ("avatar", "banner", "name", "bio")


def generate_field(cfg, field: str, prompt: str = "") -> Dict[str, str]:
    """يعيد توليد عنصر واحد من الهوية (صورة/هيدر/اسم/بايو) من البرسونا،
    مع برومبت اختياري من المستخدم يوجّه التعديل («اجعل الخلفية زرقاء»…).

    برومبت المستخدم هنا طلب صريح، فيتقدّم على قفل «البرسونا أولاً» الذي
    يفرضه generate_preview على الاسم/البايو.

    Returns ``{<identity_key>: <new_value>}`` — نفس مفاتيح dict الهوية.
    """
    from app.services import identity_bridge
    from . import identity_store

    if field not in IDENTITY_FIELDS:
        raise RuntimeError(f"حقل غير معروف: {field}")
    if not identity_bridge.start_identity_server():
        raise RuntimeError("تعذّر تشغيل سيرفر توليد الهويات — "
                           "تأكد أن OPENROUTER_API_KEY موجود في ملف .env الرئيسي.")

    params = persona_identity_params(cfg.persona)
    prompt = (prompt or "").strip()
    if prompt:
        params["description"] = (
            f"{params['description']} — طلب تعديل من المستخدم (له الأولوية): {prompt}"
        )[:480]

    if field in ("avatar", "banner"):
        prompts_res = identity_bridge.construct_prompts(params)
        if not prompts_res.get("success"):
            raise RuntimeError(f"فشل بناء برومبت الصورة: "
                               f"{prompts_res.get('error') or prompts_res.get('message', 'خطأ غير معروف')}")
        key = "profilePicPrompt" if field == "avatar" else "headerImagePrompt"
        img_prompt = prompts_res.get("data", {}).get(key, "")
        if prompt:
            # وجّه مولّد الصورة نفسه بطلب المستخدم، لا الوصف العام فقط
            img_prompt = f"{img_prompt} IMPORTANT modification requested by the user: {prompt}."
        img_res = identity_bridge.generate_image(
            img_prompt, "profile" if field == "avatar" else "header")
        if not img_res.get("success"):
            raise RuntimeError(f"فشل توليد الصورة: "
                               f"{img_res.get('error') or img_res.get('message', 'خطأ غير معروف')}")
        url = identity_store.preview_url(cfg, img_res.get("data", {}).get("imageUrl"))
        if not url:
            raise RuntimeError("المولّد لم يُرجع رابط صورة.")
        return {("avatar_url" if field == "avatar" else "banner_url"): url}

    if field == "bio":
        res = identity_bridge.regenerate_bio(params)
        if not res.get("success"):
            raise RuntimeError(f"فشل توليد البايو: "
                               f"{res.get('error') or res.get('message', 'خطأ غير معروف')}")
        bio = (res.get("data", {}).get("bio") or "").strip()
        if not bio:
            raise RuntimeError("المولّد لم يُرجع بايو.")
        return {"bio": bio}

    # field == "name"
    res = identity_bridge.regenerate_text(params)
    if not res.get("success"):
        raise RuntimeError(f"فشل توليد الاسم: "
                           f"{res.get('error') or res.get('message', 'خطأ غير معروف')}")
    name = (res.get("data", {}).get("name") or "").strip()
    if not name:
        raise RuntimeError("المولّد لم يُرجع اسماً.")
    return {"name": name}


def request_apply_identity(cfg, user_id: Optional[int],
                           entities: Optional[Dict[str, Any]] = None,
                           identity: Optional[Dict[str, Any]] = None) -> str:
    """Spawn the (generate+)apply in the background and return an immediate ack.

    إن مُرّرت ``identity`` (معاينة اعتمدها المالك) تُثبَّت صورها **الآن** — بايتات الصور
    نفسها في مجلد الموظف — ثم تُطبَّق كما هي بلا إعادة توليد. ترفع ``IdentityError``
    إن لم تعد صور المعاينة متاحة: لا تُطبَّق أبداً صورة غير التي اعتمدها المالك."""
    from . import identity_store

    from app.aeos.security import gateway
    employee = gateway.owner(cfg.id)
    if employee.user_id != user_id or employee.account_username != cfg.account_username:
        gateway._deny("identity-owner")
    if not identity:
        identity = generate_preview(cfg, entities or {})
        identity_store.remember_preview(cfg.id, user_id, identity)
        return f"أُعدّت معاينة جديدة للموظف @{cfg.account_username}. راجعها ثم اطلب تطبيق الهوية."
    gateway.decision("identity.apply", cfg.id, {"account": cfg.account_username, "identity": identity}, sensitive=True)
    record = identity_store.create_pending(cfg, user_id, identity)
    _start(cfg, record)
    what = " + ".join(lbl for lbl, on in (
        ("اسم", record["name"]), ("بايو", record["bio"]),
        ("صورة", "avatar" in record["media"]), ("هيدر", "banner" in record["media"])) if on)
    return (f"🪩 جارِ تطبيق الهوية التي اعتمدتها على حساب **@{cfg.account_username}** "
            f"في X ({what}) — بالصورة والهيدر نفسيهما كما في المعاينة. قد يستغرق ذلك "
            "دقيقة، وستصلك النتيجة المؤكَّدة في محادثة الموظف. ⏳")


def apply_from_chat(cfg, user_id: Optional[int], entities: Optional[Dict[str, Any]] = None) -> str:
    """«طبّق الهوية» في الشات: تُطبَّق آخر هوية عُرضت على المالك (خلال صلاحية صورها).

    كان الأمر يولّد هوية جديدة لحظة التطبيق، فتنزل على الحساب صورة غير التي رآها."""
    from . import identity_store

    shown = identity_store.recent_preview(cfg.id, user_id)
    if not shown:
        return request_apply_identity(cfg, user_id, entities)
    ack = request_apply_identity(cfg, user_id, identity=shown["identity"])
    identity_store.forget_preview(cfg.id)
    return ack


def resume_pending() -> int:
    """يستأنف اعتمادات قطعتها إعادة تشغيل الخادم (نشر/‏reload) — يُستدعى عند الإقلاع.

    كان التطبيق خيطاً خلفياً يموت مع العملية بلا أثر ولا رسالة، فيبقى على الحساب ما
    كان قبله ويرى المالك «صورة غير التي اعتمدها»."""
    from app.aeos.bootstrap import get_platform

    from . import identity_store
    from .employee_data import _push

    resumed = 0
    for record in identity_store.all_pending():
        cfg = get_platform().repository.get(record["employee_id"])
        if cfg is None:
            identity_store.finish(record, ok=False)
            continue
        if (int(record.get("attempts") or 0) >= identity_store.MAX_ATTEMPTS
                or identity_store.expired(record)):
            identity_store.finish(record, ok=False)
            _push(record.get("user_id"),
                  f"⚠️ لم يكتمل تطبيق الهوية التي اعتمدتها على @{cfg.account_username} "
                  "(قطعته إعادة تشغيل الخادم) — أعد توليد المعاينة واعتمادها من بطاقة الموظف.", cfg)
            continue
        _push(record.get("user_id"),
              f"▶️ أُعيد تشغيل الخادم أثناء تطبيق الهوية التي اعتمدتها على @{cfg.account_username} "
              "— أستأنف التطبيق الآن بالصورة والهيدر نفسيهما.", cfg)
        _start(cfg, record)
        resumed += 1
    return resumed


# ─────────────────────────── worker ───────────────────────────
def _start(cfg, record: Dict[str, Any]) -> None:
    threading.Thread(target=carry(_run), args=(cfg, record), daemon=True,
                     name=f"identity-apply-{cfg.account_username}").start()


def _run(cfg, record: Dict[str, Any]) -> None:
    from . import identity_store
    from .employee_data import _push

    try:
        text = _apply(cfg, record)
    except Exception as exc:  # noqa: BLE001
        logger.exception("apply identity failed for %s", cfg.account_username)
        identity_store.finish(record, ok=False)
        text = f"⚠️ تعذّر تطبيق الهوية على @{cfg.account_username}: {exc}"
    if text:
        _push(record.get("user_id"), text, cfg)


def _apply(cfg, record: Dict[str, Any]) -> Optional[str]:
    from app.services import x_bridge

    from . import identity_store

    # اعتماد أحدث حلّ محله ⇒ لا نطبّق هوية تجاوزها المالك
    if not identity_store.is_current(record):
        return None
    identity_store.start_attempt(record)
    name, bio, media = record["name"], record["bio"], record["media"]
    before = identity_store.load_x_profile(cfg.id)

    # البايتات المثبّتة نفسها تُرفع كملفات (Playwright بكوكيز الحساب عبر app/x)
    label = cfg.cookie_label or cfg.account_username
    res = x_bridge.update_profile(
        label, name=name, bio=bio, headless=True,
        avatar_path=identity_store.media_path(record, "avatar"),
        banner_path=identity_store.media_path(record, "banner"),
    )
    if not res.get("success"):
        identity_store.finish(record, ok=False)
        err = res.get("error") or res.get("message") or "خطأ غير معروف"
        return (f"⚠️ لم يكتمل تطبيق الهوية التي اعتمدتها على @{cfg.account_username}: {err}\n"
                "أعد المحاولة من بطاقة الموظف («هوية X من الشخصية»).")
    confirmed = res.get("media") or {}
    identity_store.finish(record, ok=True, confirmed=confirmed)

    # مزامنة البرسونا: الاسم والبايو المعروضان في X هما الآن هوية الموظف
    _sync_persona(cfg, name, bio)

    # بطاقة الموظف تعكس الحساب كما يظهر الآن على X — وتغيّر رابط الصورة في صفحة
    # الحساب تأكيد مستقل أن الصورة الجديدة هي المعروضة فعلاً
    after = None
    try:
        after = refresh_x_profile(cfg)
    except Exception:  # noqa: BLE001
        logger.info("x profile refresh after identity apply failed for %s",
                    cfg.account_username, exc_info=True)

    lines = [f"✅ **طبّقت الهوية التي اعتمدتها على @{cfg.account_username} في X:**", ""]
    if name:
        lines.append(f"👤 **الاسم:** {name}")
    if bio:
        lines.append(f"📝 **البايو:** {bio}")
    for kind, icon, label_ar in (("avatar", "🖼️", "الصورة الشخصية"), ("banner", "🎨", "الهيدر")):
        if kind not in media:
            continue
        old, new = (before or {}).get(f"{kind}_url"), (after or {}).get(f"{kind}_url")
        if confirmed.get(kind) == "confirmed":
            state = "نفس الصورة التي اعتمدتها ✓ (أكّد X حفظها)"
        elif old and new and new != old:
            state = "نفس الصورة التي اعتمدتها ✓ (ظهرت في صفحة حسابك على X)"
        elif old and new == old:
            state = ("⚠️ رُفعت لكن صفحة الحساب ما زالت تعرض السابقة — حدّث البطاقة بعد دقيقة، "
                     "وإن بقيت فأعد التطبيق")
        else:
            state = "رُفعت نفس الصورة التي اعتمدتها — لم يصلني تأكيد صريح من X، ألقِ نظرة على الحساب"
        lines.append(f"{icon} **{label_ar}:** {state}")
    lines.append("")
    lines.append("🎭 وحدّثت برسونا الموظف (الاسم/البايو) لتطابق هويته الجديدة.")
    if after:
        lines.append("🪪 وبطاقة الموظف تعرض الآن هويته كما تظهر على X (الصورة والهيدر).")
    return "\n".join(lines)


def refresh_x_profile(cfg, lock_timeout: float = 0.0) -> Dict[str, Any]:
    """يقرأ هوية الحساب الحالية من X (اسم/نبذة/صورة/هيدر) ويحفظها لبطاقة الموظف.

    ``lock_timeout`` > 0 لطلب المالك التفاعلي (يفشل سريعاً إن كان المتصفح مشغولاً)."""
    from app.aeos.mcp.base import MCPRequest

    from . import identity_store
    from . import tools_service as TS

    res = TS.registry().call(MCPRequest(
        employee_id=cfg.id, tool="read_account_profile",
        params={"cookie_label": cfg.cookie_label, "handle": cfg.account_username,
                "lock_timeout": lock_timeout},
        context={"server": "playwright_x", "cookie_label": cfg.cookie_label}))
    if not res.success:
        raise RuntimeError(str(res.error or "تعذّرت قراءة هوية الحساب من X")
                           .replace("read_account_profile failed: ", ""))
    snap = identity_store.save_x_profile(cfg.id, cfg.account_username,
                                         (res.data or {}).get("profile") or {}, "live")
    if not snap:
        raise RuntimeError("لم أجد اسم الحساب ولا صورته في صفحته على X.")
    return snap


def _sync_persona(cfg, name: str, bio: str) -> None:
    """يحفظ الاسم/البايو المطبَّقين في برسونا الموظف (best-effort)."""
    if not (name or bio):
        return
    try:
        from app.aeos.bootstrap import get_platform

        persona = cfg.persona.to_dict() if cfg.persona else {}
        if name:
            persona["display_name"] = name
        if bio:
            persona["bio"] = bio
        is_active = bool(getattr(cfg, "status", None)
                         and cfg.status.value == "active")
        get_platform().toggle_agent(
            platform="x", account_username=cfg.account_username,
            enabled=is_active, user_id=cfg.user_id, persona=persona,
        )
    except Exception:  # noqa: BLE001
        logger.debug("persona sync after identity apply skipped", exc_info=True)
