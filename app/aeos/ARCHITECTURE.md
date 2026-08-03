# AEOS — AI Employee Operating System

> Transforms the project from an **intent-based chatbot** into a platform of
> **autonomous AI Employees**. One Connected Account = One AI Employee.
>
> **The runtime is temporary. The employee is permanent.**

This package (`app/aeos/`) is **additive**: the legacy chatbot (`app/main.py`
WebSocket, intent service, agents) keeps working unchanged. AEOS runs alongside
it and reuses the existing X automation via an MCP adapter.

---

## 1. Mental model

An **event** wakes an employee. The `RuntimeManager` loads the employee, builds a
temporary execution context, runs **one** cognitive cycle, persists everything,
and destroys the runtime. A small shared **worker pool** serves a large fleet of
employees because each employee only occupies a worker for a single cycle.

```
Event (scheduler | mention | reply | webhook | news | manual)
   │
   ▼
EventBus ──► WakeScheduler ──► JobQueue ──► WorkerPool
                                              │
                                              ▼
                                      RuntimeManager.run_cycle()
                                              │
             ┌────────────────────────────────┴───────────────────────────────┐
             │ 1 LOAD employee config (repository)                              │
             │ 2 BUILD ExecutionContext (memory, workspace, MCP, capabilities)  │
             │ 3 RUN  CognitiveCycle (10 phases)                                │
             │ 4 PERSIST runtime state + goal progress + audit log + reschedule │
             │ 5 DESTROY runtime (clear working memory + tmp)                   │
             └─────────────────────────────────────────────────────────────────┘
```

### The cognitive cycle (phases)
`Observe → Analyze → Think → Prioritize → Plan → Decide → Execute → Evaluate → Learn → Save`
(then the RuntimeManager performs "Sleep"/destroy).

Each phase is a single-responsibility class implementing `ICognitivePhase`. A
failing phase is isolated and never aborts the whole cycle.

---

## 2. Layered design (SOLID + DI + event-driven)

| Layer | Package | Responsibility | Key interface |
|-------|---------|----------------|---------------|
| Core | `core/` | Interfaces, event bus, DI container, LLM, exceptions | `ILLMProvider`, `EventBus`, `Container` |
| Employee | `employee/` | Permanent domain model + persistence | `IEmployeeRepository` |
| Security | `security/` | Encrypt cookies/tokens/secrets (Fernet) | `ISecretVault` |
| Workspace | `workspace/` | Isolated per-employee file sandbox | `Workspace` |
| Memory | `memory/` | Working/Conversation/LongTerm/Semantic/Reflection | `IMemoryLayer` |
| MCP | `mcp/` | Pluggable tool servers (X wraps `x_bridge`) | `IMCPServer` |
| Capabilities | `capabilities/` | Business actions decoupled from MCP | `ICapability` |
| Skills | `skills/` | Installable autonomous behaviours | `ISkill` |
| Planner | `planner/` | Goals + skills → candidate actions | `IPlanner` |
| Decision | `decision/` | Score + select actions (explainable) | `IDecisionEngine` |
| Cognition | `cognition/` | ExecutionContext + phases + cycle | `ICognitivePhase` |
| Runtime | `runtime/` | Lifecycle: load→build→cycle→persist→destroy | `IRuntimeManager` |
| Scheduler | `scheduler/` | Wake events → jobs → shared workers | `IJobQueue` |
| Bootstrap | `bootstrap.py` | Composition root (wires everything via DI) | `AEOSPlatform` |
| API | `api/routes.py` | REST surface | — |

**Dependency rule:** every layer depends on *interfaces*, never on concrete
classes of another layer. The only place that knows concretes is
`bootstrap.py` (the composition root). This makes any backend swappable:
in-memory queue → Redis, SQLite repo → Postgres, OpenAI → Gemini — each is a
one-line change in bootstrap.

---

## 3. Key decoupling: Capability ⟶ MCP

The employee never calls an MCP server directly. It requests a **capability**
("publish_content"); the capability decides which **MCP tool** to invoke and
resolves credentials just-in-time from the employee's context/vault.

```
Skill proposes → Decision selects → Capability executes → MCP tool → external world
   (what)            (whether)          (how, business)     (transport)
```

The **legacy X automation** (`app/x` Flask server + Playwright, reached through
`app/services/x_bridge.py`) is now just `XPlatformMCP` — one pluggable MCP server
with no business logic of its own.

---

## 4. Multi-tenant isolation

- Each employee has its own **workspace** (`data/employees/<id>/`) with
  path-traversal protection.
- Secrets are **encrypted** per employee in the vault.
- Capabilities validate the employee actually has the required MCP servers.
- The `cookie_label` scopes every X action to the correct account.

---

## 5. Persistence (new tables)

Added to `app/db/models.py` (created by `init_db()`):

- `ai_employees` — permanent employee config (persona/goals/skills/mcp/state as JSON).
- `employee_memories` — one row per memory item, across all layers.
- `execution_jobs` — wake jobs (for a durable queue backend).
- `execution_logs` — per-cycle audit trail (decisions, outcomes, timings).

---

## 6. HTTP API (`/api/aeos`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/aeos/health` | platform health + stats |
| GET | `/api/aeos/employees` | list active employees |
| POST | `/api/aeos/employees` | create an employee |
| GET | `/api/aeos/employees/{id}` | get one employee |
| PATCH | `/api/aeos/employees/{id}/status` | activate/pause/suspend |
| POST | `/api/aeos/employees/{id}/trigger` | wake now (`sync=true`) or enqueue |
| GET | `/api/aeos/stats` | queue/mcp/capabilities/skills |

The platform starts on FastAPI `startup` and stops on `shutdown` (see
`app/main.py`).

---

## 7. Verify it works

```bash
# runs the full pipeline with a stubbed X MCP (no network/Playwright)
python -m app.aeos.smoke_test
```

Expected: a scheduler wake produces a `publish_content` decision, executes it via
the (stubbed) X MCP, writes a reflection lesson, persists `posts_today=1`, and
destroys the runtime — `SMOKE TEST PASSED`.

---

## 8. Extending the platform

- **New action** → add an `ICapability` in `capabilities/handlers.py`.
- **New tool/integration** → add an `IMCPServer` and register it in `bootstrap.py`.
- **New autonomous behaviour** → add an `ISkill` in `skills/builtin.py`.
- **New memory layer** → implement `IMemoryLayer` and add to `MemoryManager`.
- **Different LLM/queue/DB** → change one binding in `bootstrap.py`.

No core changes required (Open/Closed Principle).

---

## 9. Migration status & next steps

**Done:** full AEOS skeleton with working implementations, X automation wrapped
as MCP, DB tables, event-driven scheduler + workers, REST API, green smoke test,
zero breakage to the legacy chatbot.

**Recommended next:**
1. Bridge the existing `x_agent`/intent flow to *create* employees instead of
   one-off actions.
2. Add an X `read_mentions/notifications` endpoint in `x_bridge` and a matching
   MCP tool so `community_engager` reacts to real inbound events.
3. Move the job queue to Redis for durability + multi-process scale.
4. Replace lexical `SemanticMemory` with vector embeddings.
5. Add per-cycle unit tests around each phase and capability.
