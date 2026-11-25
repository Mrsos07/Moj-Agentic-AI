# -*- coding: utf-8 -*-
"""مضيف GraphitiStore في عملية فرعية معزولة — احتواء انهيارات Kuzu الأصلية.

لماذا: Kuzu امتداد C++‎ ينهار بـ0xC0000005 أثناء كتابات Graphiti حتى على
قاعدة جديدة تماماً خارج OneDrive وبكل الوصول متسلسلاً (انظر سجل أحداث ويندوز
5/8/2026). المشروع الأصلي مؤرشف (آخر إصدار 0.11.3) فلا إصلاح قادم، وgraphiti
نفسها تعلن سائق Kuzu مهجوراً. الحل الجذري الوحيد المتاح: عزل كل عمل Kuzu في
هذه العملية؛ انهيارها يقتلها وحدها، والبروكسي في العملية الأم يعيد تشغيلها
وتستمر المنصة بلا أي انقطاع.

البروتوكول: أسطر JSON على stdin/stdout — طلب ``{"id", "op", "kwargs"}`` وردّ
``{"id", "ok", "result"|"error"}``. stdout الحقيقي محجوز للبروتوكول حصراً:
نضاعف واصفه ثم نوجّه fd 1 إلى stderr كي لا يفسد أي print (حتى من الامتدادات
الأصلية) الردود.
"""
from __future__ import annotations

import json
import os
import sys


def _reserve_stdout():
    """أعد قناة البروتوكول، ووجّه stdout العام (حتى الأصلي) إلى stderr."""
    protocol = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1, newline="\n")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return protocol


def main() -> None:
    protocol = _reserve_stdout()

    from app.aeos.memory.graphiti_store import GraphitiStore

    store = GraphitiStore()
    ops = {
        "ping": lambda **kw: "pong",
        "available": lambda **kw: store.available,
        "search": store.search,
        "search_facts": store.search_facts,
        "facts_about": store.facts_about,
        "recent_facts": store.recent_facts,
        "stats": store.stats,
        "clear": store.clear,
    }

    stdin = os.fdopen(0, "r", encoding="utf-8", errors="replace")
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        rid = None
        try:
            req = json.loads(line)
            rid = req.get("id")
            op = req.get("op", "")
            kwargs = req.get("kwargs") or {}
            from app.aeos.security import gateway
            gateway.current.set(None)
            if op not in {"available", "ping"}:
                # This pipe is private to the authenticated supervisor process.
                gateway.current.set(gateway.Principal(**req["principal"]))
            if op == "ingest":
                # داخل المخزن هي «أطلق وانسَ» أصلاً؛ الردّ فوري والاستخراج
                # (نداء LLM ‏~40ث ثم كتابة Kuzu) يجري في خيط الحلقة الخلفي.
                store.ingest(**kwargs)
                result = True
            else:
                result = ops[op](**kwargs)
            payload = {"id": rid, "ok": True, "result": result}
        except Exception as exc:  # noqa: BLE001
            payload = {"id": rid, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
        protocol.write(json.dumps(payload, ensure_ascii=False) + "\n")
        protocol.flush()

    # أغلق الأب stdin ⇒ خروج. أمهل كتابةً جارية فرصة الاكتمال (علامة ‎.writing‎
    # موجودة أثناء الكتابة فقط) — الخروج في منتصفها يعني عزل القاعدة عند البدء
    # التالي وفقدان الذاكرة بلا داعٍ. الانهيار الأصلي لا يمرّ من هنا أصلاً.
    import time
    deadline = time.monotonic() + 90
    while store._writing_marker.exists() and time.monotonic() < deadline:  # noqa: SLF001
        time.sleep(1.0)


if __name__ == "__main__":
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[3]))
    main()
