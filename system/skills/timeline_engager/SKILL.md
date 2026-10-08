---
name: timeline_engager
description: "Interest-aware engagement with the authenticated home timeline."
kind: employee
entry: skill.py
class: TimelineEngagerSkill
order: 80
uses-capabilities: [browse_timeline, like_post, retweet_post, reply_to_user, follow_user]
---
# timeline_engager

مهارة **موظف** مدمجة: يقترح بها الموظف أفعاله في كل دورة معرفية (ليست تعليمات للمنسّق).
الكود في `skill.py`، والترتيب (`order`) مهم: المهارات تحجز أهدافها من حوض واحد، فالأفعال
النادرة عالية القيمة تحجز قبل الوفيرة.

## الوصف الكامل

Interest-aware engagement with the authenticated home timeline.

Filters the timeline (read during Observe) down to tweets that match the
employee's persona interests (e.g. a political persona engages only with
political content/accounts), then proposes like / retweet / reply / follow
on the most relevant ones. Reply text is left empty so the reply capability
generates it from the persona/instructions. Requires the ``browse_timeline``
capability (playwright_x MCP) to have populated the timeline scratch.
