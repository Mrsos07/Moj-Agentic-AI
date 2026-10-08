---
name: content_quoter
description: "Quote-tweet the strongest in-field post with the employee's own take."
kind: employee
entry: skill.py
class: ContentQuoterSkill
order: 60
uses-capabilities: [quote_post]
---
# content_quoter

مهارة **موظف** مدمجة: يقترح بها الموظف أفعاله في كل دورة معرفية (ليست تعليمات للمنسّق).
الكود في `skill.py`، والترتيب (`order`) مهم: المهارات تحجز أهدافها من حوض واحد، فالأفعال
النادرة عالية القيمة تحجز قبل الوفيرة.

## الوصف الكامل

Quote-tweet the strongest in-field post with the employee's own take.

الاقتباس أقوى من إعادة النشر الصامتة: يبني صوت الحساب وموقفه بدل نقل كلام
غيره، ويجذب جمهور الحساب الأصلي إلى النقاش. نختار أفضل عنصر في مجمّع
التفاعل (أعلى صلة، ومنشورات الأداء العالي أولاً) ونطلب تعليقاً يضيف زاوية.
