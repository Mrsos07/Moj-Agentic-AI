"""
verify_identity.py — هوية الموظف (الصورة/الهيدر): ما يعتمده المالك هو ما يُطبَّق على X.

بلا شبكة ولا متصفح: سيرفر الهويات وapp/x والمنصة مُستبدَلة، والبيانات في مجلد مؤقت.

    .\\venv\\Scripts\\python.exe verify_identity.py
"""
from __future__ import annotations

import hashlib
import inspect
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

import app.aeos.bootstrap as B  # noqa: E402
import app.x.modules.x_profile as XP  # noqa: E402
from app.aeos.employee.models import EmployeeConfig, Persona  # noqa: E402
from app.aeos.services import employee_data as ED  # noqa: E402
from app.aeos.services import identity as ONCREATE  # noqa: E402
from app.aeos.services import identity_service as SVC  # noqa: E402
from app.aeos.services import identity_store as IS  # noqa: E402
from app.services import identity_bridge  # noqa: E402
from app.services import x_bridge as XB  # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


def raises(fn, exc=Exception):
    try:
        fn()
    except exc as e:  # noqa: BLE001
        return str(e) or True
    return False


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


# ── عزل: مجلد الموظفين ومجلد صور سيرفر الهويات مؤقتان ──
TMP = Path(tempfile.mkdtemp(prefix="verify_identity_"))
GEN = TMP / "generated"
GEN.mkdir()
IS._ROOT = TMP / "employees"
IS.generated_dir = lambda: GEN


def fake_png(tag):
    """صورة معاينة مزيّفة كما يحفظها سيرفر الهويات: /static/generated/<32 hex>.png"""
    data = b"\x89PNG\r\n\x1a\n" + hashlib.sha256(tag.encode()).digest() * 4
    name = hashlib.sha256(("n" + tag).encode()).hexdigest()[:32]
    (GEN / f"{name}.png").write_bytes(data)
    return f"{identity_bridge.IDENTITY_BASE_URL}/static/generated/{name}.png", data


# ── حدود النظام مُستبدَلة ──
CALLS, PUSHED, SYNCED = [], [], []
NEXT = {"res": {"success": True, "media": {"avatar": "confirmed", "banner": "confirmed"}}}
REAL_UPDATE = XB.update_profile


def fake_update_profile(*args, **kw):
    label = args[0] if args else kw.pop("cookie_label", "")
    sent = {k: v for k, v in kw.items() if v and k != "headless"}
    for k in ("avatar_path", "banner_path"):
        if kw.get(k):
            sent[k.replace("_path", "_bytes")] = Path(kw[k]).read_bytes()
    CALLS.append((label, sent))
    return dict(NEXT["res"])


def sync_start(cfg, record):
    SVC._run(cfg, record)


X_AFTER = {"snap": None}       # ما «تعرضه» صفحة الحساب بعد التطبيق (None = تعذّرت القراءة)
REFRESHED = []
REAL_REFRESH = None


def fake_refresh(cfg, lock_timeout=0.0):
    REFRESHED.append((cfg.id, lock_timeout))
    if X_AFTER["snap"] is None:
        raise RuntimeError("offline")
    return IS.save_x_profile(cfg.id, cfg.account_username, X_AFTER["snap"], "live")


XB.update_profile = fake_update_profile
ED._push = lambda user_id, content, cfg=None: PUSHED.append((user_id, content))
SVC._sync_persona = lambda cfg, name, bio: SYNCED.append((cfg.id, name, bio))
SVC._start = sync_start
REAL_REFRESH, SVC.refresh_x_profile = SVC.refresh_x_profile, fake_refresh


def emp(i, user=1):
    return EmployeeConfig(id=f"emp{i}", user_id=user, platform="x", account_username=f"acc{i}",
                          cookie_label=f"acc{i}",
                          persona=Persona(display_name=f"اسم {i}", bio="نبذة البرسونا", role="محلل سياسي"))


print("1) الاعتماد يثبّت بايتات الصور نفسها ويرفعها كملفات")
cfg = emp(1)
a_url, a_bytes = fake_png("avatar-1")
b_url, b_bytes = fake_png("banner-1")
ack = SVC.request_apply_identity(cfg, 1, identity={"name": "رأي وتحليل", "bio": "نبذة معتمدة",
                                                   "avatar_url": a_url, "banner_url": b_url})
label, sent = CALLS[-1]
check("تُرفع بايتات الصورة والهيدر المعتمدين نفسها — لا رابط يُعاد تنزيله",
      sent.get("avatar_bytes") == a_bytes and sent.get("banner_bytes") == b_bytes
      and "avatar_url" not in sent and "banner_url" not in sent, list(sent))
check("بالاسم والنبذة كما اعتُمدا وعلى كوكيز الموظف", label == "acc1" and sent.get("name") == "رأي وتحليل"
      and sent.get("bio") == "نبذة معتمدة")
check("الإقرار يعد بالصورة والهيدر نفسيهما", "نفسيهما" in ack and "صورة" in ack and "هيدر" in ack, ack)
msg = PUSHED[-1][1]
check("رسالة النتيجة: «أكّد X» لكل صورة أكّدها X", msg.count("أكّد X") == 2, msg)
applied = IS._read("emp1", "applied.json") or {}
check("applied.json يحفظ sha256 ما طُبّق ولا يبقى اعتماد معلّق",
      (applied.get("media") or {}).get("avatar", {}).get("sha256") == hashlib.sha256(a_bytes).hexdigest()
      and IS._read("emp1", "pending.json") is None)
check("وتُزامَن البرسونا بالاسم والنبذة المطبَّقين", SYNCED[-1] == ("emp1", "رأي وتحليل", "نبذة معتمدة"))
check("نسخة الصورة المطبَّقة محفوظة في مجلد الموظف",
      Path(IS.media_path(applied, "avatar")).read_bytes() == a_bytes)

NEXT["res"] = {"success": True, "media": {"avatar": "unconfirmed"}}
a2_url, a2_bytes = fake_png("avatar-2")
SVC.request_apply_identity(cfg, 1, identity={"avatar_url": a2_url})
msg = PUSHED[-1][1]
check("بلا تأكيد صريح من X ← لا «✓» بل «لم يصلني تأكيد»", "لم يصلني تأكيد" in msg and "✓" not in msg, msg)
NEXT["res"] = {"success": False, "message": "X رفض حفظ الصورة الشخصية (HTTP 400) — لم تُطبَّق."}
SVC.request_apply_identity(cfg, 1, identity={"name": "س", "avatar_url": a2_url})
msg = PUSHED[-1][1]
check("فشل X ← رسالة بالسبب الحقيقي ولا يبقى اعتماد معلّق", "لم يكتمل" in msg and "HTTP 400" in msg
      and IS._read("emp1", "pending.json") is None, msg)
NEXT["res"] = {"success": True, "media": {"avatar": "confirmed", "banner": "confirmed"}}

print("2) ما لم يعتمده المالك لا يُطبَّق أبداً")
n = len(CALLS)
gone_url, _ = fake_png("expired")
(GEN / Path(gone_url).name).unlink()
e1 = raises(lambda: SVC.request_apply_identity(cfg, 1, identity={"avatar_url": gone_url}), IS.IdentityError)
check("صورة معاينة حذفها المولّد (بعد ساعة) ← رفض «أعد توليد» لا تطبيق صورة أخرى",
      bool(e1) and "أعد توليد" in str(e1) and len(CALLS) == n, e1)
bad_urls = ("https://evil.example/a.png", "http://127.0.0.1:5789/api/stats",
            f"{identity_bridge.IDENTITY_BASE_URL}/static/generated/../../app.py",
            "file:///C:/Windows/win.ini", "/static/uploads/aeos-img-abc.png",
            "http://127.0.0.1:5990.evil.example/static/generated/" + "a" * 32 + ".png")
rejected = [u for u in bad_urls
            if raises(lambda u=u: SVC.request_apply_identity(cfg, 1, identity={"avatar_url": u}), IS.IdentityError)]
check("رابط غير صور المعاينة (خارجي/داخلي/مسار ملتفّ/ملف) ← مرفوض بلا أي جلب",
      len(rejected) == len(bad_urls) and len(CALLS) == n, set(bad_urls) - set(rejected))
bogus = hashlib.sha256(b"bogus").hexdigest()[:32]
(GEN / f"{bogus}.png").write_bytes(b"<html>not an image</html>")
e2 = raises(lambda: SVC.request_apply_identity(
    cfg, 1, identity={"avatar_url": f"/static/generated/{bogus}.png"}), IS.IdentityError)
check("ملف ليس صورة ← مرفوض", bool(e2) and "تالف" in str(e2) and len(CALLS) == n, e2)
check("هوية بلا أي حقل ← مرفوضة", bool(raises(lambda: SVC.request_apply_identity(
    cfg, 1, identity={"name": " "}), IS.IdentityError)))

from fastapi import HTTPException  # noqa: E402

from app.aeos.api import routes as R  # noqa: E402

R._employee_or_404 = lambda eid, user: cfg
err = raises(lambda: R.identity_apply("emp1", R.IdentityApplyIn(
    identity={"avatar_url": "https://evil.example/x.png"}), current_user=NS(id=1)), HTTPException)
try:
    R.identity_apply("emp1", R.IdentityApplyIn(identity={"avatar_url": gone_url}), current_user=NS(id=1))
    status = None
except HTTPException as exc:
    status, detail = exc.status_code, exc.detail
check("واجهة «تطبيق على X» ← 400 برسالة عربية (فتبقى المعاينة في البطاقة)",
      bool(err) and status == 400 and "أعد توليد" in detail)

print("3) الاعتماد يصمد أمام إعادة تشغيل الخادم")
cfg2 = emp(2)
REPO = {cfg2.id: cfg2}
B.get_platform = lambda: NS(repository=NS(get=lambda i: REPO.get(i)))
SVC._start = lambda cfg, record: None            # العملية ماتت قبل أن يُنفَّذ
a3_url, a3_bytes = fake_png("avatar-3")
b3_url, b3_bytes = fake_png("banner-3")
SVC.request_apply_identity(cfg2, 1, identity={"name": "ن", "avatar_url": a3_url, "banner_url": b3_url})
check("الاعتماد محفوظ على القرص قبل التنفيذ", IS._read("emp2", "pending.json") is not None)
for f in GEN.glob("*"):
    f.unlink()                                   # ومرّت ساعة: المولّد حذف كل صوره
SVC._start = sync_start
n_push = len(PUSHED)
resumed = SVC.resume_pending()
label, sent = CALLS[-1]
check("عند الإقلاع يُستأنف بالبايتات المثبّتة نفسها (لا تعتمد على ملفات المولّد)",
      resumed == 1 and sent.get("avatar_bytes") == a3_bytes and sent.get("banner_bytes") == b3_bytes)
check("ويُبلَّغ المالك أن التطبيق استُؤنف ثم بالنتيجة",
      any("أستأنف" in m for _, m in PUSHED[n_push:]) and "طبّقت الهوية التي اعتمدتها" in PUSHED[-1][1])
check("ولا يُستأنف مرة ثانية", SVC.resume_pending() == 0)

SVC._start = lambda cfg, record: None
a4_url, _ = fake_png("avatar-4")
rec = IS.create_pending(cfg2, 1, {"avatar_url": a4_url})
rec["attempts"] = IS.MAX_ATTEMPTS
IS._write("emp2", "pending.json", rec)
SVC._start = sync_start
n = len(CALLS)
check("قُطع 3 مرات ← لا محاولة رابعة، ويُطلب إعادة الاعتماد",
      SVC.resume_pending() == 0 and len(CALLS) == n and "أعد توليد المعاينة واعتمادها" in PUSHED[-1][1]
      and IS._read("emp2", "pending.json") is None)
SVC._start = lambda cfg, record: None
a5_url, _ = fake_png("avatar-5")
rec = IS.create_pending(cfg2, 1, {"avatar_url": a5_url})
rec["approved_at"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
IS._write("emp2", "pending.json", rec)
cfg3 = emp(3)
a6_url, _ = fake_png("avatar-6")
IS.create_pending(cfg3, 1, {"avatar_url": a6_url})   # موظف حُذف لاحقاً (ليس في المستودع)
SVC._start = sync_start
n = len(CALLS)
check("اعتماد عمره يومان أو لموظف محذوف ← لا يُطبَّق بعد الإقلاع",
      SVC.resume_pending() == 0 and len(CALLS) == n and IS._read("emp3", "pending.json") is None)

SVC._start = lambda cfg, record: None
a7_url, _ = fake_png("avatar-7")
a8_url, a8_bytes = fake_png("avatar-8")
old = IS.create_pending(cfg2, 1, {"avatar_url": a7_url})
new = IS.create_pending(cfg2, 1, {"avatar_url": a8_url})
n = len(CALLS)
SVC._run(cfg2, old)
check("اعتماد قديم حلّ محله أحدث ← لا يُطبَّق", len(CALLS) == n)
SVC._run(cfg2, new)
check("والأحدث يُطبَّق ببايتاته", CALLS[-1][1].get("avatar_bytes") == a8_bytes)
SVC._start = sync_start

import app.aeos.mcp.employee_mcp as EMCP  # noqa: E402

EMCP.purge = lambda *a, **k: None
IS.create_pending(cfg2, 1, {"avatar_url": fake_png("avatar-9")[0]})
fake_self = NS(_repo=NS(get=lambda i: cfg2, save=lambda c: c, delete=lambda i: True),
               _graph_memory=NS(clear=lambda i: None), container=NS(resolve=lambda k: None))
B.AEOSPlatform.delete_employee(fake_self, "emp2")
check("حذف الموظف يحذف هويته المعلّقة (لا يستأنفها الإقلاع التالي)",
      not (IS._ROOT / "emp2" / "identity").exists())

print("4) الشات: «طبّق الهوية» يطبّق ما عُرض لا هوية جديدة")
cfg4 = emp(4, user=7)
GEN_CALLS = []


def fake_generate(cfg, entities=None, with_images=True):
    GEN_CALLS.append(cfg.id)
    u, _ = fake_png(f"gen-{len(GEN_CALLS)}")
    return {"name": "مولّد", "bio": "جديد", "avatar_url": u, "banner_url": ""}


SVC.generate_preview = fake_generate
from app.agents.main_agent_simple import MainAgent  # noqa: E402

txt = MainAgent._employee_identity_preview(cfg4, {}, 7, True)
shown = IS.recent_preview(cfg4.id, 7)
check("معاينة الشات تُحفظ وتعرض الصورة وتدلّ على «طبّق الهوية»",
      shown is not None and shown["identity"]["avatar_url"] in txt and "طبّق الهوية على الموظف" in txt, txt)
shown_bytes = (GEN / Path(shown["identity"]["avatar_url"]).name).read_bytes()
RAN_NEW = []
REAL_RUN_NEW = SVC._run_new
SVC._run_new = lambda cfg, user_id, entities: RAN_NEW.append((cfg.id, user_id))
gen_before = len(GEN_CALLS)
REPO4 = NS(get_by_account=lambda p, a: cfg4 if a == "acc4" else None, list_all=lambda: [cfg4],
           get=lambda i: cfg4 if i == cfg4.id else None)
B.get_platform = lambda: NS(repository=REPO4)
from app.aeos.services import employee_commands as EC  # noqa: E402

out = EC.handle("employee_apply_identity", {}, "طبّق الهوية على الموظف", None, 7)
check("«طبّق الهوية على الموظف» ← تُطبَّق الصورة المعروضة نفسها بلا توليد جديد",
      CALLS[-1][1].get("avatar_bytes") == shown_bytes and len(GEN_CALLS) == gen_before and not RAN_NEW, out)
check("والمعاينة تُستهلك (لا تُطبَّق مرة أخرى بالخطأ)", IS.recent_preview(cfg4.id, 7) is None)
IS.remember_preview(cfg4.id, 7, shown["identity"])
SVC.apply_from_chat(cfg4, 8, {})
check("معاينة مالك آخر لا تُستخدم ← توليد صريح جديد", wait_for(lambda: RAN_NEW == [(cfg4.id, 8)]))
old_preview = IS._read(cfg4.id, "preview.json")
old_preview["at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
IS._write(cfg4.id, "preview.json", old_preview)
SVC.apply_from_chat(cfg4, 7, {})
check("معاينة أقدم من صلاحية صورها ← لا تُطبَّق", wait_for(lambda: len(RAN_NEW) == 2))
SVC._run_new = REAL_RUN_NEW
n = len(CALLS)
SVC._run_new(cfg4, 7, {})
check("طلب «هوية جديدة» بلا معاينة ← يُثبَّت ما وُلّد ويُرفع كملف",
      len(CALLS) == n + 1 and CALLS[-1][1].get("avatar_bytes", b"").startswith(b"\x89PNG"))

print("5) الإنشاء التلقائي لا يرفع صورة لم يعتمدها المالك")
cfg5 = emp(5)
res = ONCREATE.apply_persona_identity(cfg5, llm=None, repo=None)
label, sent = CALLS[-1]
check("اسم + نبذة البرسونا فقط — بلا أي صورة", res["ok"] and set(sent) <= {"name", "bio"}
      and sent.get("name") == "اسم 5", sent)
check("لا مولّد صور في مسار الإنشاء", "mcp" not in inspect.signature(ONCREATE.apply_persona_identity_async).parameters
      and not hasattr(ONCREATE, "_generate_avatar"))
SVC._start = lambda cfg, record: None
IS.create_pending(cfg5, 1, {"avatar_url": fake_png("avatar-10")[0]})
SVC._start = sync_start
n = len(CALLS)
res = ONCREATE.apply_persona_identity(cfg5)
check("للمالك هوية معتمدة ← مسار الإنشاء لا يكتب فوقها", res.get("skipped") and len(CALLS) == n)
cfg6 = emp(6)
n_push = len(PUSHED)
ONCREATE.apply_persona_identity_async(cfg6, llm=None, repo=None)
for t in threading.enumerate():
    if t.name == "identity-acc6":
        t.join(5)
check("رسالة الإنشاء: الصورة والهيدر لا يتغيّران دون موافقة المالك",
      len(PUSHED) > n_push and "دون موافقتك" in PUSHED[-1][1] and "صورة شخصية جديدة" not in PUSHED[-1][1])
for path in (Path(R.__file__), Path(B.__file__)):
    src = path.read_text(encoding="utf-8")
    calls = re.findall(r"apply_persona_identity_async\(([^)]*)\)", src)
    check(f"{path.name}: استدعاء مسار الإنشاء بلا mcp", calls and all("mcp" not in c for c in calls), calls)
main_src = (Path(R.__file__).parents[3] / "app" / "main.py").read_text(encoding="utf-8")
check("main.py يستأنف الاعتمادات المقطوعة عند الإقلاع", "identity_service.resume_pending()" in main_src)

print("6) x_bridge يرفع الملفات بايتات (multipart)")
captured = {}
real_api_call = XB._api_call
XB._api_call = lambda method, endpoint, **kw: captured.update(method=method, endpoint=endpoint, **kw) or {"success": True}
p1, p2 = TMP / "x1.png", TMP / "x2.jpg"
p1.write_bytes(b"\x89PNG\r\n\x1a\nAAAA")
p2.write_bytes(b"\xff\xd8\xffBBBB")
REAL_UPDATE("lbl", name="ن", avatar_path=str(p1), banner_path=str(p2))
files = captured.get("files") or {}
check("avatar_file/banner_file بالبايتات نفسها وأسماء آمنة",
      files.get("avatar_file") == ("avatar.png", p1.read_bytes(), "image/png")
      and files.get("banner_file", ())[:2] == ("banner.jpg", p2.read_bytes()), files)
check("على /api/profile/update بمهلة تتسع لانتظار رفع الصور",
      captured.get("endpoint") == "/api/profile/update" and captured.get("timeout") == XB._PROFILE_TIMEOUT > XB.API_TIMEOUT)
REAL_UPDATE("lbl", name="ن")
check("بلا ملفات ← الطلب القديم كما هو (بلا multipart)", captured.get("files") is None
      and captured["data"] == {"cookie_label": "lbl", "headless": "1", "name": "ن"})
XB._api_call = real_api_call

print("7) x_profile: القصّ والحفظ والتأكيد (صفحة مزيّفة)")


class Clock:
    t = 0.0

    def monotonic(self):
        return self.t


CLOCK = Clock()
XP.time = CLOCK


class El:
    def __init__(self, label, testid=None):
        self.label, self.testid = label, testid

    def is_visible(self):
        return True

    def get_attribute(self, name):
        return self.testid if name == "data-testid" else None

    def click(self, timeout=None):
        PAGE.clicked.append(self.testid or self.label)


class Missing:
    def is_visible(self):
        return False


class Loc:
    def __init__(self, els):
        self.els = els

    def count(self):
        return len(self.els)

    def nth(self, i):
        return self.els[i]

    @property
    def first(self):
        return self.els[0] if self.els else Missing()


class Page:
    def __init__(self, dom, on_wait=None):
        self.dom, self.on_wait, self.clicked = dom, on_wait, []

    def locator(self, sel):
        m = re.search(r"data-testid='([^']+)'", sel)
        return Loc([e for e in self.dom(CLOCK.t) if m and e.testid == m.group(1)])

    def get_by_role(self, role, name):
        return Loc([e for e in self.dom(CLOCK.t) if name.search(e.label)])

    def get_by_test_id(self, tid):
        return Loc([e for e in self.dom(CLOCK.t) if e.testid == tid])

    def wait_for_timeout(self, ms):
        CLOCK.t += ms / 1000
        if self.on_wait:
            self.on_wait(CLOCK.t)


save_btn = El("حفظ", "Profile_Save_Button")
apply_btn = El("تطبيق", "applyButton")
CLOCK.t = 0.0
PAGE = Page(lambda t: [save_btn] + ([apply_btn] if t >= 1.0 else []))
ok = XP._handle_crop_if_any(PAGE)
check("نافذة قصّ متأخرة ← تُنتظر ويُضغط «تطبيق» لا «حفظ» البروفايل", ok and PAGE.clicked == ["applyButton"],
      PAGE.clicked)
CLOCK.t = 0.0
PAGE = Page(lambda t: [save_btn])
check("بلا نافذة قصّ ← لا يُضغط زر حفظ البروفايل أبداً", XP._handle_crop_if_any(PAGE, wait_s=2) is False
      and PAGE.clicked == [])
crop_save = El("حفظ")
PAGE = Page(lambda t: [save_btn, crop_save])
check("زر قصّ اسمه «حفظ» (بلا testid البروفايل) ← يُضغط هو", XP._handle_crop_if_any(PAGE)
      and PAGE.clicked == ["حفظ"])

seen = {}
CLOCK.t = 0.0
PAGE = Page(lambda t: [save_btn],
            on_wait=lambda t: seen.update(avatar=[200], banner=[200]) if t >= 6 else None)
XP._wait_saved(PAGE, seen, ["avatar", "banner"])
check("يُنتظر ردّ X على حفظ الصور قبل أي تنقّل (لا 2.5ث ثابتة)", 6 <= CLOCK.t < 7, CLOCK.t)
seen, CLOCK.t = {}, 0.0
PAGE = Page(lambda t: [save_btn] if t < 5 else [])
XP._wait_saved(PAGE, seen, ["avatar"])
check("النموذج أُغلق بلا ردّ ← مهلة سماح محدودة لا 90ث", 25 <= CLOCK.t <= 26, CLOCK.t)
seen, CLOCK.t = {}, 0.0
PAGE = Page(lambda t: [save_btn])
XP._wait_saved(PAGE, seen, ["avatar"])
check("لا ردّ ولا إغلاق ← سقف ثابت (90ث)", 92 <= CLOCK.t <= 93.5, CLOCK.t)
seen, CLOCK.t = {}, 0.0
XP._wait_saved(Page(lambda t: [save_btn]), seen, [])
check("بلا صور ← انتظار الحفظ المعتاد فقط", CLOCK.t == 2.5, CLOCK.t)

check("ردّ 200 ← confirmed، ولا ردّ ← unconfirmed (لا نزعم نجاحاً)",
      XP._media_result({"avatar": [200]}, ["avatar", "banner"]) == {"avatar": "confirmed", "banner": "unconfirmed"})
e3 = raises(lambda: XP._media_result({"banner": [413]}, ["banner"]), RuntimeError)
check("ردّ خطأ من X ← استثناء بالسبب (صورة مرفوضة لا تُبلَّغ كأنها طُبّقت)", bool(e3) and "HTTP 413" in str(e3), e3)
src = inspect.getsource(XP.update_profile_on_x.__wrapped__)
i_click = src.index('Profile_Save_Button").click')
i_wait = src.index("_wait_saved(page, seen, wanted)")
i_verify = src.index("if name or bio:")
check("الترتيب: حفظ ← انتظار اكتمال الحفظ ← التحقق بالتنقّل، ويُعاد التأكيد",
      i_click < i_wait < i_verify and 'page.on("response"' in src and "return media" in src)

print("8) بطاقة الموظف تعكس هويته كما على X (الاسم/النبذة/الصورة/الهيدر)")
import app.aeos.mcp.servers.playwright_x_mcp as PW  # noqa: E402
from app.aeos.mcp.base import MCPResponse  # noqa: E402

h = PW.account_header({"name": " رأي وتحليل ", "bio": "نبذة",
                       "avatar_url": "https://pbs.twimg.com/profile_images/111/abc_normal.jpg",
                       "banner_url": "https://pbs.twimg.com/profile_banners/123/1700000000/600x200"})
check("روابط X بأعلى دقة يعرضها (400x400 للصورة و1500x500 للهيدر)",
      h["avatar_url"].endswith("abc_400x400.jpg") and h["banner_url"].endswith("/1700000000/1500x500")
      and h["name"] == "رأي وتحليل", h)
h2 = PW.account_header({"name": "س", "avatar_url": "https://evil.example/a.png",
                        "banner_url": "javascript:alert(1)"})
check("روابط غير صور X تُسقط (لا تُعرض في متصفح المالك)", h2["avatar_url"] == "" and h2["banner_url"] == "")
check("الصورة الافتراضية لحساب بلا صورة تبقى كما هي",
      PW.account_header({"avatar_url": "https://abs.twimg.com/sticky/default_profile_images/default_profile_normal.png"})
      ["avatar_url"].endswith("default_profile_400x400.png"))

FAKE_X = """<main>
  <a href="/ACC8/header_photo"><div><img src="https://pbs.twimg.com/profile_banners/123/1700000000/600x200"></div></a>
  <a href="/acc8/photo"><div data-testid="UserAvatar-Container-acc8">
    <img src="https://pbs.twimg.com/profile_images/111/abc_200x200.jpg"></div></a>
  <div data-testid="UserName"><div><span><span>صقر الجزيرة </span><img alt="🇸🇦" src="https://abs-0.twimg.com/emoji/v2/svg/1f1f8-1f1e6.svg"></span></div><div><span>@acc8</span></div></div>
  <div data-testid="UserDescription"><span>حساب يقدّم متابعة</span><br><span>وتحليلًا للأحداث </span><img alt="📊"></div>
  <article data-testid="tweet">
    <a href="/someone/photo"><img src="https://pbs.twimg.com/profile_images/999/other_normal.jpg"></a>
    <a href="/acc8/status/1/photo/1"><img src="https://pbs.twimg.com/media/xyz.jpg"></a>
  </article></main>"""
FAKE_X2 = """<div data-testid="UserName"><div><span>اسم بلا غلاف</span></div></div>
  <div data-testid="UserAvatar-Container-acc9"><img src="https://pbs.twimg.com/profile_images/7/z_x96.png"></div>"""
try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw_:
        try:
            browser = pw_.chromium.launch(channel="chrome", headless=True)
        except Exception:  # noqa: BLE001
            browser = pw_.chromium.launch(headless=True)
        ctx = browser.new_context()
        ctx.route("**/*", lambda route: route.abort())     # بلا أي طلب شبكي
        page = ctx.new_page()
        page.set_content(FAKE_X)
        got = PW._profile_header(page, "acc8", wait_ms=5_000)
        page.set_content(FAKE_X2)
        got2 = PW._profile_header(page, "acc9", wait_ms=5_000)
        browser.close()
    check("رأس الصفحة في متصفح حقيقي: الاسم والنبذة (بإيموجي X المرسومة صوراً) وصورة الحساب نفسه",
          got == {"name": "صقر الجزيرة 🇸🇦", "bio": "حساب يقدّم متابعة\nوتحليلًا للأحداث 📊",
                  "avatar_url": "https://pbs.twimg.com/profile_images/111/abc_400x400.jpg",
                  "banner_url": "https://pbs.twimg.com/profile_banners/123/1700000000/1500x500"}, got)
    check("بلا رابط /photo ولا غلاف ← الصورة من حاوية الحساب والهيدر فارغ",
          got2["avatar_url"].endswith("z_400x400.png") and got2["banner_url"] == "" and got2["name"] == "اسم بلا غلاف",
          got2)
except Exception as exc:  # noqa: BLE001
    check("تشغيل JS رأس الصفحة في متصفح (يلزم Chrome/Chromium)", False, repr(exc))

px = PW.PlaywrightXMCP()
cookie = TMP / "acc8.json"
cookie.write_text("{}", encoding="utf-8")
px._cookie_file = lambda label: cookie
HEADER = {"name": "رأي وتحليل", "bio": "ن", "avatar_url": "https://pbs.twimg.com/profile_images/1/a_400x400.jpg",
          "banner_url": ""}
px._read_my_posts = lambda path, handle, limit, headless, header_out=None: (header_out.update(HEADER), [])[1]
r1 = px.call(PW.MCPRequest(employee_id="e", tool="read_my_posts", params={"handle": "acc8"},
                           context={"cookie_label": "acc8"}))
px._read_account_profile = lambda path, handle, headless, lock_timeout=0.0: dict(HEADER, lock=lock_timeout)
r2 = px.call(PW.MCPRequest(employee_id="e", tool="read_account_profile", params={"lock_timeout": 25},
                           context={"cookie_label": "acc8"}))
check("قراءة الأداء تُرجع رأس الصفحة نفسها بلا تنقّل إضافي", r1.success and r1.data.get("profile") == HEADER)
check("أداة read_account_profile مسجّلة وتمرّر مهلة القفل التفاعلية",
      "read_account_profile" in px.tools and r2.success and r2.data["profile"].get("lock") == 25.0, r2.error)

cfg8 = emp(8)
check("قراءة بلا اسم ولا صورة لا تمحو لقطة صالحة",
      IS.save_x_profile(cfg8.id, "acc8", HEADER, "live") and IS.save_x_profile(cfg8.id, "acc8", {"bio": "x"}, "live") is None
      and IS.load_x_profile(cfg8.id)["name"] == "رأي وتحليل")
view = B.AEOSPlatform._agent_view(None, cfg8)
check("قائمة الموظفين (agents) تحمل هوية X لكل بطاقة", (view.get("x_profile") or {}).get("avatar_url") == HEADER["avatar_url"])

from app.aeos.capabilities.handlers import ReadOwnMetricsCapability  # noqa: E402
from app.aeos.capabilities.base import CapabilityRequest  # noqa: E402

cfg10 = emp(10)
ctx10 = NS(config=cfg10, scratch={}, mcp=NS(call=lambda req: MCPResponse.ok(
    posts=[], profile=dict(HEADER, name="من دورة الأداء"))))
res10 = ReadOwnMetricsCapability().execute(CapabilityRequest(name="read_own_metrics", params={}), ctx10)
snap10 = IS.load_x_profile(cfg10.id) or {}
check("دورة قراءة الأداء تحدّث البطاقة تلقائياً", res10.success and snap10.get("name") == "من دورة الأداء"
      and snap10.get("source") == "metrics", res10.error)

import app.aeos.services.tools_service as TS  # noqa: E402

REG = {"res": MCPResponse.ok(profile=HEADER)}
CALLS_REG = []
TS.registry = lambda: NS(call=lambda req: CALLS_REG.append(req) or REG["res"])
snap = REAL_REFRESH(cfg8, lock_timeout=25.0)
req = CALLS_REG[-1]
check("تحديث البطاقة من X: الأداة الصحيحة بكوكيز الموظف ومهلة القفل",
      req.tool == "read_account_profile" and req.params["lock_timeout"] == 25.0
      and req.context == {"server": "playwright_x", "cookie_label": "acc8"} and snap["source"] == "live")
REG["res"] = MCPResponse.fail("read_account_profile failed: المتصفح مشغول الآن بدورة موظف ذكي")
e4 = raises(lambda: REAL_REFRESH(cfg8), RuntimeError)
check("فشل القراءة ← رسالة عربية نظيفة", e4 and str(e4).startswith("المتصفح مشغول"), e4)
REG["res"] = MCPResponse.ok(profile={"bio": "بلا اسم"})
check("صفحة بلا اسم ولا صورة ← خطأ صريح لا لقطة فارغة", bool(raises(lambda: REAL_REFRESH(cfg8), RuntimeError)))

R._employee_or_404 = lambda eid, user: cfg8
got = R.x_profile("emp8", current_user=NS(id=1))
check("GET x-profile يعيد آخر لقطة بلا متصفح", got["x_profile"]["name"] == "رأي وتحليل")
SVC.refresh_x_profile = lambda cfg, lock_timeout=0.0: (_ for _ in ()).throw(RuntimeError("المتصفح مشغول الآن"))
err5 = None
try:
    R.x_profile_refresh("emp8", current_user=NS(id=1))
except HTTPException as exc:
    err5 = exc
check("POST x-profile/refresh: فشل القراءة ← 502 برسالة عربية", err5 is not None and err5.status_code == 502
      and "مشغول" in err5.detail)
SVC.refresh_x_profile = fake_refresh

cfg11 = emp(11)
IS.save_x_profile(cfg11.id, "acc11", {"name": "قبل", "avatar_url": "https://pbs.twimg.com/profile_images/1/old_400x400.jpg",
                                      "banner_url": "https://pbs.twimg.com/profile_banners/1/100/1500x500"}, "live")
NEXT["res"] = {"success": True, "media": {"avatar": "unconfirmed", "banner": "unconfirmed"}}
X_AFTER["snap"] = {"name": "بعد", "avatar_url": "https://pbs.twimg.com/profile_images/2/new_400x400.jpg",
                   "banner_url": "https://pbs.twimg.com/profile_banners/1/100/1500x500"}
a11, _ = fake_png("avatar-11")
b11, _ = fake_png("banner-11")
SVC.request_apply_identity(cfg11, 1, identity={"avatar_url": a11, "banner_url": b11})
msg = PUSHED[-1][1]
avatar_line = next((ln for ln in msg.splitlines() if "الصورة الشخصية" in ln), "")
banner_line = next((ln for ln in msg.splitlines() if "الهيدر" in ln and "**" in ln), "")
check("بعد التطبيق تُقرأ الهوية من X فتتحدّث البطاقة", REFRESHED[-1] == (cfg11.id, 0.0)
      and IS.load_x_profile(cfg11.id)["name"] == "بعد" and "بطاقة الموظف تعرض الآن" in msg)
check("صورة تغيّر رابطها في صفحة الحساب ← ✓ «ظهرت في صفحة حسابك»", "ظهرت في صفحة حسابك" in avatar_line, avatar_line)
check("هيدر ما زال بالرابط القديم ← تحذير صريح لا ✓", "ما زالت تعرض السابقة" in banner_line
      and "✓" not in banner_line, banner_line)
X_AFTER["snap"] = None
NEXT["res"] = {"success": True, "media": {"avatar": "confirmed", "banner": "confirmed"}}

shutil.rmtree(TMP, ignore_errors=True)
print()
if FAILED:
    print(f"❌ {len(FAILED)} فحص فشل: {FAILED}")
    sys.exit(1)
print("✅ كل فحوص هوية الموظف نجحت")
