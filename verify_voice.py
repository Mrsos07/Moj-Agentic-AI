"""
تحقّق «بصمة الصوت» (الاستنساخ العميق) — بلا شبكة وبلا LLM وبلا X.

    .\\venv\\Scripts\\python.exe verify_voice.py

يغطي: القياسات الحتمية واللهجة، أزواج سياق الردود، اختيار الأمثلة، حارس النسخ
(المجاملات القصيرة أسلوب لا نسخ — حالة قيست حيّاً)، استبدال قواعد «البيت» في موجّه
الكتابة، دليل الردود في موجّه الرد، بوابة الطول، عدم نقل الاسم، والدمج عند الحفظ.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

os.environ.setdefault("PYTHONUTF8", "1")
os.environ["TOPIC_SEMANTIC"] = "0"   # لا تضمينات: الاختبار حتمي بلا شبكة
sys.stdout.reconfigure(encoding="utf-8")

from app.aeos.employee import voice_profile as VP  # noqa: E402
from app.aeos.employee.models import (  # noqa: E402
    EmployeeConfig, Persona, RuntimeState,
)

FAILED = []


def check(name, cond, detail=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


# عيّنة بأسلوب مميّز: خليجي، بلا إيموجي، مطلع «مستجدات»، ردود مجاملة قصيرة
TWEETS = [
    {"text": f"مستجدات اليمن.. غارات جوية على مواقع الحوثي في صنعاء الليلة رقم {i} والوعد صنعاء",
     "likes": 100 * i, "views": 5000 * i, "created_at": f"2026-10-0{1 + i % 3}T2{i % 3}:00:00.000Z"}
    for i in range(1, 9)
] + [
    {"text": "وش السالفة؟ ليش كذا الحين يصير هالشي؟ ترى الموضوع أكبر من كذا بكثير", "likes": 5},
    {"text": "مستجدات: القوات تتقدم في الجبهة الشرقية والحوثي يتراجع بشكل واضح", "likes": 5000},
]
REPLIES = [
    {"text": "تسلم الله يحفظك اخوي", "in_reply_to": {"handle": "@a", "text": "مبدع يا استاذ", "self": False}},
    {"text": "بيض الله وجهك استاذ فواز", "in_reply_to": {"handle": "@b", "text": "شكراً على التغطية", "self": False}},
    {"text": "الله يحفظك اخوي", "in_reply_to": {"handle": "@c", "text": "يعطيك العافية", "self": False}},
    {"text": "تكملة الخبر في التغريدة التالية", "in_reply_to": {"handle": "@me", "text": "خبر", "self": True}},
]
profile = {"handle": "@target", "bio": "صحفي", "tweets": TWEETS, "replies": REPLIES}

print("1) القياسات الحتمية")
vp = VP.analyze(profile, llm=None)
st = vp["style"]
check("طول التغريدات أطول بوضوح من الردود", st["len_median"] > st["reply_len_median"] * 2, st)
check("بلا إيموجي مقاسة", st["emoji_rate"] == 0)
check("اللهجة الخليجية مكتشفة", st["dialect_guess"] == "سعودية/خليجية", st["dialect_scores"])
check("المطلع المتكرر «مستجدات» مكتشف", "مستجدات" in st["openers"], st["openers"])
check("عبارة مميّزة متكررة عبر التغريدات", any("الحوثي" in p or "الوعد" in p for p in st["signature_phrases"]),
      st["signature_phrases"])
check("ساعات النشاط من التوقيت", bool(st["active_hours_utc"]))
check("عدّ الأزواج والثريد", vp["sample"]["pairs"] == 4 and vp["sample"]["threads"] == 1, vp["sample"])

print("2) الأمثلة المرجعية")
ex = vp["exemplars"]
check("الأمثلة بلا تكرار موضوعي (8 تغريدات متشابهة ⇒ أقل)", len(ex["posts"]) < 8, len(ex["posts"]))
check("الأعلى تفاعلاً أولاً", "يتراجع" in ex["posts"][0], ex["posts"][0][:40])
check("أزواج الردود تستثني تكملة الثريد الذاتية", all("تكملة" not in p["reply"] for p in ex["replies"]))

print("3) حارس النسخ")
check("مجاملة قصيرة مطابقة ليست نسخاً (حالة مقيسة حيّاً)",
      not VP.copies_exemplar("تسلم الله يحفظك اخوي الغالي، بيض الله وجهك", vp))
check("نسخ منشور حقيقي يُكتشف", VP.copies_exemplar(ex["posts"][0], vp))
check("نص جديد بالروح نفسها ليس نسخاً",
      not VP.copies_exemplar("الاقتصاد السعودي يسجل نمواً في الربع الثالث بقيادة القطاع غير النفطي والاستثمار", vp))

print("4) استبدال قواعد «البيت» في موجّه الكتابة")
from app.aeos.capabilities import handlers as H  # noqa: E402

cfg = EmployeeConfig(id="e", user_id=1, platform="x", account_username="me",
                     persona=Persona(dialect="سعودية"), business_rules={VP.STORE_KEY: vp},
                     runtime_state=RuntimeState())
ctx = SimpleNamespace(config=cfg, scratch={}, mcp=None)
prompt = H._content_prompt("اكتب عن الاقتصاد", ctx)
check("قاعدة الطول من القياس لا «أقل من 260» الثابتة",
      f"بين {max(st['len_p25'], 15)}" in prompt and "أقل من 260 حرفاً" not in prompt)
check("لا نبرة متناوبة مع الهوية", "تتغير في كل مرة" not in prompt)
check("بلا إيموجي كعادة الحساب", "بلا إيموجي" in prompt)
check("أمثلة المنشورات + تحذير النسخ في الموجّه", "ممنوع نسخ جملها" in prompt and ex["posts"][0][:30] in prompt)
plain = H._content_prompt("اكتب عن الاقتصاد",
                          SimpleNamespace(config=EmployeeConfig(id="p", user_id=1, platform="x",
                                                                account_username="p", runtime_state=RuntimeState()),
                                          scratch={}, mcp=None))
check("بلا هوية: القواعد القديمة كما هي", "أقل من 260 حرفاً" in plain and "تتغير في كل مرة" in plain)

print("5) دليل الردود وبوابة الطول")
vp_llm = {**vp, "analysis": VP.normalize_analysis({
    "reply_playbook": {"praise": "يرد بالدعاء: تسلم الله يحفظك", "attack": "يتجاهل", "bogus": "x"},
    "pillars": [{"name": "اليمن", "share": "0.6", "angle": "ميداني"}, {"share": 1}],
    "rhetorical_devices": "ليست قائمة", "persona": {"tone": "witty", "dos": ["a"] * 30},
})}
block = VP.voice_block(vp_llm, "reply")
check("دليل الردود في موجّه الرد", "مع الشاكر/المحيّي: يرد بالدعاء" in block)
check("مفاتيح غير معروفة تُسقط والقوائم تُسقف",
      "bogus" not in str(vp_llm["analysis"]["reply_playbook"])
      and len(vp_llm["analysis"]["persona"]["dos"]) == 10
      and vp_llm["analysis"]["rhetorical_devices"] == []
      and len(vp_llm["analysis"]["pillars"]) == 1 and vp_llm["analysis"]["pillars"][0]["share"] == 0.6)
check("طول الرد من القياس", f"≈{st['reply_len_median']}" in VP.reply_length_line(vp, False))
short = {**vp, "style": {**st, "len_p25": 30}}
check("صاحب التغريدات القصيرة لا تمنعه بوابة 70 حرفاً", VP.min_post_chars(short, 70) == 24)
check("بلا هوية: الحد الافتراضي", VP.min_post_chars({}, 70) == 70)

print("6) البرسونا والحفظ")
from app.aeos.services import persona_clone as PC  # noqa: E402

persona = PC.persona_from_voice(profile, vp)
check("اسم الحساب الأصلي ونبذته لا يُنقلان", persona["display_name"] == "" and persona["bio"] == "")
check("التعليمات تمنع ادعاء الهوية والنسخ", "لا تدّعِ" in persona["instructions"])
check("اللهجة من القياس عند غياب النموذج", persona["dialect"] == "سعودية/خليجية")
saved = []
cfg2 = EmployeeConfig(id="e2", user_id=1, platform="x", account_username="me",
                      persona=Persona(display_name="اسمي", bio="نبذتي"),
                      business_rules={"daily_post_target": 2})
PC.apply_to_employee(cfg2, persona, vp, SimpleNamespace(save=saved.append))
check("الحفظ يُبقي الاسم والنبذة الحاليين", cfg2.persona.display_name == "اسمي" and cfg2.persona.bio == "نبذتي")
check("الحفظ يدمج البصمة دون مسح القواعد", cfg2.business_rules["daily_post_target"] == 2
      and cfg2.business_rules[VP.STORE_KEY]["version"] == VP.VERSION and saved)

print("7) اختبار المطابقة")
good = VP.fidelity(vp, ["مستجدات: الحوثي يتراجع في الجبهة والقوات تتقدم بثبات ترى الموضوع كبير الحين"],
                   ["الله يحفظك اخوي"])
bad = VP.fidelity(vp, ["🔥🔥 Amazing news today!! 🚀"], [])
check("عيّنة بأسلوبه تأخذ درجة عالية", good["score"] >= 80, good)
check("عيّنة بأسلوب مختلف تأخذ درجة منخفضة", bad["score"] < 50, bad)

print()
if FAILED:
    print(f"❌ {len(FAILED)} فحص فشل: {FAILED}")
    sys.exit(1)
print("✅ كل فحوص بصمة الصوت نجحت")
