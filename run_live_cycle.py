"""
run_live_cycle.py — تشغيل دورة معرفية حقيقية واحدة لموظف ذكي (ينشر فعلاً على X).

يشغّل سيرفر app/x، يبني المنصة، يحدّث إعداد الموظف بخط الأساس الجديد (مهارات
وقواعد صانع المحتوى)، ثم ينفّذ دورة واحدة متزامنة ويطبع كل ما جرى: ما رصده،
خطة المحتوى، القرارات، ما نُفّذ، والنتيجة على X.

    python run_live_cycle.py --account emaad5079 [--force-format thread] [--dry]

``--dry`` يمنع النشر فعلياً (يضع daily_post_target=0) للتجربة الآمنة.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
for noisy in ("httpx", "httpcore", "openai", "urllib3", "asyncio"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True, help="اسم الحساب (بلا @)")
    ap.add_argument("--platform", default="x")
    ap.add_argument("--force-format", default="", help="أجبر صيغة محتوى (thread/insight/...)")
    ap.add_argument("--dry", action="store_true", help="بلا نشر فعلي")
    args = ap.parse_args()

    from app.services import x_bridge
    from app.aeos.bootstrap import AEOSPlatform

    print("→ تشغيل سيرفر app/x ...")
    x_bridge.start_xsuite_server()
    for _ in range(30):
        if x_bridge._is_running():
            break
        time.sleep(1)
    print(f"   سيرفر app/x: {'شغّال' if x_bridge._is_running() else 'غير متاح!'}")

    platform = AEOSPlatform(num_workers=1)
    # يطبّق خط الأساس الجديد (مهارات وقواعد صانع المحتوى) بلا لمس الشخصية/الأهداف
    platform.toggle_agent(platform=args.platform, account_username=args.account,
                          enabled=True)
    cfg = platform.repository.get_by_account(args.platform, args.account)
    if cfg is None:
        print("لا يوجد موظف لهذا الحساب.")
        return 1

    restore = {}
    if args.force_format:
        restore["content_formats"] = cfg.business_rules.get("content_formats")
        cfg.business_rules["content_formats"] = [args.force_format]
    if args.dry:
        restore["daily_post_target"] = cfg.business_rules.get("daily_post_target")
        cfg.business_rules["daily_post_target"] = 0
    if restore:
        platform.repository.save(cfg)

    print(f"→ الموظف: @{cfg.account_username} | اهتمامات: {cfg.persona.interests} "
          f"| دورات سابقة: {cfg.runtime_state.cycles_completed}")
    print(f"   مهارات: {cfg.installed_skills}")
    print(f"   محاور المحتوى: ", end="")
    from app.aeos.employee import strategy
    print(strategy.content_pillars(cfg), "| صيغ:", strategy.enabled_formats(cfg))

    print("\n→ تشغيل الدورة (قد تستغرق دقائق: تصفّح + توليد + نشر) ...\n")
    started = time.time()
    result = platform.run_cycle_now(cfg.id, event_type="manual",
                                   payload={"force_fresh": True})
    took = time.time() - started

    print(f"\n=== انتهت الدورة في {took:.0f} ثانية ===")
    report = result if isinstance(result, dict) else {}
    print("cycle:", report.get("cycle_id"), "| أخطاء أطوار:", report.get("phase_errors"))

    fresh = platform.repository.get(cfg.id)
    if restore:
        for key, value in restore.items():
            if value is None:
                fresh.business_rules.pop(key, None)
            else:
                fresh.business_rules[key] = value
        platform.repository.save(fresh)
        print("→ أُعيدت الإعدادات المؤقتة كما كانت:", list(restore))

    def _show(title, value):
        print(f"\n--- {title} ---")
        print(json.dumps(value, ensure_ascii=False, indent=2, default=str)[:3000])

    obs = report.get("observations") or {}
    _show("ما رصده", {k: v for k, v in obs.items() if "preview" not in k})
    _show("تفكيره", report.get("thought"))
    _show("خطة المحتوى", report.get("content_plan"))
    _show("الحالة العاطفية", report.get("emotion_state"))
    _show("القرارات", report.get("decisions"))
    _show("تقرير الحارس", report.get("guard_report"))
    _show("نتائج التنفيذ", report.get("results"))
    _show("تقرير الأداء", report.get("performance_report"))
    _show("الدرس المستفاد", report.get("lesson"))
    _show("تقييم الحارس", report.get("critique"))
    from app.aeos.cognition.critique import directives_block
    print("\n--- التوجيهات التي ستُحقن في الدورة القادمة ---")
    print(directives_block(fresh) or "(لا توجيهات)")
    _show("أزمنة الأطوار (ms)", report.get("phase_timings_ms"))

    print("\n--- سجل الأداء بعد الدورة ---")
    print(json.dumps(strategy.performance_report(fresh), ensure_ascii=False, indent=2))
    print("counters:", fresh.runtime_state.counters)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
