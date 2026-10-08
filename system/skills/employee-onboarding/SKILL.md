---
name: employee-onboarding
description: ضبط موظف ذكي جديد أو إعادة تهيئة موظف قائم خطوة بخطوة (التخصص، الشخصية، الجدول، المهارات، الأدوات). استخدمها عند «جهّز موظف» أو «أبي أضبط فلان من جديد».
allowed-tools: list_employees, employee_report, update_employee, list_employee_skills, add_employee_skill, list_tools, attach_tool, control_employee
version: "1.0"
---
# تهيئة موظف ذكي

اسأل سؤالاً واحداً في كل مرة، وطبّق كل إجابة فوراً ثم انتقل للتالي:

1. **التخصص**: ما المجالات التي يغطيها؟ ← `update_employee` (interests).
2. **الشخصية**: الدور، النبرة، اللهجة، وما لا يفعله أبداً ← `update_employee`.
3. **الإيقاع**: كل كم دقيقة يعمل؟ ساعات النشاط؟ كم منشوراً يومياً؟ ← `update_employee`.
4. **المهارات**: هل له قواعد تلقائية؟ (مثال: «كل صباح علّق على أهم خبر») ← `add_employee_skill`.
5. **الأدوات**: `list_tools` واعرض أدوات النظام المتاحة للموظفين، واربط ما يوافق عليه ← `attach_tool`.
6. **التشغيل**: بعد اكتمال الضبط اسأل «أشغّله الآن؟» ← `control_employee` (activate ثم run_cycle).

## قواعد
- عدّل فقط ما ذكره المالك صراحةً؛ لا تملأ حقولاً من عندك.
- في النهاية اعرض ملخّصاً بما ضُبط (من `employee_report`).
