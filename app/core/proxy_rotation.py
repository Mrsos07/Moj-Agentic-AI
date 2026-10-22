"""
تدوير IP لمنصة X — بروكسي سعودي مُتحقَّق لكل جلسة/موظف.

الفكرة: كل عملية متصفح (جلسة) تخرج من IP سعودي مختلف حسب سياسة التدوير، ويُتحقق
من الجنسية **فعلياً** عبر استعلام geo يمرّ من نفس البروكسي — لا نثق بوعد المزوّد.
إن خرج الـIP غير سعودي، تُرفض الجلسة ويُدار تدوير جديد (حتى `PROXY_MAX_TRIES`).

المزوّدون: Bright Data / Oxylabs / Decodo / IPRoyal / SOAX / أي مزوّد عبر قالب.
الاعتماد: stdlib فقط (urllib) لأن مسارات X تعمل في عمليات منفصلة بلا حزمة app.

الإعداد في .env:
    PROXY_PROVIDER=oxylabs
    PROXY_HOST=pr.oxylabs.io
    PROXY_PORT=7777
    PROXY_USERNAME=customer-XXXX        # الأساس بلا country/session
    PROXY_PASSWORD=...
    PROXY_COUNTRY=sa
    PROXY_CITY=riyadh                   # اختياري (مدن المزوّد)
    PROXY_ROTATE=session                # session | request | sticky
    PROXY_SESSION_TTL=600               # ثانية: عمر الجلسة الثابتة للموظف
    PROXY_VERIFY=1
    PROXY_MAX_TRIES=3
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_ROOT = Path(__file__).resolve().parents[2]

_PROVIDERS = {"brightdata", "oxylabs", "decodo", "iproyal", "soax"}

#: مضيف/منفذ افتراضي لكل مزوّد (يمكن تجاوزه بـPROXY_HOST/PROXY_PORT)
_DEFAULT_HOSTS = {
    "brightdata": ("brd.superproxy.io", 33335),
    "oxylabs": ("pr.oxylabs.io", 7777),
    "decodo": ("gate.decodo.com", 7000),
    "iproyal": ("geo.iproyal.com", 12321),
    "soax": ("proxy.soax.com", 5000),
}

_DOTENV_CACHE: Optional[Dict[str, str]] = None
_VERIFIED: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_REQ_COUNTER = itertools.count()


class ProxyError(RuntimeError):
    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


def _dotenv() -> Dict[str, str]:
    global _DOTENV_CACHE
    if os.environ.get("PROXY_DOTENV", "1").strip().lower() in ("0", "false", "no", "off"):
        return {}
    if _DOTENV_CACHE is None:
        out: Dict[str, str] = {}
        try:
            for line in (_ROOT / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    v = v.strip()
                    # تعليق في نهاية السطر (مسافة ثم #). كلمة مرور فيها # بلا مسافة
                    # تبقى كما هي — وإلا صار «PROXY_USERNAME=user  # شرح» اسماً مكسوراً.
                    cut = re.search(r"\s+#", v)
                    if cut:
                        v = v[:cut.start()]
                    out[k.strip()] = v.strip().strip('"').strip("'")
        except OSError:
            pass
        _DOTENV_CACHE = out
    return _DOTENV_CACHE


def _env(name: str, default: str = "") -> str:
    val = os.environ.get(name)
    if val is None or val == "":
        val = _dotenv().get(name, default)
    return (val or "").strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)) or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class ProxyConfig:
    enabled: bool
    provider: str
    host: str
    port: int
    username: str
    password: str
    country: str
    city: str
    rotate: str
    session_ttl: int
    verify: bool
    verify_url: str
    verify_ttl: int
    max_tries: int
    template: str

    @property
    def configured(self) -> bool:
        return bool(self.host and self.username and self.password)


def load_config() -> ProxyConfig:
    provider = _env("PROXY_PROVIDER", "oxylabs").lower()
    if provider not in _PROVIDERS and provider != "generic":
        provider = "oxylabs"
    default_host, default_port = _DEFAULT_HOSTS.get(provider, ("", 0))
    enabled = _env("PROXY_ENABLED", "1").lower() not in ("0", "false", "no", "off")
    cfg = ProxyConfig(
        enabled=enabled,
        provider=provider,
        host=_env("PROXY_HOST", default_host),
        port=_env_int("PROXY_PORT", default_port),
        username=_env("PROXY_USERNAME"),
        password=_env("PROXY_PASSWORD"),
        country=_env("PROXY_COUNTRY", "sa").lower(),
        city=_env("PROXY_CITY", "").lower().replace(" ", ""),
        rotate=_env("PROXY_ROTATE", "session").lower(),
        session_ttl=max(60, _env_int("PROXY_SESSION_TTL", 600)),
        verify=_env("PROXY_VERIFY", "1").lower() not in ("0", "false", "no", "off"),
        verify_url=_env("PROXY_VERIFY_URL",
                        "http://ip-api.com/json/?fields=status,country,countryCode,city,isp,query"),
        verify_ttl=max(60, _env_int("PROXY_VERIFY_TTL", 900)),
        max_tries=max(1, _env_int("PROXY_MAX_TRIES", 3)),
        template=_env("PROXY_USERNAME_TEMPLATE", ""),
    )
    return ProxyConfig(**{**cfg.__dict__, "enabled": cfg.enabled and cfg.configured})


def is_enabled() -> bool:
    return load_config().enabled


# ── الجلسات ─────────────────────────────────────────────────────────────────
def _short(scope: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9]+", "", scope or "x")[:8] or "x"
    return safe


def session_id(scope: str, cfg: Optional[ProxyConfig] = None, *, bucket: Optional[int] = None) -> str:
    """معرّف جلسة ثابت للموظف/الحساب خلال نافذة ``PROXY_SESSION_TTL``.

    يتغيّر تلقائياً مع كل نافذة زمنية = «IP جديد في كل جلسة» بلا حالة مخزّنة.
    الطول 13 حرفاً: بعض المزوّدين يحدّون معرّف الجلسة بـ16 (Bright Data/Oxylabs).
    """
    cfg = cfg or load_config()
    if cfg.rotate == "request":
        # عدّاد صريح: time_ns وحده قد يتكرّر على ويندوز فتتشابه جلستان
        seed = f"{scope}:{time.time_ns()}:{next(_REQ_COUNTER)}"
    elif cfg.rotate == "sticky":
        seed = f"{scope}:sticky"
    else:
        b = bucket if bucket is not None else int(time.time() // cfg.session_ttl)
        seed = f"{scope}:{b}"
    digest = hashlib.sha1(seed.encode()).hexdigest()[:10]
    return f"moj{digest}"


def _username(cfg: ProxyConfig, sid: str) -> str:
    """اسم مستخدم المزوّد بالصيغة التي يقرأها فعلاً.

    Oxylabs: ``customer-USER-cc-SA-city-riyadh-sessid-abc`` (+ ``-sesstime-N`` إن
    تجاوزت الجلسة 10 دقائق، وهي مدّتهم الافتراضية). Bright Data/Decodo: ``country-``
    و``session-``. IPRoyal: فواصل شرطة سفلية و``lifetime-``. SOAX: ``region-``.
    """
    base, country, city = cfg.username, cfg.country, cfg.city
    minutes = min(1440, max(1, cfg.session_ttl // 60))
    if cfg.provider == "generic" and cfg.template:
        return cfg.template.format(base=base, username=base, country=country,
                                   COUNTRY=country.upper(), city=city, sid=sid,
                                   lifetime=minutes)
    if cfg.provider == "oxylabs":
        # Oxylabs يرفض 407 بلا بادئة customer- (قيس فعلياً: resi_xxx ⇒ 407،
        # customer-resi_xxx ⇒ SA/Riyadh/STC). نضيفها إن غابت فقط.
        if not base.startswith("customer-"):
            base = f"customer-{base}"
        user = f"{base}-cc-{country.upper()}"
        if city:
            user += f"-city-{city}"
        user += f"-sessid-{sid}"
        if cfg.session_ttl > 600:          # الافتراضي عندهم 10 دقائق
            user += f"-sesstime-{minutes}"
        return user
    if cfg.provider == "iproyal":
        user = f"{base}_country-{country}"
        if city:
            user += f"_city-{city}"
        return f"{user}_session-{sid}_lifetime-{minutes}"
    if cfg.provider == "soax":
        user = f"{base}-country-{country}"
        if city:
            user += f"-region-{city}"
        return f"{user}-sessionid-{sid}"
    # brightdata / decodo
    user = f"{base}-country-{country}"
    if city:
        user += f"-city-{city}"
    return f"{user}-session-{sid}"


def build_url(cfg: ProxyConfig, sid: str) -> str:
    user = urllib.parse.quote(_username(cfg, sid), safe="-_.~")
    pwd = urllib.parse.quote(cfg.password, safe="-_.~")
    return f"http://{user}:{pwd}@{cfg.host}:{cfg.port}"


def mask(url: str) -> str:
    """بلا كلمة مرور — للسجلات والأخطاء."""
    try:
        p = urllib.parse.urlsplit(url)
        return f"{p.scheme}://{p.username or ''}:***@{p.hostname}:{p.port}"
    except Exception:  # noqa: BLE001
        return "http://***"


def playwright_proxy(url: str) -> Dict[str, str]:
    p = urllib.parse.urlsplit(url)
    out = {"server": f"http://{p.hostname}:{p.port}"}
    if p.username:
        out["username"] = urllib.parse.unquote(p.username)
    if p.password:
        out["password"] = urllib.parse.unquote(p.password)
    return out


# ── التحقق السعودي ──────────────────────────────────────────────────────────
def _geo(url: str, verify_url: str, timeout: int = 25) -> Dict[str, Any]:
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": url, "https": url}))
    req = urllib.request.Request(verify_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        raise ProxyError(f"فشل فحص geo عبر البروكسي: HTTP {e.code}", code="GEO_HTTP")
    except Exception as e:  # noqa: BLE001
        raise ProxyError(f"تعذّر الوصول لفحص geo عبر البروكسي: {type(e).__name__}",
                         code="GEO_NETWORK")


def check_country(url: str, cfg: Optional[ProxyConfig] = None) -> Dict[str, Any]:
    """يستعلم geo عبر البروكسي نفسه ويعيد النتيجة (بلا رفض)."""
    cfg = cfg or load_config()
    return _geo(url, cfg.verify_url)


def acquire(scope: str, *, verify: Optional[bool] = None, force_new: bool = False) -> Optional[str]:
    """بروكسي جاهز لهذا النطاق (موظف/حساب). ``None`` إن كانت الخدمة معطّلة.

    يرفع ``ProxyError`` إذا لم ينجح الحصول على IP سعودي بعد ``PROXY_MAX_TRIES``.
    """
    cfg = load_config()
    if not cfg.enabled:
        return None
    must_verify = cfg.verify if verify is None else verify

    last: Dict[str, Any] = {}
    for attempt in range(cfg.max_tries):
        sid = session_id(scope, cfg)
        if attempt or force_new:
            # جلسة جديدة صريحة عند إعادة المحاولة (نفس النطاق، بذرة مختلفة)
            sid = session_id(f"{scope}:{attempt}:{time.time_ns()}", cfg, bucket=0)
        url = build_url(cfg, sid)
        key = url
        hit = _VERIFIED.get(key)
        if hit and time.time() < hit[0]:
            return url
        if not must_verify:
            _VERIFIED[key] = (time.time() + cfg.verify_ttl, {"countryCode": cfg.country.upper(),
                                                             "assumed": True})
            return url
        try:
            info = check_country(url, cfg)
        except ProxyError as e:
            last = {"error": str(e)}
            continue
        code = str(info.get("countryCode") or "").upper()
        last = info
        if code == cfg.country.upper():
            _VERIFIED[key] = (time.time() + cfg.verify_ttl, info)
            return url
    raise ProxyError(
        f"لم أجد IP سعودياً بعد {cfg.max_tries} محاولات (آخر نتيجة: "
        f"{last.get('countryCode') or last.get('error') or 'غير معروفة'})",
        code="NOT_SAUDI")


def status(check: bool = False, scope: str = "status-probe") -> Dict[str, Any]:
    """حالة الخدمة بلا كشف كلمة المرور."""
    cfg = load_config()
    out: Dict[str, Any] = {
        "enabled": cfg.enabled,
        "provider": cfg.provider,
        "endpoint": f"{cfg.host}:{cfg.port}" if cfg.host else "",
        "country": cfg.country,
        "city": cfg.city,
        "rotate": cfg.rotate,
        "session_ttl": cfg.session_ttl,
        "verify": cfg.verify,
        "verified_sessions": len(_VERIFIED),
    }
    if check and cfg.enabled:
        try:
            url = acquire(scope, verify=True, force_new=True)
            info = _VERIFIED.get(url or "", (0, {}))[1]
            out["last_check"] = {k: info.get(k) for k in
                                 ("countryCode", "country", "city", "isp", "query")}
        except ProxyError as e:
            out["check_error"] = str(e)
    return out


def reset_cache() -> None:
    _VERIFIED.clear()
