---
name: viral_content_creator
description: "Creates original, timely 'viral' content about trending topics the employee saw on its timeline within its field of interest."
kind: employee
entry: skill.py
class: ViralContentCreatorSkill
order: 90
uses-capabilities: [publish_content]
---
# viral_content_creator

مهارة **موظف** مدمجة: يقترح بها الموظف أفعاله في كل دورة معرفية (ليست تعليمات للمنسّق).
الكود في `skill.py`، والترتيب (`order`) مهم: المهارات تحجز أهدافها من حوض واحد، فالأفعال
النادرة عالية القيمة تحجز قبل الوفيرة.

## الوصف الكامل

Creates original, timely 'viral' content about trending topics the employee
saw on its timeline within its field of interest.

Picks the most relevant recent topic from the timeline and asks the content
capability to write an engaging, shareable original post about it (grounded
in the persona). Gated by the daily post target to avoid spamming.
