"""
ميديا المنشور من Pixabay (مهارة ``stock_media``): عند قرار النشر عن موضوع يبحث الموظف عن صورة أو
فيديو **يطابق الموضوع فعلاً** ويرفقه بالمنشور.

الخطوات (نداءا نموذج صغيران كحد أقصى):
1. **التخطيط**: من نص المنشور يصوغ النموذج كلمتَي بحث إنجليزيتين حرفيتين (Pixabay يفهم الإنجليزية
   أدق)، ويقرّر صورة أم فيديو، وما يجب تجنّبه (أعلام دول أخرى، رموز دينية، شخصيات بعينها…).
2. **البحث**: ``pixabay.search_images`` / ``search_videos`` (أفقي، آمن، مخزّن 24 ساعة).
   الفيديو: ≤ 60 ثانية وحجم ≤ 40MB (حدود X لغير المشتركين 140 ثانية/512MB، والرفع عبر البروكسي بطيء).
3. **الاختيار**: النموذج يرى وسوم أفضل المرشحين ويختار الأنسب أو «لا شيء» — صورة مخزون مضلِّلة
   أسوأ من لا صورة (يُترك القرار عندها لتوليد صورة أصلية).
4. لا يُكرَّر نفس الملف للموظف نفسه خلال 30 يوماً.

السياسة لكل موظف ``business_rules.stock_media``: ``auto`` (افتراضي — فقط إن لم توجد ميديا حقيقية
من المصدر)، ``prefer`` (تتقدّم على ميديا المصدر)، ``off``. والنوع ``stock_media_kind``:
``auto`` | ``image`` | ``video``. المطوّر يطفئ المهارة كلها من اللوحة (system skill ``stock_media``).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:  # pragma: no cover
    from ..cognition.context import ExecutionContext

logger = logging.getLogger("aeos.stock_media")

SKILL_SLUG = "stock_media"
_USED_PATH = Path(__file__).resolve().parents[3] / "data" / "stock_media_used.json"
_REUSE_DAYS = 30
_MAX_VIDEO_SECONDS = 60
_MAX_VIDEO_BYTES = 40 * 1024 * 1024
_lock = threading.Lock()


def enabled(config) -> bool:
    try:
        from app.aeos.skills import system_skills

        if not system_skills.is_enabled(SKILL_SLUG):
            return False
    except Exception:  # noqa: BLE001
        pass
    return policy(config) != "off"


def policy(config) -> str:
    raw = str((getattr(config, "business_rules", None) or {}).get("stock_media", "auto")).strip().lower()
    return "off" if raw in ("off", "0", "false", "no") else ("prefer" if raw == "prefer" else "auto")


def available(context: "ExecutionContext") -> bool:
    if not (context.mcp.has("pixabay") and enabled(context.config)):
        return False
    from app.aeos.mcp.catalog import mcp_module

    try:
        return bool(mcp_module("pixabay", "server.py").api_key())
    except Exception:  # noqa: BLE001
        return False


def _used() -> Dict[str, Dict[str, float]]:
    try:
        return json.loads(_USED_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _mark_used(employee_id: str, media_id: str) -> None:
    with _lock:
        data = _used()
        cutoff = time.time() - _REUSE_DAYS * 86400
        mine = {k: v for k, v in (data.get(employee_id) or {}).items() if v > cutoff}
        mine[media_id] = time.time()
        data[employee_id] = mine
        _USED_PATH.parent.mkdir(parents=True, exist_ok=True)
        _USED_PATH.write_text(json.dumps(data), encoding="utf-8")


def _plan(context: "ExecutionContext", text: str, topic: str) -> Optional[Dict[str, Any]]:
    llm = getattr(context, "llm", None)
    if llm is None or not getattr(llm, "available", False):
        return None
    want = str((context.config.business_rules or {}).get("stock_media_kind", "auto")).lower()
    out = llm.complete_json(
        system=('أنت محرر صور لحساب أخبار على X. أعد JSON فقط: {"queries":["english words","..."],'
                '"kind":"image|video","avoid":["..."],"skip":false}'),
        prompt=(f"المنشور:\n{text[:600]}\n" + (f"الموضوع: {topic[:200]}\n" if topic else "") +
                "\nصِغ كلمتَي بحث إنجليزيتين قصيرتين (2-4 كلمات) تصفان **مشهداً حرفياً** يناسب المنشور في "
                "مكتبة صور مخزون (مثل: air defense missile night, oil refinery desert). صِف **الشيء المادي** "
                "نفسه لا المفهوم: لا charts ولا graphs ولا concept (خبر أسعار النفط ← oil pump jack, oil "
                "tanker — قيس: «oil prices chart» أعاد رسوم فوركس رفضها فحص الرؤية). لا أسماء أشخاص ولا "
                "رموز سياسية. kind=video فقط إن كان المشهد حركياً بطبيعته"
                + (f" (المالك يفضّل: {want})" if want in ("image", "video") else "") +
                ". avoid: ما يجعل صورة مخزون مضلِّلة هنا (أعلام دول أخرى، رموز دينية، أشخاص بعينهم). "
                "skip=true ما لم يكن المنشور عن **شيء ملموس يمكن تصويره** (حدث، مكان، سلعة، آلة، منشأة): "
                "الرأي والنصيحة والتحليل المجرد ← skip (قيس: رأي عن «الحوار الهادئ» أُرفقت له سبورة طباشير)."),
        temperature=0.2) or {}
    if not isinstance(out, dict) or out.get("skip"):
        return None
    queries = [re.sub(r"[^A-Za-z0-9 \-]", " ", str(q)).strip()[:60] for q in out.get("queries") or []]
    kind = want if want in ("image", "video") else ("video" if out.get("kind") == "video" else "image")
    return {"queries": [q for q in queries if q][:2], "kind": kind,
            "avoid": [str(a)[:40] for a in out.get("avoid") or []][:6]}


def _search(context: "ExecutionContext", query: str, kind: str) -> List[Dict[str, Any]]:
    from ..mcp.base import MCPRequest

    tool = "search_videos" if kind == "video" else "search_images"
    params = {"q": query, "per_page": 12, "safesearch": "true", "order": "popular"}
    params.update({"video_type": "film"} if kind == "video" else {"image_type": "photo", "orientation": "horizontal"})
    r = context.mcp.call(MCPRequest(employee_id=context.config.id, tool=tool, params=params,
                                    context={"server": "pixabay"}))
    if not r.success:
        logger.info("pixabay search failed: %s", r.error)
        return []
    items = r.data.get("results") or []
    if kind == "video":
        items = [i for i in items if (i.get("duration") or 999) <= _MAX_VIDEO_SECONDS
                 and (i.get("size") or 0) <= _MAX_VIDEO_BYTES and i.get("url")]
    else:
        items = [i for i in items if i.get("url") and (i.get("width") or 0) >= 1000]
    return items


# صور مخزون خيالية/مفاهيمية تُضلّل في منشور خبري. قيس 2026-11-09 على Pixabay الحقيقي: خبر اعتراض
# مسيّرة حوثية اختير له «ufo, intercept, science fiction» لأن الوسم «intercept» طابق.
_FAKE_TAGS = re.compile(r"(?i)\b(ufo|alien|fantasy|science fiction|sci-fi|scifi|cartoon|anime|comic|"
                        r"illustration|3d|render|rendering|cgi|vector|clipart|drawing|toy|lego|miniature|"
                        r"halloween|christmas|zombie|video game|gaming|chalk|blackboard|meme|emoji)\b")
_MIN_SCORE = 7


def _choose(context: "ExecutionContext", text: str, cands: List[Dict[str, Any]],
            avoid: List[str]) -> List[Dict[str, Any]]:
    """المرشّحون المقبولون مرتّبين (درجة ≥ 7 من 10)، بعد إسقاط الوسوم الخيالية/المفاهيمية حتمياً."""
    cands = [c for c in cands if not _FAKE_TAGS.search(c.get("tags") or "")][:8]
    if not cands:
        return []
    llm = getattr(context, "llm", None)
    listing = "\n".join(f"{i + 1}. [{c['kind']}] وسوم: {c.get('tags', '')}" for i, c in enumerate(cands))
    try:
        out = llm.complete_json(
            system='أعد JSON فقط: {"scores":[{"n":1,"score":0-10}]}',
            prompt=(f"المنشور:\n{text[:500]}\n\nمرشّحو المخزون (وسومهم فقط):\n{listing}\n\n"
                    + (f"تجنّب: {'، '.join(avoid)}\n" if avoid else "") +
                    "قيّم كل مرشّح 0-10: هل يصوّر **حرفياً** موضوع المنشور نفسه (الشيء/الحدث/المكان)؟ "
                    "10 = المشهد نفسه، 7 = نفس نوع الشيء بوضوح، ≤4 = استعارة أو مفهوم عام أو موضوع آخر."),
            temperature=0) or {}
        scores = {int(s.get("n")): float(s.get("score") or 0) for s in out.get("scores") or []
                  if str(s.get("n", "")).isdigit()}
    except Exception:  # noqa: BLE001 - بلا تقييم لا نختار (صورة مضلِّلة أسوأ من لا صورة)
        return []
    ranked = sorted((n for n, s in scores.items() if s >= _MIN_SCORE and 0 < n <= len(cands)),
                    key=lambda n: -scores[n])
    return [dict(cands[n - 1], _score=scores[n]) for n in ranked]


def _vision_ok(text: str, cand: Dict[str, Any]) -> Optional[bool]:
    """نموذج رؤية (إعداد النظام) يرى معاينة الملف فعلاً: هل تصوّر موضوع المنشور بلا تضليل؟
    None = تعذّر الفحص (لا مفتاح/لا دعم صور)."""
    preview = cand.get("preview") or ""
    if not preview:
        return None
    try:
        from openai import OpenAI

        from app.core.llm_provider import resolve_llm

        ep = resolve_llm()
        if not ep.available:
            return None
        r = OpenAI(**ep.client_kwargs(), timeout=45).chat.completions.create(
            model=ep.model, temperature=0, max_tokens=120,
            messages=[{"role": "system", "content": (
                "You check stock media for a news post on X. Reply JSON only: "
                '{"match": true|false, "reason": "short"}. match=true ONLY if the image literally shows '
                "the post's subject (same kind of object/event/place) and would not mislead a reader: "
                "no fantasy/sci-fi, no unrelated country's flag or symbols, no staged concept art.")},
                {"role": "user", "content": [
                    {"type": "text", "text": f"Post: {text[:500]}"},
                    {"type": "image_url", "image_url": {"url": preview, "detail": "low"}}]}])
        m = re.search(r"\{.*\}", r.choices[0].message.content or "", re.S)
        return bool(json.loads(m.group(0)).get("match")) if m else None
    except Exception as exc:  # noqa: BLE001
        logger.info("stock media vision check skipped: %s", exc)
        return None


def find(context: "ExecutionContext", text: str, topic: str = "") -> str:
    """رابط صورة/فيديو Pixabay مناسب للمنشور، أو "" (فيُترك لتوليد صورة أصلية أو نص فقط)."""
    if not available(context) or not (text or "").strip():
        return ""
    try:
        plan = _plan(context, text, topic)
        if not plan or not plan["queries"]:
            return ""
        used = set((_used().get(context.config.id) or {}).keys())
        cands: List[Dict[str, Any]] = []
        for q in plan["queries"]:
            cands += [c for c in _search(context, q, plan["kind"]) if f"{c['kind']}:{c['id']}" not in used
                      and c["id"] not in {x["id"] for x in cands}]
            if len(cands) >= 6:
                break
        if not cands and plan["kind"] == "video":                  # لا فيديو مناسب ← صورة
            cands = [c for q in plan["queries"] for c in _search(context, q, "image")
                     if f"{c['kind']}:{c['id']}" not in used][:8]
        pick = None
        for cand in _choose(context, text, cands, plan["avoid"])[:2]:     # فحص رؤية لأفضل اثنين
            seen = _vision_ok(text, cand)
            if seen is True or (seen is None and cand["_score"] >= 8):
                pick = cand
                break
        if not pick:
            context.scratch["stock_media"] = {"queries": plan["queries"], "picked": None,
                                              "candidates": len(cands)}
            return ""
        _mark_used(context.config.id, f"{pick['kind']}:{pick['id']}")
        context.scratch["stock_media"] = {"queries": plan["queries"], "kind": pick["kind"],
                                          "id": pick["id"], "tags": pick.get("tags", ""),
                                          "page_url": pick.get("page_url", "")}
        try:
            context.emit_progress(
                f"🖼️ اختار {'فيديو' if pick['kind'] == 'video' else 'صورة'} من Pixabay للمنشور "
                f"(«{pick.get('tags', '')[:60]}»): {pick.get('page_url', '')}", intent="aeos_cycle_media")
        except Exception:  # noqa: BLE001
            pass
        return pick["url"]
    except Exception as exc:  # noqa: BLE001 - الميديا تحسين: لا تُسقط النشر
        logger.warning("stock media failed: %s", exc)
        return ""
