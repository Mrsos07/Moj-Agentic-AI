"""
المخرج البصري — من نص المنشور إلى صورة احترافية تخدمه فعلاً.

قيس على آخر 4 صور مولّدة (2026-10-14): **كلها قطع شطرنج** (ملك منتصر وملك ساقط)، مربّعة،
بلوحة رمادية واحدة، وفيها صليب على تاج الملك وخرائط دول مرسومة خطأً — في محتوى عن
السعودية واليمن. السبب: البرومبت كان «visual metaphor for this post: "<النص العربي>"» بلا
أي توجيه، فيلجأ النموذج لأشهر استعارة سياسية في بياناته.

هنا ثلاث مراحل:
1. **توجيه فني** (LLM بدور مدير فني لحساب أخبار خليجي): مشهد **حقيقي ملموس** من الخبر نفسه
   (مكان، أشياء، أشخاص مجهولو الهوية من الخلف أو صورة ظلّية)، بزاوية وعدسة وإضاءة ولوحة
   ألوان محددة — مع قائمة كليشيهات ممنوعة وذاكرة لآخر المفاهيم حتى لا تتكرر.
2. **برومبت منظّم** بالإنجليزية (ما تفهمه نماذج الصور أفضل): الموضوع ← المشهد ← التكوين ←
   الإضاءة ← الكاميرا ← الألوان ← المزاج ← الأسلوب ← القيود، بنسبة 16:9 لتايم لاين X.
3. **مراجعة الجودة** (نموذج رؤية): نص مشوّه داخل الصورة؟ أعلام أو خرائط خاطئة؟ رموز دينية
   في غير موضعها؟ وجه شخص حقيقي؟ كليشيه شطرنج؟ ← إعادة توليد مرة واحدة بتصحيحات محددة،
   فإن بقي عيب جسيم يُنشر المنشور نصاً بلا صورة (صورة سيئة أضرّ من لا صورة).
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:  # pragma: no cover
    from ..cognition.context import ExecutionContext

logger = logging.getLogger("aeos.visual")

#: أنماط الحساب البصرية (المفاتيح نفسها في واجهة الموظف) — مواصفات تصوير لا صفات عامة
STYLES: Dict[str, str] = {
    "editorial": ("high-end editorial news photography, Reuters/AP photojournalism quality, natural "
                  "color grading, realistic textures, crisp focus on the subject"),
    "cinematic": ("cinematic film still, anamorphic 2.39 look framed in 16:9, motivated dramatic lighting, "
                  "rich but natural color contrast, subtle film grain, shallow depth of field"),
    "minimal": ("premium minimal 3D/flat illustration, two or three harmonious colors, clean geometry, "
                "generous negative space, soft studio shadows"),
    "infographic": ("modern abstract data-visual art, clean geometric forms suggesting data flows, no labels, "
                    "no numbers, no readable text, sleek tech aesthetic"),
    "photoreal": ("photorealistic documentary photograph, natural available light, true-to-life colors, "
                  "realistic skin and material textures, 35mm full-frame look"),
}

#: استعارات مستهلكة تجعل الصورة «صورة ذكاء اصطناعي» نمطية — تُمنع صراحةً
CLICHES = [
    "chess pieces or chessboards", "crowns", "puppets or marionette strings", "generic handshakes",
    "globes", "country-shaped maps or map outlines", "national flags", "scales of justice",
    "light bulbs", "gears and cogs", "jigsaw puzzles", "dominoes", "hourglasses", "cracked stone with text",
    "red/green arrows of stock charts", "money rain", "robots shaking hands", "glowing brains",
]

_DIRECTOR_SYSTEM = (
    "You are the photo editor and art director of a top-tier Arabic news & analysis account on X "
    "(audience: Saudi Arabia and the Gulf). For each post you commission ONE image that a professional "
    "photographer or illustrator would produce for a major newspaper front page.\n"
    "Principles:\n"
    "1. LITERAL & CONCRETE over metaphor: show a real, specific scene that the news is actually about "
    "(the place, the equipment, the people involved as anonymous figures, the consequence). A metaphor is "
    "allowed only if it is fresh and specific to this story.\n"
    f"2. NEVER use these clichés: {', '.join(CLICHES)}.\n"
    "3. Cultural accuracy for the Gulf/Arab world: correct clothing (thobe, shemagh/ghutra, abaya), "
    "architecture, desert/coastal/urban landscapes of the region. No religious symbols (crosses, etc.) "
    "unless the story is about religion. Never draw maps or flags (they come out wrong).\n"
    "4. People: anonymous only — silhouettes, from behind, hands, distant figures, out of focus. "
    "Never a recognizable real person, politician or official.\n"
    "5. No text of any kind in the image: no letters, numbers, signs, captions, logos, watermarks.\n"
    "6. Composition for an X timeline 16:9 image: one clear focal subject, strong foreground/background "
    "depth, leave calm negative space on one side, readable as a thumbnail.\n"
    "7. Vary viewpoint, time of day and palette between posts — avoid the concepts listed as recently used.\n"
    "Respond ONLY with minified JSON."
)


def _style_key(context: "ExecutionContext") -> str:
    key = str((context.config.business_rules or {}).get("image_style", "editorial")).strip().lower()
    return key if key in STYLES else "editorial"


def recent_concepts(context: "ExecutionContext") -> List[str]:
    return list((context.config.runtime_state.flags or {}).get("visual_concepts") or [])[-8:]


def remember_concept(context: "ExecutionContext", concept: str) -> None:
    if not concept:
        return
    flags = context.config.runtime_state.flags
    flags["visual_concepts"] = (list(flags.get("visual_concepts") or []) + [concept[:120]])[-12:]


def _fallback_brief(text: str, context: "ExecutionContext") -> Dict[str, Any]:
    """بلا LLM: توجيه حتمي معقول (أفضل من «استعارة لهذا المنشور»)."""
    interests = "، ".join((getattr(context.config.persona, "interests", None) or [])[:3])
    return {
        "concept": "documentary scene of the news topic",
        "subject": f"a real-world scene that illustrates this news topic: {text[:200]}",
        "setting": f"in the Gulf region, related to {interests or 'current affairs'}",
        "composition": "rule of thirds, clear focal subject, depth from foreground to background",
        "lighting": "golden-hour natural light", "camera": "35mm lens, eye level, shallow depth of field",
        "palette": "natural warm desert tones with cool shadows", "mood": "serious, informative",
    }


def art_direct(text: str, context: "ExecutionContext") -> Dict[str, Any]:
    """نص المنشور ← موجز بصري مفصّل (مشهد/تكوين/إضاءة/كاميرا/ألوان/مزاج)."""
    llm = getattr(context, "llm", None)
    style = _style_key(context)
    if llm is None or not getattr(llm, "available", False):
        return {**_fallback_brief(text, context), "style": style}
    persona = context.config.persona
    facts = ""
    research = (context.scratch or {}).get("research") or {}
    if research.get("items"):
        facts = "\nVerified context from research (use to make the scene accurate):\n" + "\n".join(
            f"- {(i.get('title') or '')[:90]} {(i.get('snippet') or '')[:160]}" for i in research["items"][:3])
    recent = recent_concepts(context)
    prompt = (
        f"Account: {getattr(persona, 'display_name', '') or context.config.account_username} — "
        f"{getattr(persona, 'role', '') or 'news analyst'}; fields: "
        f"{', '.join((getattr(persona, 'interests', None) or [])[:4]) or 'current affairs'}.\n"
        f"Visual style of the account: {STYLES[style]}.\n"
        f"The post (Arabic) to illustrate:\n«{text[:600]}»\n{facts}\n"
        + (f"Recently used concepts (do NOT repeat or resemble): {'; '.join(recent)}\n" if recent else "")
        + "\nReturn JSON with keys: concept (5-10 words, English, the core idea), subject (the main focal "
        "element, concrete), setting (place/time, specific to the region), composition (framing, angle, "
        "foreground/background), lighting, camera (lens, distance, depth of field), palette (3-4 colors), "
        "mood, avoid (list of story-specific things that must not appear)."
    )
    try:
        brief = llm.complete_json(_DIRECTOR_SYSTEM, prompt, temperature=0.8) or {}
    except Exception:  # noqa: BLE001 - التوجيه تحسين لا شرط للنشر
        logger.debug("art direction failed", exc_info=True)
        brief = {}
    if not brief.get("subject"):
        brief = _fallback_brief(text, context)
    brief["style"] = style
    return brief


def _txt(value: Any) -> str:
    """حقل الموجز نصاً: النموذج يعيد أحياناً قائمة (palette: [..]) فكانت تدخل البرومبت «['deep navy', …]»."""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value if v)
    if isinstance(value, dict):
        return ", ".join(f"{k}: {v}" for k, v in value.items())
    return str(value or "").strip()


def build_prompt(brief: Dict[str, Any], context: Optional["ExecutionContext"] = None,
                 fixes: Optional[List[str]] = None) -> str:
    """الموجز ← برومبت منظّم بالإنجليزية لنموذج الصور."""
    brief = {k: (_txt(v) if k not in ("avoid", "style") else v) for k, v in (brief or {}).items()}
    style = STYLES.get(brief.get("style") or "editorial", STYLES["editorial"])
    extra = ""
    if context is not None:
        extra = str((context.config.business_rules or {}).get("image_prompt_extra", "")).strip()
    avoid = [str(a) for a in (brief.get("avoid") or []) if a][:6]
    parts = [
        f"{style}.",
        f"Subject: {brief.get('subject', '')}.",
        f"Setting: {brief.get('setting', '')}." if brief.get("setting") else "",
        f"Composition: {brief.get('composition', '')}, wide 16:9 frame, one clear focal point, "
        "calm negative space on one side.",
        f"Lighting: {brief.get('lighting', '')}." if brief.get("lighting") else "",
        f"Camera: {brief.get('camera', '')}." if brief.get("camera") else "",
        f"Color palette: {brief.get('palette', '')}." if brief.get("palette") else "",
        f"Mood: {brief.get('mood', '')}." if brief.get("mood") else "",
        f"Style key: {brief.get('style', 'editorial')}.",
        "Culturally accurate for Saudi Arabia and the Gulf; people only as anonymous figures "
        "(from behind, silhouettes, hands, out of focus).",
        "Strictly NO text, letters, numbers, signage, captions, logos or watermarks anywhere. "
        "NO maps, NO flags, NO religious symbols, NO recognizable real people. "
        f"Do not include: {', '.join(CLICHES[:8])}" + (f", {', '.join(avoid)}" if avoid else "") + ".",
    ]
    if fixes:
        parts.append("Corrections from the previous attempt (must fix): " + "; ".join(fixes[:5]) + ".")
    if extra:
        parts.append(extra)
    return " ".join(p for p in parts if p)


# ─────────────────────────── مراجعة الجودة ───────────────────────────
_REVIEW_SYSTEM = (
    "You are a strict photo editor reviewing an AI-generated image before it is published on a "
    "professional Gulf news account. Check ONLY for real defects. Respond ONLY with minified JSON: "
    '{"ok": true|false, "severe": true|false, "issues": ["short english fix instruction", ...], "score": 1-10}. '
    "Severe defects (severe=true): any visible text/letters/numbers or garbled writing; flags or maps; "
    "religious symbols unrelated to the story; a recognizable face of a real person; disturbing gore. "
    "Other defects (ok=false, severe=false): chess/crown/puppet clichés, deformed hands or bodies, "
    "clearly unrelated to the brief, muddy or low-quality rendering. Do NOT fail an image for minor taste "
    "preferences — a good professional photo that fits the story is ok=true (score>=6)."
)
#: إعادة التوليد مكلفة (~0.13$ و~70 ثانية بجودة high): نعيد فقط لعيب حقيقي لا لتفضيل ذوقي.
#: قيس: أول تجربة رُفضت صورة جيدة (دفاع جوي ليلاً) وكلّفت توليداً ثانياً بلا داعٍ.
MIN_SCORE = 6


def acceptable(verdict: Dict[str, Any]) -> bool:
    if verdict.get("severe"):
        return False
    score = verdict.get("score")
    try:
        return bool(verdict.get("ok")) or (score is not None and float(score) >= MIN_SCORE)
    except (TypeError, ValueError):
        return bool(verdict.get("ok"))


def _image_data_url(path: str, max_side: int = 768) -> str:
    from PIL import Image

    from app.aeos.security import media
    with media.read(path) as image_file, Image.open(image_file) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def review(image_path: str, brief: Dict[str, Any]) -> Dict[str, Any]:
    """فحص الصورة بنموذج رؤية (إعداد النظام). يعيد {ok, severe, issues, score}؛ عند تعذّر
    المراجعة يُعتبر مقبولاً (لا نُسقط صورة جيدة لأن المراجِع غير متاح)."""
    if not image_path:
        return {"ok": True, "severe": False, "issues": [], "score": None, "skipped": "no local file"}
    try:
        from openai import OpenAI

        from app.core.llm_provider import resolve_llm

        ep = resolve_llm()
        if not ep.available:
            return {"ok": True, "severe": False, "issues": [], "score": None, "skipped": "no llm"}
        client = OpenAI(**ep.client_kwargs(), timeout=60)
        r = client.chat.completions.create(
            model=ep.model, temperature=0, max_tokens=300,
            messages=[{"role": "system", "content": _REVIEW_SYSTEM},
                      {"role": "user", "content": [
                          {"type": "text", "text": "Brief: " + json.dumps(
                              {k: brief.get(k) for k in ("concept", "subject", "setting", "style")},
                              ensure_ascii=False)},
                          {"type": "image_url", "image_url": {"url": _image_data_url(image_path), "detail": "low"}},
                      ]}])
        raw = (r.choices[0].message.content or "").strip()
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0)) if m else {}
        return {"ok": bool(data.get("ok", True)), "severe": bool(data.get("severe", False)),
                "issues": [str(i)[:140] for i in (data.get("issues") or [])][:5], "score": data.get("score")}
    except Exception as exc:  # noqa: BLE001
        logger.info("image review skipped: %s", exc)
        return {"ok": True, "severe": False, "issues": [], "score": None, "skipped": type(exc).__name__}


# ─────────────────────────── التنسيق الكامل ───────────────────────────
def create(context: "ExecutionContext", text: str) -> Dict[str, Any]:
    """توجيه ← توليد ← مراجعة ← (إعادة مرة واحدة) . يعيد {url, prompt, brief, review, attempts}
    أو {url: ""} مع السبب. يستدعي خادم الصور عبر سجل MCP الخاص بالموظف."""
    from ..mcp.base import MCPRequest

    rules = context.config.business_rules or {}
    brief = art_direct(text, context)
    fixes: List[str] = []
    last: Dict[str, Any] = {"url": "", "reason": ""}
    history: List[Dict[str, Any]] = []
    attempts = 1 + (1 if bool(rules.get("image_review", True)) else 0)
    for attempt in range(1, attempts + 1):
        prompt = build_prompt(brief, context, fixes or None)
        res = context.mcp.call(MCPRequest(
            employee_id=context.config.id, tool="generate_image",
            params={"prompt": prompt, "aspect_ratio": "16:9"}, context={"server": "image"}))
        if not res.success:
            return {"url": "", "reason": res.error, "brief": brief, "attempts": attempt}
        url, local = (res.data.get("url") or "").strip(), res.data.get("local_path") or ""
        if not bool(rules.get("image_review", True)):
            last = {"url": url, "prompt": prompt, "brief": brief, "review": None, "attempts": attempt}
            break
        verdict = review(local, brief)
        history.append({"attempt": attempt, **verdict})
        last = {"url": url, "prompt": prompt, "brief": brief, "review": verdict, "attempts": attempt,
                "history": history}
        if acceptable(verdict):
            break
        fixes = verdict.get("issues") or ["make it a concrete, literal, publication-quality news photo"]
        if attempt == attempts and verdict.get("severe"):
            # عيب جسيم بعد محاولتين (نص مشوّه، علم/خريطة، وجه حقيقي) ← بلا صورة أفضل
            last = {**last, "url": "", "reason": "severe defects: " + "; ".join(fixes)}
    if last.get("url"):
        remember_concept(context, str(brief.get("concept") or brief.get("subject") or "")[:120])
    return last
