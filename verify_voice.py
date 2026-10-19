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

print("8) العمق الزمني: لا تبتلع دفعةُ يوم واحد الشخصية")
from datetime import date, timedelta  # noqa: E402


def d(i):  # تاريخ قبل i يوماً
    return (date(2026, 10, 1) - timedelta(days=i)).isoformat() + "T10:00:00.000Z"


burst = [{"text": f"الله يحفظك اخوي {i}", "created_at": d(1)} for i in range(136)]
spread_items = [{"text": f"تعليق {i}", "created_at": d(7 * i + 2)} for i in range(30)]
sample = VP.stratify(burst + spread_items, 40, per_day=4)
check("دفعة 136 رداً في يوم ⇒ 4 فقط في العيّنة", sum(1 for x in sample if x["created_at"] == d(1)) == 4,
      sum(1 for x in sample if x["created_at"] == d(1)))
check("الحد الأدنى يكتمل بالتناوب (fill)", len(VP.stratify(burst + spread_items, 100, fill=True)) == 100)
cov = VP.coverage(burst + spread_items)
check("التغطية الزمنية تُقاس", cov["days"] == 31 and cov["weeks"] >= 25, cov)

# عبارة أسلوب عبر 8 أسابيع، وعبارة حدث في أسبوع واحد، ومجاملة متكدّسة في يوم تهانٍ
style_items = [{"text": f"والله العظيم الصورة اوضح من كذا {i}", "created_at": d(7 * i)} for i in range(8)]
era_items = [{"text": f"غارات عنيفة على قرد صعدة الليلة {i}", "created_at": d(i % 3)} for i in range(6)]
courtesy_burst = [{"text": "تسلم الله يحفظك", "created_at": d(40)} for _ in range(10)]
sp = VP.phrase_spread(style_items + era_items + courtesy_burst, topical=style_items + era_items)
check("عبارة تتكرر عبر الأسابيع ⇒ مميّزة", any(w in s for s in sp["signature"]
                                               for w in ("العظيم", "الصوره", "اوضح")), sp)
check("عبارة حدث متركّزة ⇒ مرحلة لا مميّزة", any("صعده" in e or "غارات" in e for e in sp["era"])
      and not any("صعده" in s for s in sp["signature"]), sp)
check("مجاملة متكدّسة في يوم ليست «موضوع مرحلة»", not any("يحفظك" in e for e in sp["era"]), sp["era"])

print("9) أنواع الردود وما يُردّ عليه")
for text, kind in (("الله يحفظك", "courtesy"), ("نعم باذن الله", "agree"), ("مدري", "reaction"),
                   ("أحسن الله عزاءكم وعظم الله اجركم ورحم الله والدكم وأسكنه فسيح جناته وألهمكم الصبر", "courtesy"),
                   ("متى بدأ هذا القرار؟", "question"), ("غير صحيح، القرار صدر قبل أسبوع", "disagree"),
                   ("الريال هو العملة الرسمية أما التومان فيستخدم في المعاملات اليومية بين الناس", "substantive")):
    check(f"«{text[:30]}» ⇒ {kind}", VP.reply_kind(text) == kind, VP.reply_kind(text))
for text, pk in (("انطفأت شمعة قلبي امي في ذمة الله إنا لله وإنا إليه راجعون", "social"),
                 ("ياحسين هات سنابك ياخي", "question"), ("يعطيك العافية يا استاذ", "praise"),
                 ("أنت كذاب ومرتزق", "attack"),
                 ("وزارة الداخلية تعلن ضبط شبكة تهريب على الحدود الجنوبية", "content")):
    check(f"الأصل «{text[:28]}» ⇒ {pk}", VP.parent_kind(text, ["حسين"]) == pk, VP.parent_kind(text, ["حسين"]))
kinds = VP.reply_kind_stats([{"text": "الله يحفظك"}] * 6 + [
    {"text": "القرار يحتاج آلية تنفيذ واضحة وجدولاً زمنياً معلناً قبل الحكم عليه"}] * 4)
check("الطول يتبع النوع (المجاملة قصيرة والتعليق أطول)",
      kinds["courtesy"]["len_median"] < 15 < kinds["substantive"]["len_median"], kinds)
an = VP.normalize_analysis({"lexicon": {"signature": ["الوعد صنعاء", "الله يحفظك", "غارات عنيفه"]}},
                           era=["الوعد صنعاء باذن", "غارات عنيفه"])
check("المعجم يُسقط عبارات المرحلة حتى لو اقترحها النموذج", an["lexicon"]["signature"] == ["الله يحفظك"],
      an["lexicon"])

print("10) السحب: الأصل الحقيقي لكل رد")
from system.mcps.playwright_x.server import pair_cells  # noqa: E402

T = "@target"
cells = [{"sep": True},
         {"handle": "@fan", "text": "مبدع يا استاذ", "url": "u1"},
         {"handle": "@target", "text": "الله يحفظك", "url": "u2", "reply_to": []},
         {"sep": True},
         {"handle": "@target", "text": "خبر عاجل", "url": "u3", "reply_to": []},
         {"handle": "@target", "text": "مدري", "url": "u4", "reply_to": ["@x", "@y"]},
         {"sep": True},
         {"handle": "@target", "text": "تغريدة", "url": "u5", "reply_to": []},
         {"handle": "@target", "text": "تكملة", "url": "u6", "reply_to": []}]
got = pair_cells(cells, T)
check("رد بعد بطاقة في المحادثة ⇒ زوج بسياقه", [p["url"] for p in got["pairs"]] == ["u2", "u6"]
      and got["pairs"][0]["in_reply_to"]["text"] == "مبدع يا استاذ", got)
check("وسم «ردًا على» ⇒ رد بلا سياق (لا يُنسب له أصلٌ خاطئ)",
      [p["url"] for p in got["loose"]] == ["u4"] and got["loose"][0]["replying_to"] == ["@x", "@y"], got["loose"])
check("بطاقة أولى في محادثتها ⇒ أصلية", [p["url"] for p in got["originals"]] == ["u3", "u5"], got["originals"])
check("تكملة ثريده تُعلَّم ذاتية", got["pairs"][1]["in_reply_to"]["self"] is True)
check("البحث (بلا محادثات) لا يصنع أزواجاً", pair_cells(cells, T, pairing=False)["pairs"] == [])

print("11) خطة الرد ومنع القوالب المكرّرة")
deep_replies = []
for i in range(60):
    deep_replies.append({"text": "الله يحفظك اخوي" if i % 3 else "بيض الله وجهك استاذ",
                         "created_at": d(i), "in_reply_to": {"handle": "@f", "text": "يعطيك العافية يا استاذ", "self": False}})
for i in range(60):
    deep_replies.append({"text": f"القرار {i} يحتاج آلية تنفيذ واضحة وجدولاً زمنياً معلناً قبل الحكم عليه بالنجاح",
                         "created_at": d(i), "in_reply_to": {"handle": "@news", "text": f"الحكومة تعلن قراراً جديداً رقم {i} بشأن الموانئ", "self": False}})
deep = VP.analyze({"handle": "@target", "display_name": "حسين", "tweets": TWEETS, "replies": deep_replies})
st2 = deep["style"]
check("≥100 رد تُحلَّل حين تتوفر", deep["sample"]["analyzed_replies"] >= 100, deep["sample"])
check("سياسة الرد: على الثناء مجاملة، وعلى الخبر تعليق بمضمون",
      max(st2["reply_policy"]["praise"]["kinds"], key=st2["reply_policy"]["praise"]["kinds"].get) == "courtesy"
      and max(st2["reply_policy"]["content"]["kinds"], key=st2["reply_policy"]["content"]["kinds"].get) == "substantive",
      st2["reply_policy"])
plan = VP.reply_plan(deep, "وزارة النقل تعلن خطة جديدة لتطوير الموانئ", seed="t1")
check("الخبر ⇒ تعليق بمضمون بطوله الحقيقي لا طول المجاملات",
      plan["kind"] == "substantive" and f"≈{st2['reply_kinds']['substantive']['len_median']}" in plan["length_line"], plan)
check("الخطة فيها أمثلة من النوع نفسه", "يحتاج آلية" in plan["block"] and "خبر أو رأي عام" in plan["block"], plan["block"])
plan_p = VP.reply_plan(deep, "يعطيك العافية يا استاذ على التغطية", seed="t2")
check("الثناء ⇒ مجاملة", plan_p["kind"] == "courtesy", plan_p["kind"])
check("صيغة المجاملة لا تُقترح لنوع «تعليق بمضمون»",
      "الله يحفظك" not in plan["block"].split("صيغ لا يستعملها")[0], plan["block"])
recent = ["الحسم في الميدان لا في البيان الله يحفظك", "الردع بالميدان لا بالبيان",
          "موقف مهم لكن الردع يقاس بالميدان لا بالبيان باذن الله", "الفرصة تقاس بالميدان لا بالشعار",
          "الأسلحة تسجل والأثر يحسم بالميدان باذن الله"]
banned = VP.overused_phrases(recent)
check("القالب المستهلك يُرصد ويُمنع", any("بالميدان" in b for b in banned), banned)
check("نسخة شبه حرفية من رد حديث ⇒ مرفوضة",
      "near_duplicate" in VP.reply_repetition("الأسلحة تُسجَّل والأثر يُحسم في الميدان باذن الله", recent, banned))
check("استعمال قالب مستهلك ⇒ مرفوض", "overused_phrase" in VP.reply_repetition(
    "الموقف واضح والعبرة بالميدان لا بالبيان", recent, banned))
check("صيغة مجاملة مشتركة ليست تكراراً (حالة قيست حيّاً)", VP.reply_repetition(
    "بيض الله وجهك، التوضيح مهم في هذا التوقيت", ["بيض الله وجهك، سؤال: أين رقم المصدر؟"], []) == [])
check("خبر من حساب إخباري ⇒ لا ألقاب شخصية في الخطة",
      "لا تخاطب صاحبها بلقب" in VP.reply_plan(deep, "عاجل.. متحدث التحالف: دعم إقليمي للحوثي", seed="n")["block"])
tic = ["التوثيق أدق من الضجيج هنا", "الأسماء مهمة لا الضجيج الإعلامي", "التوثيق أهم من الضجيج في الملف"]
check("لازمة بكلمة واحدة تُرصد (3 من 10 ردود)", "الضجيج" in VP.overused_phrases(tic), VP.overused_phrases(tic))
check("كلمة موجودة في التغريدة نفسها لا تُمنع", VP.banned_for(["الضجيج", "بالميدان لا"], "الضجيج الإعلامي حول الحرب")
      == ["بالميدان لا"])
check("خبر بسطر أول ينتهي بنقطتين ⇒ خبر (لا ألقاب)",
      "لا تخاطب صاحبها بلقب" in VP.reply_plan(deep, "الرئيس التركي لولي العهد هاتفيًا:\nنعلن وقوفنا", seed="n2")["block"])
check("رد جديد فعلاً ⇒ مقبول", VP.reply_repetition(
    "ميناء المخا عاد للعمل جزئياً والتحدي الآن في تأمين الإمداد للمدنيين", recent, banned) == [])
check("مجاملة قصيرة مكرّرة مقبولة إن لم تكن في آخر 3 ردود",
      VP.reply_repetition("الله يحفظك", ["الله يحفظك", "x1 y1 z1", "x2 y2 z2", "x3 y3 z3"], []) == []
      and VP.reply_repetition("الله يحفظك", ["x1 y1 z1", "الله يحفظك"], []) == ["near_duplicate"])
seen_sets = {tuple(p["reply"] for p in VP.pick_exemplars(deep, "substantive", "content", "الموانئ",
                                                         VP.random.Random(s))) for s in range(6)}
check("الأمثلة تتدوّر بين الردود (لا الأربعة نفسها دائماً)", len(seen_sets) > 1, len(seen_sets))
v1 = {**vp, "version": 1, "style": {k: v for k, v in st.items() if k != "reply_kinds"}}
check("بصمة الإصدار الأول ⇒ خطة قديمة بلا كسر", VP.reply_plan(v1, "خبر", seed="x")["kind"] == ""
      and "≈" in VP.reply_plan(v1, "خبر", seed="x")["length_line"])

print("12) الرد الفعلي: إعادة التوليد عند التكرار ورفض القالب")
from app.aeos.mcp.base import MCPResponse  # noqa: E402

H._record_interaction = lambda *a, **k: None
H._record_trend = lambda *a, **k: None


def run_reply(generations):
    cfg3 = EmployeeConfig(id="e3", user_id=1, platform="x", account_username="me",
                          persona=Persona(display_name="عماد", dialect="سعودية"),
                          business_rules={VP.STORE_KEY: deep}, runtime_state=RuntimeState())
    cfg3.runtime_state.flags["recent_reply_texts"] = list(recent)
    gens, prompts, posted = list(generations), [], []

    def call(req):
        if req.tool == "generate":
            prompts.append(req.params["prompt"])
            return MCPResponse.ok(text=gens.pop(0) if gens else "")
        posted.append(req.params.get("reply_text"))
        return MCPResponse.ok(tweet_url="https://x.com/me/status/1")

    ctx3 = SimpleNamespace(config=cfg3, scratch={}, mcp=SimpleNamespace(call=call),
                           persona_prompt=lambda: "", action_prompt=lambda *a: "")
    req = SimpleNamespace(params={"tweet_url": "https://x.com/news/status/9",
                                  "original_text": "الحكومة تعلن خطة جديدة لتطوير الموانئ"})
    return H.ReplyToUserCapability().execute(req, ctx3), prompts, posted, cfg3


res, prompts, posted, cfg3 = run_reply(["العبرة بالميدان لا بالبيان باذن الله",
                                        "الخطة تحتاج جدولاً معلناً وميزانية واضحة قبل أن نحكم عليها"])
check("قالب مكرر ⇒ إعادة توليد ثم نشر الرد الجديد", res.success and posted == [
    "الخطة تحتاج جدولاً معلناً وميزانية واضحة قبل أن نحكم عليها"] and len(prompts) == 2, (res, posted))
check("الموجّه فيه خطة الرد والقوالب الممنوعة وفصل قوالب المنشورات ومنع اختلاق الوقائع",
      "طريقة ردّك هنا" in prompts[0] and "ممنوعة هذه المرة" in prompts[0] and "خاصة بالمنشورات" in prompts[0]
      and "لا تخترع وقائع" in prompts[0])
check("نوع الرد يُحفظ لتنويع الردود القادمة", cfg3.runtime_state.flags["recent_reply_kinds"][-1] == "substantive",
      cfg3.runtime_state.flags.get("recent_reply_kinds"))
res, prompts, posted, _ = run_reply(["العبرة بالميدان لا بالبيان باذن الله", "الحسم بالميدان لا بالبيان"])
check("تكرار بعد إعادة التوليد ⇒ لا يُنشر", not res.success and "بوابة التكرار" in res.error and not posted, res)

print("13) إعادة الاستنساخ تحفظ توجيهات المالك")
cfg4 = EmployeeConfig(id="e4", user_id=1, platform="x", account_username="me",
                      persona=Persona(instructions="وصف قديم للهوية.\n- ركّز على ملف الحوثي\n- نوّع أسلوب الردود"))
PC.apply_to_employee(cfg4, {**persona, "instructions": "وصف جديد عميق."}, deep, SimpleNamespace(save=lambda c: None))
check("الوصف يُستبدل وتوجيهات المالك تبقى",
      cfg4.persona.instructions.startswith("وصف جديد عميق.") and "- ركّز على ملف الحوثي" in cfg4.persona.instructions
      and "- نوّع أسلوب الردود" in cfg4.persona.instructions and "وصف قديم" not in cfg4.persona.instructions,
      cfg4.persona.instructions)
check("فترات التاريخ تُحسب للبحث", PC.history_windows(date(2026, 10, 19))[0] == ["2026-08-05", "2026-08-25"],
      PC.history_windows(date(2026, 10, 19)))

job = PC.start_job(7, lambda progress: (progress("مرحلة"), {"ok": True})[1])
import time as _t  # noqa: E402

for _ in range(50):
    if (PC.get_job(job, 7) or {}).get("status") != "running":
        break
    _t.sleep(0.05)
check("المهمة الخلفية تنتهي بنتيجتها", PC.get_job(job, 7)["status"] == "done" and PC.get_job(job, 7)["result"] == {"ok": True})
check("مستخدم آخر لا يرى مهمة غيره", PC.get_job(job, 8) is None)
bad = PC.start_job(7, lambda progress: (_ for _ in ()).throw(RuntimeError("المتصفح مشغول")))
for _ in range(50):
    if (PC.get_job(bad, 7) or {}).get("status") != "running":
        break
    _t.sleep(0.05)
check("خطأ المهمة يصل للمالك بنصه", PC.get_job(bad, 7)["status"] == "error" and "المتصفح مشغول" in PC.get_job(bad, 7)["error"])

print()
if FAILED:
    print(f"❌ {len(FAILED)} فحص فشل: {FAILED}")
    sys.exit(1)
print("✅ كل فحوص بصمة الصوت نجحت")
