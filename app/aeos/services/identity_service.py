"""
Persona-driven X identity for AI employees.

Turns the employee's persona into a full visual identity (display name, bio,
profile picture, header image) via the app/identity Gemini service, then
applies it to the connected X account through app/x (`/api/profile/update`,
Playwright with the account cookies).

Generation (~30-60s for two images) plus the browser profile update (~60s+)
take minutes, so the apply runs in a **background thread** and the result is
pushed to the user's chat when done — same pattern as employee_data.
"""
from __future__ import annotations

import logging
import threading
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


def _absolutize(url: str) -> str:
    """صور المولّد تعود كمسارات نسبية /static/generated/… — يحوّلها لرابط كامل
    يستطيع سيرفر app/x تنزيله (download_to_temp يتطلب URL صالحاً)."""
    from app.services import identity_bridge

    if url and url.startswith("/"):
        return identity_bridge.IDENTITY_BASE_URL + url
    return url


# ─────────────────────────── public API ───────────────────────────
def generate_preview(cfg, entities: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """يولّد هوية كاملة من البرسونا (بلا تطبيق) — للمعاينة في الواجهة.

    Returns ``{name, username, bio, avatar_url, banner_url}`` (روابط مطلقة).
    Raises ``RuntimeError`` with a user-facing Arabic message on failure.
    """
    from app.services import identity_bridge

    entities = entities or {}
    if not identity_bridge.start_identity_server():
        raise RuntimeError("تعذّر تشغيل سيرفر توليد الهويات — "
                           "تأكد أن GEMINI_API_KEY موجود في ملف .env الرئيسي.")

    params = persona_identity_params(cfg.persona)
    for src, dst in _ENTITY_TO_PARAM:
        if entities.get(src):
            params[dst] = entities[src]

    result = identity_bridge.generate_profile(params, with_images=True)
    if not result.get("success"):
        raise RuntimeError(f"فشل توليد الهوية: {result.get('error', 'خطأ غير معروف')}")
    data = result.get("data", {})

    identity = {
        "name": (data.get("name") or "").strip(),
        "username": (data.get("username") or "").strip(),
        "bio": (data.get("bio") or "").strip(),
        "avatar_url": _absolutize((data.get("profilePictureUrl") or "").strip()),
        "banner_url": _absolutize((data.get("headerImageUrl") or "").strip()),
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

    if field not in IDENTITY_FIELDS:
        raise RuntimeError(f"حقل غير معروف: {field}")
    if not identity_bridge.start_identity_server():
        raise RuntimeError("تعذّر تشغيل سيرفر توليد الهويات — "
                           "تأكد أن GEMINI_API_KEY موجود في ملف .env الرئيسي.")

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
        url = _absolutize((img_res.get("data", {}).get("imageUrl") or "").strip())
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

    إن مُرّرت ``identity`` جاهزة (من معاينة سابقة) تُطبّق مباشرة بلا إعادة توليد."""
    threading.Thread(
        target=_run, args=(cfg, user_id, entities or {}, identity), daemon=True,
    ).start()
    if identity:
        return (f"🪩 جارِ تطبيق الهوية المولّدة على حساب **@{cfg.account_username}** "
                "في X (اسم + بايو + صورة + هيدر)… قد يستغرق ذلك دقيقة، "
                "وستصلك النتيجة في الشات. ⏳")
    return (f"🎨 جارِ توليد هوية جديدة من برسونا الموظف **@{cfg.account_username}** "
            "وتطبيقها على حسابه في X (اسم + بايو + صورة + هيدر)…\n"
            "قد يستغرق ذلك ٢-٣ دقائق، وسأوافيك بالنتيجة هنا. ⏳")


# ─────────────────────────── worker ───────────────────────────
def _run(cfg, user_id: Optional[int], entities: Dict[str, Any],
         identity: Optional[Dict[str, Any]] = None) -> None:
    try:
        text = _apply(cfg, entities, identity)
    except Exception as exc:  # noqa: BLE001
        logger.exception("apply identity failed for %s", cfg.account_username)
        text = f"⚠️ تعذّر تطبيق الهوية على @{cfg.account_username}: {exc}"
    from .employee_data import _push

    _push(user_id, text, cfg)


def _apply(cfg, entities: Dict[str, Any],
           identity: Optional[Dict[str, Any]] = None) -> str:
    from app.services import x_bridge

    # 1) هوية جاهزة من معاينة سابقة، أو توليد جديد من البرسونا
    if not identity:
        identity = generate_preview(cfg, entities)
    name = (identity.get("name") or "").strip()
    bio = (identity.get("bio") or "").strip()
    avatar_url = _absolutize((identity.get("avatar_url") or "").strip())
    banner_url = _absolutize((identity.get("banner_url") or "").strip())
    if not any([name, bio, avatar_url, banner_url]):
        return "⚠️ لا توجد حقول قابلة للتطبيق في الهوية."

    # 2) تطبيقها على حساب X عبر app/x (Playwright بكوكيز الحساب)
    label = cfg.cookie_label or cfg.account_username
    res = x_bridge.update_profile(
        label, name=name, bio=bio,
        avatar_url=avatar_url, banner_url=banner_url, headless=True,
    )
    if not res.get("success"):
        err = res.get("error") or res.get("message") or "خطأ غير معروف"
        return (f"⚠️ ولّدت الهوية لكن تطبيقها على X فشل: {err}\n\n"
                f"👤 الاسم المولّد: {name}\n📝 البايو المولّد: {bio}")

    # 3) مزامنة البرسونا: الاسم والبايو المعروضان في X هما الآن هوية الموظف
    _sync_persona(cfg, name, bio)

    lines = [f"✅ **حدّثت هوية الموظف @{cfg.account_username} في X من برسوناه:**", ""]
    if name:
        lines.append(f"👤 **الاسم:** {name}")
    if bio:
        lines.append(f"📝 **البايو:** {bio}")
    if avatar_url:
        lines.append("🖼️ **الصورة الشخصية:** جُدّدت ✓")
    if banner_url:
        lines.append("🎨 **الهيدر:** جُدّد ✓")
    lines.append("")
    lines.append("🎭 وحدّثت برسونا الموظف (الاسم/البايو) لتطابق هويته الجديدة.")
    return "\n".join(lines)


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
