"""
إدارة حساب X للموظف مباشرةً من المنسّق: عرض منشوراته، وحذفها، والتراجع عن
التفاعلات (إعجاب/ريتويت/حفظ/متابعة)، وتعديل الملف الشخصي، وإدارة المجدول.

لماذا ليست دورة موظف؟ هذه أوامر إدارية صريحة من المالك على هدف محدّد، لا محتوى
يكتبه الموظف بشخصيته — فلا معنى لتمريرها بالمخطط والحارس وLLM الدورة. تُنفّذ
مباشرةً عبر خادم MCP ‏``x`` بكوكيز الموظف نفسه، في الخلفية، والنتيجة **المؤكدة**
من X تصل للشات.

ضمانات:
- **العزل**: المتصل يمرّر موظفاً مسحوباً بنطاق المالك (``_Tools._cfg``)، فلا وصول
  لحساب مستخدم آخر. الحذف يمرّر ``expected_handle`` فيرفض الخادم حذف تغريدة ليست
  لهذا الحساب حتى لو أُعطي رابطها.
- **التأكيد على مرحلتين** للأفعال التي لا رجعة فيها (حذف، إلغاء متابعة، تعديل
  الملف، إلغاء مجدول): الاستدعاء الأول يعيد معاينة و``confirmation_id``، والتنفيذ
  لا يقبل المعرّف إلا في **رسالة لاحقة** من المالك. فالموديل لا يستطيع أن يعاين
  ويؤكد في الجولة نفسها مهما فعل.
- **لا نجاح بلا تأكيد**: طبقة X تعيد النجاح فقط بعد تبدّل حالة الزر / ردّ
  ``DeleteTweet`` من X؛ و``already`` تُبلَّغ كما هي («لم يكن معجباً بها أصلاً»).
"""
from __future__ import annotations

import logging
import re
import threading
from ..security.gateway import carry
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger("aeos.x_control")

#: action -> (أداة MCP، نوع الهدف، يحتاج تأكيداً، وصف عربي)
ACTIONS: Dict[str, Dict[str, Any]] = {
    "delete_post": {"tool": "delete_tweet", "target": "own_tweet", "confirm": True, "label": "حذف منشور"},
    "unlike": {"tool": "unlike", "target": "tweet", "confirm": False, "label": "إلغاء إعجاب"},
    "unretweet": {"tool": "unretweet", "target": "tweet", "confirm": False, "label": "التراجع عن إعادة نشر"},
    "unbookmark": {"tool": "unbookmark", "target": "tweet", "confirm": False, "label": "إلغاء حفظ"},
    "like": {"tool": "like", "target": "tweet", "confirm": False, "label": "إعجاب"},
    "retweet": {"tool": "retweet", "target": "tweet", "confirm": False, "label": "إعادة نشر"},
    "bookmark": {"tool": "bookmark", "target": "tweet", "confirm": False, "label": "حفظ"},
    "follow": {"tool": "follow", "target": "profile", "confirm": False, "label": "متابعة"},
    "unfollow": {"tool": "unfollow", "target": "profile", "confirm": True, "label": "إلغاء متابعة"},
    "update_profile": {"tool": "update_profile", "target": "none", "confirm": True, "label": "تعديل الملف الشخصي"},
    "cancel_scheduled": {"tool": "cancel_scheduled_post", "target": "scheduled", "confirm": True,
                         "label": "إلغاء منشور مجدول"},
}
READ_ACTIONS = ("list_posts", "list_scheduled")
MAX_TARGETS = 10
PROFILE_FIELDS = ("name", "bio", "location", "website")
_CONFIRM_TTL = 15 * 60
_DELETED_KEEP = 300

_STATUS_RE = re.compile(r"(?:https?://)?(?:www\.|mobile\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,30})/status/(\d{5,25})")
_ANY_STATUS_RE = re.compile(r"/status/(\d{5,25})")
_HANDLE_RE = re.compile(r"^@?([A-Za-z0-9_]{1,30})$")
_PROFILE_URL_RE = re.compile(r"(?:https?://)?(?:www\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,30})/?$")
_LAST_RE = re.compile(r"^(?:last|latest|آخر|اخر|الأخير|الاخير|آخر\s*منشور|اخر\s*منشور)$", re.I)
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

#: (user_id, confirmation_id) -> طلب معلّق
_PENDING: Dict[tuple, Dict[str, Any]] = {}
#: (user_id, employee_id) -> (وقت، آخر قائمة عُرضت للمالك) — «احذف 2» تعني رقم 2 فيما رآه
_LISTINGS: Dict[tuple, tuple] = {}
_LISTING_TTL = 60 * 60
_LOCK = threading.Lock()


def remember_listing(user_id, employee_id: str, posts: List[Dict[str, Any]]) -> None:
    _LISTINGS[(user_id, employee_id)] = (time.time(), posts)


def last_listing(user_id, employee_id: str) -> Optional[List[Dict[str, Any]]]:
    hit = _LISTINGS.get((user_id, employee_id))
    return hit[1] if hit and time.time() - hit[0] < _LISTING_TTL else None


# ─────────────────────────── منشورات الموظف ───────────────────────────
def _url_of(out: Dict[str, Any]) -> str:
    for k in ("tweet_url", "url", "first_url"):
        if isinstance(out.get(k), str) and "/status/" in out[k]:
            return out[k]
    urls = out.get("urls") or out.get("tweet_urls") or []
    return next((u for u in urls if isinstance(u, str) and "/status/" in u), "")


_KIND = {"publish_content": "منشور", "publish_thread": "ثريد", "quote_post": "اقتباس",
         "reply_to_user": "رد"}


def deleted_ids(cfg) -> set:
    return set((cfg.runtime_state.flags or {}).get("deleted_posts") or [])


def recorded_posts(cfg, limit: int = 15) -> List[Dict[str, Any]]:
    """ما نشره الموظف فعلاً حسب سجل دوراته (فوري، بلا متصفح)، الأحدث أولاً،
    بلا ما حُذف. كل عنصر: ``{n, kind, text, url, tweet_id, created_at}``."""
    from . import insights as I

    gone = deleted_ids(cfg)
    items: List[Dict[str, Any]] = []
    seen: set = set()
    for r in I._fetch_rows(cfg.id, 300):
        outcome = I._loads(r.outcome, {})
        if isinstance(outcome, list):
            outcome = {"results": outcome}
        for res in I._iter_results(outcome):
            cap = res.get("capability")
            if cap not in _KIND or not res.get("success"):
                continue
            out = res.get("output") or {}
            url = _url_of(out)
            m = _ANY_STATUS_RE.search(url)
            tid = m.group(1) if m else ""
            if not tid or tid in gone or tid in seen:
                continue
            seen.add(tid)
            text = out.get("text") or out.get("reply_text") or " / ".join(out.get("texts") or [])
            items.append({"kind": _KIND[cap], "text": re.sub(r"\s+", " ", str(text or "")).strip()[:220],
                          "url": url, "tweet_id": tid,
                          "created_at": r.created_at.isoformat()[:16].replace("T", " ") if r.created_at else ""})
            if len(items) >= limit:
                break
        if len(items) >= limit:
            break
    for i, it in enumerate(items, 1):
        it["n"] = i
    return items


def live_posts(cfg, limit: int = 15) -> List[Dict[str, Any]]:
    """منشورات الحساب كما تظهر على X الآن (متصفح، ~30-60 ث) — تشمل ما نُشر يدوياً."""
    from app.aeos.mcp.base import MCPRequest
    from . import tools_service as TS

    res = TS.registry().call(MCPRequest(
        employee_id=cfg.id, tool="read_my_posts",
        params={"cookie_label": cfg.cookie_label, "handle": cfg.account_username, "limit": limit},
        context={"server": "playwright_x", "cookie_label": cfg.cookie_label}))
    if not res.success:
        raise RuntimeError(res.error or "تعذّرت قراءة منشورات الحساب")
    gone = deleted_ids(cfg)
    out = []
    for p in res.data.get("posts") or []:
        m = _ANY_STATUS_RE.search(p.get("url") or "")
        if not m or m.group(1) in gone:
            continue
        out.append({"kind": "منشور", "text": re.sub(r"\s+", " ", str(p.get("text") or "")).strip()[:220],
                    "url": p.get("url"), "tweet_id": m.group(1),
                    "metrics": {k: p.get(k) for k in ("likes", "reposts", "replies", "views") if p.get(k) is not None}})
    for i, it in enumerate(out, 1):
        it["n"] = i
    return out


# ─────────────────────────── تحليل الأهداف ───────────────────────────
def _tweet_target(cfg, raw: str, own: bool, posts: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    raw = raw.strip().translate(_AR_DIGITS)
    handle = (cfg.account_username or "").lower()
    m = _STATUS_RE.search(raw)
    if m:
        author, tid = m.group(1), m.group(2)
        if own and author.lower() not in (handle, "i"):
            raise PermissionError(f"المنشور {tid} لحساب @{author} وليس لـ @{cfg.account_username} — "
                                  "لا يمكن حذف منشورات الآخرين")
        return {"tweet_id": tid, "url": f"https://x.com/{author}/status/{tid}"}
    if re.fullmatch(r"\d{12,25}", raw):
        return {"tweet_id": raw, "url": f"https://x.com/{cfg.account_username}/status/{raw}"}
    # «آخر» / رقم من قائمة list_posts
    idx = 1 if _LAST_RE.match(raw) else (int(raw.lstrip("#")) if re.fullmatch(r"#?\d{1,2}", raw) else 0)
    if idx:
        posts = posts if posts is not None else recorded_posts(cfg, 20)
        hit = next((p for p in posts if p["n"] == idx), None)
        if hit is None:
            raise LookupError(f"لا يوجد منشور رقم {idx} في قائمة منشورات @{cfg.account_username} "
                              f"(المتاح {len(posts)}) — استخدم list_posts أولاً")
        return {"tweet_id": hit["tweet_id"], "url": hit["url"], "text": hit.get("text", "")}
    raise ValueError(f"هدف غير مفهوم: {raw!r} — مرّر رابط التغريدة أو معرّفها أو رقمها من list_posts أو «آخر»")


def _profile_target(raw: str) -> Dict[str, Any]:
    raw = raw.strip()
    m = _PROFILE_URL_RE.search(raw) or _HANDLE_RE.match(raw)
    if not m:
        raise ValueError(f"حساب غير مفهوم: {raw!r} — مرّر @اليوزر أو رابط الملف الشخصي")
    h = m.group(1)
    return {"handle": h, "url": f"https://x.com/{h}"}


def resolve_targets(cfg, action: str, targets: List[str],
                    posts: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    kind = ACTIONS[action]["target"]
    targets = [str(t).strip() for t in targets if str(t or "").strip()]
    if kind == "none":
        return [{}]
    if not targets:
        raise ValueError("حدّد الهدف (target/targets)")
    if len(targets) > MAX_TARGETS:
        raise ValueError(f"الحد الأقصى {MAX_TARGETS} أهداف في الأمر الواحد")
    out: List[Dict[str, Any]] = []
    for t in targets:
        if kind == "profile":
            p = _profile_target(t)
            if p["handle"].lower() == (cfg.account_username or "").lower():
                raise ValueError("لا يتابع الحساب نفسه ولا يلغي متابعتها")
            out.append(p)
        elif kind == "scheduled":
            out.append({"event_id": t})
        else:
            out.append(_tweet_target(cfg, t, kind == "own_tweet", posts))
    if kind == "own_tweet":
        # نص المنشور في المعاينة: المالك يوافق على ما يراه، لا على رقم
        known = {p["tweet_id"]: p for p in (posts or []) + recorded_posts(cfg, 30)}
        for o in out:
            if not o.get("text") and o["tweet_id"] in known:
                o["text"] = known[o["tweet_id"]].get("text", "")
    uniq = {tuple(sorted(o.items())): o for o in out}
    return list(uniq.values())


def scheduled_for(cfg) -> List[Dict[str, Any]]:
    """المنشورات المجدولة لهذا الحساب فقط (الخادم يعيد مجدول كل الحسابات)."""
    from app.services import x_bridge

    res = x_bridge.list_scheduled_posts(limit=200) or {}
    return [{"event_id": p.get("event_id"), "run_at": p.get("run_at"), "status": p.get("status"),
             "text": str(p.get("content") or "")[:200]}
            for p in res.get("posts") or [] if p.get("cookie_label") == cfg.cookie_label]


# ─────────────────────────── التأكيد على مرحلتين ───────────────────────────
def _gc() -> None:
    now = time.time()
    for k in [k for k, v in _PENDING.items() if v["expires"] < now]:
        _PENDING.pop(k, None)


def create_pending(user_id, turn: str, cfg, action: str, targets: List[Dict[str, Any]],
                   fields: Dict[str, Any]) -> str:
    cid = uuid.uuid4().hex[:8]
    with _LOCK:
        _gc()
        _PENDING[(user_id, cid)] = {"turn": turn, "employee_id": cfg.id, "action": action,
                                    "targets": targets, "fields": fields,
                                    "expires": time.time() + _CONFIRM_TTL}
    return cid


def pending_for(user_id, employee_id: Optional[str] = None) -> List[Dict[str, Any]]:
    with _LOCK:
        _gc()
        return [{"confirmation_id": cid, **v} for (uid, cid), v in _PENDING.items()
                if uid == user_id and (employee_id is None or v["employee_id"] == employee_id)]


def pending_brief(user_id, employee_id: Optional[str] = None) -> str:
    """سطر لكل طلب معلّق — يُحقن في موجّه المنسّق: نتيجة الأداة لا تُحفظ في سجل
    المحادثة، فبدون هذا لا يعرف الموديل في الرسالة التالية ما الذي يوافق عليه المالك."""
    from app.aeos.bootstrap import get_platform

    rows = pending_for(user_id, employee_id)
    if not rows:
        return ""
    repo = get_platform().repository
    lines = []
    for r in rows:
        cfg = repo.get(r["employee_id"])
        who = f"@{cfg.account_username}" if cfg else r["employee_id"]
        what = "، ".join(_describe(t) for t in r["targets"])[:300]
        if r["fields"]:
            what += " " + ", ".join(f"{k}={v[:60]}" for k, v in r["fields"].items())
        lines.append(f"- confirmation_id={r['confirmation_id']} · {ACTIONS[r['action']]['label']} "
                     f"(action={r['action']}) · {who}: {what}")
    return ("طلبات على X بانتظار موافقة المالك (إن وافق صراحةً في رسالته هذه فاستدعِ x_account "
            "بنفس action وconfirmation_id؛ إن رفض أو غيّر الموضوع فلا تنفّذ):\n" + "\n".join(lines))


def drop_stale(user_id, turn: str) -> int:
    """أسقط معاينات المالك التي أُنشئت قبل هذه الرسالة ولم تُؤكَّد فيها."""
    with _LOCK:
        stale = [k for k, v in _PENDING.items() if k[0] == user_id and v["turn"] != turn]
        for k in stale:
            _PENDING.pop(k, None)
    return len(stale)


def take_pending(user_id, turn: str, cid: str, cfg, action: str, start=None) -> Dict[str, Any]:
    """أعد الطلب المعلّق واحذفه — فقط من رسالة لاحقة للمالك، لنفس الموظف ونفس الإجراء."""
    with _LOCK:
        _gc()
        req = _PENDING.get((user_id, str(cid or "").strip()))
        if req is None:
            raise LookupError("معرّف التأكيد غير صالح أو انتهت صلاحيته (15 دقيقة) — اعرض المعاينة من جديد")
        if req["turn"] == turn:
            raise PermissionError("لا يُنفَّذ التأكيد في الرسالة نفسها: اعرض المعاينة على المالك "
                                  "وانتظر موافقته الصريحة في رسالته التالية")
        if req["employee_id"] != cfg.id:
            raise PermissionError("معرّف التأكيد يخص موظفاً آخر")
        if req["action"] != action:
            raise PermissionError(f"معرّف التأكيد يخص «{ACTIONS[req['action']]['label']}» "
                                  f"لا «{ACTIONS.get(action, {}).get('label', action)}»")
        if start:
            start(cfg, action, req["targets"], req["fields"])
        return _PENDING.pop((user_id, str(cid).strip()))


# ─────────────────────────── التنفيذ ───────────────────────────
def _mcp_params(action: str, cfg, t: Dict[str, Any], fields: Dict[str, Any]) -> Dict[str, Any]:
    kind = ACTIONS[action]["target"]
    if kind == "own_tweet":
        return {"tweet_id": t["tweet_id"], "expected_handle": cfg.account_username}
    if kind == "tweet":
        return {"tweet_url": t["url"]}
    if kind == "profile":
        return {"profile_url": t["url"]}
    if kind == "scheduled":
        return {"event_id": t["event_id"]}
    return {k: v for k, v in fields.items() if k in PROFILE_FIELDS and v}


def _describe(t: Dict[str, Any]) -> str:
    if "handle" in t:
        return f"@{t['handle']}"
    if "event_id" in t:
        return f"المجدول {t['event_id']}"
    if "tweet_id" in t:
        snippet = f" «{t['text'][:70]}»" if t.get("text") else ""
        return f"[{t['tweet_id']}]({t['url']}){snippet}"
    return "الملف الشخصي"


def _record(cfg, action: str, done: List[Dict[str, Any]]) -> None:
    """أثر الفعل في حالة الموظف: المحذوف لا يُعرض ولا يُحسب، وما تراجع عنه المالك لا
    يعود إليه الموظف تلقائياً في دوراته."""
    from app.aeos.bootstrap import get_platform

    platform = get_platform()
    fresh = platform.repository.get(cfg.id) or cfg
    flags = fresh.runtime_state.flags
    if action == "delete_post":
        ids = list(flags.get("deleted_posts") or [])
        ids += [t["tweet_id"] for t in done if t["tweet_id"] not in ids]
        flags["deleted_posts"] = ids[-_DELETED_KEEP:]
    elif action in ("unlike", "unretweet", "unbookmark", "unfollow"):
        engaged = flags.setdefault("engaged_urls", [])
        for t in done:
            if t.get("url") and t["url"] not in engaged:
                engaged.append(t["url"])
    platform.repository.save(fresh)
    try:
        from app.aeos.memory.base import MemoryLayerType

        platform.container.resolve("memory").remember(
            cfg.id, MemoryLayerType.REFLECTION,
            f"بأمر المالك: {ACTIONS[action]['label']} — " + "، ".join(_describe(t) for t in done)[:400],
            importance=3)
    except Exception:  # noqa: BLE001 - الذاكرة تحسين لا شرط
        logger.debug("x_control memory note failed", exc_info=True)


def execute(cfg, action: str, targets: List[Dict[str, Any]], fields: Dict[str, Any],
            notify=None) -> Dict[str, Any]:
    """نفّذ على X بالتتابع وأعد الملخّص. ``notify(text)`` يُستدعى بالنتيجة النهائية."""
    from app.aeos.mcp.base import MCPRequest
    from . import tools_service as TS

    meta = ACTIONS[action]
    done, already, failed = [], [], []
    for t in targets:
        r = TS.registry().call(MCPRequest(
            employee_id=cfg.id, tool=meta["tool"], params=_mcp_params(action, cfg, t, fields),
            context={"server": "x", "cookie_label": cfg.cookie_label}))
        if not r.success:
            failed.append((t, str(r.error or "فشل غير معروف")[:200]))
        elif (r.data or {}).get("already"):
            already.append(t)
        else:
            done.append(t)
    if done:
        try:
            _record(cfg, action, done)
        except Exception:  # noqa: BLE001
            logger.exception("x_control state update failed")

    lines = [f"🛠️ **@{cfg.account_username} — {meta['label']}**"]
    lines += [f"✅ {_describe(t)}" for t in done]
    lines += [f"ℹ️ {_describe(t)} — لم يكن قائماً أصلاً (لا تغيير)" for t in already]
    lines += [f"❌ {_describe(t)} — {err}" for t, err in failed]
    text = "\n".join(lines)
    if notify:
        notify(text)
    return {"done": len(done), "already": len(already), "failed": len(failed), "summary": text}


def run_background(cfg, action: str, targets: List[Dict[str, Any]], fields: Dict[str, Any]) -> None:
    def notify(text: str) -> None:
        from app.aeos.bootstrap import get_platform

        get_platform().notify_owner(cfg.id, text, "aeos_x_control")

    def run() -> None:
        try:
            execute(cfg, action, targets, fields, notify)
        except Exception as exc:  # noqa: BLE001
            logger.exception("x_control %s failed", action)
            notify(f"❌ تعذّر تنفيذ «{ACTIONS[action]['label']}» على @{cfg.account_username}: {exc}")

    from ..security import gateway
    p = gateway.current.get()
    with gateway.execution(p.user_id, cfg.id):
        params = [_mcp_params(action, cfg, t, fields) for t in targets]
        with gateway.x_batch(cfg.id, cfg.cookie_label, ACTIONS[action]["tool"], params):
            threading.Thread(target=carry(run), daemon=True).start()
