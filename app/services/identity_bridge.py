#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Identity Bridge — جسر التواصل مع سيرفر app/identity
يشغّل السيرفر بالخلفية ويوفر دوال استدعاء API الخاصة بتوليد الهويات.
"""
import os
import sys
import time
import atexit
import subprocess
import requests
from pathlib import Path
from typing import Dict, Any, Optional


IDENTITY_PORT = int(os.getenv("IDENTITY_PORT", "5990"))
IDENTITY_BASE_URL = f"http://127.0.0.1:{IDENTITY_PORT}"
IDENTITY_DIR = Path(__file__).resolve().parent.parent / "identity"
API_TIMEOUT = 120  # الطلبات النصية
# generate-full = نص + صورتا Gemini متتاليتان (بروفايل + هيدر)، كل صورة قد
# تأخذ 30-90 ثانية — 120ث كانت تقطع الطلب قبل اكتماله («انتهت مهلة الانتظار»).
IMAGE_API_TIMEOUT = 300

_process: Optional[subprocess.Popen] = None
_log_fh = None  # منع تجمّد العملية الابنة على أنبوب stdout غير مقروء
IDENTITY_LOG = IDENTITY_DIR / "identity_server.log"


def _resolve_gemini_key() -> Optional[str]:
    """يجلب مفتاح Gemini من كل المصادر الممكنة بالترتيب.

    1) متغيرات البيئة (GEMINI_API_KEY أو API_KEY).
    2) settings (pydantic يقرأ .env الرئيسي لكنه لا يصدّر للبيئة).
    3) قراءة .env الرئيسي مباشرة كحل أخير.
    """
    key = os.getenv("GEMINI_API_KEY") or os.getenv("API_KEY")
    if key:
        return key
    try:
        from app.core.config import settings
        if getattr(settings, "GEMINI_API_KEY", None):
            return settings.GEMINI_API_KEY
    except Exception:
        pass
    try:
        from dotenv import dotenv_values
        root_env = Path(__file__).resolve().parent.parent.parent / ".env"
        values = dotenv_values(root_env)
        return values.get("GEMINI_API_KEY") or values.get("API_KEY")
    except Exception:
        return None


def _is_running() -> bool:
    """تحقق إن سيرفر identity شغّال"""
    try:
        r = requests.get(f"{IDENTITY_BASE_URL}/api/constants", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def _kill_process_on_port(port: int) -> bool:
    """يقتل أي عملية على البورت (Windows + Linux)"""
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True, text=True, timeout=5
            )
            pids_to_kill = set()
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    if parts and parts[-1].isdigit():
                        pids_to_kill.add(parts[-1])
            for pid in pids_to_kill:
                try:
                    subprocess.run(["taskkill", "/F", "/PID", pid],
                                   capture_output=True, timeout=5)
                    print(f"[Identity-Bridge] قتل العملية PID={pid} على بورت {port}")
                except Exception:
                    pass
            return bool(pids_to_kill)
        else:
            result = subprocess.run(["lsof", "-ti", f":{port}"],
                                    capture_output=True, text=True, timeout=5)
            pids = [p.strip() for p in result.stdout.splitlines() if p.strip()]
            for pid in pids:
                try:
                    subprocess.run(["kill", "-9", pid], capture_output=True, timeout=5)
                except Exception:
                    pass
            return bool(pids)
    except Exception as e:
        print(f"[Identity-Bridge] kill_port error: {e}")
        return False


def start_identity_server() -> bool:
    """تشغيل سيرفر identity إذا لم يكن شغالاً"""
    global _process

    if _process and _process.poll() is None and _is_running():
        print(f"[Identity-Bridge] السيرفر شغّال على بورت {IDENTITY_PORT}")
        return True

    # لو في سيرفر شغّال بدون _process لدينا، استخدمه
    if _is_running():
        print(f"[Identity-Bridge] السيرفر شغّال مسبقاً على بورت {IDENTITY_PORT}")
        return True

    print(f"[Identity-Bridge] تشغيل سيرفر identity على بورت {IDENTITY_PORT} ...")

    python_exe = sys.executable
    app_py = str(IDENTITY_DIR / "app.py")
    if not Path(app_py).exists():
        print(f"[Identity-Bridge] ❌ ملف غير موجود: {app_py}")
        return False

    env = os.environ.copy()
    env["FLASK_RUN_PORT"] = str(IDENTITY_PORT)
    env["PORT"] = str(IDENTITY_PORT)

    # تمرير مفتاح Gemini من .env الرئيسي إلى خدمة Identity.
    # المفتاح موجود في .env لكنه لا يصل إلى os.environ تلقائياً: إعدادات
    # pydantic تقرأ .env داخل كائن settings فقط ولا تصدّره للبيئة، لذلك
    # os.getenv وحده يفشل ويقلع السيرفر بلا مفتاح فينهار عند الاستيراد.
    gemini_key = _resolve_gemini_key()
    if gemini_key:
        env["API_KEY"] = gemini_key
        env["GEMINI_API_KEY"] = gemini_key
    else:
        print("[Identity-Bridge] ❌ GEMINI_API_KEY غير موجود في .env — لن أشغّل السيرفر")
        return False

    try:
        # وجّه المخرجات إلى ملف سجل بدل PIPE غير مقروء يتجمّد عند امتلائه.
        global _log_fh
        try:
            if _log_fh and not _log_fh.closed:
                _log_fh.close()
        except Exception:
            pass
        _log_fh = open(IDENTITY_LOG, "w", encoding="utf-8", errors="ignore")

        _process = subprocess.Popen(
            [python_exe, "-m", "flask", "--app", "app", "run",
             "--host", os.getenv("BIND_HOST", "127.0.0.1"), "--port", str(IDENTITY_PORT)],
            cwd=str(IDENTITY_DIR),
            env=env,
            stdout=_log_fh,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        atexit.register(stop_identity_server)

        # انتظر السيرفر يبدأ
        for _ in range(30):
            time.sleep(1)
            if _is_running():
                print(f"[Identity-Bridge] ✅ سيرفر identity جاهز على بورت {IDENTITY_PORT}")
                return True
            if _process.poll() is not None:
                out = ""
                try:
                    _log_fh.flush()
                    out = IDENTITY_LOG.read_text(encoding="utf-8", errors="ignore")[-500:]
                except Exception:
                    pass
                print(f"[Identity-Bridge] ❌ السيرفر توقّف مبكراً:\n{out}")
                return False

        print("[Identity-Bridge] ❌ تجاوز وقت انتظار السيرفر")
        return False

    except Exception as e:
        print(f"[Identity-Bridge] ❌ فشل تشغيل السيرفر: {e}")
        return False


def stop_identity_server():
    global _process, _log_fh
    if _process and _process.poll() is None:
        print("[Identity-Bridge] إيقاف سيرفر identity ...")
        try:
            _process.terminate()
            _process.wait(timeout=5)
        except Exception:
            _process.kill()
        _process = None
    try:
        if _log_fh and not _log_fh.closed:
            _log_fh.close()
    except Exception:
        pass
    _log_fh = None


def _api_call(method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
    """استدعاء API generic"""
    url = f"{IDENTITY_BASE_URL}{endpoint}"
    kwargs.setdefault("timeout", API_TIMEOUT)
    try:
        resp = requests.request(method, url, **kwargs)
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text}
        if resp.status_code >= 400:
            error = data.get("error", data.get("raw", f"HTTP {resp.status_code}"))
            return {"success": False, "error": str(error), "status_code": resp.status_code}
        return data
    except requests.ConnectionError:
        return {"success": False,
                "error": f"سيرفر identity غير متاح ({IDENTITY_BASE_URL})"}
    except requests.Timeout:
        return {"success": False, "error": "انتهت مهلة الانتظار"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Endpoints المرتبطة بتوليد الهوية ──

def generate_random_profile() -> Dict[str, Any]:
    """ولّد هوية عشوائية (نصوص فقط، بدون صور)"""
    return _api_call("POST", "/api/profile/random")


def generate_profile(params: Dict[str, Any], with_images: bool = False) -> Dict[str, Any]:
    """ولّد هوية بمعايير مُحددة. with_images=True يولّد الصور أيضاً."""
    endpoint = "/api/profile/generate-full" if with_images else "/api/profile/generate"
    timeout = IMAGE_API_TIMEOUT if with_images else API_TIMEOUT
    return _api_call("POST", endpoint, json=params, timeout=timeout)


def generate_random_profile_with_images() -> Dict[str, Any]:
    """ولّد هوية عشوائية + صور"""
    # نأخذ المعايير العشوائية ثم نولّد بالصور
    rand = generate_random_profile()
    if not rand.get("success"):
        return rand
    params = rand.get("data", {}).get("params", {})
    return generate_profile(params, with_images=True)


def regenerate_bio(params: Dict[str, Any]) -> Dict[str, Any]:
    """أعد توليد البايو فقط"""
    return _api_call("POST", "/api/bio/regenerate", json=params)


def regenerate_text(params: Dict[str, Any]) -> Dict[str, Any]:
    """أعد توليد كل النصوص (اسم/يوزر/بايو) بلا صور"""
    return _api_call("POST", "/api/text/regenerate", json=params)


def construct_prompts(params: Dict[str, Any]) -> Dict[str, Any]:
    """يبني برومبتات الصورتين من المعايير (حتمي، بلا استدعاء LLM)"""
    return _api_call("POST", "/api/image/prompts", json=params)


def generate_image(prompt: str, image_type: str = "profile") -> Dict[str, Any]:
    """ولّد صورة واحدة. image_type: 'profile' أو 'header'"""
    return _api_call("POST", "/api/image/generate",
                     json={"prompt": prompt, "type": image_type},
                     timeout=IMAGE_API_TIMEOUT)
