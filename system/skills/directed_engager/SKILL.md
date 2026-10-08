---
name: directed_engager
description: "Engage with a SPECIFIC post the account owner pasted in chat."
kind: employee
entry: skill.py
class: DirectedEngagementSkill
order: 100
uses-capabilities: [reply_to_user, like_post, retweet_post]
---
# directed_engager

مهارة **موظف** مدمجة: يقترح بها الموظف أفعاله في كل دورة معرفية (ليست تعليمات للمنسّق).
الكود في `skill.py`، والترتيب (`order`) مهم: المهارات تحجز أهدافها من حوض واحد، فالأفعال
النادرة عالية القيمة تحجز قبل الوفيرة.

## الوصف الكامل

Engage with a SPECIFIC post the account owner pasted in chat.

Triggered by an owner directive («تفاعل مع هذا المحتوى …» / «رد على …» /
«اعمل ريتويت …») delivered via the ``directed_engagement`` event payload.
The owner is the top authority, so this bypasses the specialty scope (the
guard exempts directed engagement). If a tweet URL is given it engages
directly; otherwise it live-searches X for the pasted text/handle, picks the
best-matching tweet, and proposes reply / like / retweet as requested.
Proposes nothing (and records a chat note) when the target cannot be found.
