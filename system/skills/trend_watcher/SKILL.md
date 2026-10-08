---
name: trend_watcher
description: "Proposes researching a source URL provided via a news event."
kind: employee
entry: skill.py
class: TrendWatcherSkill
order: 50
uses-capabilities: [research_topic, store_knowledge]
---
# trend_watcher

مهارة **موظف** مدمجة: يقترح بها الموظف أفعاله في كل دورة معرفية (ليست تعليمات للمنسّق).
الكود في `skill.py`، والترتيب (`order`) مهم: المهارات تحجز أهدافها من حوض واحد، فالأفعال
النادرة عالية القيمة تحجز قبل الوفيرة.

## الوصف الكامل

Proposes researching a source URL provided via a news event.
