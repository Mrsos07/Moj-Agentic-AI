#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
موج AI - Chatbot Runner
تشغيل سريع للتطبيق
"""
import os
import sys

# Fix encoding for Windows console - MUST be done first
if sys.platform == 'win32':
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except:
            pass
    else:
        # Fallback for older Python versions
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Set environment variable for UTF-8
os.environ['PYTHONIOENCODING'] = 'utf-8'

from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

# Check if running in venv
print(f"Python executable: {sys.executable}")
print(f"Python version: {sys.version}")

import uvicorn

PORT = int(os.getenv("PORT", "8000"))
# هذا الجهاز فقط افتراضياً: على 0.0.0.0 كانت الواجهة البرمجية مكشوفة لكل الشبكة.
# BIND_HOST=0.0.0.0 يعيد الإتاحة للأجهزة الأخرى صراحةً (الخوادم الداخلية تقرأه أيضاً).
BIND_HOST = os.getenv("BIND_HOST", "127.0.0.1")


def _free_port(port: int) -> None:
    """يُخلي المنفذ من أي بقية تشغيل سابق قبل البدء.

    السبب حقيقي ومُشاهَد: عند إيقاف التطبيق بـCtrl+C (أو انهيار عملية الخدمة
    أثناء إعادة تحميل) قد تموت عملية الخدمة ويبقى **مشرف الإعادة** ممسكاً
    بالسوكت المستمع. النتيجة أسوأ من التوقف الكامل: المنفذ يقبل كل اتصال ولا
    يجيب أبداً، فتتجمّد الواجهة («الموظف الذكي لا يظهر»، «سجل المحادثات لا
    يظهر») ويغرق سجل vite بـECONNRESET، وتفشل حتى /api/aeos/health.
    ولأن المنفذ مشغول، التشغيل الجديد لا يستطيع أخذ مكانه.

    نفس المعالجة المستخدمة أصلاً لمنفذ app/x (5789) في x_bridge.
    """
    import socket
    import subprocess

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        if probe.connect_ex(("127.0.0.1", port)) != 0:
            return  # المنفذ حر

    print(f"[Runner] المنفذ {port} مشغول ببقية تشغيل سابق — أُخليه ...")
    try:
        if sys.platform == "win32":
            out = subprocess.run(["netstat", "-ano", "-p", "tcp"],
                                 capture_output=True, text=True, timeout=5).stdout
            pids = {
                line.split()[-1] for line in out.splitlines()
                if f":{port}" in line and "LISTENING" in line
            }
            for pid in pids - {str(os.getpid())}:
                subprocess.run(["taskkill", "/F", "/T", "/PID", pid],
                               capture_output=True, timeout=10)
                print(f"[Runner] أُوقفت العملية القديمة PID={pid}")
        else:
            out = subprocess.run(["lsof", "-ti", f":{port}"],
                                 capture_output=True, text=True, timeout=5).stdout
            for pid in {p.strip() for p in out.splitlines() if p.strip()}:
                subprocess.run(["kill", "-9", pid], capture_output=True, timeout=5)
                print(f"[Runner] أُوقفت العملية القديمة PID={pid}")
    except Exception as exc:  # noqa: BLE001
        print(f"[Runner] ⚠️ تعذّر إخلاء المنفذ {port}: {exc}")
        print(f"[Runner]    أوقف العملية يدوياً أو شغّل بمنفذ آخر: PORT=8001")


def _run_with_self_healing_reload(config: "uvicorn.Config") -> None:
    """مشرف إعادة تحميل يعيد إحياء العامل إن مات — لا زومبي بعد اليوم.

    مشرف uvicorn الأصلي يعيد التشغيل عند **تغيّر ملف فقط**. إن انهار العامل
    (مثلاً انهيار أصلي في Kuzu بكود 0xC0000005) يبقى المشرف ممسكاً بالمنفذ
    8000 إلى الأبد: يقبل TCP ولا يجيب أبداً، فيختفي الموظف الذكي وسجل
    المحادثة من الواجهة حتى يعيد المستخدم التشغيل يدوياً. هذا هو العرض
    المُشاهَد «بعد أول دورة يختفي كل شيء».

    هنا نفحص حياة العامل في كل لفة انتظار (~كل 10 ثوانٍ بحكم مهلة watchfiles)
    ونعيد تشغيله إن مات بشكل غير متوقع — فيتعافى النظام خلال ثوانٍ، وتقوم
    آلية `.writing` في GraphitiStore بعزل القاعدة المتضررة عند الإقلاع.
    """
    import time as _time

    from uvicorn.supervisors.watchfilesreload import WatchFilesReload
    from uvicorn._subprocess import get_subprocess

    class SelfHealingReload(WatchFilesReload):
        _last_revive = 0.0

        def should_restart(self):
            exitcode = self.process.exitcode
            if exitcode is not None and not self.should_exit.is_set():
                # مهلة قصيرة: عند Ctrl+C يصل الإشعار للعامل قبل المشرف أحياناً
                _time.sleep(0.5)
                if not self.should_exit.is_set():
                    since = _time.monotonic() - SelfHealingReload._last_revive
                    if since < 5.0:  # انهيار فوري متكرر — لا ندخل حلقة جنونية
                        _time.sleep(5.0 - since)
                    print(f"[Runner] ⚠️ عملية الخدمة ماتت (exitcode={exitcode}) — "
                          "إعادة تشغيل تلقائية ...")
                    self.process.join()
                    self.process = get_subprocess(
                        config=self.config, target=self.target, sockets=self.sockets
                    )
                    self.process.start()
                    SelfHealingReload._last_revive = _time.monotonic()
            return super().should_restart()

    server = uvicorn.Server(config)
    sock = config.bind_socket()
    SelfHealingReload(config, target=server.run, sockets=[sock]).run()


if __name__ == "__main__":
    print("=" * 60)
    print("🤖 موج AI - Chatbot Interface")
    print("=" * 60)
    print("Starting server...")
    print(f"URL: http://localhost:{PORT}")
    print("تأكد من إضافة OPENROUTER_API_KEY في ملف .env")
    print("=" * 60)

    _free_port(PORT)

    # إعادة التحميل التلقائية مفيدة للتطوير لكن مشرفها الأصلي يتحول زومبي إن
    # انهار العامل — لذلك نستخدم مشرفاً يعيد الإحياء. RELOAD=0 يشغّل عملية
    # واحدة بلا مشرف — أهدأ وأثبت للتشغيل الطويل.
    reload = os.getenv("RELOAD", "1").strip().lower() not in ("0", "false", "no")

    if reload:
        _run_with_self_healing_reload(uvicorn.Config(
            "app.main:app",
            host=BIND_HOST,
            port=PORT,
            reload=True,
            # Only watch our own source. Watching the whole tree (incl. venv)
            # caused constant reloads that dropped API connections (ECONNRESET).
            reload_dirs=["app"],
            reload_excludes=["venv/*", "*/site-packages/*"],
            log_level="info",
        ))
    else:
        print("[Runner] إعادة التحميل معطّلة (RELOAD=0) — عملية واحدة ثابتة")
        uvicorn.run(
            "app.main:app",
            host=BIND_HOST,
            port=PORT,
            reload=False,
            log_level="info",
        )
