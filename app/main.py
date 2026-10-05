# تحميل .env الرئيسي إلى متغيرات البيئة (يخدم جميع الخدمات الفرعية)
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any
import uvicorn
import json
from datetime import datetime
from typing import List
import asyncio
import uuid
from io import BytesIO

try:
    from PIL import Image
except Exception:
    Image = None

from app.services.ai_service import AIService, ai_service
from app.services.webhook_service import WebhookService
from app.core.config import settings
from app.db.database import init_db, get_db
from app.auth.routes import router as auth_router
from app.api.intent_routes import router as intent_router
from app.api.admin_routes import router as admin_router
from app.api.agent_routes import router as agent_router
from app.api.conversation_routes import router as conversation_router
from app.api.x_routes import router as x_router
from app.api.admin_accounts_routes import router as admin_accounts_router
from app.api.user_accounts_routes import router as user_accounts_router
from app.api.schedule_routes import router as schedule_router
from app.api.telegram_routes import router as telegram_router
from app.api.trend_routes import router as trend_router
from app.agents.agent_manager import agent_manager
from app.services import x_bridge
from app.services.memory_service import memory_service
from app.trend_detector.scheduler.scheduler import trend_scheduler
from app.scheduler.tick import scheduler_tick
from app.aeos.api.routes import router as aeos_router
from app.aeos.bootstrap import get_platform
from app.aeos.services import orchestrator

app = FastAPI(title="كنق الاتمته - Chatbot API", version="1.0.0")

# Security Headers Middleware
@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    return response

# Initialize database on startup
@app.on_event("startup")
async def startup_event():
    init_db()
    print("Database initialized")
    
    try:
        agent_manager.initialize()
        print("AI Agents initialized successfully")
    except Exception as e:
        print(f"Warning: AI Agents initialization failed: {str(e)}")
        print("Check .env.agents file for LLM configuration")

    try:
        trend_scheduler.start()
        print("Trend Detector scheduler started")
    except Exception as e:
        print(f"Warning: Trend Detector scheduler failed: {str(e)}")

    try:
        x_bridge.start_xsuite_server()
    except Exception as e:
        print(f"Warning: X Suite server failed to start: {str(e)}")

    try:
        x_bridge.start_loginx_server()
    except Exception as e:
        print(f"Warning: LoginX server failed to start: {str(e)}")

    try:
        from app.services import identity_bridge
        identity_bridge.start_identity_server()
    except Exception as e:
        print(f"Warning: Identity server failed to start: {str(e)}")

    try:
        asyncio.create_task(scheduler_tick())
        print("Scheduler tick started (every 30s)")
    except Exception as e:
        print(f"Warning: Scheduler tick failed: {str(e)}")

    # Capture the running loop + register the realtime sink so background
    # AEOS workers can push live cycle updates to connected chat clients.
    try:
        from app.services import realtime_bus
        manager.loop = asyncio.get_running_loop()
        realtime_bus.register_sink(manager.push_from_thread)
        print("Realtime chat bridge registered (AEOS -> WebSocket)")
    except Exception as e:
        print(f"Warning: realtime chat bridge failed to register: {str(e)}")

    try:
        get_platform().start()
        print("AEOS (AI Employee OS) started — scheduler + worker pool running")
    except Exception as e:
        print(f"Warning: AEOS platform failed to start: {str(e)}")


@app.on_event("shutdown")
async def shutdown_event():
    try:
        get_platform().stop()
        print("AEOS platform stopped")
    except Exception as e:
        print(f"Warning: AEOS shutdown error: {str(e)}")

# Include auth routes
app.include_router(auth_router)

# Include intent recognition routes
app.include_router(intent_router)

# Include admin routes
app.include_router(admin_router)

# Include agent routes
app.include_router(agent_router)

# Include conversation routes
app.include_router(conversation_router)

# Include X platform routes
app.include_router(x_router)

# Include accounts management routes
app.include_router(admin_accounts_router)
app.include_router(user_accounts_router)

# Include trend detector routes
app.include_router(trend_router)

# Include schedule event routes
app.include_router(schedule_router)

# Include telegram integration routes
app.include_router(telegram_router)

# Include AEOS (AI Employee Operating System) routes
app.include_router(aeos_router)

# CORS Configuration - تقييد النطاقات المسموحة
import os
default_allowed_origins = ",".join([
    "http://localhost:3000",
    "http://localhost:5173",
    "https://stopping-idly-endearing.ngrok-free.dev",
])
ALLOWED_ORIGINS = [
    origin.strip().rstrip("/")
    for origin in os.getenv("ALLOWED_ORIGINS", default_allowed_origins).split(",")
    if origin.strip()
]
ALLOWED_ORIGIN_REGEX = os.getenv("ALLOWED_ORIGIN_REGEX", r"https://.*\.ngrok-free\.dev").strip() or None

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=ALLOWED_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=3600,
)

static_path = Path(__file__).parent.parent / "static"
static_path.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

uploads_path = static_path / "uploads"
uploads_path.mkdir(parents=True, exist_ok=True)

# ─── واجهة React المبنية (frontend/dist) ───
FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"
if FRONTEND_DIST.exists() and (FRONTEND_DIST / "assets").exists():
    app.mount(
        "/assets",
        StaticFiles(directory=str(FRONTEND_DIST / "assets")),
        name="react_assets",
    )

ai_service = AIService()
webhook_service = WebhookService()

def _norm_uid(value) -> Optional[int]:
    """Best-effort normalize a user id to int (frontend may send str/int)."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# Keyword groups to detect an employee-focused question in the chat.
_EMP_STATS_KW = ("احصائ", "إحصائ", "تفاعل", "أداء", "اداء", "تقرير", "انجاز", "إنجاز")
_EMP_CONTENT_KW = ("ماذا نشر", "وش نشر", "شنو نشر", "المحتوى", "المحتويات", "منشورات",
                   "التغريدات", "نشرت", "آخر ما نشر", "اخر ما نشر", "المنشورات")
_EMP_SUGGEST_KW = ("اقترح", "اقتراح", "اقتراحات", "توصية", "توصيات", "نصيح", "رأيك", "رايك")
_EMP_TOPICS_KW = ("المواضيع", "مواضيع", "الموضوعات", "راجع المواضيع", "وش المواضيع",
                  "ايش المواضيع", "المواضيع المتاحة")
# action verbs that must go to the normal agents, not the employee Q&A
_ACTION_PREFIXES = ("انشر", "اكتب", "غرد", "غرّد", "رد ", "ردّ", "تابع", "اعجب",
                    "أعجب", "لايك", "ريتويت", "احذف", "امسح")


# مهمّة حرّة موجّهة للموظف: «روح للتايم لاين وشوف تغريدة تناسب تخصصك وردّ عليها»
def _maybe_mission_employee(message: str, pinned_account: Optional[str],
                            user_id: Optional[int]) -> Optional[str]:
    """نقاش مفتوح ينتهي بتنفيذ: المستخدم يحدّد **المصدر والفعل** فقط، والموظف
    يختار الهدف بنفسه. يعيد None إن كانت الرسالة نقاشاً أو محتوى ملصوقاً فيمرّ
    الطلب للمعالجات الأخرى."""
    if not (message or "").strip() or not pinned_account:
        return None
    try:
        from app.aeos.bootstrap import get_platform
        from app.aeos.services import employee_mission

        platform = get_platform()
        cfg = platform.repository.get_by_account("x", pinned_account)
        if not cfg:
            return None
        return employee_mission.handle_message(
            message, cfg, platform.llm, user_id)
    except Exception as e:  # noqa: BLE001
        print(f"Warning: employee mission routing failed: {e}")
        return None


# توجيه الموظف للتفاعل مع محتوى محدّد من الشات: «تفاعل مع هذا المحتوى …»
def _maybe_engage_employee(message: str, pinned_account: Optional[str]) -> Optional[str]:
    """إن وجّه المستخدم الموظف المثبّت للتفاعل مع محتوى محدّد ألصقه (رد/إعجاب/
    إعادة نشر)، نشغّل دورة تفاعل موجّهة في الخلفية ونعيد ردّاً تأكيدياً. الموظف
    يبحث عن التغريدة على X من نصّها/حسابها ثم يتفاعل معها. يعيد None ليمر الطلب
    للمعالجات الأخرى."""
    import re as _re
    import threading as _threading

    msg = (message or "").strip()
    if not msg or not pinned_account:
        return None
    if msg.endswith("؟") or msg.endswith("?"):
        return None

    trigger = _re.search(
        r"تفاعل\s*مع|تفاعل|رد\s*على|ردّ\s*على|علّق\s*على|علق\s*على|"
        r"اعمل\s*(?:لايك|ريتويت|رتويت)|اعد\s*نشر|أعد\s*نشر|إعادة\s*نشر|"
        r"اعادة\s*نشر|ريتويت|رتويت",
        msg)
    if not trigger:
        return None

    # بوابة صارمة لتقليل الالتقاط الخاطئ: لا نفعّلها إلا مع مؤشّر محتوى واضح
    # (كلمة «المحتوى/التالي…»، أو رابط، أو رسالة متعددة الأسطر، أو أمر في البداية).
    lines = [ln for ln in msg.splitlines() if ln.strip()]
    markers = _re.search(
        r"هذا\s*المحتوى|المحتوى|التالي|التغريدة|البوست|المنشور|الخبر|النص", msg)
    has_url = bool(_re.search(r"https?://", msg))
    multiline = len(lines) >= 2
    starts = trigger.start() <= 3
    if not (markers or has_url or multiline or starts):
        return None

    low = msg.lower()
    actions = set()
    if _re.search(r"رد\s*على|ردّ\s*على|علّق|علق|تعليق", low):
        actions.add("reply")
    if _re.search(r"لايك|اعجب|أعجب|إعجاب|اعجاب|قلب", low):
        actions.add("like")
    if _re.search(r"ريتويت|رتويت|اعد\s*نشر|أعد\s*نشر|إعادة\s*نشر|اعادة\s*نشر", low):
        actions.add("retweet")
    if ("تفاعل" in low) or not actions:
        actions |= {"reply", "like"}

    # استخرج المحتوى المُلصق: من الأسطر التالية، أو بعد كلمة مؤشّر، أو بعد الفعل
    content = ""
    if multiline and trigger.start() < 60:
        content = "\n".join(lines[1:]).strip()
    if not content and markers:
        mm = _re.search(
            r"(?:المحتوى|التالي|التغريدة|البوست|المنشور|الخبر|النص)\s*[:：]?\s*(.+)$",
            msg, _re.DOTALL)
        if mm:
            content = mm.group(1).strip()
    if not content:
        content = msg[trigger.end():].lstrip(" :：-–—مع").strip()
    content = content.strip("*«»\"'“” \n").strip()
    if not content or len(content) < 4:
        return None

    url_m = _re.search(r"https?://\S+", content)
    url = url_m.group(0) if url_m else ""
    handle_m = _re.search(r"@(\w{2,30})", content)
    handle = handle_m.group(0) if handle_m else ""

    try:
        from app.aeos.bootstrap import get_platform

        platform = get_platform()
        cfg = platform.repository.get_by_account("x", pinned_account)
        if not cfg:
            return None
        from app.aeos.services import employee_mission as _EM

        acts_ar = "، ".join({
            "reply": "الرد", "like": "الإعجاب", "retweet": "إعادة النشر",
        }.get(a, a) for a in sorted(actions))
        snippet = _re.sub(r"\s+", " ", content).strip()[:90]
        # المُرسِل المضمون: يعيد المحاولة إن كانت هناك دورة جارية، ويتحقّق من أن
        # الفعل وقع فعلاً على X، ويُبلّغ الشات بالسبب إن لم يقع.
        _threading.Thread(
            target=_EM.dispatch_directed,
            args=(cfg, {"force_fresh": True, "directed_engagement": {
                "text": content, "handle": handle, "url": url,
                "actions": sorted(actions),
            }},
                [_EM.EXPECTED_CAPABILITY[a] for a in sorted(actions)
                 if a in _EM.EXPECTED_CAPABILITY],
                f"{acts_ar} على «{snippet}»"),
            daemon=True,
        ).start()
        return (f"🫡 تمام! وجّهت الموظف @{pinned_account} للتفاعل ({acts_ar}) مع المحتوى: "
                f"«{snippet}». يبحث عنه على X الآن ويتفاعل بأسلوب شخصيته، "
                "وسيوافيك بالنتيجة هنا في الشات. ⏳")
    except Exception as e:  # noqa: BLE001
        print(f"Warning: employee engage routing failed: {e}")
        return None


# توجيه مباشر للموظف الذكي من الشات: «انشر عن 2» أو «تكلم عن الذكاء الاصطناعي»
def _maybe_direct_employee(message: str, pinned_account: Optional[str]) -> Optional[str]:
    """إن وجّه المستخدم الموظف المثبّت للكتابة عن موضوع (بالاسم أو برقمه من
    قائمة مواضيع آخر دورة)، شغّل دورة موجّهة فوراً في الخلفية وأعد ردّاً
    تأكيديّاً. يعيد None ليمر الطلب للمعالجات الأخرى."""
    import re as _re
    import threading as _threading

    msg = (message or "").strip()
    if not msg or not pinned_account:
        return None

    topic = None
    # (أ) صيغة صريحة مبدوءة بأمر: «انشر/اكتب/تكلم/غرّد عن …»
    m = _re.match(
        r"^(?:انشر|أنشر|اكتب|أكتب|تكلم|تكلّم|غرد|غرّد)(?:\s+تغريدة)?\s+(?:عن|حول)\s+(.+)$",
        msg,
        _re.DOTALL,
    )
    if m:
        topic = m.group(1).strip().strip("«»\"'“”")
    else:
        # (ب) صيغة طبيعية: طلب صريح بالنشر + رغبة/أمر، مع لصق خبر/موضوع داخل
        # الرسالة (مثل: «الخبر هذا ابيك تجيب له محتوى قوي وتنشره»). نتجنّب
        # خطف الأسئلة (؟) حتى لا نلتقط «متى ينشر الموظف؟».
        if not (msg.endswith("؟") or msg.endswith("?")):
            publish = _re.search(
                r"انشر|أنشر|تنشر|تنشره|وتنشر|وتنشره|ننشر|ينشر|ينشره|نشره|نشرها|"
                r"انشره|انشرها|غرّد|غرد", msg)
            desire = _re.search(
                r"اب(?:ي|غى)|أب(?:ي|غى)|ابيك|أبيك|بغيت|جهّ?ز|حضّ?ر|اكتب|أكتب|"
                r"سوّ?ي|من\s*فضلك|تكفى|ودّ?ي|ابغاك|أبغاك", msg)
            if publish and desire:
                mm = _re.search(r"(?:عن|حول)\s+(.+)$", msg, _re.DOTALL)
                if mm:
                    topic = mm.group(1).strip().strip("«»\"'“”")
                else:
                    # لا يوجد «عن …» صريح — مرّر الطلب كاملاً كموضوع موجّه؛
                    # منشئ المحتوى سيستخرج الخبر/الموضوع الجوهري منه.
                    topic = msg
    if not topic:
        return None

    try:
        from app.aeos.bootstrap import get_platform

        platform = get_platform()
        cfg = platform.repository.get_by_account("x", pinned_account)
        if not cfg:
            return None

        # رقم موضوع؟ استبدله بنصّه من قائمة مواضيع آخر دورة (topic_suggestions)
        num = _re.fullmatch(r"(?:رقم\s*|الموضوع\s*)?(\d{1,2})", topic)
        if num:
            idx = int(num.group(1)) - 1
            sugg = (cfg.runtime_state.flags or {}).get("topic_suggestions") or []
            if 0 <= idx < len(sugg):
                topic = str(sugg[idx])
            else:
                shown = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sugg)) or "(لا توجد)"
                return (f"❗ ما عندي موضوع رقم {idx + 1} من آخر دورة. "
                        f"المواضيع المتاحة:\n{shown}")

        # اعرض مقتطفاً نظيفاً من الطلب (سطر واحد، مضغوط المسافات)
        snippet = _re.sub(r"\s+", " ", topic).strip()[:90]
        # دورة موجّهة فورية في الخلفية (لا نحجب الـWebSocket — التنفيذ يأخذ دقائق).
        # عبر المُرسِل المضمون: النشر يجب أن يقع على X فعلاً، وإلا يُبلَّغ السبب.
        from app.aeos.services import employee_mission as _EM

        _threading.Thread(
            target=_EM.dispatch_directed,
            args=(cfg, {"directed_topic": topic},
                  ["publish_content", "publish_thread"],
                  f"نشر منشور عن «{snippet}»"),
            daemon=True,
        ).start()
        return (f"🫡 تمام! استلم الموظف @{pinned_account} الطلب: «{snippet}». "
                "يبحث فيه الآن ويجهّز منشوراً قوياً بأسلوب شخصيته ثم ينشره، "
                "وسيوافيك بالنتيجة والرابط هنا في الشات. ⏳")
    except Exception as e:  # noqa: BLE001
        print(f"Warning: employee directive routing failed: {e}")
        return None


def _format_topic_suggestions(cfg) -> str:
    """اعرض المواضيع/الوسوم التي اطّلع عليها الموظف في آخر دورة (topic_suggestions)
    ليراجعها المستخدم ويوجّهه للكتابة عن أحدها بـ«انشر عن رقم»."""
    account = getattr(cfg, "account_username", "") or "الموظف"
    sugg = (getattr(cfg, "runtime_state", None) and cfg.runtime_state.flags.get("topic_suggestions")) or []
    if not sugg:
        return (f"🗒️ لا توجد مواضيع محفوظة من آخر دورة للموظف @{account} بعد. "
                "شغّل دورة الآن ثم راجع المواضيع.")
    listing = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sugg))
    return (f"🗒️ **مواضيع آخر دورة للموظف @{account}:**\n{listing}\n\n"
            "👉 وجّهه للكتابة عن أحدها: اكتب «انشر عن 2» مثلاً، وسينفّذ بأسلوب شخصيته.")


def _maybe_show_employee_data(message: str, pinned_account: Optional[str],
                              user_id: Optional[int]) -> Optional[str]:
    """عند وجود حساب مثبّت لموظف ذكي، إن طلب المستخدم «عرض» بيانات حيّة
    (أخبار/ترند/تايم لاين/أهم البوستات) نجلبها في الخلفية ونرسل النتيجة للشات.
    يتطلب فعل «عرض» صريحاً حتى لا نخطف أسئلة النقاش («ناقشني في الخبر»)."""
    import re as _re

    msg = (message or "").strip()
    if not msg or not pinned_account:
        return None
    low = msg.lower()
    show = _re.search(
        r"اعرض|أعرض|اظهر|أظهر|عرض|جيب|هات|ورني|ورّني|وريني|اعطني|أعطني|show|"
        r"ابغى\s*(?:اشوف|أشوف)|ابي\s*(?:اشوف|أشوف)|ابغى\s*ترى",
        low,
    )
    if not show:
        return None
    if _re.search(r"خبر|أخبار|اخبار|news", low):
        kind = "news"
    elif _re.search(r"ترند|الرائج|المتداول|trend", low):
        kind = "trends"
    elif _re.search(r"تايم\s*لاين|التايم|الخط\s*الزمني|timeline", low):
        kind = "timeline"
    elif _re.search(r"أهم\s*البوستات|اهم\s*البوستات|أهم\s*المنشورات|اهم\s*المنشورات|"
                    r"منش(?:ئ|يء)\s*المحتوى|توب\s*بوست|top\s*posts", low):
        kind = "top_posts"
    else:
        return None
    try:
        from app.aeos.bootstrap import get_platform
        from app.aeos.services import employee_data

        platform = get_platform()
        cfg = platform.repository.get_by_account("x", pinned_account)
        if not cfg:
            return None
        return employee_data.request_data(kind, cfg, user_id)
    except Exception as e:  # noqa: BLE001
        print(f"Warning: employee data show failed: {e}")
        return None


def _maybe_analyze_employee(message: str, pinned_account: Optional[str],
                            user_id: Optional[int]) -> Optional[str]:
    """إن طلب المستخدم من الموظف المثبّت أن يبحث/يحلّل عبر مصادره ويستنتج
    المواضيع المرتبطة باهتماماته، نُشغّل التحليل عبر كل المصادر في الخلفية."""
    import re as _re

    msg = (message or "").strip()
    if not msg or not pinned_account:
        return None
    low = msg.lower()
    analyze = bool(_re.search(
        r"حلّ?ل|استنتج|ابحث\s*وحلّ?ل|المواضيع\s*المرتبط[ةه]|المواضيع\s*المتعلق[ةه]", low))
    if not analyze:
        if _re.search(r"(?:ما\s*(?:هي\s*)?|وش\s*|أي\s*|ايش\s*)?المواضيع", low) and \
           _re.search(r"اهتمام|تخصص", low):
            analyze = True
        elif _re.search(r"بناء[ًا]?\s*على", low) and _re.search(r"مواضيع|موضوع|اهتمام", low):
            analyze = True
    if not analyze:
        return None
    try:
        from app.aeos.bootstrap import get_platform
        from app.aeos.services import employee_data

        platform = get_platform()
        cfg = platform.repository.get_by_account("x", pinned_account)
        if not cfg:
            return None
        return employee_data.request_analysis(cfg, user_id)
    except Exception as e:  # noqa: BLE001
        print(f"Warning: employee analyze failed: {e}")
        return None


def _maybe_answer_employee_query(message: str, pinned_account: Optional[str]) -> Optional[str]:
    """If a pinned account maps to an AI employee and the user is asking about it
    (stats/contents/suggestions/discussion), answer using the insights service.
    Returns the answer text, or None to fall through to the normal agents."""
    msg = (message or "").strip()
    if not msg or not pinned_account:
        return None
    low = msg.lower()
    if any(low.startswith(p) for p in _ACTION_PREFIXES):
        return None
    try:
        from app.aeos.bootstrap import get_platform
        from app.aeos.services import insights as I

        platform = get_platform()
        cfg = platform.repository.get_by_account("x", pinned_account)
        if not cfg:
            return None
        llm = platform.llm
        eid = cfg.id

        if any(k in low for k in _EMP_STATS_KW):
            return I.format_stats(cfg, I.aggregate_stats(eid))
        if any(k in low for k in _EMP_CONTENT_KW):
            return I.format_contents(cfg, I.recent_contents(eid, 8))
        if any(k in low for k in _EMP_SUGGEST_KW):
            return I.build_suggestions(cfg, I.aggregate_stats(eid), llm)
        if any(k in low for k in _EMP_TOPICS_KW):
            return _format_topic_suggestions(cfg)
        # نقاش حرّ مؤصَّل بآخر بيانات/تحليل جلبه الموظف. نفتح النقاش إذا: ذُكر
        # «موظف»، أو كان سؤالاً (؟)، أو كان متابعةً لتحليل/بيانات سابقة (فصّل/ليش/
        # اشرح/رأيك أو إشارة لموضوع/خبر/ترند/تغريدة/بوست/رقم) مع وجود سياق محفوظ.
        import re as _re
        try:
            from app.aeos.services import employee_data
            extra = employee_data.recent_data_text(eid)
        except Exception:  # noqa: BLE001
            extra = ""
        followup = bool(_re.search(
            r"موضوع|المواضيع|الخبر|الأخبار|الاخبار|الترند|التغريد|البوست|المنشور|"
            r"رقم|فصّ?ل|اشرح|وضّ?ح|ليش|لماذا|علّق|علق|رأيك|رايك|حلّ?ل", low))
        if ("موظف" in low) or msg.endswith("؟") or msg.endswith("?") or (extra and followup):
            return I.answer_question(cfg, msg, llm, eid, extra_context=extra)
        return None
    except Exception as e:  # noqa: BLE001
        print(f"Warning: employee query routing failed: {e}")
        return None


def _employee_open_discussion(message: str, pinned_account: Optional[str]) -> Optional[str]:
    """نقاش مفتوح مع الموظف المثبّت بلا أي بوابة كلمات مفتاحية.

    آخر محطة قبل رسالة «خارج النطاق»: عندما يوجد موظف ذكي مثبّت في الشات فالمالك
    يخاطبه هو، فمن الخطأ أن يُجاب بقائمة خدمات البوت. النقاش مؤصَّل ببيانات
    الموظف وآخر ما جلبه فعلاً، لا بكلام عام."""
    if not (message or "").strip() or not pinned_account:
        return None
    try:
        from app.aeos.bootstrap import get_platform
        from app.aeos.services import insights as I

        platform = get_platform()
        cfg = platform.repository.get_by_account("x", pinned_account)
        if not cfg:
            return None
        try:
            from app.aeos.services import employee_data
            extra = employee_data.recent_data_text(cfg.id)
        except Exception:  # noqa: BLE001
            extra = ""
        return I.answer_question(cfg, message, platform.llm, cfg.id,
                                 extra_context=extra)
    except Exception as e:  # noqa: BLE001
        print(f"Warning: employee open discussion failed: {e}")
        return None


def _maybe_handle_employee_command(message: str, pinned_account: Optional[str],
                                   user_id: Optional[int]) -> Optional[str]:
    """Detect and execute an AI-employee control command (activate/pause/run/
    status/list/delete/schedule/persona/teach) from natural language, in any
    dialect. Runs BEFORE the employee Q&A helper so control commands are
    executed rather than answered as discussion. Returns a reply or None."""
    import re as _re

    msg = (message or "").strip()
    if not msg:
        return None
    low = msg.lower()
    # مرشّح رخيص: لا نُشغّل المصنّف إلا إذا كان النص يشير للموظف/دورة عمل.
    if not _re.search(r"موظف|employee|agent|\bدورة\b", low):
        return None
    try:
        from app.services.intent_service import intent_service
        from app.aeos.services.employee_commands import (
            EMPLOYEE_INTENT_VALUES, handle as _handle_employee,
        )

        res = intent_service.detect_intent(msg)
        if res.intent.value in EMPLOYEE_INTENT_VALUES:
            return _handle_employee(
                res.intent.value, res.entities, msg, pinned_account, user_id
            )
    except Exception as e:  # noqa: BLE001
        print(f"Warning: employee command routing failed: {e}")
    return None


async def _run_coordinator(message: str, user_id, pinned: Optional[str], db,
                           session_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """المنسّق العام في خيط منفصل: حلقة الأدوات تأخذ عدة نداءات LLM، وتشغيلها
    داخل حلقة الأحداث كان سيجمّد كل اتصالات الشات الأخرى حتى تنتهي."""
    try:
        return await asyncio.to_thread(
            orchestrator.handle, message, _norm_uid(user_id),
            pinned=pinned, db=db, session_id=session_id)
    except Exception as e:  # noqa: BLE001
        print(f"Warning: coordinator failed: {e}")
        return None


def _remember_turn(user_id, session_id: Optional[str], user_message: str, reply: str,
                   employee_handle: Optional[str] = None) -> None:
    """الذاكرة بعد كل دور (في الخلفية — نداءات LLM لا تؤخّر الرد): تحديث الملخّص
    المتدحرج للمحادثة، واستخراج ما يستحق الحفظ طويلاً لنطاق المنسّق أو الموظف."""
    if not (user_message or "").strip() or not (reply or "").strip() or not session_id:
        return
    import threading as _threading

    def run():
        try:
            from app.aeos.memory import agent_memory as AM

            uid = _norm_uid(user_id)
            handle = employee_handle or (session_id[4:] if session_id.startswith("emp_") else None)
            team = orchestrator.employees_for(uid)
            employee = next((c for c in team if handle and c.account_username.lower() == handle.lower()), None)
            db2 = next(get_db())
            try:
                conv = memory_service.get_or_create_conversation(db=db2, user_id=uid, session_id=session_id)
                conv_id = conv.id
            finally:
                db2.close()
            AM.after_turn(uid, conv_id, user_message, reply, get_platform().llm, team, employee)
        except Exception as e:  # noqa: BLE001
            print(f"Warning: memory update failed: {e}")

    _threading.Thread(target=run, daemon=True).start()


async def _send_employee_reply(websocket: WebSocket, db, user_id, session_id,
                               user_message: str, reply: str, intent: str, agent: str,
                               metadata: Optional[Dict[str, Any]] = None,
                               employee_handle: Optional[str] = None) -> None:
    """احفظ رسالة المستخدم والرد في المحادثة ثم أرسل الرد للواجهة."""
    try:
        conv = memory_service.get_or_create_conversation(
            db=db, user_id=user_id, session_id=session_id)
        memory_service.add_message(
            db=db, conversation_id=conv.id, role="user", content=user_message)
        memory_service.add_message(
            db=db, conversation_id=conv.id, role="assistant",
            content=reply, intent=intent, agent=agent)
    except Exception as _e:  # noqa: BLE001
        print(f"Warning: persist {intent} failed: {_e}")
    await manager.send_message({"type": "typing", "status": False}, websocket)
    await manager.send_message({
        "type": "assistant_message",
        "message": reply,
        "metadata": metadata or {"agent": agent},
        "timestamp": datetime.now().isoformat(),
    }, websocket)
    _remember_turn(user_id, session_id, user_message, reply, employee_handle)


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        # mapping من user_id إلى قائمة WebSockets للإشعارات (تسجيل دخول، إلخ)
        self.user_websockets: Dict[int, List[WebSocket]] = {}
        # websocket -> user_id (set once the client sends a message with user_id)
        self.user_ids: Dict[WebSocket, Optional[int]] = {}
        # main asyncio loop, captured at startup so background threads can push
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        # إزالة من mapping المستخدمين
        for uid in list(self.user_websockets.keys()):
            if websocket in self.user_websockets[uid]:
                self.user_websockets[uid].remove(websocket)
                if not self.user_websockets[uid]:
                    del self.user_websockets[uid]
        self.user_ids.pop(websocket, None)

    def register_user(self, user_id, websocket: WebSocket):
        """ربط user_id بـ WebSocket لإرسال إشعارات لاحقة."""
        if user_id is None:
            return
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            return
        self.user_websockets.setdefault(uid, [])
        if websocket not in self.user_websockets[uid]:
            self.user_websockets[uid].append(websocket)

    def set_user(self, websocket: WebSocket, user_id) -> None:
        self.user_ids[websocket] = _norm_uid(user_id)

    async def send_message(self, message: dict, websocket: WebSocket):
        await websocket.send_json(message)

    async def send_to_user(self, user_id, message: dict) -> int:
        """يرسل رسالة لكل WebSockets الخاصة بمستخدم. يرجع عدد الرسائل المرسلة."""
        if user_id is None:
            return 0
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            return 0
        sent = 0
        for ws in list(self.user_websockets.get(uid, [])):
            try:
                await ws.send_json(message)
                sent += 1
            except Exception:
                # WebSocket مقطوع — نشيله
                try:
                    self.user_websockets[uid].remove(ws)
                except Exception:
                    pass
        return sent

    async def broadcast_to_user(self, user_id: Optional[int], message: dict):
        """Send a message to every live connection owned by ``user_id``.

        If ``user_id`` is None we broadcast to all connections (best-effort).
        """
        uid = _norm_uid(user_id)
        conns = list(self.active_connections)
        targets = [
            ws for ws in conns
            if uid is None or self.user_ids.get(ws) == uid
        ]
        # Fallback: if no socket has identified as this user yet (e.g. the chat
        # was opened but the user hasn't sent a message), still deliver to all
        # open connections so autonomous cycle updates are never silently lost.
        if not targets and uid is not None:
            targets = conns
        for ws in targets:
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001
                pass

    def push_from_thread(self, user_id: Optional[int], payload: dict) -> None:
        """Thread-safe entry point used by background workers (AEOS)."""
        loop = self.loop
        if loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self.broadcast_to_user(user_id, payload), loop
            )
        except Exception:  # noqa: BLE001
            pass


manager = ConnectionManager()

# Models for API requests
class MessageRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

@app.get("/", response_class=HTMLResponse)
async def get_react_app():
    """يخدّم تطبيق React المبني — هو الواجهة الرئيسية للنظام."""
    react_index = FRONTEND_DIST / "index.html"
    if react_index.exists():
        return FileResponse(react_index)
    # احتياطي: لو React لم يُبنَ بعد، نخدّم الواجهة القديمة كـ fallback
    legacy_html = Path(__file__).parent.parent / "templates" / "chat.html"
    if legacy_html.exists():
        return FileResponse(legacy_html)
    return HTMLResponse(
        content=(
            "<h1>تطبيق React غير مبني</h1>"
            "<p>شغّل: <code>cd frontend && npm install && npm run build</code></p>"
        ),
        status_code=503,
    )

@app.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                message_data = json.loads(data)
            except Exception:
                await manager.send_message({
                    "type": "error",
                    "message": "Invalid JSON payload",
                    "timestamp": datetime.now().isoformat()
                }, websocket)
                continue
            
            # نبضة على مستوى التطبيق: المتصفح لا يرى ping/pong الخاص بالبروتوكول،
            # فبدونها يظل السوكت «مفتوحاً» ظاهرياً بعد موت الخادم ولا تصل رسائل.
            # يجب أن تُعالج قبل أي شيء، وإلا حُسبت رسالةً فارغة وأُزعج الوكيل بها.
            if message_data.get("type") == "ping":
                await manager.send_message(
                    {"type": "pong", "timestamp": datetime.now().isoformat()},
                    websocket,
                )
                continue

            user_message = message_data.get("message", "")
            session_id = message_data.get("session_id", None)
            user_id = message_data.get("user_id", None)
            user_email = message_data.get("user_email", None)
            attachment = message_data.get("attachment", None)
            file_upload = message_data.get("file_upload", None)
            pinned_account = message_data.get("pinned_account", None)
            # سياق المحادثة الجارية لذاكرة الموظف قصيرة المدى (انظر agent_memory.CURRENT_CHAT)
            from app.aeos.memory.agent_memory import CURRENT_CHAT
            CURRENT_CHAT.set({"user_id": _norm_uid(user_id), "session_id": session_id})

            # remember which user owns this socket so background AEOS cycle
            # updates can be pushed to the right chat client in real time.
            if user_id is not None:
                manager.set_user(websocket, user_id)

            # ربط هذا الـ WebSocket بـ user_id لإرسال إشعارات تسجيل الدخول لاحقاً
            if user_id:
                manager.register_user(user_id, websocket)

            await manager.send_message({
                "type": "user_message",
                "message": user_message,
                "attachment": attachment,
                "timestamp": datetime.now().isoformat()
            }, websocket)
            
            await manager.send_message({
                "type": "typing",
                "status": True
            }, websocket)
            
            try:
                # معالجة ملفات الكوكيز و CSV
                if file_upload:
                    file_name = file_upload.get("name", "")
                    file_content = file_upload.get("content", "")
                    file_type = file_upload.get("type", "")
                    
                    # ── ملف CSV لتسجيل دخول جماعي ──
                    if file_name.endswith('.csv'):
                        try:
                            import csv, io, threading
                            from app.agents.tools import x_login_account, LOGINX_BASE_URL, LOGINX_API_KEY
                            
                            reader = csv.DictReader(io.StringIO(file_content))
                            accounts = []
                            for row in reader:
                                username = (row.get("username") or "").strip()
                                password = (row.get("password") or "").strip()
                                email = (row.get("email") or "").strip()
                                if username and password:
                                    accounts.append({"username": username, "password": password, "email": email})
                            
                            if not accounts:
                                await manager.send_message({
                                    "type": "assistant_message",
                                    "message": "❌ ملف CSV فارغ أو لا يحتوي على أعمدة username و password",
                                    "timestamp": datetime.now().isoformat()
                                }, websocket)
                                continue
                            
                            await manager.send_message({
                                "type": "typing",
                                "status": False
                            }, websocket)
                            
                            await manager.send_message({
                                "type": "assistant_message",
                                "message": f"📋 تم قراءة **{len(accounts)}** حساب من ملف CSV\n\n🔄 جاري بدء تسجيل الدخول الجماعي...\n\n⏳ سيتم تسجيل كل حساب على حدة. هذه العملية قد تستغرق وقتاً.",
                                "timestamp": datetime.now().isoformat()
                            }, websocket)
                            
                            # تسجيل دخول جماعي عبر API
                            import requests
                            headers = {"X-API-Key": LOGINX_API_KEY, "Content-Type": "application/json"}
                            try:
                                resp = requests.post(
                                    f"{LOGINX_BASE_URL}/api/login/bulk",
                                    json={"accounts": accounts},
                                    headers=headers, timeout=10
                                )
                                data = resp.json()
                                if data.get("success"):
                                    session_id = data.get("session_id")
                                    await manager.send_message({
                                        "type": "assistant_message",
                                        "message": f"✅ بدأت عملية تسجيل الدخول الجماعي\n\n📊 عدد الحسابات: {len(accounts)}\n🔑 Session ID: `{session_id}`\n\nسيتم إعلامك عند الانتهاء.",
                                        "timestamp": datetime.now().isoformat()
                                    }, websocket)
                                else:
                                    await manager.send_message({
                                        "type": "assistant_message",
                                        "message": f"❌ فشل بدء التسجيل الجماعي: {data.get('error', 'خطأ')}",
                                        "timestamp": datetime.now().isoformat()
                                    }, websocket)
                            except requests.ConnectionError:
                                await manager.send_message({
                                    "type": "assistant_message",
                                    "message": "❌ سيرفر LoginX غير متصل!\n\nتأكد من تشغيل سيرفر loginx على بورت 5000",
                                    "timestamp": datetime.now().isoformat()
                                }, websocket)
                        except Exception as e:
                            await manager.send_message({
                                "type": "typing",
                                "status": False
                            }, websocket)
                            await manager.send_message({
                                "type": "assistant_message",
                                "message": f"❌ خطأ في معالجة ملف CSV: {str(e)}",
                                "timestamp": datetime.now().isoformat()
                            }, websocket)
                        continue
                    
                    # ── ملف JSON كوكيز ──
                    elif file_name.endswith('.json'):
                        # معالجة ملف كوكيز X — التحقق والحفظ مركزيان في _x_save_cookies_sync
                        from app.agents.tools import _x_save_cookies_sync
                        from pathlib import Path as _Path

                        # اسم الحساب: من نص المستخدم إن وُجد، وإلا من اسم الملف
                        label = (str(user_message).strip() if user_message else "") or _Path(file_name).stem.strip()

                        # نمرّر المحتوى الخام (نصاً) — الدالة تتكفّل بالتحليل والتحقق من auth_token والحفظ
                        result = _x_save_cookies_sync(file_content, label, user_id=user_id) or {}

                        await manager.send_message({
                            "type": "typing",
                            "status": False
                        }, websocket)

                        if result.get("success"):
                            saved_label = result.get("label", label)
                            await manager.send_message({
                                "type": "assistant_message",
                                "message": (
                                    f"✅ تمّت إضافة الحساب **@{saved_label}** بنجاح عبر الكوكيز!\n\n"
                                    f"يمكنك الآن:\n"
                                    f"• تثبيته من زر **حساباتي** لتنفيذ الإجراءات عليه\n"
                                    f"• تفعيله كـ **موظف ذكي** ليعمل تلقائياً"
                                ),
                                "timestamp": datetime.now().isoformat()
                            }, websocket)
                        else:
                            await manager.send_message({
                                "type": "assistant_message",
                                "message": (
                                    f"❌ تعذّر إضافة الحساب: {result.get('message', 'فشل حفظ الكوكيز')}\n\n"
                                    f"تأكد أن الملف بصيغة JSON صالحة ويحتوي على كوكي `auth_token`."
                                ),
                                "timestamp": datetime.now().isoformat()
                            }, websocket)
                        continue
                    else:
                        await manager.send_message({
                            "type": "assistant_message",
                            "message": "تم استلام الملف. الأنواع المدعومة:\n\n📄 **CSV** — تسجيل دخول جماعي (أعمدة: username, password, email)\n📎 **JSON** — ملف كوكيز X (يحتوي على auth_token)",
                            "timestamp": datetime.now().isoformat()
                        }, websocket)
                        continue

                if (not user_message or not str(user_message).strip()) and attachment:
                    try:
                        db = next(get_db())
                        conversation = memory_service.get_or_create_conversation(
                            db=db,
                            user_id=user_id,
                            session_id=session_id
                        )
                        memory_service.add_message(
                            db=db,
                            conversation_id=conversation.id,
                            role="user",
                            content="",
                            metadata={"attachment": attachment}
                        )
                    except Exception as e:
                        print(f"Warning: Failed to persist attachment-only message: {str(e)}")

                    await manager.send_message({
                        "type": "typing",
                        "status": False
                    }, websocket)
                    await manager.send_message({
                        "type": "assistant_message",
                        "message": "تم استلام المرفق. أرسل نصًا مع المرفق إذا كنت تريد مني معالجته.",
                        "timestamp": datetime.now().isoformat()
                    }, websocket)
                    continue

                # الحصول على جلسة قاعدة البيانات
                db = next(get_db())

                # ── المنسّق العام: رسالة للفريق كله (≥2 موظف مذكور أو «كل الموظفين») ──
                team_mentions = orchestrator.mentioned_employees(user_message, user_id)
                if orchestrator.is_fleet_message(user_message, team_mentions):
                    coord = await _run_coordinator(user_message, user_id, pinned_account,
                                                   db, session_id)
                    if coord:
                        await _send_employee_reply(
                            websocket, db, user_id, session_id, user_message,
                            coord["message"], "aeos_coordinator", "Coordinator",
                            {"agent": "Coordinator", "dispatched": coord["dispatched"]})
                        continue

                # موظف واحد مذكور بـ@ يعمل كتثبيت لهذه الرسالة فقط، ويُزال من نص
                # الأمر لأن الأنماط الحتمية مثبّتة على بداية النص («^انشر عن …»).
                effective_pin = pinned_account
                route_message = user_message
                if len(team_mentions) == 1:
                    effective_pin = team_mentions[0].account_username
                    route_message = orchestrator.strip_mentions(user_message, team_mentions) or user_message

                # بناء الـ metadata: المرفق + الحساب المثبّت من الشات
                agent_metadata = {}
                if attachment:
                    agent_metadata["attachment"] = attachment
                if effective_pin:
                    agent_metadata["pinned_account"] = effective_pin

                # ── المسارات الحتمية للموظف (الترتيب مقصود: التنفيذ قبل النقاش) ──
                employee_routes = (
                    # مهمّة حرّة موجّهة («روح للتايم لاين وشوف تغريدة وردّ عليها»)
                    (lambda: _maybe_mission_employee(route_message, effective_pin, user_id),
                     "aeos_employee_mission"),
                    # التفاعل مع محتوى محدّد («تفاعل مع هذا المحتوى …»)
                    (lambda: _maybe_engage_employee(route_message, effective_pin),
                     "aeos_employee_engage"),
                    # توجيه مباشر («انشر عن 2» / «تكلم عن …»)
                    (lambda: _maybe_direct_employee(route_message, effective_pin),
                     "aeos_employee_directive"),
                    # بحث وتحليل عبر كل المصادر واستنتاج المواضيع المرتبطة بالاهتمام
                    (lambda: _maybe_analyze_employee(route_message, effective_pin, user_id),
                     "aeos_employee_analyze"),
                    # عرض بيانات الموظف الحيّة (أخبار/ترند/تايم لاين/أهم البوستات)
                    (lambda: _maybe_show_employee_data(route_message, effective_pin, user_id),
                     "aeos_employee_data"),
                    # أوامر التحكم بالموظف الذكي (تنفيذ قبل النقاش/الاستعلام)
                    (lambda: _maybe_handle_employee_command(route_message, effective_pin, user_id),
                     "aeos_employee_command"),
                    # نقاش/استعلام عن الموظف الذكي المثبّت (قبل الوكلاء العامة)
                    (lambda: _maybe_answer_employee_query(route_message, effective_pin),
                     "aeos_employee_query"),
                )
                handled = False
                for route, intent_name in employee_routes:
                    reply = route()
                    if reply:
                        await _send_employee_reply(
                            websocket, db, user_id, session_id, user_message, reply,
                            intent_name, "AEOS",
                            {"agent": "AEOS",
                             "dispatched": [effective_pin] if effective_pin
                             and intent_name != "aeos_employee_query" else []},
                            employee_handle=effective_pin)
                        handled = True
                        break
                if handled:
                    continue

                # استخدام نظام الوكلاء الذكية مع الذاكرة
                try:
                    agent_result = agent_manager.process_user_message(
                        message=user_message,
                        user_id=user_id,
                        session_id=session_id,
                        metadata=agent_metadata or None,
                        db=db
                    )
                except Exception as e:
                    agent_result = {"success": False, "message": f"حدث خطأ داخل المعالج: {str(e)}"}

                await manager.send_message({
                    "type": "typing",
                    "status": False
                }, websocket)

                # إرسال رد الوكيل
                if not agent_result or not isinstance(agent_result, dict):
                    agent_result = {"success": False, "message": None}

                response_message = agent_result.get("message")

                if agent_result.get("success") and response_message:
                    # إضافة معلومات إضافية إذا كانت متاحة
                    metadata = {}
                    if agent_result.get("intent_result"):
                        metadata["intent"] = agent_result["intent_result"].get("intent")
                        metadata["confidence"] = agent_result["intent_result"].get("confidence")
                    if agent_result.get("agent"):
                        metadata["agent"] = agent_result["agent"]

                    await manager.send_message({
                        "type": "assistant_message",
                        "message": response_message,
                        "metadata": metadata,
                        "attachment": attachment,
                        "timestamp": datetime.now().isoformat()
                    }, websocket)
                    _remember_turn(user_id, session_id, user_message, response_message, effective_pin)
                elif response_message:
                    # في حالة الفشل مع وجود رسالة
                    await manager.send_message({
                        "type": "assistant_message",
                        "message": response_message,
                        "attachment": attachment,
                        "timestamp": datetime.now().isoformat()
                    }, websocket)
                else:
                    # موظف ذكي مثبّت؟ المالك يخاطبه هو، فافتح النقاش معه بدل
                    # رسالة «خارج النطاق» — هذا ما يجعل التوجيه حواراً مفتوحاً.
                    open_reply = _employee_open_discussion(route_message, effective_pin)
                    if open_reply:
                        conversation_id = agent_result.get("conversation_id")
                        if db and conversation_id:
                            try:
                                memory_service.add_message(
                                    db=db, conversation_id=conversation_id,
                                    role="assistant", content=open_reply,
                                    intent="aeos_employee_discussion", agent="AEOS",
                                )
                            except Exception as _e:  # noqa: BLE001
                                print(f"Warning: persist employee discussion failed: {_e}")
                        await manager.send_message({
                            "type": "assistant_message",
                            "message": open_reply,
                            "metadata": {"agent": "AEOS"},
                            "timestamp": datetime.now().isoformat(),
                        }, websocket)
                        _remember_turn(user_id, session_id, user_message, open_reply, effective_pin)
                        continue

                    # لا موظف مثبّت ولا نية مطابقة ⇒ المنسّق العام يتولّى الطلب.
                    await manager.send_message({"type": "typing", "status": True}, websocket)
                    coord = await _run_coordinator(user_message, user_id, None, db, session_id)
                    if coord:
                        conversation_id = agent_result.get("conversation_id")
                        if db and conversation_id:
                            try:
                                memory_service.add_message(
                                    db=db, conversation_id=conversation_id,
                                    role="assistant", content=coord["message"],
                                    intent="aeos_coordinator", agent="Coordinator",
                                )
                            except Exception as _e:  # noqa: BLE001
                                print(f"Warning: persist coordinator reply failed: {_e}")
                        await manager.send_message({"type": "typing", "status": False}, websocket)
                        await manager.send_message({
                            "type": "assistant_message",
                            "message": coord["message"],
                            "metadata": {"agent": "Coordinator", "dispatched": coord["dispatched"]},
                            "timestamp": datetime.now().isoformat(),
                        }, websocket)
                        _remember_turn(user_id, session_id, user_message, coord["message"])
                        continue
                    await manager.send_message({"type": "typing", "status": False}, websocket)

                    # الوكيل ما لقى نية مطابقة → النظام بوت خدمات، مو شات عام
                    out_of_scope_message = (
                        "🤖 أنا **موج**، بوت متخصص لإدارة حساباتك على منصات التواصل — لست شاتاً عاماً.\n\n"
                        "🎯 **خدماتي الرئيسية:**\n"
                        "• 📝 نشر التغريدات\n"
                        "• ❤️ التفاعل (لايك، إعادة نشر، رد، بوكمارك)\n"
                        "• 📊 متابعة الترندات وتحليلها\n"
                        "• 🆔 توليد هويات وهمية بالصور\n"
                        "• 👤 إدارة الحسابات (إضافة، حذف، عرض)\n"
                        "• ⏰ جدولة المنشورات\n"
                        "• 🎨 تحديث البروفايل\n\n"
                        "💡 اكتب **مساعدة** لعرض كل الأوامر بأمثلة."
                    )

                    conversation_id = agent_result.get("conversation_id")
                    if db and conversation_id:
                        try:
                            memory_service.add_message(
                                db=db,
                                conversation_id=conversation_id,
                                role="assistant",
                                content=out_of_scope_message,
                                agent="Scope_Guard",
                            )
                        except Exception as e:
                            print(f"[ScopeGuard] Failed to persist message: {str(e)[:200]}")

                    await manager.send_message({
                        "type": "assistant_message",
                        "message": out_of_scope_message,
                        "metadata": {"agent": "Scope_Guard"},
                        "attachment": attachment,
                        "timestamp": datetime.now().isoformat()
                    }, websocket)
                    
            except Exception as e:
                await manager.send_message({
                    "type": "typing",
                    "status": False
                }, websocket)
                await manager.send_message({
                    "type": "error",
                    "message": f"حدث خطأ: {str(e)}",
                    "timestamp": datetime.now().isoformat()
                }, websocket)
    
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001 - أي انقطاع آخر (ECONNRESET/إعادة تحميل)
        print(f"WebSocket closed unexpectedly: {exc}")
    finally:
        # تنظيف مضمون للاتصال في كل الحالات لمنع تسرّب الاتصالات في active_connections
        manager.disconnect(websocket)


@app.post("/api/uploads")
async def upload_file(file: UploadFile = File(...)):
    max_size_bytes = 10 * 1024 * 1024
    filename = file.filename or ""
    ext = Path(filename).suffix.lower()

    allowed_images = {".png", ".jpg", ".jpeg", ".webp"}
    allowed_docs = {".pdf", ".doc", ".docx", ".xls", ".xlsx"}
    allowed_data = {".csv", ".json", ".txt"}
    allowed = allowed_images | allowed_docs | allowed_data
    if ext not in allowed:
        raise HTTPException(status_code=400, detail="نوع الملف غير مسموح")

    data = await file.read()
    if len(data) > max_size_bytes:
        raise HTTPException(status_code=400, detail="حجم الملف كبير جدًا")

    kind = "image" if ext in allowed_images else "document"

    if kind == "image":
        if Image is not None:
            try:
                Image.open(BytesIO(data)).verify()
            except Exception:
                raise HTTPException(status_code=400, detail="ملف الصورة غير صالح")
        else:
            is_png = data.startswith(b"\x89PNG\r\n\x1a\n")
            is_jpg = data.startswith(b"\xff\xd8\xff")
            is_webp = len(data) >= 12 and data[0:4] == b"RIFF" and data[8:12] == b"WEBP"
            if not (is_png or is_jpg or is_webp):
                raise HTTPException(status_code=400, detail="ملف الصورة غير صالح")
    else:
        if ext == ".pdf" and not data.startswith(b"%PDF"):
            raise HTTPException(status_code=400, detail="ملف PDF غير صالح")
        if ext in {".docx", ".xlsx"} and not data.startswith(b"PK"):
            raise HTTPException(status_code=400, detail="ملف غير صالح")

    safe_name = f"{uuid.uuid4().hex}{ext}"
    out_path = uploads_path / safe_name
    out_path.write_bytes(data)

    return {
        "url": f"/static/uploads/{safe_name}",
        "original_name": filename,
        "content_type": file.content_type,
        "size": len(data),
        "kind": kind
    }

@app.post("/api/send-message")
async def send_message_to_n8n(request: MessageRequest):
    """
    Endpoint POST لإرسال رسالة إلى n8n webhook واستقبال الرد
    
    يمكن استخدام هذا الـ endpoint لإرسال رسائل مباشرة إلى n8n والحصول على الرد
    """
    try:
        n8n_response = await webhook_service.send_message_to_n8n(
            user_message=request.message,
            session_id=request.session_id,
            user_id=request.user_id,
            metadata=request.metadata
        )
        
        if n8n_response:
            return JSONResponse(
                status_code=200,
                content={
                    "status": "success",
                    "message": "تم إرسال الرسالة إلى n8n بنجاح",
                    "response": n8n_response,
                    "timestamp": datetime.now().isoformat()
                }
            )
        else:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "error",
                    "message": "فشل الحصول على رد من n8n. يرجى التحقق من أن n8n webhook يعمل بشكل صحيح.",
                    "timestamp": datetime.now().isoformat()
                }
            )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"حدث خطأ أثناء إرسال الرسالة: {str(e)}"
        )

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


# ────────────────────────────────────────────────────────────────────────────
# Callback داخلي يستدعيه سيرفر loginx عند اكتمال تسجيل دخول حساب X.
# يرسل إشعار في الشات للمستخدم عبر WebSocket.
# ليس مكشوفًا للعموم — يقبل اتصال محلي فقط (127.0.0.1).
# ────────────────────────────────────────────────────────────────────────────
@app.post("/api/internal/login-callback", include_in_schema=False)
async def login_callback(payload: Dict[str, Any], request):
    # حماية: يقبل فقط من localhost (loginx يشتغل محلياً)
    client_host = (request.client.host if request.client else "") if hasattr(request, "client") else ""
    if client_host not in ("127.0.0.1", "localhost", "::1", ""):
        raise HTTPException(status_code=403, detail="callback مسموح محلياً فقط")

    user_id = payload.get("user_id")
    if not user_id:
        return {"ok": True, "note": "no user_id, nothing to notify"}

    account = payload.get("account") or payload.get("username") or "حساب"
    success = bool(payload.get("success"))
    finalized = bool(payload.get("finalized"))
    error = payload.get("error", "")

    if success and finalized:
        msg = (
            f"✅ **تم تسجيل دخول الحساب '{account}' بنجاح!**\n\n"
            f"🎉 الحساب أصبح متاحاً للنشر والتفاعل.\n"
            f"💡 جرّب: `الحساب {account} انشر [النص]`"
        )
    elif success and not finalized:
        msg = (
            f"⚠️ **تسجيل دخول '{account}' اكتمل لكن فيه مشكلة في حفظ الكوكيز.**\n\n"
            f"حاول مرة ثانية أو ارفع ملف الكوكيز يدوياً."
        )
    else:
        msg = (
            f"❌ **فشل تسجيل دخول الحساب '{account}'**\n\n"
            + (f"📋 السبب: {error}\n\n" if error else "")
            + "💡 تأكد من اسم المستخدم وكلمة المرور وأعد المحاولة."
        )

    sent = await manager.send_to_user(user_id, {
        "type": "assistant_message",
        "message": msg,
        "metadata": {"agent": "X_Login_Notification"},
        "timestamp": datetime.now().isoformat(),
    })

    return {"ok": True, "notified": sent}


# ════════════════════════════════════════════════════════════════════════════
# Catch-all لدعم React Router (يجب أن يبقى آخر route — لا تضف شيئاً بعده!)
# يلتقط أي مسار غير معروف ويخدّم index.html لتتولاه React.
# ════════════════════════════════════════════════════════════════════════════
@app.get("/{full_path:path}", include_in_schema=False)
async def serve_react_spa(full_path: str):
    # لا نلتقط مسارات الـ API أو WebSocket (هذي مسجلة قبل، الـ catch-all
    # يلتقط فقط ما لم يُطابق قبله، لكن نضيف حماية إضافية).
    if full_path.startswith(("api/", "ws/", "static/", "assets/")):
        raise HTTPException(status_code=404)

    if not FRONTEND_DIST.exists():
        raise HTTPException(status_code=404, detail="React build not found")

    # لو الملف موجود فعلياً في dist (favicon, robots.txt, ...) → نخدمه مباشرة
    file_path = FRONTEND_DIST / full_path
    if file_path.is_file():
        return FileResponse(file_path)

    # خلاف ذلك → نرجع index.html و React Router يتولى التوجيه
    return FileResponse(FRONTEND_DIST / "index.html")


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )
