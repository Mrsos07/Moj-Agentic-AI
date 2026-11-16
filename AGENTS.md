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

## Security rules (review 2026-11-08)
- X session cookies were tracked in Git despite `.gitignore`: 6 JSON files with `auth_token`/`ct0` in
  `app/x/loginx/CookiesBackup/` and `app/x/loginx2026/cookies/`, plus `debug_post.html`, which held
  `ct0` in `document.cookie`. They were tracked BEFORE the ignore rule, so the old staged-diff check
  never saw them. They are now untracked (local copies kept). `publish.ps1` scans the WHOLE tree
  (`git ls-tree` paths + `git grep` for key/`auth_token`/`ct0` values and cookie-export JSON shape)
  before every push. Deleting a forbidden file is allowed; adding one is not. The history still has them
  until the owner approves a rewrite.
- Auth has ONE gate: `app/auth/token_guard.authenticate(token, db=None)` checks signature+expiry,
  then revocation, then that the user exists and is active, then that the token was issued after the
  user's `revoke_user` cutoff. `middleware`, `dependencies`, `ownership.user_from_token` (WebSocket)
  and `/verify` all call it. Never decode a JWT for auth anywhere else.
  - Revocations are stored as the sha256 of the token, in Redis AND in `data/auth_revocations.json`.
    A Redis outage never turns logout into a fake success; if the local write fails, logout returns 503.
  - `revoke_user` runs on password change, deactivation and deletion.
  - Tokens carry `jti`: two same-second tokens used to be byte-identical.
- The chat WebSocket re-authenticates on EVERY message and every 60 s while idle. On failure it sends
  `auth_expired` and closes with 4401.
- Internal callbacks (`app/auth/internal.py` v2) use a key domain-separated from the JWT secret
  (`INTERNAL_CALLBACK_SECRET` overrides it). `exp` and `nonce` are inside the HMAC; a nonce is consumed
  once, persisted in `data/internal_callback_nonces.json`. Login TTL is 30 min; schedule TTL is
  run_at + 1 day. Tests: section 4/4c of `verify_isolation.py`, which isolates those files in a temp dir.

## Production (Docker) — re-verified 2026-11-10 from a clean clone (all 5 services healthy)
- The server install guide is `دليل_الدوكر.txt` (Ubuntu 26.04 via Docker's official apt repo — verified in a clean ubuntu:26.04 container: Docker 29.9.3, Compose v5.7.1; DNS, ufw, .env with `openssl` secrets, first
  admin, update, backup/restore, troubleshooting). Keep it in sync when deployment changes.
- `cp .env.docker.example .env` (UTF-8), fill the keys + `NEO4J_PASSWORD` + `DOMAIN`, then `docker compose up -d --build`.
  Services: `caddy` (the ONLY public ports, 80/443; automatic Let's Encrypt when `DOMAIN` is set, plain
  HTTP on `:80` when it is empty), `backend` (ONE process: `python run.py` with RELOAD=0, BIND_HOST=0.0.0.0; it spawns
  xsuite 5789, loginx 5000 and identity 5990 inside the container), `frontend` (Nginx: serves
  dist and proxies /api, /ws, /static, /health), `neo4j:5.26-community` and `redis:7.4-alpine`.
  backend and frontend are published on 127.0.0.1 only (diagnostics). Never scale the backend (>1 replica).
- First admin / any account: `docker compose exec backend python scripts/create_user.py --email … [--role admin] [--reset]`.
  The template sets `ALLOW_REGISTRATION=false`: open signup let anyone spend the system LLM key, and
  there is no API that creates users. The script validates the email with `EmailStr` (a `*.local`
  admin could never log in), and `--reset` calls `revoke_user`.
- Client IP behind the proxies: uvicorn saw nginx's container IP for EVERY user, so the per-IP login limit
  (20/15 min) would have locked everyone out together. Now Caddy sets XFF, nginx trusts it only from
  private ranges (`set_real_ip_from` + `real_ip_recursive`) and forwards ONE address
  (`X-Forwarded-For $remote_addr`, never `$proxy_add_x_forwarded_for`, which the client can prepend
  to), and the backend sets `FORWARDED_ALLOW_IPS=*`. With `*`, uvicorn takes the first XFF entry.
  Verified live: a forged `X-Forwarded-For: 6.6.6.6` is ignored.
- Gotchas found by the 2026-11-10 run (all locked in section 7 of `verify_security.py`):
  - An empty `DOMAIN` reached Caddy as `""`, and the Caddyfile default didn't apply («unrecognized global
    option: encode»). The default now lives in compose: `${DOMAIN:-:80}`.
  - nginx drops server-level `add_header` in any location that has its own `add_header`, so UI pages had
    no X-Frame-Options. The headers are now repeated in the locations, and Caddy also sets them plus HSTS.
  - A single-file bind mount keeps the OLD inode after `git pull` replaces the file, and without `--watch`
    Caddy never reloads. Caddy now mounts `./deploy` and runs with `--watch`.
  - `backups/` was in neither `.gitignore` nor `.dockerignore`, so `--build` would have copied cookies and
    secrets into the image.
  - `*.sh` must be LF (`.gitattributes`). The scripts handle Git Bash path conversion (`cygpath`/`MSYS_NO_PATHCONV`).
- Backup/restore: `scripts/backup.sh [dir] [--with-neo4j]` takes an SQLite online backup, tars the
  backend volumes via `--volumes-from`, and copies `.env`. `scripts/restore.sh <dir>` asks for confirmation
  and replaces everything. Both were verified live: a user created after the backup is gone after the
  restore, and login still works.
- Identity (5990) is skipped without `GEMINI_API_KEY`, by design. With any key it boots (verified, HTTP 200).
- Gotchas found by the 2026-11-09 smoke run:
  - The old healthcheck hit `/api/health`, which doesn't exist; it is `/health`.
  - `.dockerignore` excluded `*.md`, which dropped every `system/skills/*/SKILL.md`, so the image had no skills.
  - `requirements.txt` listed `uiautomator2` twice (3.7.0 and 3.7.1), and pip failed with ResolutionImpossible.
  - Copying from Windows/OneDrive gives read-only dirs, so loginx died on `PermissionError` until `chmod -R u+w`.
  - `neo4j_service._port_open` probed 127.0.0.1 only. It now uses the host from `NEO4J_URI`.
- Volumes: `moj_data` (DB, employees, revocations, secrets), `moj_cookies` (X sessions),
  identity images and uploads. Runtime-added MCP manifests under `system/mcps` are NOT in a
  volume; they live in the image layer, so re-add them after a rebuild or bake them into git.

## Pixabay + stock_media
- `system/mcps/pixabay`: in-process Python port of Unlock-MCP/pixabay-mcp-server (MIT, see
  `UPSTREAM.md`). Same 4 tools. Results are cached 24 h (Pixabay terms). It needs `PIXABAY_API_KEY`,
  which is scrubbed from errors.
- `stock_media` (always-on employee skill + `app/aeos/employee/stock_media.py`) runs at publish time
  (post + thread), BEFORE `_maybe_generate_image`:
  1. The LLM writes English literal-scene queries and picks image/video, plus things to avoid.
  2. Pixabay search: horizontal, safesearch; video ≤ 60 s and ≤ 40 MB, otherwise it falls back to an image.
  3. Selection (hardened after a live test on 2026-11-09):
     - The news post «اعتراض مسيّرة حوثية» first got a «ufo, intercept, science fiction» image,
       because the tag `intercept` matched. An opinion post got a chalkboard. «oil prices chart» returned forex graphs.
     - Now `_FAKE_TAGS` drops fantasy/concept/illustration tags deterministically. The LLM scores
       candidates 0-10, and only scores ≥ 7 survive.
     - Then `_vision_ok` (system model, `detail: low`) LOOKS at the preview of the best two. If vision
       is unavailable, a pick needs a score ≥ 8.
     - The plan prompt says: skip unless the post is about something physical, and query the object
       itself, never a chart or concept.
     - Live results: the oil post got an oil rig / petrol station image; fireworks got a 2 MB video;
       the drone interception got nothing, because the military stock looked like another country's
       (that is correct: it falls back to a generated image).
  4. The same file is not reused per employee for 30 days.
  5. Video as well as images (2026-11-16, owner request). Before this, stock media was always an image:
     the planner only said `video` for "kinetic" scenes, and the search used ONE kind, so a video was never
     ranked against images.
     - In `auto`, `_collect` interleaves up to 6 videos and 6 images into ONE ranking.
     - `_VIDEO_BONUS` (1.0) is added for ranking only: a video wins ties and a one-point gap. The ≥7
       acceptance threshold is on the raw score.
     - Vision checks the top 2, then the best candidate of the other kind. Before, two rejected videos left
       the post with no media even when a matching image was behind them.
     - A video frame is judged as "one frame of a clip" (the same kind of scene, not every detail of the
       post). A strong (≥9) video that fails gets ONE re-check, because verdicts on the same frame were
       measured flipping True/False/True/True.
     - `scratch["stock_media"]["checked"]` logs `kind:id:score:vision` for every candidate tried.
     - `stock_media_kind=image` never searches videos. `video` means videos first, with images as a fallback.
     - Live runs vary run to run because the vision verdict is non-deterministic. That is the safety gate
       (a wrong video is worse than none), so don't loosen it to force videos.
     - Test: section 4 of `verify_cycle_quality.py`.
  Policy is `business_rules.stock_media` = `auto` (default: only when the source has no media) | `prefer` | `off`,
  and `stock_media_kind` = `auto` | `image` | `video`. Test: section 5g7 of `verify_creator.py`.

## Environment
- The working virtualenv is `venv\` (NOT `.venv\`, which is missing most deps).
  Use `.\venv\Scripts\python.exe` for everything.
- Windows console is cp1252 by default; set `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8`
  before running scripts that print Arabic, otherwise they crash with a
  `charmap` codec error.
- `.env` must be UTF-8. Saved as UTF-16 (BOM `FF FE`, e.g. by PowerShell `>`/Notepad) it
  crashes `load_dotenv` in `app/main.py` with `UnicodeDecodeError ... 0xff` and the runner
  restarts the worker forever.
- Local services on a fresh machine:
  - **Neo4j** (graph memory): `.\venv\Scripts\python.exe scripts\install_neo4j.py` — installs
    pinned JDK 21 + Neo4j 5.26 LTS into `%LOCALAPPDATA%\Moj-Agentic-AI` (SHA-256 checked), sets
    the initial password and verifies bolt. After that `neo4j_service.ensure_neo4j()` autostarts it.
    Without it: `Neo4j autostart: install missing` and the employee has no emotional state.
  - **Redis** (session cache, optional): Docker container
    `docker run -d --name moj-redis --restart unless-stopped -p 127.0.0.1:6379:6379 -v moj-redis-data:/data redis:7.4.10-alpine redis-server --appendonly yes`.
    It only runs while Docker Desktop runs. `RedisClient` caches a failed connect for the
    whole process, so start Redis before `run.py`, or restart the app.
  - **loginx** needs `uiautomator2` (in requirements.txt). Without it the server dies at startup with `ModuleNotFoundError`.
- X reads (timeline/trends/search/profile) must wait via `playwright_x/server.py::_wait_cards`,
  never a bare `locator.wait_for`. Through the residential proxy X sometimes fails to load its
  vendor JS (`?failedScript=vendor`) and shows «Something went wrong, but don't fret… Try again».
  Waiting never fixes it; clicking Try again (≤3) does. Measured 2026-10-26: one whole cycle did
  nothing because timeline, trends and specialty search each burned 45 s on that screen. The
  account itself was fine (HomeTimeline 200, rate limit 499/500). Test: section 5g2 of `verify_creator.py`.
- Reply composer (`x_actions._reply_scope` / `_open_reply_composer`): a tweet page has TWO
  `tweetTextarea_0` (inline `inline_reply_offscreen` + the reply modal). The modal's mask covers
  the page immediately, but the `[aria-modal]` dialog stays 0 px high until its content loads (slow
  over the proxy). So never require the dialog to be `is_visible()`: a dialog that contains the editor
  IS the scope, and a visible mask with no editor-dialog means "wait", never "use the page".
  Measured 2026-10-28: the 800 ms settle wasn't enough and replies failed with a mask-intercepted
  click. Test: section 5g3 of `verify_creator.py`. Verified live with a real posted reply.
- X length limit: `app/x/modules/x_text.py` (`weighted_len` per twitter-text v3: URLs 23, emoji/CJK 2,
  Arabic 1; `fit` cuts at a sentence end). Over 280 (no Premium) X DISABLES the Reply/Post button,
  so nothing is ever sent. Measured 2026-10-28: a 3-point reply was 13 over, failing with «بقي
  المحرّر ممتلئاً». Replies had no length cap at all. Now: a hard ≤240 line in the reply prompt,
  `fit(…, 270)` before sending (10 margin: our count said 288 where X said 293), and
  `x_actions.reply_to_tweet` fails fast with a clear message if text over the limit reaches it.
  `_normalize_post_text` uses the same `fit`. Test: section 5g4 of `verify_creator.py`.
- The owner can rename the X handle at any time. Measured 2026-11-08: nxjrjrjdj became nxjrjrjdj2 and
  `x.com/nxjrjrjdj` showed «هذا الحساب غير موجود», so the identity refresh timed out waiting for
  `UserName`. Readers of the account's OWN page (`_read_account_profile`, `_read_my_posts`) go
  through `_goto_own_profile`, which reads the real handle from `AppTabBar_Profile_Link` and
  re-navigates. The snapshot stores the current `handle` plus `handle_changed_from`.
  `cfg.account_username` is NOT auto-renamed (chat mentions/ledger key on it). Test: section 5g5 of `verify_creator.py`.
- Proxy failover (since 2026-10-28): a residential exit node can die mid-session. Seen live:
  `ERR_PROXY_CONNECTION_FAILED` / `ERR_TIMED_OUT` on every proxied read for ~15 min while direct
  sources worked. `captcha_browser.goto` classifies route errors (`proxy_rotation.ROUTE_ERRORS`, or
  a `Page.goto` timeout) on a rotated context (`ctx._moj_proxy_scope`). It then calls `mark_bad(scope)`,
  so the rest of the window uses a NEW Saudi session. On the page's FIRST navigation (nothing done on X yet)
  it raises `ProxyRouteError`, and `with_proxy_failover` retries the whole operation once. That wrapper is
  applied in `sync_bridge.sync_safe` (all X actions) and `@_failover` on the 6 playwright_x readers.
  A later-navigation error is NOT retried (it could duplicate an action), only rotated.
  `proxy_rotation` must exist as ONE module instance (it's registered under both names), otherwise
  the rotation state splits in two. `PROXY_CITY` is empty = all of Saudi Arabia (owner decision; the
  Riyadh-only pool kept dropping). Test: section 6 of `verify_proxy_rotation.py`.
- Port 8000 can be taken by another desktop app (measured: Beem, an Electron app with `--inspect=8000`).
  The bind then fails with `WinError 10013`, and the listener is invisible to `netstat`
  (psutil sees it, status `NONE`). `run.py::_free_port` uses psutil and kills only `python*`
  leftovers. Any other owner gets a clear message and exit 1. Never auto-kill another app.

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
- **Picking any OpenRouter model from the chat** (since 2026-10-28). `llm_catalog.openrouter_catalog()`
  reads OpenRouter's own public `GET /models` (no key, cached 1 h) and keeps ONLY chat models
  whose `supported_parameters` include `tools`, because the coordinator manages employees through
  tool calls (owner decision; 390 of 456 models). Each item has name, $/1M in/out, context and free.
  Admin/developer (`llm.system_models` permission) may pick any of them on the SYSTEM key
  (`via_system`, `_system_openrouter()`); a normal user needs their own key, so nobody drains the
  system credit with an expensive model. `set_selection` rejects ids not in that list. Ways to change it:
  the chat header `ChatModelPicker` (search box + prices), `GET /api/llm/openrouter/models`, and the
  coordinator tool `chat_model` (current/search/set/reset; `set` needs a full id from `search`).
  The change applies from the next message. Test: section 6b of `verify_llm_providers.py`.
- **Per-employee model** (employee card «🧠 نموذج الذكاء الاصطناعي», `GET/PUT /api/aeos/employees/{id}/llm`,
  or the coordinator: `chat_model` with an `employee` arg). Stored in `business_rules.llm` (existing JSON
  column, no migration). `bootstrap` merges `business_rules`, so saving the card doesn't wipe it.
  `runtime/manager.py` sets `LLM_OWNER = (user_id, "employee", business_rules.llm)`, and
  `endpoint_for(user, purpose, override)` gives it priority over the owner's general «employee»
  model, validated with the OWNER's keys/permission (a deleted key or revoked permission means it's ignored).
  Resolution order: employee choice → owner's employee model → system. Test: section 6c of `verify_llm_providers.py`.
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
- Directed publishing ("وجّه الموظفين للنشر عن X") is never blind: `order_publish` (and
  `employee_mission.dispatch_directed` for pinned/mission paths, via `media_brief.attach`) first
  builds a **media brief** (`app/aeos/services/media_brief.py`): Tavily `advanced` news search
  (day → widen to week), then an editor LLM writes headline/summary/latest/facts/numbers/
  statements (each with source [n]), angles and `do_not_claim` from the sources ONLY. Cached
  30 min per (user, topic) so N employees = one search; each employee gets a different angle
  (`assign_angle`). The skill writes from `media_brief` (no shallow research). The quality gate's
  echo check uses `echo_ref` = instructions only — comparing against the full brief (with
  research/facts) flagged faithful fact-quoting as `echo_brief`. Test: `verify_media_brief.py`.
- **Campaigns** (`plan_campaign` coordinator tool, prompt rule 5ب) are for any direct "make the team
  post about X" task:
  1. One media brief.
  2. `_propose_assignments`: the LLM, acting as editor-in-chief, gives each employee a DIFFERENT
     sub-topic, angle, hook and format (post/thread) based on role and interests. An employee far from
     the topic gets `skip`, and unknown handles are dropped. Without an LLM it falls back to rotating the angles.
  3. With `dispatch=false` (the default) the coordinator shows the brief and the proposals and asks «أوجّههم؟».
  4. `dispatch=true` (or `only=[…]`) reuses the SAME cached plan (30 min, no new search) and dispatches
     with `for_employee(brief, angle_text=…)`.
  `order_publish` stays for a single employee. When there is no `TAVILY_API_KEY`, `_research` falls
  back to Google News RSS (`when:2d`, then `7d`). The key IS set in `.env`, and the server loads it in
  `app/main.py`. Ad-hoc test scripts must `load_dotenv('.env')` first, or they wrongly report
  "not configured". NEVER print `search_provider()`: it returns the raw key. Test: the campaign section of `verify_media_brief.py`.
- Post images go through `app/aeos/employee/visual_director.py` (NOT a raw prompt): an LLM art
  director turns the post into a concrete, literal news scene (subject/setting/composition/
  lighting/camera/palette/mood) with banned clichés (chess, crowns, maps, flags…), Gulf cultural
  accuracy, anonymous people only, and a memory of recent concepts; then a vision review
  (system model, `detail: low`) — retry ONCE only for a real defect (severe, or score < 6), and
  publish text-only after two severe failures. Measured before: 4/4 images were chess pieces,
  square, same grey palette, a cross on the king in Saudi/Yemen content. Default output is
  16:9 `quality: high` (~70-90 s, ~$0.13/image on OpenRouter gpt-image-2); explicit `size`
  (avatar 1024x1024) keeps its ratio. `image_review: false` disables the review. Test:
  `verify_visual.py`.
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
- **Scope every button to the target.** On a `/status/` page the parent tweet is
  rendered ABOVE a reply: use `x_actions.focal_tweet()` (the article whose time
  link ends in `/status/<id>`), never `page.locator(...).first`. On a profile the
  sidebar «Who to follow» always has ~3 `-follow` buttons (measured): use
  `[data-testid="primaryColumn"] [data-testid="placementTracking"]` — the old
  unscoped `follow_user` could follow a suggested account instead.
- Undo actions are idempotent and verified: `undo_like/undo_repost/undo_bookmark/
  unfollow/follow` return `"done"` (state flipped) or `"already"` (nothing to undo);
  the old ones clicked `like`/`bookmark` when `unlike`/`removeBookmark` was absent,
  i.e. they LIKED/BOOKMARKED instead of undoing. The API returns `already` so the
  owner is told «لم يكن قائماً» instead of a fake success.
- Delete (`x_delete.py`) = focal tweet + `expected_handle` ownership check + the
  exact «Delete/حذف» menuitem + `DeleteTweet` GraphQL 200 without `errors`
  (fallback: tweet gone after reload). `/api/delete-tweet` used to ignore the
  `False` return and answer «تم الحذف ✅» for a tweet it never found.

## Owner-driven X account management from the coordinator (`x_account`)
- `app/aeos/services/x_control.py`, coordinator/employee tool `x_account`:
  list_posts (from the cycle log; `live=true` reads X), delete_post, unlike,
  unretweet, unbookmark, like, retweet, bookmark, follow, unfollow,
  update_profile, list_scheduled, cancel_scheduled. Runs directly on the `x` MCP
  with the employee's cookie (no cycle/LLM), in the background; the verified
  result is pushed via `notify_owner`.
- Irreversible actions (delete_post, unfollow, update_profile, cancel_scheduled)
  are two-phase: preview + `confirmation_id`, executable only from a LATER owner
  message (`_Tools.turn`), same user/employee/action. Tool results are not in
  chat history, so pending previews are injected into the next system prompt
  (`pending_brief`), and the next message either confirms or drops them
  (`drop_stale`). While a preview is pending, main-chat messages route to the
  coordinator (`has_pending_x`), as do delete/undo messages (`is_x_management`)
  — the legacy X agent deleted immediately with no ownership check.
- Deleted ids → `flags.deleted_posts` (hidden from listings, «آخر» moves on);
  undone targets → `flags.engaged_urls` so cycles don't redo them.
  Test: `verify_x_control.py` (80 checks, offline). Live checks so far were
  read-only («already» paths + delete refusal on another account's tweet).

## Profile identity (avatar/header): what the owner approves is exactly what lands on X
- Reported: the card «هوية X من الشخصية» previewed a portrait + header, the owner pressed
  «تطبيق على X», and the account showed a different picture. Measured on @nxjrjrjdj
  (2026-10-22, app/x `operations` + chat log): (a) creating the employee ran
  `apply_persona_identity_async`, which generated its OWN avatar (image MCP) and uploaded it
  unseen (ops 1110→1111, 14:10, «وصورة شخصية جديدة مولّدة»); (b) the approved apply (op 1113,
  14:18:48) was a bare daemon thread — the `git pull` into the running copy at 14:19:21 reloaded
  uvicorn and killed it: no result, no message, the card had already closed. Same class: chat
  «ولّد هوية» → «طبّق الهوية» generated a NEW identity at apply time.
- On create only persona name+bio are synced — **never an image** — and nothing at all if the
  owner already has an approved identity (`identity_store.has_owner_identity`).
- Approval pins bytes (`identity_store.create_pending`): the previewed files are copied from the
  identity server's `static/generated/` (URL must be `/static/generated/<32hex>.<ext>` — no network
  fetch, foreign/internal URLs rejected) into `data/employees/<id>/identity/` with sha256, plus
  `pending.json`. The apply uploads those bytes as multipart (`x_bridge.update_profile(avatar_path=…)`
  → `avatar_file`), never a URL. The identity server deletes previews after 1h → approval fails with
  «أعد توليد المعاينة» (HTTP 400; the card keeps the preview) instead of applying something else.
- `identity_service.resume_pending()` (main.py startup) resumes an interrupted approval with the same
  pinned files (≤3 attempts, ≤24h, deleted employee ⇒ dropped); a newer approval supersedes an older
  one (`is_current`). Publishing into the copy the app runs from ALWAYS kills in-flight background
  work (op 1112, an X thread post, died the same way at the 14:14:30 pull) — anything the owner
  approved must be persisted.
- Chat: persona-based «ولّد هوية للموظف» uses `generate_preview` (persona-first name/bio, same as the
  card) and remembers it; «طبّق الهوية» (`apply_from_chat`) applies that preview (same owner, ≤1h) and
  consumes it; it generates only when nothing was shown.
- `x_profile.update_profile_on_x`: `_handle_crop_if_any` polls for the crop dialog and never clicks
  `Profile_Save_Button` (a late dialog made the name list reach «حفظ» = save before the image was
  applied); after Save it waits for X's `account/update_profile_image|banner` responses (or the modal
  closing + 20s) instead of navigating after a fixed 2.5s (which aborts slow uploads through the
  proxy while name/bio still verify); returns `media: {avatar|banner: confirmed|unconfirmed}` and
  raises on an HTTP error from those endpoints. The chat says «أكّد X» only for `confirmed`. Endpoint
  names come from the web client (rettiwt-api calls `…/i/api/1.1/account/update_profile_banner.json`)
  and were **observed live** on 2026-10-22 (op 1137 on @nxjrjrjdj: `media: {avatar: confirmed,
  banner: confirmed}`); the images on the profile page then matched the pinned approved files at
  perceptual-hash distance 1/256 (unrelated images ≈115). If X renames them, the result degrades to
  «unconfirmed», never to a false failure.
- The employee card mirrors the account as X shows it (`x_profile.json` in the same folder, exposed as
  `agent.x_profile` by `_agent_view`): banner, avatar overlapping it, name, @handle, bio, last sync.
  Sources: (1) every metrics read — `read_my_posts` already opens `x.com/<handle>`, so it fills
  `header_out` at no extra navigation and `ReadOwnMetricsCapability` saves it; (2) the card's
  «تحديث من X» → `POST /employees/{id}/x-profile/refresh` (`read_account_profile`, `_browser_slot(25)`
  fail-fast); (3) right after an approved apply (the frontend polls `GET …/x-profile` until
  `fetched_at` changes). After the apply, a changed avatar/banner URL on the profile page is a second,
  independent confirmation; an unchanged URL is reported as a warning, never as ✓.
- Header extraction (`_HEADER_JS`): name/bio by the proven `UserName`/`UserDescription` testids, avatar
  via the `/<handle>/photo` link (fallback `UserAvatar-Container-<handle>`; tweets link
  `/status/<id>/photo/1`, so other users' avatars never match), banner via `/<handle>/header_photo`
  (only the account's banner uses `profile_banners` on its own page). Text is built from child nodes
  with `img[alt]`: X draws emoji as images, and `innerText` dropped the 🇸🇦 from «صقر الجزيرة 🇸🇦»
  (measured live). `account_header` keeps only `pbs.twimg.com`/`abs.twimg.com` URLs and upsizes them
  (`_400x400`, `/1500x500`). A read with neither name nor avatar never overwrites a good snapshot.
- Test: `.\venv\Scripts\python.exe verify_identity.py` (offline, temp dirs, fake page/clock, plus
  `_HEADER_JS` executed in a real headless Chrome on an X-shaped page with all network aborted).

## Captcha is solved by a provider — never by a human, never silently
- One solver: `app/core/captcha_solver.py` (stdlib only) for four providers
  (YesCaptcha / 2Captcha / CapMonster / Anti-Captcha). Keys live in `.env`
  (`YESCAPTCHA_KEY` … or a generic `CAPTCHA_KEY`); `CAPTCHA_PROVIDER` picks one,
  otherwise the first key found. `GET /api/x/captcha-status` reports
  provider/balance and only the last 4 characters of the key.
- It is loaded **by path** from every process that needs it: xsuite runs with
  `cwd=app/x`, `x_auth` runs as `python -m x_auth.login_sync`, loginx runs with
  `cwd=app/x/loginx` — none can import `app`. Hence the module has no app imports,
  and `captcha_bridge.py` sits next to every copy of `x_auth` (4 copies; 3 are
  byte-identical).
- Playwright paths call `guard_page()` (through `app/x/modules/x_captcha.py`)
  right after `goto` and again after submit: `x_post` (post/thread), `x_actions`
  (like/repost/bookmark/reply/quote/follow/unfollow), `x_delete`, `x_profile`,
  and every read in `system/mcps/playwright_x/server.py`. The guard **raises**
  when it cannot solve — a swallowed captcha is a phantom success (see
  «Never report success…»), and on `x_post` it used to consume `posts_today`
  while nothing was published. Reads are the same trap in reverse: a captcha page
  has no tweet cards, so the employee used to «observe» an empty timeline.
- Captcha solving adds up to `CAPTCHA_TIMEOUT` (150s) to a login, so the login
  timeouts are `max(120, CAPTCHA_TIMEOUT + 120)`: `x_login._LOGIN_TIMEOUT` (the old
  `loginx2026`/`x_login_new` copies were removed as unused on 2026-11-10). A fixed 120s
  kills the process mid-solve and reports a timeout that looks like a wrong password.
- `x_bridge._POST_TIMEOUT` (`CAPTCHA_TIMEOUT + 120`) applies to `/api/post*` only:
  the guard also runs **after** submit, and a solved captcha must not outlive the
  client timeout — a late success reported as failure is the mirror image of the
  phantom success above.
- API login returns `subtask_id: "ArkoseLogin"`. The token is submitted as
  `web_modal.completion_deeplink = "twitter://onboarding/web_modal/next_link?access_token=<token>"`
  (shape taken from the twscrape/tweety implementations, not guessed). X's login
  Arkose public key is `0152B4EB-D2DC-460A-89A1-629838B529C9` (signup:
  `2CB16598-CB82-4CF7-B332-5990DB66F3AB`); the `blob` is optional — take it from
  the subtask if X sends it. `captcha_solver.solve_x_login_captcha()` is the one
  entry point.
- YesCaptcha's changelog (2026-10-16) says the FunCaptcha **token** routes are not
  stable (`FunCaptchaTaskProxyless` fails and is refunded; `…M1` is the current
  one). So the order is: token first (2Captcha/CapMonster/Anti-Captcha often
  succeed), then `FunCaptchaClassification` (3 points, synchronous) + clicking the
  tile in the page. The emulator has no JS injection, so it only uses the
  classification + tap path (`j4.solve_captcha`).
- Measured with the owner's key (2026-10-21): `getBalance` 46,958 points;
  `ImageToTextTaskOcrBase` returned `ABCD5` exactly; `FunCaptchaClassification` on
  the docs' sample (`q62`, an **arrows** puzzle) returned `objects: [1]`.
- Arrows puzzles are therefore **skipped, not tapped**: the classification answers
  «which image», not «which cell», and the docs describe switching with the arrows
  — a 3×2 tile tap would be a wrong click that burns attempts. Only `Pick the …`
  6-tile grids use the tap path. Don't «fix» this by guessing an arrow count.
- **Cloudflare is a different animal** («Just a moment…» / «لحظة…» / «Performing
  security verification», Ray ID). Measured on 2026-10-21 with @emaad5079's valid
  cookies: `x.com/home` redirects to `/account/access` and stays challenged for 30s
  in both headless and visible Chrome, even with `--disable-blink-features=
  AutomationControlled` and `navigator.webdriver` hidden — i.e. it is IP/egress
  level, not a widget we can inject into. `detect()` now returns
  `kind: "cloudflare"`; the guard raises a message naming `CAPTCHA_PROXY` instead of
  silently passing (it used to return `None` and the caller then «observed» an empty
  timeline).
- Cloudflare solving is `CloudFlareTaskS3` (20 points) and **requires a public
  proxy** (`CAPTCHA_PROXY`); local/private addresses are rejected by the provider.
  `cf_clearance` is bound to the exit IP **and** the User-Agent, and YesCaptcha
  passes the check with desktop Chrome on **Linux**. So the returned cookies only
  help a context launched with the returned `user_agent` (and the same proxy).
- Pre-solving is implemented: `captcha_browser.new_context()` (wired in `x_post`,
  `x_actions`, `x_delete`, `x_follow`, `x_unfollow`, `x_profile`, `x_bookmark`, and
  the `playwright_x` reads) solves `CloudFlareTaskS3` **before** creating the
  context when `CAPTCHA_PROXY` is set, then creates it with the returned UA and
  injects `cf_clearance`; results are cached 45 min (`_CLEARANCE_CACHE`). The proxy
  must be **global at launch** (`launch_proxy_kwargs()`), not per-context:
  Playwright refuses a per-context proxy without a global one, and `cf_clearance`
  is IP-bound anyway — so every request has to leave through it.
  `CAPTCHA_CLOUDFLARE_PRESOLVE=0` disables the pre-solve.
  **Only an explicit `CAPTCHA_PROXY` (or `proxy=` argument) is pre-solved — never a
  rotation session** (since 2026-10-25). The rotation proxy used to flow into the same
  variable, so with a captcha key every new context paid 20 points and got YesCaptcha's
  **Linux** UA on a **Windows** Chrome (UA says Linux, `navigator.platform` and
  `sec-ch-ua-platform` say Windows — a detection signal on every page), while the Saudi
  exit shows no challenge at all. `verify_stealth.py` section 6 asserts both sides.
- Measured 2026-10-21 on @emaad5079 (valid `auth_token`+`ct0`): the real blocker was
  **not** a captcha. `api.x.com` GraphQL returned code 326 «this account is
  temporarily locked … log in to https://twitter.com to unlock», and every browser
  request bounced to `/account/access` (which Cloudflare fronts). So `detect()`
  reports `kind: "account_locked"` and the guard raises «الحساب مقفل مؤقتاً … افتحه
  من /account/access» before any paid call. **No captcha provider can unlock an
  account** — only the owner can, by logging in (which is where Arkose solving
  applies, if X asks for it).
- The image/OCR path (`solve_image`) is for X's own text captcha; the emulator
  cannot crop it from the WebView, so it only handles the Arkose grid.
- Never log a captcha key. Errors keep the provider code (`ERROR_…`, `HTTP_401`) so
  «wrong key» stays distinguishable from «wrong wiring».
- Verify: `.\venv\Scripts\python.exe verify_captcha.py` (offline, fake provider
  servers; `--live` checks balance + one OCR with a real key).

## IP rotation: one **verified Saudi** exit per employee session
- `app/core/proxy_rotation.py` — stdlib only, loaded **by path** from every process
  that needs it (same reason as `captcha_solver`: xsuite runs with `cwd=app/x`,
  `x_auth` as `python -m x_auth.login_sync`, loginx with `cwd=app/x/loginx`).
- Providers: `oxylabs` (default), `brightdata`, `decodo`, `iproyal`, `soax`, and
  `generic` via `PROXY_USERNAME_TEMPLATE`. The geo/session suffixes are **ours**,
  not the customer's: `PROXY_USERNAME` is the base only. Exact shapes are asserted
  in `verify_proxy_rotation.py` — change a template there first, then in code.
  Oxylabs uses **`cc-SA`** (not `country-sa`) and `sessid-…`, with a 10-minute
  default session, so a longer `PROXY_SESSION_TTL` must append `-sesstime-<min>`
  (max 1440) — otherwise the exit IP changes mid-session and `cf_clearance` dies.
  Bright Data/Decodo use `country-`/`session-`; IPRoyal uses underscores and
  `lifetime-`; SOAX uses `region-`/`sessionid-`.
- `PROXY_ROTATE=session` (default) derives the session id from
  `sha1(scope:bucket)` where `bucket = epoch // PROXY_SESSION_TTL`; `request` adds a
  `time_ns` **and a counter** (measured: `time_ns` alone repeated on Windows and two
  «different» sessions came out identical). Ids are 13 chars because Bright Data /
  Oxylabs cap session ids at 16.
- **The Saudi guarantee is runtime, not marketing**: `acquire()` sends a geo query
  *through the proxy itself* and rejects anything whose `countryCode != SA`, rotating
  to a new session up to `PROXY_MAX_TRIES`, then raising `NOT_SAUDI`. Never «trust»
  `PROXY_COUNTRY` alone; provider pools occasionally answer from a neighbour country.
  Results are cached for `PROXY_VERIFY_TTL` (15 min) so a cycle does not pay a geo
  call per action.
- Playwright needs a **global** proxy before a context may override it, so
  `launch_proxy_kwargs()` returns the placeholder `{"server": "http://per-context"}`
  when rotation is on, and `new_context(scope=...)` sets the session proxy. `scope`
  defaults to the cookie file stem = the account/employee. If the proxy cannot be
  acquired, `new_context` **raises** — running without it would leak the real IP and
  defeat the whole feature.
- The emulator path is different: Android's global proxy
  (`j4.set_android_proxy`) has no username/password fields, so it only works with a
  provider that whitelists our public IP; auth-based proxies are browser-only.
- Operational: rotating an account's IP on every request is *worse* for X trust than
  a stable IP per employee. Start at `PROXY_SESSION_TTL=900` and raise it if
  challenges appear. Per-account scope is the account label, so two accounts of the
  same employee get different IPs — that is intended.
- Measured live with the owner's Oxylabs sub-user (2026-10-22):
  `resi_xxx-cc-SA-city-riyadh-sessid-…` returns **407**; the same string with a
  **`customer-` prefix** returns SA/Riyadh (Saudinet/STC, then Zain). The code adds
  the prefix when missing. Two more real defects found the same way:
  (a) a password containing `+` is built as `%2B` in the URL and **must be
  unquoted** before handing it to Playwright, or every request is 407 — this bit
  both `proxy_rotation.playwright_proxy` and `captcha_browser._proxy_dict`;
  (b) Playwright's default headless UA contains `HeadlessChrome`, and **x.com
  answers 403** to it (same context with a real Chrome UA: 307 → login page).
  That UA is now set browser-wide from the real Chrome version (see «The X browser is
  Patchright + the installed Chrome»); the old hard-coded `DEFAULT_UA` is gone.
  With the Saudi proxy, public pages (`x.com/explore`, `/i/flow/login`) load with
  **no Cloudflare challenge at all**; only `/account/access` (the locked-account
  page) still shows it — so the CF problem was the egress IP, as suspected.
- Provider research (prices/coverage read from their own pages, 2026-10-22):
  Oxylabs 1,024,158 SA residential + Riyadh city + SA mobile ($8/$9 per GB);
  Bright Data 416,641 SA IPs, city/ZIP targeting, ISP static 10 IPs $18/mo, $8/GB
  (−50% promo); Decodo $2–3.75/GB with city/ZIP targeting (verify SA in dashboard);
  IPRoyal/ProxyEmpire list SA cities and mobile on stc/Mobily/Zain. IP counts are
  provider claims — the runtime geo check is what actually enforces «Saudi».
- Test: `.\venv\Scripts\python.exe verify_proxy_rotation.py` (35 checks, a local fake
  proxy answers geo and records the usernames, so the provider templates are verified
  without an account).

## The X browser is Patchright + the installed Chrome — one launcher, no automation traces
- Every X browser (`x_post/x_actions/x_delete/x_follow/x_unfollow/x_profile/x_bookmark` and
  the `playwright_x` reads) comes from `app/core/stealth_browser.py` via
  `captcha_browser.launch_browser(p, headless)` (= `x_captcha.launch_browser`), inside
  `with sync_playwright()` from the same module. Never `p.chromium.launch(...)` or
  `from playwright.sync_api import sync_playwright` in an X path: `verify_stealth.py` §1 fails.
- Measured before (2026-10-25, Chrome 155, Playwright 1.46): deviceandbrowserinfo.com
  «You are a bot!» (`hasWebdriverTrue`, `isAutomatedWithCDP`, `isAutomatedWithCDPInWebWorker`),
  sannysoft «WebDriver present», CreepJS 67% headless with `HeadlessChrome/155` leaking from the
  workers; the UA header said Chrome/142 (post/delete: Chrome/120) while `sec-ch-ua` said 155;
  post/delete ran with `--disable-web-security` (no same-origin policy in a browser holding the
  account cookies) and `--no-sandbox`. After (Patchright 1.64.1 + installed Chrome, headless):
  «You are human!» with no flag, sannysoft all passed, CreepJS 0% headless / 0% stealth (its
  «31% like headless» is identical for a real headful Chrome on this machine), x.com/explore 200
  through the Saudi proxy, @nxjrjrjdj home + profile read with its cookies.
- Why Patchright, not Camoufox: same API (every live-verified flow — Lexical composer, uploads,
  crop dialog, GraphQL response checks — keeps working), drives the real auto-updated Chrome
  (genuine TLS/JA4 and client hints), and patches the CDP `Runtime.enable` leak and the
  automation flags. Camoufox is a Firefox fork with injected fingerprints whose README still says
  it "may not be suitable for stable production use"; every Chrome-verified flow would need
  re-verification.
- UA: headless gets `--user-agent` built from the real `browser.version` (reduced
  `Chrome/<major>.0.0.0`, frozen OS token). A context `user_agent` does NOT reach (Shared)Workers
  or the service worker — exactly where CreepJS found `HeadlessChrome`. The version is probed once
  per process; a launch whose major differs (Chrome auto-updated) relaunches with the right UA.
  Headful keeps the native UA. `context_options()` adds no UA for these browsers, plus a `screen`
  larger than the viewport (Playwright makes screen == viewport, a headless tell). Never
  hard-code a UA again (an HTTP client using curl_cffi `impersonate=chromeNNN`, like `x_auth`,
  is different: its UA must match its impersonated version).
- Flags: only `--disable-dev-shm-usage` (Linux) and `--no-sandbox` (Linux as root = Docker);
  Patchright adds `--disable-blink-features=AutomationControlled` and drops `--enable-automation`
  itself. No `--disable-web-security`, no `--no-sandbox` on Windows, no `--disable-gpu`
  (software WebGL «Microsoft Basic Render Driver» = VM/headless signal).
- Patchright runs `evaluate` in an isolated world: DOM reads (every scraper JS here) work
  unchanged, page globals are invisible. Anything that must call page JS (captcha callbacks,
  `___grecaptcha_cfg`, `window[cb]`) goes through `stealth_browser.main_world_evaluate` (it
  checks the signature, so plain-Playwright test pages still work). `page.on("console")` does not
  fire in Patchright (Console domain disabled) — don't build on it.
- `stealth_browser.goto()` retries a navigation ONCE on a transient network error
  (`ERR_CONNECTION_CLOSED/RESET`, tunnel/proxy failures). The reported
  `read_timeline/read_my_posts … ERR_CONNECTION_CLOSED` was a dropped residential tunnel, not
  detection: the same path then loaded home + profile 4/4 times, urllib through the same proxy
  got 200, and X answers a detected fingerprint with 403 (`ERR_HTTP_RESPONSE_CODE_FAILURE`).
  Timeouts, 403 and everything else still raise.
- `X_BROWSER_ENGINE=playwright` forces plain Playwright (diagnostics); a missing patchright warns
  once and falls back (with `AutomationControlled` + `ignore_default_args=--enable-automation`).
  `XSUITE_CHROME_CHANNEL` still picks the channel (`chromium` = bundled build, which needs
  `patchright install chromium`; Docker installs it).
- Deps: `patchright==1.64.1` + `playwright==1.64.0` with `pyee==14.0.0`/`greenlet==3.5.6`
  (1.46 pinned pyee 11 / greenlet 3.0.3 — incompatible). After `pip install`, check
  `site-packages/{patchright,playwright}/driver/node.exe` and `greenlet/_greenlet*.pyd` exist: one
  install in the OneDrive venv silently left both drivers without `node.exe`
  (`FileNotFoundError [WinError 2]`) and greenlet without its `.pyd`;
  `pip install --force-reinstall --no-deps <pkg>` fixed it. Never upgrade the venv under a running
  app (the live xsuite had 1.46 in memory and the 1.64 driver on disk) — upgrade right before the
  restart.
- **Kaspersky Endpoint Security (installed on the owner's machine) removed the drivers once**
  (2026-10-25): right after `pip install`, its log (`Kaspersky Endpoint Security`, event 4662,
  behavior-detection bases) showed the pip command, and both `driver/node.exe` + `driver/package/
  cli.js` (+ ~50 package files) vanished from the venv the app runs from — every X read then died
  with `FileNotFoundError [WinError 2]` at `sync_playwright()`, while the same files in the OneDrive
  venv survived (so it was not an install bug). Fix without a reinstall: copy `driver/` from a
  healthy venv of the same version (robocopy /E); the files survived the next live runs. `engine()`
  now also falls back to plain Playwright (with a warning) when the Patchright driver is missing,
  and the launch error names the reinstall command. If it recurs, the owner must exclude the venv
  (or `…\site-packages\patchright\driver`) from Kaspersky — do not try to disable the AV.
- Test: `.\venv\Scripts\python.exe verify_stealth.py` (offline: static rules, engine, a probe page
  served by `route` + Worker/SharedWorker, captcha main-world injection, goto retry,
  context/presolve rules); `--live` adds deviceandbrowserinfo/sannysoft/CreepJS and x.com through
  the Saudi proxy; `--account <label|cookies.json>` reads home + profile with that account's
  cookies (read-only).

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

## Checkpoints on disconnect / restart (since 2026-11-10, owner decision: notify + manual resume)
- Before this, a socket drop mid-task lost the reply on screen. The reply was saved in the conversation,
  but `send_json` failed on the dead socket and the new socket never got it. A backend restart lost the
  task entirely, with no notice and no record of what had already happened on X.
- `app/services/task_checkpoints.py` has two DB tables (`create_all`, no migration):
  - `task_checkpoints`: one row per chat request (`kind=chat`, id = the browser's `task_id`) and per
    employee cycle (`cycle_<cycle_id>`). Status is running → succeeded/failed, or interrupted → resumed/cancelled.
  - `realtime_outbox`: every `assistant_message`/`error` pushed to a user, with a monotonic `seq`
    (SQLite AUTOINCREMENT), kept 48 h.
- Chat: the browser sends `task_id` with every message and keeps it in a localStorage outbox until a
  `task_update` acks it. A resent `task_id` is NOT executed twice (`duplicate: true`). While a task is
  running, `_CURRENT_TASK` tags replies with `task_id`/`session_id`. If the socket died, the reply goes
  to the user's OTHER sockets (the one that reconnected) instead of raising.
- After `auth_ok` the client sends `{"type":"resume","since":<last seq or null>}`. The server replays
  the outbox after `since` (`replayed: true`, deduped client-side by seq), then sends `tasks_snapshot`.
  `since: null` = fresh page: snapshot only, because the history is loaded from the conversation.
- Boot: `mark_interrupted_on_boot()` runs BEFORE `get_platform().start()`. Running it after would mark a
  cycle that just started as interrupted. `_announce_interrupted` then posts a notice into each
  task's conversation (cycles via `notify_owner`). Nothing auto-resumes: part of the task may have
  already run on X.
- Resume (`resume_task` / `dismiss_task` over WS, buttons in `TaskActivityPanel`) uses `claim_resume`,
  which is atomic (two clicks or two tabs never run it twice) and checks ownership.
  - Chat tasks re-run with their original text and context.
  - Cycles: `AEOSPlatform.resume_cycle` replays the same event with `source="resume:<id>"`. It does NOT
    use the payload, because the payload is printed into the planning prompt.
- Cycle checkpoints: `CognitiveCycle` calls `context.checkpoint("phase", …)` after every phase. On
  success, `ExecutePhase` immediately records `resume_key(capability, target)`. Publishing is ONE key,
  `publish`, because content is regenerated on resume, so a text match would never dedupe it.
  - On resume those keys are skipped («نُفّذ قبل انقطاع الدورة السابقة»), and the prior actions are
    added to today's budgets via `count_success`, because the cut cycle never reached `_persist`.
  - The task is finished AFTER `_persist`, not before.
  - If the resume is skipped (paused or busy), the task returns to `interrupted` and the owner is told why.
- `broadcast_to_user` used to fall back to EVERY open connection when the owner was offline, which pushed
  one user's employee reports to other users. Removed: the outbox delivers them when the owner reconnects.
- File uploads (cookies/CSV) are never checkpointed: their content would land in the DB.
- Owner orders (`employee_mission.dispatch_directed`) get an `order_<emp>_<hex>` checkpoint BEFORE
  research and the busy-wait (up to 8×20 s). Before this, a restart during the wait lost the order
  silently. The cycle is linked through `Event.source` (`order:<id>;resume:<id>`), never through the
  payload, because the payload is printed into the planning prompt.
  - When the cycle starts, its checkpoint takes the order's title and `request.order` and DISCARDS the
    order row (pushed as `task_update` with `removed: true`), so one order never shows two resume buttons.
  - Resuming any task that has `request.order` goes back through `dispatch_directed(..., resume_of=)`,
    keeping the busy-retry and outcome report.
- Resume rebuilds learning, not only counters: each successful action's checkpoint stores capability,
  target, handle, arm, url, text, pillar and format, and `_replay_learning` writes them back to the
  interaction ledger, strategy ledger, `engaged_urls` and learning arms.
  - Inherited actions are carried into the new cycle's checkpoint, so a second interruption still skips them.
  - `persisted=True` (set right after `_persist`) prevents double counting when the crash hit between
    save and finish.
  - `_action_checkpoint` never raises: an exception inside the action's try-block would mark a
    successful X action as failed.
- Test (temp DB, no network/LLM/X): `.\venv\Scripts\python.exe verify_checkpoints.py` (sections 4b/4c cover the above).

## Agent-cycle review 2026-11-16 (measured on 74 real cycles in execution_logs)
- **Editorial pick choice/angle mismatch.** The editor LLM's `choice` number pointed to one candidate
  while its `why`/`angle` described another.
  - Logged: title «معسكر آيزنكوت بالكنيست…» with why «توازنات العراق مع إيران». Another: title
    «#اليوم_العالمي_للتسامح» with why about the Houthis.
  - Result: research ran on one topic (`research_query` = title) and the post was written about the other.
  - Now the editor must echo `title`, and `builtin._reconcile_pick` maps it by word overlap. When there
    is no title, the angle decides the candidate. When title and angle contradict, the topic is kept
    and the misleading angle is dropped.
- **Overused signature phrases in posts.** «يا خبل» closed 5 of 9 posts and «هيا خذها» 4. The persona
  asks to sprinkle them, not to close every post with them.
  - The reply detector (`voice_profile.overused_phrases`) misses them: it drops grams that start with a
    particle, and its single-word rule would ban «اللي».
  - Posts now use `handlers._post_grams` / `_overused_in_post`:
    - a 2–3-gram in ≥2 of the last 5 or ≥3 of the last 20 posts counts as overused;
    - texts come from the strategy ledger, because memory «Published post» cuts at 200 chars and drops
      the closing;
    - phrases that appear in the brief/topic are exempt.
  - `repeated_signature` triggers ONE rewrite with the phrases listed. It is not blocking.
- **False exclusivity.** «خاص:»/«حصري:» was stuck on news every agency had published. It is now
  stripped as a label prefix in `_LABEL_PREFIX_RE`.
- Not bugs, verified:
  - A 68-hour cycle: the machine was asleep (no other cycle or message in that window).
  - «—-» and «يا خبل» are the owner's cloned persona style.
  - Pixabay works (picks are visible in chat).
- Test: `.\venv\Scripts\python.exe verify_cycle_quality.py`.
- **Never open a DB session with `next(get_db())` outside a FastAPI dependency.** The
  generator is finalised immediately, so its `finally: db.close()` runs on an EMPTY session;
  the first query then checks out a fresh connection that only returns to the pool when the
  cyclic GC collects the Session (it sits in a reference cycle). Measured (2026-10-25): the
  `/ws/chat` loop did this per message, every chat message pinned one connection, the pool
  (5+10) filled while all threads were idle (py-spy), and the scheduler died with
  `QueuePool limit of size 5 overflow 10 reached`. The loop now uses `SessionLocal()` with a
  per-message `finally: db.close()`. `GET /api/aeos/health` exposes `db_pool`
  (`checked_out` > 0 on an idle server = a leaked session). Verified live: 5 messages,
  `checked_out` back to 0 after each.

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
- Persona cloning is DEEP since 2026-10-19 (voice profile v2). The old 40 tweets + 30
  replies were 1-2 weeks of «era topics», and the clone turned them into identity:
  @emaad5079's replies collapsed into one slogan («… بالميدان لا بالبيان باذن الله», the
  same reply posted twice in 2h) because (a) topical n-grams («الوعد صنعاء», «غارات جوية»)
  became signature phrases/lexicon injected into every prompt, (b) the reply length was
  ONE median (24 chars, dominated by «الله يحفظك») so a news reply became a 5-word
  slogan, (c) only 4 courtesy exemplars, (d) the owner's post-format instructions
  («خطاف… خاتمة حاسمة») applied to replies too, (e) only the last 4 replies were checked.
- Scrape (`playwright_x._read_user_profile`): `_CELLS_JS` + `pair_cells()` read
  `cellInnerDiv`s — a cell without an article separates conversations; inside one the
  previous card is the real parent; a card labelled «ردًا على @…» (parent hidden) is a
  loose reply — the old "previous card = parent" attached random own replies as
  «thread continuations». Replies pool up to 400 (120s), plus `history_windows` searched
  with `from:<h> since: until:` (4 windows back to ~1 year). Measured: 202 tweets + 451
  replies (380 with parent), 2025-10 → 2026-10, 108s.
- Analysis (`voice_profile` v2): `stratify()` caps items per day (one day had 134 of 451
  replies); ≥100 replies analysed (`MIN_ANALYZED_REPLIES`). `phrase_spread()`: signature =
  recurs across ≥20% of weeks; era = concentrated (courtesy formulas excluded from era —
  they cluster on congratulation days). `reply_kind`/`parent_kind` (social occasions are
  NOT «content», or news replies inherit condolences), `reply_kind_stats` (length per
  kind), `reply_policy` (what he does per parent kind), `phrase_kind_rates`, and a
  stratified exemplar bank (~40 pairs with kind/parent_kind/date).
- Replies (`ReplyToUserCapability`): `reply_plan()` picks the kind from the policy for this
  parent (seeded, recent kinds damped), the kind's real length, 4 matching exemplars
  (rotated), and `overused_phrases()` (≥3 of last 20 / ≥2 of last 5 / repeated endings /
  a single word in ≥3 of last 10 — «الضجيج» was the next tic) as banned, minus words
  present in the tweet itself (`banned_for`); `reply_repetition()` (formula words like
  «بيض الله وجهك» ignored, Arabic tashkeel normalised) → one regeneration → `fail`
  («بوابة التكرار») if it still repeats. 30 own replies + kinds kept in flags. The prompt
  forbids invented facts (a «disagree» reply once denied the news with a made-up one) and
  titles («أستاذ/يا الغالي») towards news accounts.
- Live A/B on the same 10 tweets @emaad5079 had answered with the slogan: the new replies
  mixed agree/substantive/courtesy/reaction with lengths 15-180 chars and no template.
- Re-cloning keeps owner-taught lines (`- …` appended by `_handle_teach`). The API runs
  the clone as a background job (`background: true` → `GET /persona/clone/jobs/{id}`;
  UI helper `frontend/src/hooks/clonePersona.js`) — a deep clone takes 3-5 min.
  Test: `verify_voice.py` (82 checks).

## Clone sample = 70 original tweets, no replies (owner decision, 2026-11-01)
- `persona_clone.SAMPLE = {limit: 70, replies_limit: 0}`; `scrape_profile`/`clone_persona` default to
  `deep=False`, which sends `include_replies=False` to `read_user_profile`: the scraper reads the
  profile tab only (no `/with_replies`, no history windows) and trims to `limit`. `replies_limit=0`
  alone is NOT enough — the scraper used to fall back to `want // 2` replies. The API caps `limit`
  at 70. `nawah_clone._texts` sends tweets only (`MAX_TEXTS = 70`), never replies.
- Consequence: `voice_profile` has empty reply kinds/policy/exemplars for new clones, so
  `reply_plan` runs on defaults and the card shows no reply section — intended. `deep=True`
  still exists for the old behaviour (verify_voice's deep-profile checks use it in-memory).

## Voice fingerprint comes from the NAWAH clone server (since 2026-10-25)
- Persona cloning still scrapes X with the employee's cookies (`persona_clone.scrape_profile`) and still
  measures the Arabic reply side itself (reply kinds, reply policy, exemplars, era phrases). What changed is
  the **LLM style analysis**: `voice_profile.analyze(profile, llm, nawah=…)` asks the NAWAH clone server
  first (`create_clone` → fingerprint: dialect, register, one_line, style_guide, signature_phrases,
  never_uses, notes) and `services/nawah_clone.to_analysis()` maps it into the `analysis` shape that
  `voice_block`/`persona_from_voice` already consume. NAWAH does not produce pillars/values/red_lines —
  those stay EMPTY on purpose (never invent identity); interests come from the measured topic distribution.
- The old `_ANALYSIS_SCHEMA` LLM pass is now only an *announced* fallback: on any NAWAH failure the
  reason lands in `voice_profile.nawah_error` (shown in the card and the chat message), never silently.
- The server is a fork of `nawah-mcp-servers` under `system/mcps/nawah_clone/` (stdio, `node
  dist/clone-server.mjs`, registered as `ext_nawah_clone` with `scope` coordinator/employees = false:
  internal only). Its Gemini SDK layer was replaced by `src/core/llm.ts` — plain OpenAI-compatible
  chat completions (json_schema → json_object fallback, tool calling, retries) — and Google Search
  grounding by Tavily. **Edit `src/`, then `npm run build:clone`**: the app runs `dist/`, which is the
  one `dist/` folder NOT gitignored. Image/video helpers were dropped (unused by the servers).
- The key/base_url/model are NOT stored for it: `external_mcp.register_env_provider("nawah_clone",
  nawah_clone.runtime_env)` injects `NAWAH_LLM_*` from `resolve_llm()` (system endpoint, like embeddings)
  at connect time, blanks `GEMINI_API_KEY`, and the client reconnects if that env changes. Model:
  `NAWAH_MODEL` (default `google/gemini-3.7-flash`, provider-qualified; an unqualified name is a 404 that
  the server reports in Arabic). Clone files: `data/nawah/clones/<label>__<handle>.json`, one per
  (employee account, cloned account) — private, no shared library; `business_rules.voice_profile`
  remains the source of truth. `fidelity_test` adds `nawah_score` (deterministic `score` tool).
- The original drop `MCP Servers nawah/` is gitignored (real accounts' posts + 5 MB bundles).
- Verify: `.\venv\Scripts\python.exe verify_nawah.py` (offline) / `--live` (one real clone, ~10 s).

## «مهمة للفريق» runs on the NAWAH brief server (since 2026-10-25)
- Second server from the same package: `system/mcps/nawah_brief/` holds only `mcp.json` and a
  3-line launcher that imports `../nawah_clone/dist/brief-server.mjs` (so it has its own card,
  scope, settings and test button in the dashboard while sharing code and clone data). Registered
  as `ext_nawah_brief`, internal scope, tools `brief` + `research`.
- Flow (`services/nawah_brief.py`): `order_publish` → `NB.prepare(topic, user)` starts ONE
  background `brief` call per (user, topic) for the whole team (cached 30 min, in-flight calls
  share one `Event`, so N employees = one call); it returns at once with `status: building`.
  `dispatch_directed` → `NB.attach` waits for it (≤7 min), injects `for_employee(brief, handle)`
  into `payload["media_brief"]`, announces the directive to the owner once (`announce_once`) and
  each employee's angle; on any NAWAH failure it says why and falls back to the old
  `media_brief` (Tavily + editor LLM). The employee's own writer + quality gate still produce the
  final post — NAWAH gives research, directive (message, facts, per-employee beliefs/stance/
  angle/avoid) and a draft in that employee's voice, never the published text.
- **Every employee gets a seat, cloned or not** (owner decision): `voice_for(cfg)` sends the stored
  clone id when `voice_profile.nawah.id` exists on disk, otherwise an ad-hoc `persona` (role, bio,
  interests, tone, dos/donts, voice-profile style guide, + its own published posts from the cycle
  log and the performance ledger). Server side `src/core/adhoc.ts` turns that into a transient
  clone: stats measured from ≥3 own posts, else neutral stats and **no score** (never score a text
  against numbers that were not measured). `brief` also takes `drafts: false` for a directive-only
  fast path. Pieces/directive are keyed by voice id (`<label>__<handle>` or `persona:<handle>`) and
  `normalize()` maps them back to the employee handle — the clone's own `handle` is the cloned
  TARGET, never use it as the employee.
- Dashboard: both NAWAH cards expose `NAWAH_MODEL`, `NAWAH_FALLBACK_MODEL`, `NEWSDATA_API_KEY` as
  env settings (`env_keys`); values live in `data/system_mcp_secrets.json`, win over `.env`, and a
  save triggers rediscovery = restart with the new env. `external_mcp.runtime_env(cfg)` now passes
  `cfg` to env providers, drops empty stored values (an empty value used to clobber the process
  env), and `public_view.env_values` shows non-secret settings so the UI can prefill them (a key
  whose name looks like a secret stays a password field).
- Verify: `verify_nawah.py` sections 7-8 offline (voices, normalize, cache/in-flight, attach
  fallback), section 9 `--live` (real brief with one measured persona and one bare persona).

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

## unslop: no AI-sounding writing, for every employee (since 2026-10-26)
- Upstream skill https://github.com/theclaymethod/unslop (MIT) is vendored UNCHANGED in
  `system/skills/unslop/` (coordinator skill; commit pinned in `UPSTREAM.md`). Don't edit it.
  Its own AGENTS.md/CLAUDE.md rules (eval-first) apply only to the upstream repo.
- **Its scanners are English-only** and decline other languages. Measured: an English AI
  paragraph gave 4 flags, and the same text in Arabic gave 0. So `app/aeos/employee/unslop.py` has two layers:
  - English → the upstream `scripts/banned_phrase_scan.py` (`scan_for_violations`), loaded via
    `system_loader`. sys.path is restored after loading so its scripts can't shadow packages.
  - Arabic → a pattern table built on unslop's own categories. Following unslop, patterns are
    contextual, not word bans: «يقف شاهداً على» flags, literal «شاهد على الحادث» doesn't. «ليس مجرد/فقط X بل Y»
    flags, a real correction «ليس اليوم بل غداً» doesn't. Text inside «…» / "…" quotes is exempt.
    Text is normalised first (tashkeel, أإآ→ا, ى→ي, ة→ه): «الحقيقه» is common on X.
- Policy (owner decision): any hard finding, or ≥2 soft ones → ONE rewrite with the actual
  phrases (`fix_hint`). The new version is kept only if it scores cleaner (`polish`, plus `accept`
  for replies: no repetition). It NEVER blocks publishing: `ai_slop` is in `_HARD_QUALITY_ISSUES`,
  NOT in `_BLOCKING_QUALITY_ISSUES`. Owner-written text (`params.text`/`reply_text`) is never touched.
- Wired into: posts (`_quality_issues` + `_generate_quality_post`), replies, quotes, threads
  (whole thread), scheduled posts, plus a prevention line in `_content_prompt` and in the
  reply/quote prompts. `system/skills/unslop_guard` is the always_on employee skill (dashboard
  toggle). Per-employee opt-out: `business_rules.unslop = false`.
- `use_system_skill` now takes `resource` so the coordinator can read files a SKILL.md points to
  (`references/…`). Text files only, no path escape, 60k cap.
- Adding a skill changes the counts hard-coded in `verify_system_admin.py`
  (`default_skills()` length and overview `coordinator`/`employee`): update them.
- Test (no posting, no LLM): `.\venv\Scripts\python.exe verify_unslop.py`.

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
- **Usage plan per MCP server** (`tool_use.usage_plan`, stored in `data/mcp_usage_plans.json`, rebuilt
  when the tool list changes). Tools that aren't search/extract/news (e.g. trendsmcp
  `get_top_trends(type,country)`) used to be ignored. Now the system model writes a plan once per
  server: `observe` = called once each cycle with fixed args (2h cache) → `scratch["mcp_signals"]`
  → post prompt; `research` = called before writing with the topic in `topic_arg`; `skip` = never
  called. `_valid_plan` keeps only real schema args and drops a tool if a required arg has no
  value or the tool is a write tool. Failed calls are not cached. The plan covers ONLY tools
  `classify` does NOT handle (search/extract/news keep their `gather` path; a topic is never passed
  as a URL arg). When the plan is built, each observe board is probed once. Measured 2026-11-08:
  trendsmcp's X and Google boards for SA returned `count: 0`, while TikTok SA was full. Empty boards
  are re-planned once, then dropped. `_compact` turns replies into one ranked line. `get_context`
  arrives cut at 6000 chars (invalid JSON), so titles are pulled by regex. Test: section 5g6 of `verify_creator.py`.
- **Specialty trends every cycle** (`tool_use.specialty_trends`, called in `ObservePhase` BEFORE
  `browse_specialty_news`, and also on the signal-snapshot path):
  1. Two search keywords are derived from the persona's role, bio and audience (`_specialty_keywords`,
     cached per day). The raw interest `politics` returned US politics in English for a Saudi account.
  2. The first `research` tool is called with each keyword (1 h cache), together with the boards and the X trends.
  3. The employee's LLM picks ≤2 topics INSIDE the specialty, each with an `x_query` and evidence headline numbers.
  4. Results go into `scratch["specialty_trends"]` and `scratch["trend_x_queries"]`:
     - `browse_specialty_news` searches X with those queries first, and the relevance filter accepts them.
     - `_grounded_candidates` adds a `specialty_trend` candidate (`_priority` 6) with the headlines and
       matching X tweets, and `_editorial_pick` chooses among the candidates.
  Measured live: «اليمن والحوثيون» was picked, with 3 real headlines. Test: `check_specialty_trends_drive_cycle`. A server is used by
  employees only if `scope.employees` is true AND its keys are set: trendsmcp needs `Authorization: Bearer …`.
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

## Clone sample + identity (2026-11-16, owner request)
- `persona_clone.SAMPLE` = 200 original posts + 150 replies (was 70 posts, no replies). Replies feed the reply playbook, exemplars and reply policy. Nawah `create_clone` gets up to `MAX_TEXTS`=150 posts. Replies never enter the Nawah fingerprint.
- `voice_profile._enrich_identity`: after a Nawah fingerprint, one LLM identity analysis over posts AND replies fills ONLY the empty identity fields (pillars, values, red_lines, identity_core, stance, reply_playbook, interaction...). Nawah style fields are never overwritten. `analysis.identity_source = "nawah+llm"`.
- `verify_nawah.py`: the 2 "scope closed" checks fail on main independently of this change (scope state); `write/delete` fails only with the owner's local uncommitted mcp.json edit.

## Topic media library for briefed campaigns (2026-11-16, owner request)
- `dispatch_directed` → `_attach_media_pool`: once the media brief (Nawah or the `media_brief` fallback) is attached, `stock_media.build_pool(topic, message, user_id)` builds ONE Pixabay library per (owner, topic). Queries come from the message, videos and images are mixed, fake tags are dropped, and the LLM pre-scores against the message (score 7 or higher, up to 10 items). It is cached 30 min, a per-key lock means one build per campaign, and it is announced once in chat.
- The payload carries ONLY `media_pool_key` (the payload is printed into the planning prompt). `handlers._stock_media` calls `pick_from_pool` BEFORE the normal search. Each agent re-scores the library against ITS OWN tweet text, and `_verify` runs the vision check.
- A file another teammate took (`pool["taken"]`, checked again under the lock) or one the agent used in the last 30 days is skipped. `stock_media=off` or `stock_media_kind` is respected. A missing or expired key (e.g. after a restart) falls back to `find()`, then to a generated image.
- Live 2026-11-16 («حرائق الغابات وجهود الإطفاء الجوي»): the library had 3 videos and 3 images. Two different tweets got two DIFFERENT firefighting-plane videos, and one candidate was rejected by vision. Test: section 5 of `verify_cycle_quality.py`.
- Optional news-article images (owner request 2026-11-16): employee setting `news_media` (UI: «صور مقالات الأخبار في حملات التوجيه الإعلامي»), default OFF because copyright belongs to the publishers.
  - When it is on, `_attach_media_pool` passes `media_brief_items` to `build_pool`, and `news_images` adds each source's og:image/twitter:image (parallel, 8 s timeout, reads at most 600 KB). Logo/favicon/svg images are dropped.
  - `_public_url` (DNS check: no private/loopback/link-local/reserved address, redirects re-checked) guards against SSRF from search-result pages.
  - Agents without the setting never pick `source=news` items. The progress line names the site.
  - Live: 6 article images from 6 real sources in 2.8 s, one of them unrelated (a politician photo). The per-tweet scoring and the vision gate are what filter those. Test: section 5b of `verify_cycle_quality.py`.

## Security audit 2026-11-16
- pip-audit (in a python:3.13-slim container) found 24 vulnerable packages. 16 were bumped within their major version (pip `--uploaded-prior-to` 7 days): python-multipart 0.0.6→0.0.31 (public form/upload DoS), requests, urllib3, idna, certifi, dotenv, pyasn1, werkzeug, anyio (+typing_extensions 4.16), dnspython, pydantic-settings, mako, ecdsa, httplib2, aiohttp, flask. `gunicorn` was unused and removed. Verified: 10 verify suites and a Docker build + import.
- Remaining (major upgrades, owner decision pending): starlette 0.50 (needs a FastAPI upgrade to reach 1.3.1), cryptography 46→50, pillow 12→13, flask-cors 4→6, protobuf 5→6, opentelemetry-api, python-jose (no fix; mitigated: `jwt.decode(..., algorithms=[HS256])` pins the alg), ecdsa (only used by jose for EC keys; we use HS256).
- npm: axios 1.13.2→1.21.0 (20 days old, not the 7-day-old 1.21.1), follow-redirects 1.16.3, form-data 4.0.6, @remix-run/router 1.23.4. 2 moderate react-router advisories remain (need a react-router 7 major).
- `/api/x/health` and `/api/aeos/health` are public. They used to expose the server cookies path, the X account count, the employee/MCP server list and DB pool state. Anonymous callers now get the status only; full details need admin/developer (`app.auth.dependencies.get_current_user` = optional).
- OPEN, owner decision: `app/x/loginx/uploads/accounts.csv` is tracked in git since 2026-05-19 with 5 rows of username/password/email in plaintext. The repo is private (anonymous API 404), but anyone with repo access or a clone has them.
