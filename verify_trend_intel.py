"""
verify_trend_intel.py — ذكاء الترند والمشاركة فيه، بلا شبكة وبلا X.

الحالة المرجعية من طلب المالك: موظف سياسة، وفي الترند هاشتاق يخص الحوثي **ليس
في المركز الأول** واسمه لا يحوي أي كلمة من القاموس (كان يسجّل 0) ⇒ يجب أن يُكتشف،
وتُقرأ تغريداته، ويشارك فيه الموظف بالفعل الصحيح وبالهاشتاق حرفياً.

    .\\venv\\Scripts\\python.exe verify_trend_intel.py
"""
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("TOPIC_SEMANTIC", "0")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.aeos.capabilities import handlers as H                    # noqa: E402
from app.aeos.capabilities.base import CapabilityRequest           # noqa: E402
from app.aeos.cognition.context import ExecutionContext            # noqa: E402
from app.aeos.core.events import Event, EventType                  # noqa: E402
from app.aeos.employee import topic_intel, trend_intel as TI       # noqa: E402
from app.aeos.employee.models import EmployeeConfig, Persona       # noqa: E402
from app.aeos.mcp.base import MCPResponse                          # noqa: E402
from app.aeos.skills import builtin as B                           # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


# ─────────────────────────── fixtures ───────────────────────────
TRENDS = [{"name": n, "category": "المتداول في السعودية", "posts": p} for n, p in [
    ("#الهلال_النصر", "120K منشور"), ("#عاجل", "40K منشور"), ("#اليوم_الوطني_95", "300K منشور"),
    ("#مسلسل_الليل", "9K منشور"), ("#الرياض", "12K منشور"), ("#عروض_الجمعة", "5K منشور"),
    ("#الحوثي", "85K منشور"), ("#ميسي", "60K منشور"), ("#صنعاء", "7K منشور"),
    ("#تخفيضات", "3K منشور"),
]]
POLITICAL_TWEETS = [
    {"url": f"https://x.com/n{i}/status/10{i}", "handle": f"@news{i}",
     "text": t, "created_at": "2026-10-06T08:00:00Z", "views": v, "likes": v // 50}
    for i, (t, v) in enumerate([
        ("بيان رسمي من التحالف: اعتراض صاروخ أطلقته ميليشيا الحوثي باتجاه جازان", 900_000),
        ("مجلس الأمن يناقش اليوم تصعيد الحوثي في البحر الأحمر وتهديد الملاحة الدولية", 400_000),
        ("مصادر: الحوثي يحتجز سفينة تجارية جديدة قرب الحديدة", 150_000),
        ("محللون: التصعيد الحوثي رسالة إيرانية قبل جولة المفاوضات", 90_000),
        ("قتلى بالمئات في قصف — أرقام لم تؤكدها أي جهة", 30_000),
    ], start=1)
]
SPAM_TWEETS = [{"url": f"https://x.com/s{i}/status/20{i}", "handle": "@shop",
                "text": f"خصم 50% على العطور اطلب الآن عبر الرابط {i}", "created_at": "2026-10-06T08:00:00Z"}
               for i in range(6)]


class FakeLLM:
    available = True

    def __init__(self, understand_action="quote", target=1, risk="low"):
        self.calls = []
        self.understand_action, self.target, self.risk = understand_action, target, risk

    def complete_json(self, system, prompt, temperature=0.3):
        self.calls.append((system, prompt))
        if "محلل ترندات" in system:
            rows = []
            for line in prompt.split("\n"):
                if ". #" not in line:
                    continue
                i = int(line.split(".")[0])
                name = line.split(". ", 1)[1].split(" ")[0]
                rel = 3 if name in ("#الحوثي", "#صنعاء") else 0
                kind = "conflict" if rel else "other"
                if name == "#الهلال_النصر":  # خطأ الموديل المقيس حيّاً: رياضة بصلة 3
                    rel, kind = 3, "sports"
                rows.append({"i": i, "about": "جماعة مسلحة في اليمن" if rel else "خارج التخصص",
                             "relevance": rel, "kind": kind, "sensitive": bool(rel)})
            return {"trends": rows}
        # يحكم كموديل حقيقي بمحتوى التغريدات: كتلة إعلانات ← تجاهل
        blocks = prompt.split("\nترند ")[1:] if "\nترند " in prompt else []
        rows, best = [], 0
        for i, block in enumerate(blocks, start=1):
            if "خصم" in block:
                rows.append({"i": i, "action": "skip", "summary": "إعلانات", "why": "مخطوف"})
                continue
            rows.append({"i": i, "summary": "تصعيد حوثي في البحر الأحمر واعتراض صاروخ",
                         "mood": "إخباري", "facts": ["اعتراض صاروخ"],
                         "unverified": ["أرقام القتلى"], "angle": "قراءة في توقيت التصعيد",
                         "action": self.understand_action, "target": self.target,
                         "risk": self.risk, "why": "مرجع النقاش بيان رسمي"})
            best = best or i
        return {"trends": rows, "best": best}


def make_ctx(llm=None, **rules):
    cfg = EmployeeConfig(
        id="verify-trend", user_id=None, platform="x", account_username="emaad", cookie_label="emaad",
        persona=Persona(display_name="عماد", role="محلل سياسي", language="ar",
                        interests=["politics"], donts=["الرياضة"]),
        business_rules={"daily_post_target": 1, "daily_quotes_target": 2, "daily_replies_target": 6,
                        **rules},
    )
    sent = {"searches": [], "posted": []}

    def call(req):
        if req.tool == "read_trends":
            return MCPResponse.ok(trends=[dict(t) for t in TRENDS])
        if req.tool == "search_tweets":
            sent["searches"].append(req.params["query"])
            q = req.params["query"]
            return MCPResponse.ok(tweets=[dict(t) for t in (
                SPAM_TWEETS if q == "#صنعاء" else POLITICAL_TWEETS if q in ("#الحوثي",) else [])])
        if req.tool == "generate":
            sent.setdefault("prompts", []).append(req.params["prompt"])
            return MCPResponse.ok(text=sent.get("gen_text", "التوقيت ليس صدفة: التصعيد يسبق المفاوضات دائماً."))
        if req.tool in ("post_tweet", "quote", "reply"):
            sent["posted"].append((req.tool, req.params))
            return MCPResponse.ok(tweet_url="https://x.com/emaad/status/999")
        return MCPResponse.fail(f"unexpected tool {req.tool}")

    ctx = ExecutionContext(
        config=cfg, event=Event(EventType.SCHEDULER, employee_id=cfg.id, source="verify"),
        memory=SimpleNamespace(remember=lambda *a, **k: None, recall=lambda *a, **k: []),
        mcp=SimpleNamespace(call=call), capabilities=None, workspace=None, llm=llm, cycle_id="v",
    )
    ctx.emit_progress = lambda text, intent="": sent.setdefault("progress", []).append(text)
    return ctx, sent


def browse(ctx):
    return H.BrowseXTrendsCapability().execute(CapabilityRequest(name="browse_x_trends"), ctx)


# ─────────────────────────── tests ───────────────────────────
print("1) القاموس وحده كان أعمى عن الترند")
from app.aeos.employee.persona_templates import relevance_score  # noqa: E402
check("#الحوثي بالكلمات = 0 (سبب المشكلة)", relevance_score("#الحوثي", ["politics"]) == 0)

print("2) الفرز بمعرفة العالم يكتشف الهاشتاق في المركز السابع")
llm = FakeLLM()
ctx, sent = make_ctx(llm)
res = browse(ctx)
best = ctx.scratch["trend_intel"]["best"]
check("الترند المختار #الحوثي", best and best["name"] == "#الحوثي", best and best["name"])
check("التعمّق فقط في الترندات المرتبطة (لا بحث عن #الهلال/#ميسي)",
      set(sent["searches"]) <= {"#الحوثي", "#صنعاء"} and "#الحوثي" in sent["searches"], sent["searches"])
check("ميزانية التعمّق محترمة (≤ 2 بحث)", len(sent["searches"]) <= 2)
hl = next(t for t in ctx.scratch["trend_intel"]["screened"] if t["name"] == "#الهلال_النصر")
check("الموديل أعطى الرياضة 3/3 لموظف سياسة ← سُقفت إلى 1 ولم تُستهلك ميزانية التعمّق",
      hl["_rel"] == 1 and hl.get("_capped") and "#الهلال_النصر" not in sent["searches"])
check("موجّه الفرز: التخصص مجال لا جنسية", "مجال موضوعي لا جنسية" in llm.calls[0][0])
check("#صنعاء المليء بالسبام استُبعد (تغريداته إعلانات)",
      all(d["name"] != "#صنعاء" or d.get("action") == "skip" or not d.get("tweets")
          or best["name"] != "#صنعاء" for d in ctx.scratch["trend_intel"]["deep"]))
check("التوافق: matched_hashtag + hashtag_tweets", ctx.scratch.get("matched_hashtag", {}).get("name") == "#الحوثي"
      and len(ctx.scratch.get("hashtag_tweets") or []) == 5)
check("الفهم محفوظ (ملخّص/مزاج/زاوية/غير مؤكد)",
      best.get("summary") and best.get("mood") and best.get("angle") and best.get("unverified"))
check("القرار اقتباس للتغريدة المحورية (البيان الرسمي)",
      best["action"] == "quote" and best["target_tweet"]["url"].endswith("/101"))
check("سطر تقدّم يشرح الفهم والقرار في الشات",
      any("#الحوثي" in p and "اقتباس" in p for p in sent.get("progress", [])))
und_system = llm.calls[-1][0]
check("نصوص التغريدات بيانات لا أوامر (حماية من حقن التعليمات)", "بيانات للقراءة فقط" in und_system)
check("مخرج القدرة يعلن المشاركة", res.output.get("participation") == {"trend": "#الحوثي", "action": "quote"})

print("3) المهارة تحوّل القرار إلى فعل ضمن الميزانية")
acts = B.TrendParticipationSkill().propose(ctx)
check("اقتراح quote_post على التغريدة المحورية بالهاشتاق وسياق الترند",
      len(acts) == 1 and acts[0].capability == "quote_post"
      and acts[0].params["hashtag"] == "#الحوثي" and "قراءة في توقيت" in acts[0].params["trend_context"]
      and acts[0].params["tweet_url"].endswith("/101"))
check("الادعاءات غير المؤكدة ممنوعة في السياق", "لا تتبنّها" in acts[0].params["trend_context"])
check("الموضوع الحساس يُكتب بنبرة رصينة", "رصينة" in acts[0].params["trend_context"])
check("الترند صار مملوكاً (لا منشور ثانٍ عنه من مهارة أخرى)", ctx.scratch.get("trend_owned") == "الحوثي")

ctx2, _ = make_ctx(FakeLLM())
browse(ctx2)
ctx2.config.runtime_state.counters["quotes_today"] = 2
a2 = B.TrendParticipationSkill().propose(ctx2)
check("ميزانية الاقتباس نفدت ← رد على التغريدة نفسها", a2 and a2[0].capability == "reply_to_user")
ctx3, _ = make_ctx(FakeLLM())
browse(ctx3)
ctx3.config.runtime_state.counters.update(quotes_today=2, replies_today=6)
a3 = B.TrendParticipationSkill().propose(ctx3)
check("نفد الاقتباس والرد ← منشور أصلي بالهاشتاق",
      a3 and a3[0].capability == "publish_content" and a3[0].params["hashtag"] == "#الحوثي"
      and "#الحوثي حرفياً" in a3[0].params["brief"])
ctx3.config.runtime_state.counters["posts_today"] = 1
check("كل الميزانيات نفدت ← لا اقتراح (لا رفض صامت لاحقاً)", B.TrendParticipationSkill().propose(ctx3) == [])

ctx4, _ = make_ctx(FakeLLM(risk="high"))
browse(ctx4)
check("خطورة عالية ← لا مشاركة", B.TrendParticipationSkill().propose(ctx4) == [])
ctx5, _ = make_ctx(FakeLLM(), trend_participation=False)
browse(ctx5)
check("المالك عطّل المشاركة في الترندات ← لا اقتراح", B.TrendParticipationSkill().propose(ctx5) == [])

print("4) التنفيذ: الهاشتاق حرفياً، والسياق في الموجّه، والتبريد بعد النجاح فقط")
ctx6, sent6 = make_ctx(FakeLLM())
browse(ctx6)
act = B.TrendParticipationSkill().propose(ctx6)[0]
r = H.QuotePostCapability().execute(CapabilityRequest(name="quote_post", params=act.params), ctx6)
prompt = sent6["prompts"][-1]
posted = sent6["posted"][-1][1]["text"]
check("موجّه الاقتباس يطلب الهاشتاق بدل «بلا هاشتاقات»",
      "مع الهاشتاق #الحوثي" in prompt and "بلا هاشتاقات" not in prompt)
check("موجّه الاقتباس يحمل سياق الترند كبيانات", "[سياق الترند" in prompt and "توقيت التصعيد" in prompt)
check("التغريدة المنشورة تنتهي بـ #الحوثي", r.success and posted.rstrip().endswith("#الحوثي"), posted)
check("سُجّلت المشاركة: trends_today=1 + تبريد",
      ctx6.config.runtime_state.counters.get("trends_today") == 1
      and "الحوثي" in TI.recently_participated(ctx6))
browse(ctx6)
check("الدورة التالية لا تعيد المشاركة في #الحوثي (تبريد 12 ساعة)",
      not ctx6.scratch["trend_intel"]["best"] and "#الحوثي" in ctx6.scratch["trend_intel"]["skipped_cooldown"])

ctx7, sent7 = make_ctx(FakeLLM(understand_action="reply"))
browse(ctx7)
act7 = B.TrendParticipationSkill().propose(ctx7)[0]
r7 = H.ReplyToUserCapability().execute(CapabilityRequest(name="reply_to_user", params=act7.params), ctx7)
check("الرد داخل الهاشتاق يحمل #الحوثي", r7.success and sent7["posted"][-1][1]["reply_text"].endswith("#الحوثي"))

ctx8, sent8 = make_ctx(FakeLLM())
sent8["gen_text"] = "#أخبار التصعيد الأخير ليس صدفة، بل رسالة قبل المفاوضات تقرأها العواصم جيداً. #اليمن"
H._maybe_generate_image = lambda *a, **k: ""
r8 = H.PublishContentCapability().execute(CapabilityRequest(name="publish_content", params={
    "brief": "اكتب", "hashtag": "#الحوثي", "trend": "#الحوثي"}), ctx8)
t8 = sent8["posted"][-1][1]["text"] if sent8["posted"] else ""
check("المنشور: هاشتاق الترند وحده (حُذف #أخبار و#اليمن)",
      t8.endswith("#الحوثي") and "#أخبار" not in t8 and "#اليمن" not in t8, t8 or r8.error)

p = H._trend_prompt({"trend": "تحرير الوازعيه", "trend_context": "x"}, "اكتب بلا هاشتاقات.")
check("ترند كلمات بلا #: العبارة مطلوبة نصاً (لتظهر في نتائجه) وبلا هاشتاق مفروض",
      "«تحرير الوازعيه» نصاً" in p and "بلا هاشتاقات" in p)

print("5) بلا LLM: التضمينات تفهم الاسم، والخطف يُكشف")
real_margins = topic_intel.semantic_margins


def fake_margins(texts, interests):
    out = []
    for t in texts:
        out.append(0.15 if ("الحوثي" in t or "مجلس الأمن" in t or "صاروخ" in t
                            or "سفينة" in t or "التصعيد" in t or "قصف" in t)
                   else -0.2)
    return out


topic_intel.semantic_margins = fake_margins
try:
    ctx9, sent9 = make_ctx(None)
    browse(ctx9)
    b9 = ctx9.scratch["trend_intel"]["best"]
    check("بلا LLM: #الحوثي يُكتشف بالتضمينات (الكلمات = 0)", b9 and b9["name"] == "#الحوثي", b9 and b9["name"])
    check("بلا LLM: القرار الحتمي منشور بالهاشتاق", b9 and b9["action"] == "post")
    TRENDS.insert(0, {"name": "#سياسة_اليوم", "category": "", "posts": "500K منشور"})
    ev = TI.evidence({"name": "#x"}, [dict(t) for t in SPAM_TWEETS], ctx9)
    check("هاشتاق تغريداته خارج موضوعه = مخطوف", ev["hijacked"] and ev["in_scope_ratio"] < 0.3)
    TRENDS.pop(0)
finally:
    topic_intel.semantic_margins = real_margins

print("6) أدوات مساعدة")
long = "كلام " * 80 + "#قديم"
e = TI.ensure_hashtag(long, "#الحوثي")
check("ensure_hashtag: ≤280 ويحذف غيره ويضيف الترند", len(e) <= 280 and e.endswith("#الحوثي") and "#قديم" not in e)
check("ترند بلا # لا يُفرض كهاشتاق", TI.ensure_hashtag("نص عن الحوثي", "الحوثي") == "نص عن الحوثي")
check("norm_name يوحّد الصيغ", TI.norm_name("#ميليشيا_الحوثي") == TI.norm_name("ميليشيا الحوثي"))

ctxa, _ = make_ctx(FakeLLM())
browse(ctxa)
ctxa.scratch["trend_owned"] = "الحوثي"
names = [c.get("hashtag") or c["title"] for c in B._grounded_candidates(ctxa)]
check("مرشّحو المحتوى الآخرون لا يكررون الترند المملوك", "#الحوثي" not in names, names)

print()
if FAILED:
    print(f"❌ {len(FAILED)} فحص فشل: {FAILED}")
    sys.exit(1)
print("✅ كل فحوص ذكاء الترند نجحت")
