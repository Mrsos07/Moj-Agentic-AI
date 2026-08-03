# Project notes for coding agents

## Environment
- The working virtualenv is `venv\` (NOT `.venv\`, which is missing most deps).
  Use `.\venv\Scripts\python.exe` for everything.
- Windows console is cp1252 by default; set `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8`
  before running scripts that print Arabic, otherwise they crash with a
  `charmap` codec error.

## Graph memory (Zep/Graphiti + embedded Kuzu)
- Code: `app/aeos/memory/graphiti_store.py`, wired in `app/aeos/bootstrap.py`,
  consumed by `EmotionPhase` / `GraphMemoryPhase` in `app/aeos/cognition/phases.py`.
- Graph DB file: `data/graphiti_kuzu` (gitignored). If it becomes unopenable
  (older Kuzu version / truncated WAL) the store quarantines it as
  `graphiti_kuzu.corrupt-<ts>` and rebuilds automatically.
- Verify end-to-end (needs `OPENAI_API_KEY`, ~2 min, real OpenAI calls):
  `.\venv\Scripts\python.exe verify_graph_memory.py`
  Inspect live data instead: `... verify_graph_memory.py --db data/graphiti_kuzu --employee <id> --inspect-only`
- Runtime inspection endpoint: `GET /api/aeos/employees/{id}/graph-memory[?query=...]`
- `GRAPHITI_MIN_SCORE` defaults to 0.3; Graphiti's own default (0.6) is too
  strict for loose Arabic queries and silently returns zero facts.
- Retrieved facts are filtered by `_drop_near_duplicates`: Graphiti extracts a
  fact per episode, so one repeated event produces many paraphrases. Filtering
  uses containment (`|A∩B| / min`) not Jaccard, normalises Arabic `ال/بال/لل`
  prefixes, drops stop-words, keeps the RICHER of two duplicates, and caps facts
  per mentioned @handle (`_MAX_PER_SUBJECT`) so a single account cannot occupy
  every slot. Without this, one mention repeated 10× produced an entire
  "emotional state" on its own.
- Do NOT put bookkeeping (cycle number, platform, timestamp) in a graph episode:
  it gets extracted as facts like "X works on platform X in cycle 145" and
  pollutes retrieval. The cycle number already lives in the episode name.

## The emotional state must come from graph memory, never from instructions
- `EmotionPhase` used to pass the whole `persona_prompt()` into the LLM, so the
  model paraphrased the user's standing instructions back as "behaviour rules" —
  the state was a copy of the instructions, not a reflection of experience.
- Now the prompt contains ONLY a minimal identity brief (name/role/field/language)
  plus the numbered graph facts. The model must return `based_on` (fact numbers)
  and `evidence`, and `_drop_instruction_echoes` deterministically removes any
  line that is a near-restatement of `persona.instructions/dos/donts/values`.
- No facts ⇒ no emotional state. Never synthesise one from the persona.

## Kuzu is single-process — never treat a lock error as corruption
- Only ONE process may open `data/graphiti_kuzu`. While the app runs, any side
  script that opens it fails with `Could not set lock on file`.
- `GraphitiStore._open_driver` must classify errors: lock/busy → retry later and
  **never** quarantine. Quarantining on a lock error once renamed the live DB out
  from under the running app. `GRAPHITI_AUTO_REPAIR=0` disables repair entirely,
  and tools that inspect a live DB pass `auto_repair=False`.
- To inspect a live graph, use the API (`GET /api/aeos/employees/{id}/graph-memory`),
  not a script.
- Do not run two app instances (`run.py`) at once: only one gets graph memory.

## Self-critique guard (per-cycle evaluation → next-cycle instructions)
- `app/aeos/cognition/critique.py` + `CritiquePhase` (runs after `LearnPhase`,
  before `GraphMemoryPhase`).
- Two layers: deterministic `findings` (guard-rejection rate, failure rate, topic
  repetition, hashtags, unanswered mentions, plan adherence, engagement trend)
  which work with no LLM, then an LLM critique on top. Rule-based directives are
  never dropped by the LLM.
- Output is stored in `runtime_state.flags`: `cycle_critique`, `critique_history`,
  `recurring_issues` (an issue seen 3+ times gets a harsher wording; a fixed one
  decays away).
- Directives are injected into `ExecutionContext.persona_prompt()` automatically,
  so they steer the NEXT cycle. They must never override user instructions.
- `focus.format` from the LLM is validated against `strategy.CONTENT_FORMATS` and
  fed to `strategy.choose_plan(prefer_format=...)`; proven performance still wins.
- Verify: `.\venv\Scripts\python.exe verify_critique.py`
- Inspect: `GET /api/aeos/employees/{id}/critique`

## Never report success for an X action you did not verify
- Every state-changing action in `app/x/modules/x_actions.py` must confirm its
  effect and `raise` otherwise, because a click that silently does nothing is the
  most common failure on X's new composer (Lexical ignores some programmatic
  clicks). Confirmations in use: `unlike` (like), `unretweet` (repost),
  `removeBookmark` (bookmark), `<id>-unfollow` (follow), `_reply_submitted`
  (reply), `_confirm_submitted` (post/thread/quote).
- `_confirm_submitted(..., allow_status_url_fallback=False)` is REQUIRED for
  quote/reply: those flows start on a `/status/` URL, so the "we are on a status
  page" last-resort check would always return success. Quote used to report
  success while publishing nothing because of exactly this.
- Publishing paths also need the `Ctrl+Enter` fallback (`_submit_via_keyboard`)
  before declaring failure.
- When adding such a check, verify the testid live first — a wrong selector turns
  a working feature into a permanent failure. `removeBookmark` and `-unfollow`
  were confirmed against the real UI.

## The browser lock is the usual cause of "انتهت المهلة"
- `browser_lock` (in `app/services/x_bridge.py`) serialises ALL Playwright work.
  An agent's Observe phase holds it for 60–80s and cycles fire every 30s, so any
  interactive request that waits on it unbounded will hit the HTTP timeout.
- Interactive tools must use `playwright_x_mcp._browser_slot(timeout)`, which
  fails fast with an actionable Arabic message. Agent-driven tools keep blocking
  (they should wait their turn).
- Scrapers must be time-budgeted, not iteration-budgeted: fixed scroll counts
  burn seconds after new content stops arriving. Use a deadline + "N idle rounds"
  early exit, and never re-`goto` a page that is already loaded.
- When collecting from a second tab (e.g. `/with_replies`), pass the already-seen
  URLs as `exclude` so progress is measured on NEW items — otherwise the first
  screen looks idle and collection stops immediately (persona cloning returned
  exactly 1 reply because of this).
- Persona cloning: ~25s scrape + ~20s LLM. Verified on a real account
  (@halgawi: 20 tweets + 10 replies in 24s).

## Per-cycle limits must be real
- `decision/engine.py` exposes `CYCLE_LIMIT_RULES` / `cycle_limit()`. The engine
  previously hard-capped every capability at 1 per cycle, so a UI setting like
  `max_likes_per_cycle=3` did nothing. Any new per-cycle setting must be added to
  `CYCLE_LIMIT_RULES` **and** the proposing skill must propose up to that many.
- A limit of `0` disables the capability; that is enforced in `_allowed()` (not
  only in the selection loop) because the anti-wait fallback and the LLM review
  would otherwise bypass it.
- `max_actions_per_cycle` is the overall ceiling — raising a per-type limit above
  it has no effect.

## Content quality gate
- `handlers._quality_issues()` blocks clichés, meta-talk, too-short posts,
  restatements of the brief, and repeated openings; one corrective regeneration
  is attempted. `_BLOCKING_QUALITY_ISSUES` (a subset) is what actually refuses to
  publish — `repeated_opening` deliberately is NOT blocking, or a persona with a
  fixed opening style could never publish.
- `_is_weak_media()` rejects source logos / favicons / profile images / page URLs
  so a Google News logo is never attached; a generated image takes over instead.
- `_image_prompt()` keeps one visual style (`image_style`) for brand consistency
  and forbids text in the image (Arabic renders garbled in image models).

## Content-creator brain
- `app/aeos/employee/strategy.py` owns content pillars, formats (incl. threads),
  the per-post performance ledger and the weighted choice. State lives in
  existing JSON columns (`business_rules`, `runtime_state.flags`) — no migrations.
- Cycle order: `Observe → Performance → Emotion → Analyze → Think → Strategy →
  Prioritize → Plan → Decide → Guard → Execute → Evaluate → Learn → GraphMemory
  → Support → Save`.
- Verify the whole creator brain (no posting, no network):
  `.\venv\Scripts\python.exe verify_creator.py`
- Live smoke test (POSTS FOR REAL, needs cookies in `app/x/cookies/<label>.json`):
  `.\venv\Scripts\python.exe verify_creator.py --live <cookie_label> --handle <account>`
- Inspect strategy + measured performance: `GET /api/aeos/employees/{id}/content-strategy`
- Thread posting is native (one browser session, composer "+" button):
  `post_thread_to_x` in `app/x/modules/x_post.py` → `/api/post-thread` →
  `x_bridge.post_thread` → `x` MCP tool `post_thread` → `publish_thread` capability.
- Metrics are scraped in-process by `playwright_x.read_my_posts`; the parser
  handles Arabic/English aria-labels and `1.2K` / `١٢` / `12 ألف` numbers.
  Note the profile page collapses parts of the account's own threads, so only
  the thread head is measured (that is the URL the ledger stores anyway).
- `strategy.engagement_score` is the single source of the performance formula
  and **must keep a views term**: real accounts can have 0 likes with hundreds
  of views, and ignoring views makes every post score 0 (no learning at all).
- Run one real cycle end-to-end (starts app/x, publishes for real):
  `.\venv\Scripts\python.exe run_live_cycle.py --account <handle> [--force-format thread] [--dry]`
  Use `--dry` first: it sets `daily_post_target=0` so nothing is published.
