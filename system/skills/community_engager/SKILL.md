---
name: community_engager
description: "Reacts to mentions/replies to the account."
kind: employee
entry: skill.py
class: CommunityEngagerSkill
order: 40
uses-capabilities: [reply_to_user, like_post, read_notifications]
---
# community_engager

مهارة **موظف** مدمجة: يقترح بها الموظف أفعاله في كل دورة معرفية (ليست تعليمات للمنسّق).
الكود في `skill.py`، والترتيب (`order`) مهم: المهارات تحجز أهدافها من حوض واحد، فالأفعال
النادرة عالية القيمة تحجز قبل الوفيرة.

## الوصف الكامل

Reacts to mentions/replies to the account.

Two sources:
1. An event-delivered mention/reply (webhook/notification wake).
2. The Mentions tab read during Observe (``scratch['mentions']``) — the
   people who replied to / mentioned the account. The employee replies back
   **briefly**, **one reply per incoming reply**, and **never twice** to the
   same one (permanent de-duplication via ``engaged_urls``).
