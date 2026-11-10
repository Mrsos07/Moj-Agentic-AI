"""
حل الكابتشا عبر مزوّد خدمة خارجي — نقطة واحدة لكل المزوّدين.

ليه ملف مستقل بلا أي اعتماد على التطبيق؟ لأن مسار تسجيل الدخول يعمل كـsubprocess
(cwd = x_auth) وسيرفر X Suite يعمل من app/x — كلاهما يحمّل هذا الملف بمساره،
فلا يمكنه الاعتماد على حزمة ``app``. لذلك: stdlib فقط + قراءة ``.env`` يدوياً.

المزوّدون المدعومون (نفس الواجهة، يختارهم ``CAPTCHA_PROVIDER`` أو أول مفتاح موجود):
    yescaptcha  — createTask/getTaskResult (يدعم التصنيف الصوري أيضاً)
    2captcha    — in.php/res.php
    capmonster  — createTask/getTaskResult
    anticaptcha — createTask/getTaskResult

الأنواع:
    توكن: funcaptcha (Arkose)، recaptcha_v2، recaptcha_v3، hcaptcha، turnstile
    صورة: نص من صورة (OCR)
    تصنيف: أي بلاطة نضغطها في شبكة FunCaptcha / reCAPTCHA v2 (YesCaptcha فقط)

مفاتيح X (Arkose) — عامة وثابتة، وليست أسراراً؛ تُضبط من البيئة لو تغيّرت:
    تسجيل الدخول : 0152B4EB-D2DC-460A-89A1-629838B529C9
    إنشاء حساب   : 2CB16598-CB82-4CF7-B332-5990DB66F3AB
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── ثوابت X ─────────────────────────────────────────────────────────────────
X_LOGIN_URL = "https://x.com/i/flow/login"
X_SIGNUP_URL = "https://x.com/i/flow/signup"
X_ARKOSE_LOGIN_KEY = "0152B4EB-D2DC-460A-89A1-629838B529C9"
X_ARKOSE_SIGNUP_KEY = "2CB16598-CB82-4CF7-B332-5990DB66F3AB"
ARKOSE_SUBDOMAIN = "client-api.arkoselabs.com"

PROVIDERS = {
    "yescaptcha": {"base": "https://api.yescaptcha.com", "key_env": "YESCAPTCHA_KEY"},
    "2captcha": {"base": "https://2captcha.com", "key_env": "TWOCAPTCHA_KEY"},
    "capmonster": {"base": "https://api.capmonster.cloud", "key_env": "CAPMONSTER_KEY"},
    "anticaptcha": {"base": "https://api.anti-captcha.com", "key_env": "ANTICAPTCHA_KEY"},
}

_ROOT = Path(__file__).resolve().parents[2]


class CaptchaError(RuntimeError):
    """فشل الحل. ``code`` كود المزوّد، و``retryable`` يعني «أعد المحاولة بمهمة جديدة»."""

    def __init__(self, message: str, *, code: str = "", provider: str = "",
                 retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.provider = provider
        self.retryable = retryable


# ── الإعدادات ────────────────────────────────────────────────────────────────
_DOTENV_CACHE: Optional[Dict[str, str]] = None


def _dotenv() -> Dict[str, str]:
    """قراءة ``.env`` في جذر المستودع (بدون مكتبة) — لأن الخوادم الابنة لا تقرأه.

    ``CAPTCHA_DOTENV=0`` يتجاهله (اختبارات معزولة/CI بلا أسرار على القرص).
    """
    global _DOTENV_CACHE
    if os.environ.get("CAPTCHA_DOTENV", "1").strip().lower() in ("0", "false", "no", "off"):
        return {}
    if _DOTENV_CACHE is None:
        out: Dict[str, str] = {}
        try:
            for line in (_ROOT / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip()
                cut = re.search(r"\s+#", v)   # تعليق نهاية السطر (وليس # داخل المفتاح)
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
class Config:
    enabled: bool
    provider: str
    key: str
    base_url: str
    proxy: str
    timeout: int
    poll: float
    funcaptcha_type: str
    cloudflare_type: str
    x_login_key: str
    x_signup_key: str

    @property
    def key_tail(self) -> str:
        return f"…{self.key[-4:]}" if len(self.key) >= 8 else ("***" if self.key else "")


def load_config() -> Config:
    """الإعداد الفعلي. يُقرأ كل مرة (رخيص) حتى يلتقط تغيير البيئة في الاختبارات."""
    provider = _env("CAPTCHA_PROVIDER").lower()
    if provider not in PROVIDERS:
        provider = ""
    if not provider:
        for name, meta in PROVIDERS.items():
            if _env(meta["key_env"]) or _env("CAPTCHA_KEY"):
                provider = name
                break
    if not provider:
        provider = "yescaptcha"

    key = _env("CAPTCHA_KEY") or _env(PROVIDERS[provider]["key_env"])
    base = _env("CAPTCHA_BASE_URL") or PROVIDERS[provider]["base"]
    enabled = _env("CAPTCHA_ENABLED", "1").lower() not in ("0", "false", "no", "off") and bool(key)
    return Config(
        enabled=enabled,
        provider=provider,
        key=key,
        base_url=base.rstrip("/"),
        proxy=_env("CAPTCHA_PROXY"),
        timeout=max(30, _env_int("CAPTCHA_TIMEOUT", 150)),
        poll=max(1.0, float(_env_int("CAPTCHA_POLL", 5))),
        funcaptcha_type=_env("CAPTCHA_YESCAPTCHA_FUNCAPTCHA_TYPE", "FunCaptchaTaskProxylessM1"),
        cloudflare_type=_env("CAPTCHA_YESCAPTCHA_CLOUDFLARE_TYPE", "CloudFlareTaskS3"),
        x_login_key=_env("CAPTCHA_X_ARKOSE_KEY", X_ARKOSE_LOGIN_KEY),
        x_signup_key=_env("CAPTCHA_X_SIGNUP_ARKOSE_KEY", X_ARKOSE_SIGNUP_KEY),
    )


def is_enabled() -> bool:
    return load_config().enabled


# ── HTTP ────────────────────────────────────────────────────────────────────
def _post_json(url: str, payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json"})
    return _open_json(req, timeout)


def _post_form(url: str, fields: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    return _open_json(req, timeout)


def _open_json(req: urllib.request.Request, timeout: int) -> Dict[str, Any]:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:400]
        except Exception:  # noqa: BLE001 - جسم غير متاح
            pass
        raise CaptchaError(f"مزوّد الكابتشا رد HTTP {e.code}: {body}".strip(),
                           code=f"HTTP_{e.code}", retryable=e.code >= 500 or e.code == 429)
    except urllib.error.URLError as e:
        raise CaptchaError(f"تعذّر الوصول إلى مزوّد الكابتشا: {e.reason}",
                           code="NETWORK", retryable=True)
    except TimeoutError:
        raise CaptchaError("انتهت مهلة الاتصال بمزوّد الكابتشا", code="TIMEOUT", retryable=True)
    try:
        return json.loads(body or "{}")
    except json.JSONDecodeError as e:
        raise CaptchaError(f"رد غير JSON من مزوّد الكابتشا: {body[:200]}", code="BAD_JSON") from e


def _b64(image: bytes) -> str:
    if not image:
        raise CaptchaError("صورة الكابتشا فارغة", code="EMPTY_IMAGE")
    return base64.b64encode(image).decode("ascii")


# ── المزوّدون ───────────────────────────────────────────────────────────────
class _Provider:
    name = ""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def create(self, task: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def wait(self, task_id: str) -> Dict[str, Any]:
        raise NotImplementedError

    def balance(self) -> Optional[float]:
        return None

    def solve(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """ينشئ المهمة وينتظر نتيجتها. الأنواع المتزامنة ترجع الحل من ``create``."""
        created = self.create(task)
        solution = created.get("solution")
        if solution:
            return solution
        task_id = created.get("taskId") or created.get("task_id")
        if not task_id:
            raise CaptchaError("مزوّد الكابتشا لم يُرجع معرّف مهمة", code="NO_TASK_ID",
                               provider=self.name)
        return self.wait(str(task_id))


class _YesCaptcha(_Provider):
    name = "yescaptcha"

    def create(self, task: Dict[str, Any]) -> Dict[str, Any]:
        resp = _post_json(f"{self.cfg.base_url}/createTask",
                          {"clientKey": self.cfg.key, "task": task}, 30)
        if resp.get("errorId"):
            raise CaptchaError(
                f"YesCaptcha: {resp.get('errorCode')} — {resp.get('errorDescription')}",
                code=str(resp.get("errorCode") or ""), provider=self.name,
                retryable=resp.get("errorCode") in ("ERROR_CAPTCHA_UNSOLVABLE",
                                                    "ERROR_TASK_TIMEOUT"))
        return resp

    def wait(self, task_id: str) -> Dict[str, Any]:
        deadline = time.time() + self.cfg.timeout
        while time.time() < deadline:
            time.sleep(self.cfg.poll)
            resp = _post_json(f"{self.cfg.base_url}/getTaskResult",
                              {"clientKey": self.cfg.key, "taskId": task_id}, 30)
            if resp.get("errorId"):
                raise CaptchaError(
                    f"YesCaptcha: {resp.get('errorCode')} — {resp.get('errorDescription')}",
                    code=str(resp.get("errorCode") or ""), provider=self.name,
                    retryable=resp.get("errorCode") in ("ERROR_CAPTCHA_UNSOLVABLE",
                                                        "ERROR_TASK_TIMEOUT"))
            if resp.get("status") == "ready":
                return resp.get("solution") or {}
        raise CaptchaError("انتهت مهلة انتظار حل الكابتشا", code="TIMEOUT",
                           provider=self.name, retryable=True)

    def balance(self) -> Optional[float]:
        resp = _post_json(f"{self.cfg.base_url}/getBalance",
                          {"clientKey": self.cfg.key}, 20)
        if resp.get("errorId"):
            raise CaptchaError(f"YesCaptcha: {resp.get('errorCode')}", code="BALANCE",
                               provider=self.name)
        try:
            return float(resp.get("balance"))
        except (TypeError, ValueError):
            return None


class _TwoCaptcha(_Provider):
    name = "2captcha"

    def create(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(fields)
        payload.update({"key": self.cfg.key, "json": 1})
        resp = _post_form(f"{self.cfg.base_url}/in.php", payload, 40)
        if resp.get("status") != 1:
            raise CaptchaError(f"2Captcha: {resp.get('request')}",
                               code=str(resp.get("request") or ""), provider=self.name)
        return {"taskId": str(resp.get("request"))}

    def wait(self, task_id: str) -> Dict[str, Any]:
        deadline = time.time() + self.cfg.timeout
        while time.time() < deadline:
            time.sleep(self.cfg.poll)
            resp = _post_form(f"{self.cfg.base_url}/res.php",
                              {"key": self.cfg.key, "action": "get", "id": task_id, "json": 1}, 30)
            if resp.get("status") == 1:
                return {"token": str(resp.get("request") or "")}
            err = str(resp.get("request") or "")
            if err != "CAPCHA_NOT_READY":
                raise CaptchaError(f"2Captcha: {err}", code=err, provider=self.name,
                                   retryable=err in ("ERROR_CAPTCHA_UNSOLVABLE",))
        raise CaptchaError("انتهت مهلة انتظار حل الكابتشا", code="TIMEOUT",
                           provider=self.name, retryable=True)

    def balance(self) -> Optional[float]:
        resp = _post_form(f"{self.cfg.base_url}/res.php",
                          {"key": self.cfg.key, "action": "getbalance", "json": 1}, 20)
        try:
            return float(resp.get("request"))
        except (TypeError, ValueError):
            return None


class _JsonTask(_Provider):
    """CapMonster وAnti-Captcha: نفس الغلاف تماماً (clientKey/task/getTaskResult)."""

    def create(self, task: Dict[str, Any]) -> Dict[str, Any]:
        resp = _post_json(f"{self.cfg.base_url}/createTask",
                          {"clientKey": self.cfg.key, "task": task}, 30)
        if resp.get("errorId"):
            raise CaptchaError(
                f"{self.name}: {resp.get('errorCode')} — {resp.get('errorDescription')}",
                code=str(resp.get("errorCode") or ""), provider=self.name,
                retryable=resp.get("errorCode") in ("ERROR_CAPTCHA_UNSOLVABLE",))
        return resp

    def wait(self, task_id: str) -> Dict[str, Any]:
        deadline = time.time() + self.cfg.timeout
        while time.time() < deadline:
            time.sleep(self.cfg.poll)
            resp = _post_json(f"{self.cfg.base_url}/getTaskResult",
                              {"clientKey": self.cfg.key, "taskId": task_id}, 30)
            if resp.get("errorId"):
                raise CaptchaError(
                    f"{self.name}: {resp.get('errorCode')} — {resp.get('errorDescription')}",
                    code=str(resp.get("errorCode") or ""), provider=self.name,
                    retryable=resp.get("errorCode") in ("ERROR_CAPTCHA_UNSOLVABLE",))
            if resp.get("status") == "ready":
                return resp.get("solution") or {}
        raise CaptchaError("انتهت مهلة انتظار حل الكابتشا", code="TIMEOUT",
                           provider=self.name, retryable=True)

    def balance(self) -> Optional[float]:
        resp = _post_json(f"{self.cfg.base_url}/getBalance",
                          {"clientKey": self.cfg.key}, 20)
        try:
            return float(resp.get("balance"))
        except (TypeError, ValueError):
            return None


class _CapMonster(_JsonTask):
    name = "capmonster"


class _AntiCaptcha(_JsonTask):
    name = "anticaptcha"


def _provider(cfg: Config) -> _Provider:
    return {"yescaptcha": _YesCaptcha, "2captcha": _TwoCaptcha,
            "capmonster": _CapMonster, "anticaptcha": _AntiCaptcha}[cfg.provider](cfg)


# ── الواجهة العامة ──────────────────────────────────────────────────────────
class Solver:
    """حلّال واحد لكل الأنواع. لا يُنشأ بلا مفتاح — استخدم ``get_solver()``."""

    def __init__(self, cfg: Optional[Config] = None):
        self.cfg = cfg or load_config()
        if not self.cfg.enabled:
            raise CaptchaError(
                "حلّ الكابتشا غير مُفعَّل: أضف مفتاح المزوّد إلى .env "
                "(YESCAPTCHA_KEY أو TWOCAPTCHA_KEY أو CAPMONSTER_KEY أو ANTICAPTCHA_KEY)",
                code="NOT_CONFIGURED")
        self._p = _provider(self.cfg)

    # ── توكن ──
    def solve_funcaptcha(self, page_url: str = X_LOGIN_URL, public_key: str = "",
                         blob: str = "", subdomain: str = ARKOSE_SUBDOMAIN,
                         user_agent: str = "", proxy: str = "") -> str:
        """توكن Arkose. تنبيه: YesCaptcha تقول إن مسار التوكن غير مستقر — البديل هو
        ``classify_funcaptcha`` مع النقر في المتصفح."""
        key = public_key or self.cfg.x_login_key
        proxy = proxy or self.cfg.proxy
        if self.cfg.provider == "yescaptcha":
            task: Dict[str, Any] = {"type": self.cfg.funcaptcha_type,
                                    "websiteURL": page_url, "websitePublicKey": key,
                                    "funcaptchaApiJSSubdomain": subdomain}
            if blob:
                task["data"] = json.dumps({"blob": blob})
            if user_agent:
                task["userAgent"] = user_agent
            if proxy:
                task["proxy"] = proxy
        elif self.cfg.provider == "2captcha":
            task = {"method": "funcaptcha", "pageurl": page_url, "publickey": key,
                    "surl": f"https://{subdomain}"}
            if blob:
                task["data"] = json.dumps({"blob": blob})
            if user_agent:
                task["userAgent"] = user_agent
            if proxy:
                task["proxy"] = proxy
        else:
            task = {"type": "FunCaptchaTaskProxyless", "websiteURL": page_url,
                    "websitePublicKey": key, "funcaptchaApiJSSubdomain": subdomain}
            if blob:
                task["data"] = {"blob": blob}
            if user_agent:
                task["userAgent"] = user_agent
            if proxy:
                task["proxy"] = proxy
        return self._token(task)

    def solve_recaptcha_v2(self, page_url: str, sitekey: str, *, invisible: bool = False) -> str:
        if self.cfg.provider == "yescaptcha":
            task = {"type": "NoCaptchaTaskProxyless", "websiteURL": page_url,
                    "websiteKey": sitekey}
            if invisible:
                task["isInvisible"] = True
        elif self.cfg.provider == "2captcha":
            task = {"method": "userrecaptcha", "pageurl": page_url, "googlekey": sitekey}
            if invisible:
                task["invisible"] = 1
        else:
            task = {"type": "RecaptchaV2TaskProxyless", "websiteURL": page_url,
                    "websiteKey": sitekey}
        return self._token(task)

    def solve_recaptcha_v3(self, page_url: str, sitekey: str, *, action: str = "verify",
                           min_score: float = 0.7) -> str:
        if self.cfg.provider == "yescaptcha":
            task = {"type": "RecaptchaV3TaskProxyless", "websiteURL": page_url,
                    "websiteKey": sitekey, "pageAction": action, "minScore": min_score}
        elif self.cfg.provider == "2captcha":
            task = {"method": "userrecaptcha", "pageurl": page_url, "googlekey": sitekey,
                    "version": "v3", "action": action, "min_score": min_score}
        else:
            task = {"type": "RecaptchaV3TaskProxyless", "websiteURL": page_url,
                    "websiteKey": sitekey, "pageAction": action, "minScore": min_score}
        return self._token(task)

    def solve_hcaptcha(self, page_url: str, sitekey: str, *, user_agent: str = "",
                       rqdata: str = "") -> str:
        ua = user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " \
                           "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
        if self.cfg.provider == "yescaptcha":
            task = {"type": "HCaptchaTaskProxyless", "websiteURL": page_url,
                    "websiteKey": sitekey, "userAgent": ua}
            if rqdata:
                task["rqdata"] = rqdata
        elif self.cfg.provider == "2captcha":
            task = {"method": "hcaptcha", "pageurl": page_url, "sitekey": sitekey}
        else:
            task = {"type": "HCaptchaTaskProxyless", "websiteURL": page_url,
                    "websiteKey": sitekey, "userAgent": ua}
            if rqdata:
                task["rqdata"] = rqdata
        return self._token(task)

    def solve_turnstile(self, page_url: str, sitekey: str, *, action: str = "",
                        cdata: str = "", user_agent: str = "") -> str:
        if self.cfg.provider == "yescaptcha":
            task = {"type": "TurnstileTaskProxyless", "websiteURL": page_url,
                    "websiteKey": sitekey}
        elif self.cfg.provider == "2captcha":
            task = {"method": "turnstile", "pageurl": page_url, "sitekey": sitekey}
            if action:
                task["action"] = action
            if cdata:
                task["data"] = cdata
        else:
            task = {"type": "TurnstileTaskProxyless", "websiteURL": page_url,
                    "websiteKey": sitekey}
            if user_agent:
                task["userAgent"] = user_agent
        return self._token(task)

    def _token(self, task: Dict[str, Any]) -> str:
        sol = self._p.solve(task)
        for field in ("token", "gRecaptchaResponse", "request"):
            val = sol.get(field)
            if isinstance(val, str) and val.strip():
                return val.strip()
        raise CaptchaError(f"رد المزوّد بلا توكن: {list(sol)[:6]}", code="NO_TOKEN",
                           provider=self.cfg.provider)

    # ── صفحة تحدي Cloudflare («Just a moment…») ──
    def solve_cloudflare(self, page_url: str, proxy: str = "") -> Dict[str, Any]:
        """يعيد ``solution`` كاملاً (cookies/cf_clearance + user_agent + content).

        يحتاج **بروكسي عاماً**: المزوّد يفتح الصفحة عبره و``cf_clearance`` يُربط
        بمخرج الـIP والـUA — لذلك بعد الحل يجب استخدام نفس البروكسي والـUA.
        """
        if self.cfg.provider != "yescaptcha":
            raise CaptchaError("حل صفحة Cloudflare مدعوم في YesCaptcha فقط",
                               code="UNSUPPORTED", provider=self.cfg.provider)
        proxy = (proxy or self.cfg.proxy or "").strip()
        if not proxy:
            raise CaptchaError(
                "Cloudflare يحتاج بروكسي عاماً: ضع CAPTCHA_PROXY=http://user:pass@host:port "
                "(العناوين المحلية/الخاصة مرفوضة من المزوّد)",
                code="PROXY_REQUIRED", provider=self.cfg.provider)
        return self._p.solve({
            "type": self.cfg.cloudflare_type,
            "websiteURL": page_url,
            "proxy": proxy,
            "requiredCookies": ["cf_clearance"],
        })

    # ── صورة (OCR) ──
    def solve_image(self, image: bytes, *, numeric: bool = False, case: bool = False,
                    math: bool = False, min_length: int = 0, max_length: int = 0) -> str:
        body = _b64(image)
        if self.cfg.provider == "yescaptcha":
            sync = not (numeric or case or math or min_length or max_length)
            if sync:
                task = {"type": "ImageToTextTaskOcrBase", "body": body}
            else:
                task = {"type": "ImageToTextTask", "body": body}
                if case:
                    task["case"] = True
                if numeric:
                    task["numeric"] = 1
                if math:
                    task["math"] = True
                if min_length:
                    task["minLength"] = int(min_length)
                if max_length:
                    task["maxLength"] = int(max_length)
        elif self.cfg.provider == "2captcha":
            task = {"method": "base64", "body": body}
            if case:
                task["case"] = 1
            if numeric:
                task["numeric"] = 1
            if math:
                task["math"] = 1
            if min_length:
                task["min_len"] = int(min_length)
            if max_length:
                task["max_len"] = int(max_length)
        else:
            task = {"type": "ImageToTextTask", "body": body}
        sol = self._p.solve(task)
        text = sol.get("text") or sol.get("token") or ""
        text = str(text).strip()
        if not text:
            raise CaptchaError("تعذّر قراءة نص الصورة", code="NO_TEXT",
                               provider=self.cfg.provider)
        return text

    # ── تصنيف (اختيار بلاطة) ──
    def classify_funcaptcha(self, image: bytes, question: str) -> List[int]:
        if self.cfg.provider != "yescaptcha":
            raise CaptchaError("التصنيف الصوري مدعوم في YesCaptcha فقط",
                               code="UNSUPPORTED", provider=self.cfg.provider)
        sol = self._p.solve({"type": "FunCaptchaClassification",
                             "image": _b64(image), "question": question})
        return _indices(sol)

    def classify_recaptcha_v2(self, image: bytes, question_id: int) -> List[int]:
        if self.cfg.provider != "yescaptcha":
            raise CaptchaError("التصنيف الصوري مدعوم في YesCaptcha فقط",
                               code="UNSUPPORTED", provider=self.cfg.provider)
        sol = self._p.solve({"type": "ReCaptchaV2Classification",
                             "image": _b64(image), "question": int(question_id)})
        return _indices(sol)

    def balance(self) -> Optional[float]:
        return self._p.balance()


def _indices(solution: Dict[str, Any]) -> List[int]:
    """``solution.objects`` قد تكون أرقاماً أو سلاسل؛ نطبّعها لأعداد صحيحة."""
    out: List[int] = []
    for item in solution.get("objects") or []:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    if not out:
        raise CaptchaError("لم يُحدَّد أي بلاطة في الصورة", code="NO_OBJECTS")
    return out


def get_solver() -> Optional[Solver]:
    """الحلّال إن كان مُفعَّلاً، وإلا ``None`` (المسارات القديمة تبقى كما هي)."""
    try:
        return Solver()
    except CaptchaError:
        return None


def require_solver() -> Solver:
    return Solver()


def status(check_balance: bool = False) -> Dict[str, Any]:
    """حالة الإعداد — بلا أي مفتاح كامل (آخر 4 أحرف فقط)."""
    cfg = load_config()
    out: Dict[str, Any] = {
        "enabled": cfg.enabled,
        "provider": cfg.provider,
        "base_url": cfg.base_url,
        "key": cfg.key_tail,
        "timeout": cfg.timeout,
        "x_login_key": cfg.x_login_key,
        "x_signup_key": cfg.x_signup_key,
        "proxy": bool(cfg.proxy),
        "configured_keys": {name: bool(_env(meta["key_env"]) or _env("CAPTCHA_KEY"))
                            for name, meta in PROVIDERS.items()},
    }
    if check_balance and cfg.enabled:
        try:
            out["balance"] = Solver(cfg).balance()
        except CaptchaError as e:
            out["balance_error"] = str(e)
    return out


def solve_x_login_captcha(*, blob: str = "", public_key: str = "",
                          page_url: str = X_LOGIN_URL, user_agent: str = "") -> str:
    """توكن Arkose لتدفق دخول X (يُرسل في ``web_modal.completion_deeplink``)."""
    cfg = load_config()
    return Solver(cfg).solve_funcaptcha(page_url=page_url, public_key=public_key or cfg.x_login_key,
                                        blob=blob, user_agent=user_agent)
