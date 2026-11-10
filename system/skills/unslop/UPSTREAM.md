# unslop (vendored)

- Source: https://github.com/theclaymethod/unslop
- Commit: `17ed39c9d0b522f44190ff0c6233867eadee192a` (2026-09-07), skill version 2.3.0
- License: MIT (declared in the upstream README and SKILL.md frontmatter; the repo has no
  LICENSE file, so the standard MIT text with the author's attribution is in `LICENSE` here).

Copied unchanged: `SKILL.md`, `README.md`, `references/`, `presets/`, `scripts/`.
Not copied (upstream development only): `evals/`, `plans/`, `docs/`, `assets/`, `.github/`.

Do not edit these files. Moj-specific behavior lives outside this folder:
- `app/aeos/employee/unslop.py` — runs `scripts/banned_phrase_scan.py` on English text, plus
  an Arabic pattern table built on the same categories (unslop declines non-English input).
- `system/skills/unslop_guard/` — the always-on employee skill that applies it to every
  post, reply, quote, thread and scheduled post.

To update: replace the folders above from a newer upstream commit, update the commit line,
then run `.\venv\Scripts\python.exe verify_unslop.py`.
