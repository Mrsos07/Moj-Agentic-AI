---
name: directed_mission
description: "Execute a free-form owner mission where the EMPLOYEE picks the target."
kind: employee
entry: skill.py
class: DirectedMissionSkill
order: 110
uses-capabilities: [reply_to_user, like_post, retweet_post, quote_post, follow_user]
---
# directed_mission

مهارة **موظف** مدمجة: يقترح بها الموظف أفعاله في كل دورة معرفية (ليست تعليمات للمنسّق).
الكود في `skill.py`، والترتيب (`order`) مهم: المهارات تحجز أهدافها من حوض واحد، فالأفعال
النادرة عالية القيمة تحجز قبل الوفيرة.

## الوصف الكامل

Execute a free-form owner mission where the EMPLOYEE picks the target.

Triggered by ``directed_mission`` in the event payload — produced from open
chat («روح للتايم لاين وشوف تغريدة تناسب تخصصك وردّ عليها»، «تابع حسابين في
مجالك»، «اقتبس أقوى تغريدة في الترند»). Unlike ``directed_engager`` (which
acts on a post the owner pasted), here the owner names only the *source*,
the *actions* and optionally a topic constraint; the employee scans that
source, ranks candidates by the constraint then by specialty, and proposes
the requested actions on DIFFERENT targets (never the same tweet twice).

Owner authority: actions carry ``owner_directed`` so the persona guard and
the daily engagement budgets do not silently drop an explicit order. The
specialty filter still applies unless the owner allowed a free scope.
Records an Arabic note when the source has no usable target, so the chat
report explains *why* nothing happened instead of going silent.
