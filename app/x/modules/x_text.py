"""طول النص كما يحسبه X وقصّه ضمن الحد — مكتبة قياسية فقط (يُستورد من app/x ومن app.aeos).

قيس 2026-10-28: ردٌّ من 3 نقاط تجاوز الحد بـ13 حرفاً، فعطّل X زر «ردّ» (العدّاد ‎-13 بالأحمر
و«الترقية إلى Plus»)، وفشل الإجراء بـ«بقي المحرّر ممتلئاً». الردود لم يكن لها أي حد طول.

العدّ وفق twitter-text v3: الروابط 23 حرفاً، والمحارف في النطاقات 0–4351 و8192–8205
و8208–8223 و8242–8247 (ومنها العربية كلها) بوزن 1، وما عداها (الإيموجي، CJK…) بوزن 2.
"""
from __future__ import annotations

import re

X_LIMIT = 280
_URL = re.compile(r"https?://\S+")
_LIGHT = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))


def _weight(ch: str) -> int:
    cp = ord(ch)
    return 1 if any(a <= cp <= b for a, b in _LIGHT) else 2


def weighted_len(text: str) -> int:
    text = text or ""
    n, pos = 0, 0
    for m in _URL.finditer(text):
        n += sum(_weight(c) for c in text[pos:m.start()]) + 23
        pos = m.end()
    return n + sum(_weight(c) for c in text[pos:])


def fit(text: str, limit: int = X_LIMIT) -> str:
    """النص كما هو إن كان ضمن الحد، وإلا قصّ عند آخر نهاية جملة/سطر (لا يبدو مبتوراً)، ثم آخر
    مسافة مع «…». لا يقطع كلمة في منتصفها."""
    t = (text or "").strip()
    if weighted_len(t) <= limit:
        return t
    budget, cut = limit - 1, ""
    for ch in t:                                 # أطول بادئة ضمن الحد بالوزن الفعلي
        if weighted_len(cut + ch) > budget:
            break
        cut += ch
    ends = list(re.finditer(r"[\.!\?؟…]|\n", cut))
    if ends and ends[-1].end() >= len(cut) // 2:
        return cut[: ends[-1].end()].strip()
    sp = cut.rfind(" ")
    if sp >= len(cut) // 2:
        return cut[:sp].rstrip() + "…"
    return cut.rstrip() + "…"
