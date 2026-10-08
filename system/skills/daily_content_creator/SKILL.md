---
name: daily_content_creator
description: "Proposes an original post ONLY when there is a real, grounded topic."
kind: employee
entry: skill.py
class: DailyContentCreatorSkill
order: 30
uses-capabilities: [publish_content]
---
# daily_content_creator

مهارة **موظف** مدمجة: يقترح بها الموظف أفعاله في كل دورة معرفية (ليست تعليمات للمنسّق).
الكود في `skill.py`، والترتيب (`order`) مهم: المهارات تحجز أهدافها من حوض واحد، فالأفعال
النادرة عالية القيمة تحجز قبل الوفيرة.

## الوصف الكامل

Proposes an original post ONLY when there is a real, grounded topic.

Never posts generic filler: if there is no trending topic (Google Trends /
Trend Detector) and no relevant timeline tweet, it proposes nothing. When a
topic exists it grounds the post on it and attaches the source media.
