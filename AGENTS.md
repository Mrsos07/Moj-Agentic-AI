# Project notes for coding agents

## Publishing — standing instruction from the owner (automatic, every time)
- After any change is finished AND verified (relevant `verify_*.py` + frontend build if touched),
  publish it without asking: `.\scripts\publish.ps1 -Message "<why, in Arabic>"`. It commits,
  pushes `main` to GitHub (fast-forward only, never force) and updates the OneDrive copy
  (`C:\Users\pc\OneDrive\Desktop\Moj-Agentic-AI`, `git pull --ff-only`, skipped if it has local edits).
- The script refuses and unstages if a forbidden file (`.env`, `data/`, cookies, `*.db`,
  secrets files) or a secret-key pattern is in the diff. Never bypass it; move the value to
  `.env`/`data/` instead. Exit code 2 = the three copies disagree — investigate.
- Do NOT publish half-done or failing work just to "keep it synced".

## Environment
- The working virtualenv is `venv\` (NOT `.venv\`, which is missing most deps).
  Use `.\venv\Scripts\python.exe` for everything.
- Windows console is cp1252 by default; set `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8`
  before running scripts that print Arabic, otherwise they crash with a
  `charmap` codec error.

## The LLM provider is OpenRouter — resolve it ONLY via `resolve_llm()`
- `app/core/llm_provider.py::resolve_llm()` is the single source of truth for
  "which provider / key / base_url / model name". It returns an `LLMEndpoint`
  with `client_kwargs()` (for the `openai` SDK), `auth_headers()` + `chat_url`
  (for raw HTTP) and `images_url`. **Never read `OPENAI_API_KEY` or
  `OPENROUTER_API_KEY` directly in a new call site** — the choice lives in one
  place because there are 8 call sites and a forgotten one silently talks to the
  wrong provider (or the wrong account's bill).
- **Per-user model provider** (Settings → «مزوّد النماذج», chat header picker): a user links
  OpenRouter / OpenAI / Claude / Google / Ollama with their OWN key (Fernet-encrypted in
  `user_llm_settings`, never returned — last 4 chars only) and picks two independent models:
  `chat` (coordinator + chat conversations) and `employee` (automated cycles).
  `resolve_llm(user_id=…, purpose="chat"|"employee")` returns the user's endpoint or falls back
  to the system one; calls WITHOUT `user_id` are unchanged. Employee cycles reach it through
  `RoutedLLMProvider` (the injected platform LLM) + the `LLM_OWNER` contextvar set around
  `cycle.run` in `runtime/manager.py`; the WebSocket handler sets `(user, "chat")`. A failing user
  model (key/credit) falls back to the system model for that call. Embeddings and images ALWAYS
  stay on the system endpoint. Ollama's URL is server config (`OLLAMA_BASE_URL`), never user
  input (SSRF). Catalog: `app/core/llm_catalog.py`; service: `app/services/user_llm.py`; API:
  `/api/llm/*`; test: `verify_llm_providers.py [--live]`.
- OpenRouter wins whenever `OPENROUTER_API_KEY` is set; OpenAI is the fallback.
  Both use the same `openai` package — only `base_url` and the model name differ.
- **Model names must be provider-qualified**: `openai/gpt-4o-mini`, not `gpt-4`.
  An unqualified name is rejected by OpenRouter and every reasoning path just
  returns an empty string — no exception, no log. `OPENAI_MODEL` is therefore
  never passed to OpenRouter; `OPENROUTER_MODEL` is a separate setting.
- OpenRouter is NOT a drop-in for one thing: **image generation**. Its endpoint
  is `POST /api/v1/images`, while the SDK's `client.images.generate()` hits
  `/images/generations` (404). `image_mcp.py` therefore has two branches —
  raw HTTP for OpenRouter, SDK for OpenAI — and reads `data[0].b64_json`.
  `size` does not exist there either; it is converted to `aspect_ratio`.
  Chat, embeddings and responses ARE shape-compatible (verified against the
  docs), so those only needed `base_url`.
- Verify with real calls: `.\venv\Scripts\python.exe verify_openrouter.py`
  (`--config-only` for no network, `--no-image` to skip the priciest check).
  A wrong key produces 401 on every path — which is exactly how you tell
  "wiring is right, key is wrong" apart from "wiring is wrong".
- Image generation defaults to GPT Image 2: `openai/gpt-image-2` on OpenRouter,
  `gpt-image-2` on OpenAI. `verify_openrouter.py --config-only` also checks the
  image-model defaults and mocked ImageMCP dispatch without generating paid images.
- Do not change `OPENROUTER_EMBEDDING_MODEL` casually: a different embedding
  model changes vector dimensions, and every vector already stored in the graph
  becomes incomparable — the whole graph must be rebuilt.

## Graph memory (Zep/Graphiti — Neo4j by default since 2026-08-05)
- `Graphiti()` builds its OWN llm/embedder/reranker clients against
  `api.openai.com` from `OPENAI_API_KEY` if you don't pass them. So switching
  provider is NOT a key change: `_build_ai_clients()` passes all three
  explicitly, sharing one `AsyncOpenAI` built from `endpoint.client_kwargs()`
  (which also carries the OpenRouter attribution headers `LLMConfig` cannot).
  The `cross_encoder` is never actually called (search uses
  `COMBINED_HYBRID_SEARCH_RRF`, no reranker) but the constructor builds it
  anyway, so it must carry our key.
- Keep the extraction client as `OpenAIClient` (the `/responses` API via
  `responses.parse`); OpenRouter serves `/responses` as a unified endpoint and
  the SDK builds the strict schema, so it just works.
  **Do NOT switch to `OpenAIGenericClient` in its default `json_schema` mode.**
  It builds `response_format=json_schema` from a raw `model_json_schema()`,
  which lacks `additionalProperties: false`, and OpenAI's strict validator
  rejects it. Measured: HTTP 400 `invalid_json_schema` on `ExtractedEntities`
  from both the OpenAI and Azure upstreams through OpenRouter, on every single
  ingest. The symptom is nasty because nothing surfaces: `ingest_failed` climbs,
  the graph stays at `{episodes: 0, entities: 0, facts: 0}`, and the employee
  simply has no emotional state — no error in the UI or the HTTP layer.
  `GRAPHITI_STRUCTURED_OUTPUT=json_object` switches to the generic client in the
  mode that injects the schema into the prompt instead of enforcing it (also
  measured working) — use it only for a model where `/responses` fails.
- Verify the whole graph path after any provider change (temp DB, no Neo4j
  needed, ~3 min): `$env:GRAPHITI_DRIVER='kuzu'; $env:GRAPHITI_ISOLATED='0'`
  then `.\venv\Scripts\python.exe verify_graph_memory.py`. Watch
  `ingest_failed` — it must be 0. A green "ingest" line only means the episode
  was queued; extraction happens later in the background thread.
- Code: `app/aeos/memory/graphiti_store.py`, wired in `app/aeos/bootstrap.py`,
  consumed by `EmotionPhase` / `GraphMemoryPhase` in `app/aeos/cognition/phases.py`.
- **The default driver is `neo4j`, not Kuzu** (`GRAPHITI_DRIVER=kuzu` brings the
  old engine back for diagnosing old data). `app/aeos/memory/neo4j_service.py`
  starts/reaches a local server; on Neo4j `build_indices_and_constraints()`
  actually creates the indexes, unlike the Kuzu driver where `_setup_fts_indexes`
  had to create them by hand. Everything below about Kuzu segfaults is the
  reason for that migration — keep it, it explains the surrounding defensive
  code (isolated worker process, write markers, quarantine), which still runs.
- Retrieval is not only generic: `facts_about(employee_id, entities)` asks the
  graph about SPECIFIC handles before we interact with them, and the result
  lands in `scratch["graph_handle_facts"]` (loaded by `EmotionPhase`, consumed by
  the decision engine's LLM review and by `PersonaGuardPhase`). Before this the
  graph was write-mostly: read once per cycle with 4-5 fixed generic queries,
  used only to produce a "mood" string, so it influenced no decision at all.
  `facts_about` only attributes a fact to an entity if the fact literally
  mentions it — hybrid search returns semantic neighbours, and attributing those
  invents a relationship history that never happened.
- Search results are cached per `(queries, limit)` with a TTL
  (`GRAPHITI_SEARCH_TTL`, default 300s) and an ingest epoch: any `ingest()`
  clears the cache instantly, so a stale graph view is impossible. Without this
  every cycle paid 5 hybrid queries under the global lock for an unchanged graph.
- `_last_digest` (skip re-ingesting an identical episode) is **persisted** to
  `graphiti_last_digests.json` next to the DB. In memory only, every restart —
  or every worker respawn after a crash — re-ingested the same episode and paid
  the LLM extraction again for zero new information.
- Graph DB file (Kuzu path only): `%LOCALAPPDATA%\Moj-Agentic-AI\graphiti_kuzu`
  (`_default_db_path()`: the repo lives under OneDrive, and OneDrive syncing a
  live Kuzu file corrupts it — a freshly rebuilt DB crashed again 2 days later
  with all access serialized, which ruled out concurrency). The repo-relative
  `data/graphiti_kuzu` default only applies outside OneDrive; `GRAPHITI_KUZU_DB`
  overrides either. If the DB becomes unopenable (older Kuzu version / truncated
  WAL) the store quarantines it as `graphiti_kuzu.corrupt-<ts>` and rebuilds
  automatically.
- Verify end-to-end (needs `OPENROUTER_API_KEY`, ~2 min, real LLM calls):
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

## Kuzu segfaults kill the whole backend — serialize EVERY access
*(historical: Kuzu is no longer the default driver, but this is why — and the
defences it forced are still in the code and still required if you set
`GRAPHITI_DRIVER=kuzu`.)*
- Symptom the user sees: "the employee runs one cycle, then the AI employee and
  the chat history disappear". The backend process vanishes with **no Python
  traceback** — because a native extension crashed, not Python.
- Proof, not guesswork: Windows Event Log had 15 crashes in one day, all with
  `Faulting module: _kuzu.cp312-win_amd64.pyd`, `Exception code: 0xc0000005`
  (access violation), same fault offset. Get it with:
  `Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName='Application Error'}`
  A Python-side repro exits with code `-1073741819` (= 0xC0000005).
- Trigger: a Kuzu **read concurrent with a write**. `ingest()` is fire-and-forget
  (LLM entity extraction takes ~40s) and the next cycle's `EmotionPhase` calls
  `search_facts` 30s later — straight into the write. The lock used to guard
  writes only.
- Therefore: `search_facts`, `recent_facts`, `stats` and `ingest` must ALL hold
  `self._lock()`, and `KuzuDriver(max_concurrent_queries=1)` — Graphiti runs its
  hybrid-search queries in parallel via `asyncio.gather`, and the outer lock does
  not stop that internal parallelism. `verify_creator.py` asserts all of this.
- Cost of serializing: the first search after an ingest waits ~10-16s for the
  write to finish. That is the correct trade — the alternative is a dead process.
- **Half-written DBs crash again on the next write** (measured: a copy of the live
  65 MB DB crashed even WITH the fix, while a fresh DB survived). That is the
  death loop: crash → damaged file → crash. `_mark_write_start/_end` write a
  `<db>.writing` marker; finding it at startup means the previous process died
  mid-write, so `_recover_from_hard_crash()` quarantines the file (rename, never
  delete) and rebuilds. Fixing the concurrency does NOT repair an already damaged
  file — it must be rotated once.

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
- UI signals are NOT enough for threads: `post_thread_to_x` once reported
  «تم نشر الثريد ✅» (dialog closed) while nothing appeared on X — and the phantom
  success also consumed `posts_today`/`threads_today` so no retry happened that
  day. The fix is NETWORK-level verification: capture `CreateTweet` GraphQL
  responses via `page.on("response", ...)` (append only in the handler — never
  read bodies inside a sync-API event handler), then require ≥1 response with
  status 200 or raise. The head URL comes from
  `data.create_tweet.tweet_results.result.rest_id` — the FIRST `rest_id` in the
  body can be the USER id (10 digits ≈ 2015 accounts); tweet ids are ≥15 digits.
  Keep the success criterion (any 200 CreateTweet) separate from URL extraction
  so a payload-shape change can never turn a published thread into a false
  failure. Verified live twice on @emaad5079 (second run returned a real
  19-digit head URL).
- The xsuite server (`app/x/app.py`, port 5789) is a child process that imports
  these modules at startup: after editing `x_post.py`/`x_actions.py`, kill the
  5789 listener — the bridge respawns it with fresh code on the next X action.

## Backend "hangs" are usually a zombie reloader, not a blocked event loop
- Symptom: port 8000 accepts TCP but answers nothing (even `/api/aeos/health`),
  the UI shows no employees and no conversations, and vite floods with
  `ws proxy error: read ECONNRESET`.
- Cause: uvicorn's `--reload` supervisor OWNS the listening socket. If the worker
  process dies (Ctrl+C hitting only the child, a crash mid-reload) the supervisor
  keeps listening forever with nothing behind it. A new `run.py` then cannot bind.
- `run.py::_free_port()` now clears the port before binding (same trick x_bridge
  uses for 5789). `RELOAD=0` runs a single process with no supervisor, and
  `PORT=8010` runs a throwaway instance — **use a separate PORT when testing so
  you never kill the user's running app** (I did this once; don't repeat it).
- `run.py::_run_with_self_healing_reload` replaces the stock reloader: it checks
  worker liveness on every watch-loop wakeup (~10s) and respawns a worker that
  died unexpectedly (e.g. a Kuzu segfault), instead of holding the port as a
  zombie. Ctrl+C is still clean (`should_exit` guard + 0.5s grace) and a
  crash-at-startup loop is throttled to one respawn per 5s. Verified by killing
  the worker of a dummy app on PORT=8010 and watching it revive.
- Diagnose with py-spy, not guesswork: `venv\Scripts\py-spy.exe dump --pid <pid>`.
  A stack in `watchfiles/main.py` + `basereload.py` and NO child process means
  exactly this zombie state.
- A 45s `search_tweets` timeout does NOT block HTTP: cycles run in worker threads
  (verified — `/api/conversations/` kept returning 200 during one).

## WebSocket drops: the browser cannot see protocol-level pongs
- uvicorn pings every 20s (`ws_ping_interval`), which keeps idle sockets alive,
  but the browser API exposes no pong. So after an abrupt backend death the
  client keeps `readyState === OPEN` and silently receives nothing forever.
- Fixed with an app-level heartbeat: the client sends `{"type":"ping"}` every 25s
  and the `/ws/chat` handler replies `{"type":"pong"}`. That reply MUST be handled
  before the chat-message path, or a ping is treated as an empty user message and
  wakes the agent.
- The client also force-reconnects after 70s of inbound silence, uses exponential
  backoff (1→15s, was a flat 3s that hammered a restarting backend), and
  reconnects immediately on `online` / `visibilitychange`.
- `ECONNRESET` in the vite proxy is the normal signature of a backend restart or a
  page refresh (all sockets reset at once — you will see a burst with identical
  timestamps). It is logged as one quiet line so real errors stay visible.

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

## Per-employee custom skills: a SPEC, never generated code
- `app/aeos/skills/custom.py` + `CustomSkillRunner` (registered in
  `default_skills()`, `always_on = True`). The owner writes a free-text prompt in
  the employee card; an LLM turns it into a declarative JSON spec (trigger /
  target source + filters / actions / content brief / priority) that ONE generic
  runner interprets. Stored in `business_rules["custom_skills"]` — safe because
  `configure_agent` does `business_rules.update(...)`, so saving the persona from
  the UI merges instead of wiping the skills.
- **Never generate and exec Python for this.** The backend process holds users'
  X cookies, API keys and the DB; a single prompt injection (a tweet on the
  timeline is enough) would own the machine. The spec gives the same practical
  power — pick a source, filter targets, act, steer the writing — while being
  whitelisted, reviewable and revocable.
- `normalize_spec()` is the ONLY security boundary: unknown action types /
  sources / formats are dropped, every number is clamped, and unknown top-level
  keys (including `owner_directed`) are discarded. Never trust "the model will
  follow the schema".
- Custom skills only *propose*. They go through the decision engine, the persona
  guard and the daily budgets like any built-in skill. They must never set
  `owner_directed` — that flag is for a command the owner types in chat right
  now, not a rule they enabled once and forgot.
- Hard filters win over the owner's filters: ads/spam, persona donts and
  already-engaged topics are dropped even when the spec says
  `specialty_only: false`.
- `generate_spec()` has a deterministic fallback (Arabic verb/source patterns
  with prefix tolerance, same approach as `employee_mission.py`) so a missing
  key or a network failure still produces a sane skill — and so the verifier can
  run with no network.
- The daily quota (`trigger.max_per_day`) is consumed on **execution**, not on
  proposal: `ExecutePhase` reads `params["_skill"]` and calls `note_run`. Counting
  proposals would burn the quota on actions the engine or the guard dropped.
- API: `GET|POST /employees/{id}/skills`,
  `POST /employees/{id}/skills/generate` (preview by default,
  `activate=true` to save), `PATCH`/`DELETE /employees/{id}/skills/{skill_id}`.
- UI: inside the persona editor of the employee card
  (`frontend/src/components/AgentsDrawer.jsx`) — prompt box → Arabic preview
  (`describe_spec`) → activate, plus enable/disable/delete for existing ones.
- Verify (no network, no LLM): `.\venv\Scripts\python.exe verify_custom_skill.py`

## Interaction decisions must LEARN — they used to be hard-coded constants
- `app/aeos/employee/interaction_ledger.py` is to interactions what
  `strategy.py` is to posts. Before it, `expected_impact` for reply/like/quote/
  retweet/follow was a number typed by hand in the skill (0.68 / 0.55 / 0.35…),
  so hundreds of cycles taught the employee nothing about who is worth engaging.
- It records every executed interaction (hooked once, in
  `handlers._record_interaction` — the single choke point) and **credits the
  reciprocal signal**: when a handle we engaged shows up in the Mentions tab,
  `ObservePhase._credit_interactions` marks that entry `reciprocated`. That is
  the only moment where "did this interaction work?" is observable.
- Exposed signals: `handle_affinity` (will THIS account respond),
  `capability_yield` (which action type pays off), `handle_fatigue` (are we
  pestering one account). All smoothed with a prior and centred on
  `base_rate(config)`, so an unknown handle/type scores exactly neutral — never
  rewarded or punished without evidence. Samples count is exposed so the engine
  can add an exploration bonus (`interaction_exploration`).
- Bookmarks are deliberately OUT of the ledger: not public, can never be
  reciprocated, and including them drags the base rate down for everything else.
- Storage is `runtime_state.flags["interaction_ledger"]` (same no-migration
  approach as the performance ledger), trimmed to 200 entries / 150 handles.
- Inspect: `GET /api/aeos/employees/{id}/interactions`
- Verify (no network, no LLM): `.\venv\Scripts\python.exe verify_interaction.py`

## Diversity is a STRUCTURAL property, not a prompt instruction
Reported symptom: "it insists on threads and quotes and keeps repeating". Measured
with `sim_decisions.py` (runs the real planner + engine over 40 cycles, no LLM):
after ONE early high-view thread the account became **100% thread, one pillar,
zero quotes, 5-cycle reply streaks**, and published 5 posts instead of 10. Four
independent structural causes — none of them fixable by telling the model to
"vary":

1. **Unbounded performance scale.** `strategy._pick` compared a raw engagement
   mean (a 20k-view thread ⇒ ≈400) against a fixed prior (6.0) with a fixed
   additive exploration bonus (≈2.1) and a repeat penalty capped at ×0.2. 400×0.2
   ≫ 6+2.1, so one early win locked the format forever. Performance is now
   **relative** (`rel = smoothed / best`, 0..1) so penalty and exploration can
   actually flip the choice, plus a hard `max_same_choice_streak` (default 2) for
   format AND pillar.
2. **Plan blind to its own quota.** Threads have `daily_thread_target` (1). The
   plan kept choosing "thread", the skill proposed `publish_thread`, `_allowed`
   rejected it — and no alternative format was ever picked, so the daily post
   target silently died. `strategy.available_formats()` now removes formats whose
   quota is spent BEFORE choosing.
3. **Blind rotation instead of pacing.** `ENGAGEMENT_ROTATION[cycles % 5]` gave a
   flat bonus by calendar arithmetic. Replaced by `_day_progress()` +
   `_pace_delta()`: a capability behind its share of the day is boosted, one ahead
   of it is damped. The action mix now tracks the user's configured daily budgets
   instead of static impact constants. Rotation is gone.
4. **Every skill picked the SAME top target.** All skills read one
   `_engagement_pool` sorted identically, so they all grabbed item[0]; the loud-
   action dedupe then always dropped the lowest-priority one ⇒ zero quotes in 40
   cycles. Fixed with `claim_targets(context, items, count, owner=...)`: skills
   reserve distinct targets, so cycle variety became *topic* variety.
   - `owner` matters: without it `propose()` gains a side effect and a second call
     (guard alternates, retries, a test asserting twice) returns nothing.
   - **Claim order must not come from `installed_skills`.** That list order is
     accidental (creation date). `ISkill.claim_priority` + sorting in
     `SkillRegistry.resolve` puts scarce/high-value actions (quote 2/day) ahead of
     abundant ones (likes 12/day), which always find another target.
- Plus `max_same_capability_streak` (default 3) in the engine: a capability
  executed in the last N consecutive cycles is skipped this cycle no matter how
  high it scores — and this is enforced **after** the LLM review too, because the
  model consistently gravitates to the most "impressive" actions (quote/thread)
  and would otherwise reproduce the exact behaviour being fixed. Mention replies
  and owner orders are exempt.
- Also: `bookmark_post` is excluded from the per-handle cap (it is not public, so
  it cannot pester anyone — counting it dropped bookmarks from 14 to 0), and
  `min_quote_chars` (60, was a buried 80) made quoting possible on Arabic text.
- The measured guarantees are now locked in `verify_interaction.py` section 4
  (40-cycle simulation, both from-scratch and with a seeded early win).

## Three decision-engine traps (all were real, all are now guarded)
1. **Blind rotation.** `ENGAGEMENT_ROTATION[cycles % 5]` gave +0.20 to an action
   type by calendar arithmetic — including types the user had disabled or whose
   daily budget was exhausted. Rotation now only considers capabilities with
   `cycle_limit > 0` AND remaining budget.
2. **Double penalty.** `_history_penalty` (type repeated in recent cycles) and
   `_budget_factor` (daily budget consumed) were subtracted together, so likes
   with a target of 12/day were penalised for "repetition" after 2 likes and the
   employee interacted far less than configured. The history penalty is now
   scaled by budget consumption (`0.35 + 0.65 × used_ratio`).
3. **One target, several loud actions.** `_dedupe` keys on
   `(capability, params)` while every skill draws from the same
   `_engagement_pool`, so reply + quote + retweet could all land on the SAME
   tweet in one cycle (a spam signature). `SkillBasedPlanner._limit_per_target`
   keeps one "loud" action (reply > quote > retweet) per URL, allows a like
   alongside it, and caps actions per handle
   (`max_actions_per_handle_per_cycle`, default 2). `owner_directed` is never
   touched — the owner may explicitly ask for both.
- Mention replies have their own cycle cap (`max_reply_notifications_per_cycle`,
  via `engine.is_mention_reply`): the general `max_replies_per_cycle` (default 1)
  used to trim three people who addressed us down to one reply, contradicting
  the stated rule that whoever addresses us deserves an answer. A limit of `0`
  still disables replies entirely — that is a user decision, not a cap.
- The LLM decision review now sees, per candidate: our history with that handle,
  what the graph memory knows about it, and the day's remaining budgets. It was
  choosing blind before.

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
- **Exception: `params["owner_directed"]`.** An explicit chat order bypasses the
  daily budgets, the per-cycle caps, the persona guard and the LLM decision
  review (which used to silently drop it). Otherwise «انشر عن 2» after the daily
  target was met, or «ردّ على ثلاث تغريدات» with `max_replies_per_cycle=1`,
  produced a cycle with no action and no explanation. Set it in every skill that
  executes an owner directive.

## Free-form chat missions: the employee picks the target itself
- `app/aeos/services/employee_mission.py` (parser) + `DirectedMissionSkill` in
  `skills/builtin.py`, wired from `_maybe_mission_employee` in `app/main.py`
  BEFORE the pasted-content path.
- Three chat paths exist and must not hijack each other: `directed_topic`
  (publish about a topic), `directed_engagement` (act on content the owner
  pasted — has a URL or ≥3 pasted lines), and `directed_mission` (owner names
  only the **source** + **actions**; the employee scans and chooses). The parser
  therefore refuses messages containing a URL or pasted blocks, and requires an
  explicit source («تايم لاين/منشنات/ترند») or a movement verb («روح، شوف، افتح»).
- Parsing is deterministic first (works with no LLM/network), then one optional
  LLM call refines `query`/`count`/`note`. Arabic verbs need prefix tolerance
  (`_V`): «تقتبس»/«وترد» were missed by naive patterns. Match only at word start
  (`(?<!\w)`), otherwise «رد» hits inside «تردد».
- Mission sources are **not** mixed: a mentions mission must never engage the
  timeline. Specialty filtering is skipped for `mentions` (whoever addresses us
  deserves a reply) but ad/spam and persona-donts filters always apply.
- No usable target ⇒ write `scratch["directed_mission_note"]`; `cycle.py`
  forwards it as `directed_note` so the chat says *why* nothing happened.
- Skills that only react to an owner directive set `always_on = True`, since
  employees created before the skill existed have a stale `installed_skills`
  list in the DB and would ignore the order.
- When no intent matches at all and an employee is pinned, `main.py` falls back
  to `_employee_open_discussion` instead of the "out of scope" bot menu.
- Verify: `.\venv\Scripts\python.exe verify_mission.py` (no network, no LLM).

## A chat order must land on X — never fire-and-forget a cycle
- ALL three chat paths go through `employee_mission.dispatch_directed(cfg,
  payload, expected_caps, label)`. Calling `platform.run_cycle_now` in a bare
  thread loses the order in two silent ways:
  - `status != ACTIVE` ⇒ `RuntimeManager.run_cycle` returns
    `{"skipped": true, "reason": "status=paused"}` and says nothing, while the
    chat already promised «ينفّذها الآن».
  - a cycle already running ⇒ `cycle_already_running`, order dropped. The
    dispatcher now re-tries (8 × 20s) instead of cancelling the owner's order.
- Success is NOT assumed from "the cycle finished": the dispatcher requires a
  `results` entry with one of `expected_caps` **and** `success == True`. On
  failure/rejection/no-target it pushes the reason to chat via
  `platform.notify_owner(...)` (new, wraps `RuntimeManager._emit_progress`).
  Success stays silent because the cycle narrative already carries the URL.
- `EXPECTED_CAPABILITY` maps a mission action to the capability that proves it
  happened; keep it in sync when adding actions.

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
- **Editorial layer** (`_editorial_pick` in `skills/builtin.py`): the
  deterministic candidate ranking (`_grounded_candidates`) only measures keyword
  overlap, so the topic repeating the most specialty words won — not the most
  important/interesting one. One LLM call now reviews the top-8 candidates as a
  chief editor (given persona role/interests + recent posts to avoid repeats)
  and returns `{choice, angle, hook, why}`. The angle/hook go into the brief,
  `why` is streamed to chat («🗞️ اخترت موضوع المنشور كرئيس تحرير…») and stored
  in `scratch["editorial_pick"]`. Any failure/no-LLM/invalid choice falls back
  to the deterministic best — publishing never stops. The brief also carries
  craft rules (hook ≤ 12 words, one idea, explicit stance, discussion-opening
  ending). Covered by section 5 of `verify_mission.py`.
- verify_critique.py's two LLM-path checks call the REAL OpenAI API — they fail
  with 429 `insufficient_quota` when the key has no credits; that is not a code
  regression (deterministic checks still pass).
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
- Measure behaviour instead of arguing about it: `sim_decisions.py` runs the real
  planner + decision engine over N cycles (no network, no LLM) and prints the
  action mix, formats, pillars, per-handle share and consecutive-repeat streaks.
  `--seed` injects one early high-view thread — the exact state that used to
  freeze the account. `measure()` returns the numbers and is asserted by
  `verify_interaction.py`.
- Run one real cycle end-to-end (starts app/x, publishes for real):
  `.\venv\Scripts\python.exe run_live_cycle.py --account <handle> [--force-format thread] [--dry]`
  Use `--dry` first: it sets `daily_post_target=0` so nothing is published.

## Cycle plan and execution consistency
- `DecidePhase` stores `initial_decisions`; it must not announce the final plan.
  `PersonaGuardPhase` can remove or substitute candidates. `ExecutePhase` snapshots
  the post-guard decisions as `execution_plan`, then announces that exact plan.
  A failed plan/decide/guard phase blocks tool execution instead of using stale decisions.
- Each result carries the plan's `action_id`, capability and target. Preserve both
  initial decisions and execution plan in the cycle report and persisted outcome.
  Re-check business limits before each action: earlier successful actions can consume
  the remaining daily budget. `status=skipped` is not a tool failure; evaluation,
  critique, support messages and graph episodes must preserve that distinction.
- `scratch["active_action"]` exists only during one action and is cleared in `finally`.
  Reply/quote generation uses `ExecutionContext.action_prompt()` for that action's
  rationale and target-specific graph facts/interaction signals. Never invent missing
  relationship history or let target memory replace the owner's persona instructions.
- Offline regression coverage: `verify_creator.py` (plan/guard/execution consistency)
  and `verify_interaction.py` (mocked generation and per-target memory isolation).

## Employee-local skill creation and import
- Skill routes require an authenticated owner of the selected employee. Create and
  import are previews; activation saves only that employee's `custom_skills`.
  Preview IDs are unique so two pending previews cannot accidentally update one another.
- Import accepts JSON specs or text/Markdown documents (32 KiB UTF-8 maximum).
  HTTPS fetching validates every redirect and DNS address, pins the public IP while
  preserving TLS hostname verification, and never forwards credentials or executes code.
  Unsupported/ambiguous requirements must be rejected, not replaced with a default reply.
- `skill_readiness()` checks supported capabilities and configured MCPs, not live success.
  A missing tool blocks activation; user persona, guard and execution quotas still apply.
  Interaction instructions travel as `skill_directive`, NOT string `brief` (the reply
  handler interprets `brief` as a boolean short-reply mode).
- Repository saves merge custom-skill changes against the loaded baseline. Preserve this:
  otherwise an in-flight cycle can erase a newly saved skill or resurrect a deleted one.
- Offline checks: `venv\Scripts\python.exe verify_custom_skill.py`.
  Optional UI checks use a separate Vite port and a fresh headless Chrome with mocked APIs:
  call `verify_custom_skill.check_skill_browser('http://127.0.0.1:3017/')` after starting
  `npm run dev -- --host 127.0.0.1 --port 3017 --strictPort` in `frontend`.

## All MCP servers and skills live in `system/` (since 2026-10-07)
- `system/mcps/<slug>/` = every MCP server: the 8 built-ins (`kind: builtin` in mcp.json +
  `server.py`, loaded in-process by `app/aeos/mcp/catalog.py`) and uploaded ones (stdio/HTTP/
  package via `external_mcp.py`). `system/skills/<name>/` = every skill: `kind: coordinator`
  (SKILL.md for the coordinator) and `kind: employee` (SKILL.md + `skill.py`, ordered by `order`).
- **Edit the code in `system/`**, not in `app/aeos/mcp/servers/*_mcp.py` or the skill class names
  in `app/aeos/skills/builtin.py`: those are aliases. `app/core/system_loader.py` loads each file
  once under `moj_system.*`, so old imports get the SAME module/class objects.
- Employee skills call shared helpers through `_h.` (= `app.aeos.skills.builtin`) so patching a
  helper in a test reaches every skill. A new helper goes in builtin.py and is used as `_h.name`.
- The coordinator gets every enabled server with `scope.coordinator` EXCEPT `x` (X actions go
  through employees) and `memory`. `playwright_x` reads need `account` and are checked with
  `label_owner_ids` (real ownership — no admin override).
- Only the `developer` role (`app/auth/roles.py`) may add/edit/delete MCPs and skills.
  Verify: `.\venv\Scripts\python.exe verify_system_admin.py`.
- Second level — **employee-private MCP** (`app/aeos/mcp/employee_mcp.py`): any owner may add
  one to their own employee, but HTTP only and public addresses only (`check_public_url`, re-run
  on every call; `_HttpTransport` never follows redirects). Never relax this to stdio/packages or
  private IPs for owners: registration is open, so that would be remote code execution / SSRF to
  X Suite and loginx. Registry name `ext_e<id>_<slug>`; secrets in `data/employee_mcps/`;
  `AEOSPlatform.delete_employee` purges them. Employee-private skills = `custom_skills`.
- Dashboard «paste curl / Python / CLI / JSON» (`app/aeos/mcp/snippet_import.py`) only PARSES
  (shlex + ast, no exec/eval) into the quick-add form; keep it that way. Doc placeholders
  (`your_..._api_key`, `<KEY>`, `os.environ[...]`) become an empty key + a warning.
- Employees use MCP tools automatically (`system/skills/system_tools` always_on +
  `app/aeos/employee/tool_use.py`): every enabled system server with `scope.employees` is ON for
  every employee unless the owner opts out (`business_rules.tools_opt_out`); tools are classified
  by name/description/schema (search/extract/news), write-like names (delete/post/send…) are never
  auto-called, and calls happen only at write time (posts, quotes; replies if
  `research_before_replies`). 6h query cache; built-in search via Tavily suppresses a duplicate
  Tavily-MCP search.
- Tavily is the ONLY search/trend provider (user decision): built-in `system/mcps/search`
  (`tavily_search()` helper) and the Trend Detector's single `TavilyCollector` (queries = Saudi/Gulf
  news + employees' top interests, `topic=news`, `time_range=day`; landing pages/social hosts
  dropped). Reddit / Google Trends (SerpAPI) / custom X API / TikTok collectors, Google Vision and
  the X validator were removed; `ScoreGate` decides HOT/EARLY from score (multi-outlet stories → HOT).
  Credit cost: `TREND_TAVILY_MAX_QUERIES` (4) every `TREND_TAVILY_INTERVAL` (240 min).
- Adding an MCP must "just work": `system_catalog.autodetect()` (used by test-connection AND install)
  retries the given form with alternatives — same-host `/mcp` (REST `/api` pasted from docs), legacy
  SSE transport (`http_mode: "sse"`, `_SseTransport`), and key placement (Bearer / bare
  Authorization / X-API-Key / query) — and returns `adjusted` + the corrected form to the UI. A real
  MCP reply (401/0 tools) pins the URL; only URL/transport errors move to the next URL. Never treat
  "any JSON" as success: non-JSON-RPC, missing protocolVersion/capabilities, 0 tools and OAuth-only
  (401 + `resource_metadata`) all raise explicit messages. SSE reader uses `raw.readline`
  (`iter_lines` buffers 512 bytes and swallowed the `endpoint` event).
- `verify_tools.py` needs the official SDK in a separate env at
  `%LOCALAPPDATA%\Moj-Agentic-AI\mcp_testenv` (`pip install mcp==2.2.0`). Not `%TEMP%`: Windows
  cleanup half-deleted it once and the test failed with `cannot import name 'MCPServer'`.
