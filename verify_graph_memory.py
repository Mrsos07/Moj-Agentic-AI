"""
verify_graph_memory.py — تحقّق عملي من فاعلية الذاكرة المعرفية الزمنية
(Zep/Graphiti + Kuzu) للموظف الذكي.

يشغّل سلسلة فحوص حقيقية (لا محاكاة): تهيئة المخزن، تغذية حلقتين مهيكلتين،
استرجاع بالبحث الهجين، الاحتياطي الزمني، عزل الموظفين، تجاهل التكرار،
عدّادات الرسم، ثم يشغّل طورَي ``EmotionPhase`` و ``GraphMemoryPhase`` على
سياق وهمي للتأكد من أن الحالة العاطفية تُستنتج فعلاً وتُحقن في persona_prompt.

    python verify_graph_memory.py            # قاعدة مؤقتة (لا تلمس بيانات المنصة)
    python verify_graph_memory.py --db data/graphiti_kuzu --inspect-only
    python verify_graph_memory.py --employee <id> --inspect-only

يحتاج ``OPENROUTER_API_KEY`` (أو ``OPENAI_API_KEY`` احتياطاً، يُقرأ من .env)
لأن الاستخراج والـ embeddings يمرّان بمزوّد الـ LLM.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
import time
from typing import List

logging.basicConfig(level=logging.WARNING,
                    format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("aeos.memory.graphiti").setLevel(logging.INFO)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.aeos.memory.graphiti_store import GraphitiStore  # noqa: E402

CYCLE_1 = {
    "الموظف": "مازن",
    "المنصة": "x",
    "الحدث_المُشغِّل": "scheduled_wake",
    "رقم_الدورة": 1,
    "الإجراءات": [
        {"الإجراء": "publish_content", "النتيجة": "نجح",
         "المحتوى": "خمس أدوات ذكاء اصطناعي تختصر تحضير الدروس للمعلم",
         "الموضوع": "الذكاء الاصطناعي في التعليم"},
        {"الإجراء": "reply_to_user", "النتيجة": "فشل",
         "سبب_الفشل": "انتهت صلاحية الكوكيز", "الهدف": "@ahmed_dev"},
    ],
    "إشارات_الجمهور": [
        {"من": "@sara_edu", "قال": "منشور رائع! نريد المزيد عن أدوات التصحيح الآلي"},
        {"من": "@critic99", "قال": "الطرح سطحي ولا يناسب المعلمين فعلياً"},
    ],
    "الحصيلة": {"إجراءات": 2, "ناجحة": 1, "فاشلة": 1},
    "الدرس_المستفاد": "المنشورات التعليمية القصيرة العملية تحصل على تفاعل أعلى من الطويلة النظرية",
    "الحالة_العاطفية": "متحفّز",
}

CYCLE_2 = {
    "الموظف": "مازن",
    "المنصة": "x",
    "الحدث_المُشغِّل": "mention",
    "رقم_الدورة": 2,
    "الإجراءات": [
        {"الإجراء": "reply_to_user", "النتيجة": "نجح", "الهدف": "@sara_edu",
         "المحتوى": "شكراً سارة، سأخصص منشوراً لأدوات التصحيح الآلي"},
        {"الإجراء": "retweet_post", "النتيجة": "نجح", "الهدف": "@edu_tech_ar"},
    ],
    "رفض_حارس_الشخصية": {
        "عدد": 1, "إجراءات": ["like_post عن مباراة كرة قدم"],
        "الأسباب": ["خارج مجال التخصص (التعليم والتقنية)"],
    },
    "الحصيلة": {"إجراءات": 2, "ناجحة": 2, "فاشلة": 0},
    "الدرس_المستفاد": "الرد المباشر على المتفاعلين يرفع الثقة ويجلب أسئلة جديدة",
    "الحالة_العاطفية": "متعاطف",
}

QUERIES = [
    "تفاعل الجمهور وآراؤه حول الذكاء الاصطناعي في التعليم",
    "إجراءات فشلت وأسباب الفشل والرفض",
    "الدروس المستفادة وما نجح في جذب التفاعل",
    "سلوك وتفاعل الحسابات @sara_edu @critic99",
]

_results: List[tuple] = []


def check(name: str, passed: bool, detail: str = "") -> bool:
    _results.append((name, passed, detail))
    print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    return passed


def inspect(store: GraphitiStore, employee_id: str) -> None:
    print("\n=== حالة الذاكرة المعرفية ===")
    st = store.stats(employee_id if employee_id else None)
    for k, v in st.items():
        print(f"  {k}: {v}")
    if not st.get("available"):
        return
    facts = store.recent_facts(employee_id, limit=15) if employee_id else []
    print(f"\n=== أحدث الحقائق ({len(facts)}) ===")
    for f in facts:
        print("  -", f)


def run_full(store: GraphitiStore, employee_id: str) -> None:
    other_id = f"{employee_id}-other"

    if not check("تهيئة المخزن (available)", store.available, store._db_path):
        print("\nالمخزن غير متاح — تحقق من OPENROUTER_API_KEY و graphiti-core.")
        return

    # 1) تغذية حلقتين مهيكلتين + انتظار الاستخراج
    for idx, episode in enumerate((CYCLE_1, CYCLE_2), start=1):
        t0 = time.time()
        fut = store.ingest(employee_id, episode, name=f"cycle-{idx}")
        ok, detail = False, "لم تُرسل"
        if fut is not None:
            try:
                fut.result(timeout=240)
                ok, detail = True, f"{time.time() - t0:.1f}s"
            except Exception as exc:  # noqa: BLE001
                detail = f"{type(exc).__name__}: {str(exc)[:120]}"
        check(f"تغذية الحلقة {idx} (JSON مهيكل)", ok, detail)

    # 2) تجاهل الحلقة المكرّرة
    check("تجاهل الحلقة المكرّرة",
          store.ingest(employee_id, CYCLE_2, name="cycle-2-dup") is None,
          f"ingest_duplicate={store.counters['ingest_duplicate']}")

    # 3) الرسم يحتوي كيانات وحقائق فعلاً
    graph = store.stats(employee_id).get("graph", {})
    check("الرسم يحتوي حلقات وكيانات وحقائق",
          graph.get("episodes", 0) >= 2 and graph.get("entities", 0) > 0
          and graph.get("facts", 0) > 0, str(graph))

    # 4) الاسترجاع بكل استعلام من استعلامات EmotionPhase الحقيقية
    per_query = {}
    for q in QUERIES:
        facts = store.search(employee_id, q, limit=10)
        per_query[q] = len(facts)
        print(f"      · {q[:50]}… -> {len(facts)} حقيقة")
        for f in facts[:3]:
            print(f"          - {f[:150]}")
    hit_ratio = sum(1 for n in per_query.values() if n) / len(QUERIES)
    check("البحث الهجين يسترجع لأغلب الاستعلامات", hit_ratio >= 0.75,
          f"{int(hit_ratio * 100)}% من الاستعلامات أعادت نتائج")

    # 5) الاستعلامات المدمجة (كما تستخدمها EmotionPhase)
    merged = store.search_facts(employee_id, QUERIES, limit=12)
    check("search_facts يدمج بلا تكرار", len(merged) >= 3 and len(merged) == len(set(merged)),
          f"{len(merged)} حقيقة فريدة")

    # 6) الاحتياطي الزمني يعمل على استعلام لا يطابق شيئاً
    nonsense = store.search(employee_id, "زرافة بنفسجية تقود غواصة في المريخ", limit=5)
    check("الاحتياطي الزمني يمنع ذاكرة فارغة", bool(nonsense),
          f"recent_fallbacks={store.counters['recent_fallbacks']}, {len(nonsense)} حقيقة")

    # 7) العزل بين الموظفين
    leaked = store.search_facts(other_id, QUERIES, limit=10)
    check("عزل الموظفين (group_id)", not leaked, f"{len(leaked)} حقيقة مسرّبة")

    # 8) الحذف يمسح حقائق الموظف فقط
    store.clear(employee_id)
    time.sleep(4)
    after = store.recent_facts(employee_id, limit=5)
    check("clear يمسح حقائق الموظف", not after, f"{len(after)} حقيقة متبقية")


def run_phases(store: GraphitiStore, employee_id: str) -> None:
    """تشغيل الطورين على سياق وهمي: هل تُستنتج الحالة وتُحقن في البرومبت؟"""
    from app.aeos.cognition.context import ExecutionContext
    from app.aeos.cognition.phases import EmotionPhase, GraphMemoryPhase
    from app.aeos.core.events import Event, EventType
    from app.aeos.core.llm import build_llm_provider
    from app.aeos.employee.models import EmployeeConfig, Persona

    cfg = EmployeeConfig(
        id=employee_id, user_id=None, platform="x", account_username="mazen_edu",
        persona=Persona(display_name="مازن", role="معلّم تقني", tone="ودّي",
                        language="ar", interests=["الذكاء الاصطناعي في التعليم"]),
    )
    event = Event(EventType.SCHEDULER, employee_id=employee_id, source="verify")
    ctx = ExecutionContext(
        config=cfg, event=event, memory=None, mcp=None, capabilities=None,
        workspace=None, llm=build_llm_provider(), cycle_id="verify-1",
    )
    ctx.scratch["observations"] = {
        "timeline_preview": [{"handle": "@sara_edu", "text": "أدوات التصحيح الآلي"}],
    }
    ctx.scratch["mentions"] = [{"handle": "@critic99", "text": "الطرح سطحي"}]

    # نُعيد تغذية حلقة (بعد الحذف في الفحص السابق) ليكون للطور ما يقرؤه
    fut = store.ingest(employee_id, CYCLE_1, name="cycle-phase-1")
    if fut is not None:
        try:
            fut.result(timeout=240)
        except Exception:  # noqa: BLE001
            pass

    emotion = EmotionPhase(store)
    emotion.run(ctx)
    facts = ctx.scratch.get("graph_facts") or []
    check("EmotionPhase يقرأ حقائق من الرسم", bool(facts), f"{len(facts)} حقيقة")
    state = ctx.scratch.get("emotion_state") or {}
    print("      الحالة المستنتجة:", state)
    check("EmotionPhase يستنتج حالة عاطفية/سلوكية", bool(state.get("mood")),
          str(state.get("mood", ""))[:80])
    check("الحالة تُحقن في persona_prompt",
          "حالتك العاطفية والسلوكية" in ctx.persona_prompt(),
          "")
    emotion.run(ctx)  # ثانية: يجب أن تُخدم من الكاش بلا نداء LLM
    check("كاش الحالة يمنع نداء LLM متكرراً",
          bool(emotion._cache.get(employee_id)), "")

    ctx.scratch["execution_results"] = [
        {"capability": "publish_content", "success": True,
         "output": {"text": "منشور تجريبي عن أدوات التصحيح الآلي"}},
    ]
    ctx.scratch["evaluation"] = {"actions": 1, "successes": 1, "failures": 0}
    ctx.scratch["lesson"] = "المنشورات التطبيقية تتفوق على النظرية"
    GraphMemoryPhase(store).run(ctx)
    check("GraphMemoryPhase يغذّي الرسم", bool(ctx.scratch.get("graph_ingested")), "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="", help="مسار قاعدة Kuzu (الافتراضي: مؤقتة)")
    ap.add_argument("--employee", default="verify-emp", help="معرّف الموظف")
    ap.add_argument("--inspect-only", action="store_true",
                    help="اعرض حالة الذاكرة والحقائق فقط بلا تغذية")
    ap.add_argument("--min-score", type=float, default=None)
    args = ap.parse_args()

    db = args.db or os.path.join(tempfile.gettempdir(), f"graphiti_verify_{int(time.time())}")
    # الفحص لا يُصلح شيئاً: قاعدة Kuzu أحادية العملية، وفتحها أثناء عمل التطبيق
    # يفشل بخطأ قفل — يجب ألا يُفهم أبداً كتلف فتُزاح قاعدة سليمة.
    store = GraphitiStore(db_path=db, min_score=args.min_score,
                          auto_repair=not args.inspect_only)
    print(f"قاعدة الرسم: {db}\n")

    if args.inspect_only:
        inspect(store, args.employee)
        store.close()
        return 0

    run_full(store, args.employee)
    print()
    run_phases(store, args.employee)
    inspect(store, args.employee)
    store.close()

    failed = [n for n, ok, _ in _results if not ok]
    print(f"\n=== النتيجة: {len(_results) - len(failed)}/{len(_results)} نجحت ===")
    for n in failed:
        print("  FAIL:", n)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
