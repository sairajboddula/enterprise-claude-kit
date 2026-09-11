# Architecture Deep Dive

This document explains *why* enterprise-claude-kit is designed the way it is,
not just *what* it does. It is intended for engineers evaluating the library,
architects designing an integration, and contributors planning significant changes.

---

## Table of Contents

1. [Design philosophy](#1-design-philosophy)
2. [The four-layer model](#2-the-four-layer-model)
3. [Request lifecycle](#3-request-lifecycle)
4. [GovernanceLayer internals](#4-governancelayer-internals)
5. [AgentOrchestrator internals](#5-agentorchestrator-internals)
6. [TokenMonitor internals](#6-tokenmonitor-internals)
7. [AuditLogger internals](#7-auditlogger-internals)
8. [AdoptionTracker internals](#8-adoptiontracker-internals)
9. [SQLite as the persistence layer](#9-sqlite-as-the-persistence-layer)
10. [Async model and thread safety](#10-async-model-and-thread-safety)
11. [Error hierarchy](#11-error-hierarchy)
12. [Extension points](#12-extension-points)
13. [What this library is not](#13-what-this-library-is-not)

---

## 1. Design philosophy

### Why a layer, not a framework

LangChain and LlamaIndex are **frameworks**: they provide chains, agents,
retrievers, and expect you to compose your application *within* them. That
makes them flexible for research and prototyping, but heavy for production
enterprise deployments where you already have an application and need to add
AI capability to it safely.

enterprise-claude-kit is a **layer**: a thin wrapper above the Anthropic SDK
that adds the compliance, observability, and rollout machinery your enterprise
needs, then gets out of the way. Your application code calls the Anthropic SDK
the same way it always would — the library intercepts, checks, records, and
passes through.

```
Without the library:        With the library:
─────────────────────       ─────────────────────────────────────
Your App                    Your App
    │                           │
    ▼                           ▼
Anthropic SDK              AgentOrchestrator  ← thin routing layer
    │                           │
    ▼                           ├─ GovernanceLayer  ← policy
Claude API                      ├─ TokenMonitor     ← cost
                                ├─ AuditLogger      ← compliance
                                │
                                ▼
                           Anthropic SDK
                                │
                                ▼
                           Claude API
```

The additional latency is measured in low single-digit milliseconds (regex
evaluation + two SQLite writes). For LLM workloads where the model itself
takes 1–30 seconds, this is negligible.

### Principle: explicit over implicit

Every piece of information that flows through the system is typed and named.
`GovernanceResult` tells you *which* checks ran, *which* fired, and *why*.
`AuditEvent` stores a `governance_result: Literal["pass", "flag", "block"]`
rather than a boolean. `CostSummary` breaks spend down by model *and* by
persona, not just in aggregate.

This verbosity is intentional. The stakeholders who audit AI deployments —
legal, compliance, finance — need to reconstruct exactly what happened weeks
or months after the fact. Ambiguous logs do not survive audits.

### Principle: fail loudly on policy, silently on observability

A governance violation raises `GovernanceViolation` — loud, explicit, must
be handled by the caller. A failed metric write to the token monitor logs a
warning but does not raise. A checksum that cannot be computed logs an error
but the audit event is still written (without the checksum field).

This asymmetry is deliberate. Enforcement must be reliable. Observability
should degrade gracefully rather than taking down the application.

---

## 2. The four-layer model

```
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 0 · Your Application                                          │
│                                                                      │
│  Business logic. Prompts. User sessions. Result rendering.           │
│  Knows nothing about governance, cost, or audit.                     │
└──────────────────────────────┬──────────────────────────────────────┘
                               │  await agent.run(prompt, user_id=…)
┌──────────────────────────────▼──────────────────────────────────────┐
│  Layer 1 · AgentOrchestrator                                         │
│                                                                      │
│  Routing layer. Manages the lifecycle of named agents.               │
│  Wires GovernanceLayer + TokenMonitor + AuditLogger together.        │
│  Resolves model tier → concrete model ID.                            │
│  Returns AgentResult (content + tokens + cost + governance result).  │
└──────┬───────────────────────┬──────────────────────┬───────────────┘
       │                       │                      │
┌──────▼──────────┐   ┌────────▼────────┐   ┌────────▼────────────────┐
│  Layer 2a       │   │  Layer 2b       │   │  Layer 2c               │
│  GovernanceLayer│   │  TokenMonitor   │   │  AuditLogger            │
│                 │   │                 │   │                         │
│  Pre-checks:    │   │  Pre-call:      │   │  Post-call:             │
│  • PII scan     │   │  • Budget check │   │  • Write AuditEvent     │
│  • Blocked words│   │  Post-call:     │   │  • Compute SHA-256      │
│  • Length cap   │   │  • Record usage │   │  • (no UPDATE/DELETE)   │
│  • Persona gate │   │  • Fire alerts  │   │                         │
│  • Custom hooks │   │  • Update cache │   │                         │
│                 │   │                 │   │                         │
│  Post-checks:   │   │  Persistence:   │   │  Persistence:           │
│  • GxP citation │   │  SQLite         │   │  SQLite (append-only)   │
│  • Custom hooks │   │  (usage_records)│   │  (audit_events)         │
└──────┬──────────┘   └────────┬────────┘   └────────┬────────────────┘
       └───────────────────────┴──────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│  Layer 3 · Anthropic SDK / Claude API                                │
│                                                                      │
│  anthropic.AsyncAnthropic().messages.create(…)                       │
│  Returns message.content[0].text + usage.input_tokens + …           │
└─────────────────────────────────────────────────────────────────────┘
```

Layer 2 components are **independently composable**. You can use
`GovernanceLayer` alone, without a `TokenMonitor`. You can use `AuditLogger`
without `GovernanceLayer`. The `AgentOrchestrator` wires them together only
when all three are provided.

---

## 3. Request lifecycle

The following sequence traces a single `await agent.run(prompt, user_id="alice")`
call through every component.

```
caller
  │
  │  agent.run(prompt, user_id="alice")
  ▼
AgentOrchestrator.run()
  │
  ├─► [1] GovernanceLayer.pre_check(prompt, persona, model, system_prompt)
  │         • Length check
  │         • Blocked keyword scan (case-insensitive)
  │         • PII detection (email, phone, SSN, credit card, IP)
  │         • Persona allowlist check
  │         • Run pre_hooks (sync or async, in registration order)
  │         • If any violation → raise GovernanceViolation (stops here)
  │         • Returns GovernanceResult(passed=True) if all clear
  │
  ├─► [2] TokenMonitor.check_budget(user_id)          [if monitor attached]
  │         • Query today's spend from SQLite
  │         • If spend ≥ daily_budget_usd → raise BudgetExceededError
  │
  ├─► [3] anthropic_client.messages.create(…)         [the actual API call]
  │         • model, messages, max_tokens, system
  │         • Returns: content, input_tokens, output_tokens, stop_reason
  │
  ├─► [4] GovernanceLayer.post_check(response_text, persona, model)
  │         • GxP citation check (if gxp_mode=True)
  │           → scan for [SOURCE:], [REF:], [CITATION:] markers
  │           → sets gxp_compliant=True/False on result, adds "gxp_flag" if missing
  │         • Run post_hooks (sync or async, in registration order)
  │         • Returns updated GovernanceResult
  │
  ├─► [5] TokenMonitor.record_usage(…)                [if monitor attached]
  │         • Write UsageRecord to SQLite: timestamp, tokens, cost_usd, model,
  │           persona, user_id, session_id
  │         • Update in-memory budget cache
  │         • If cumulative spend now ≥ alert_threshold_pct → fire on_alert callbacks
  │
  ├─► [6] AuditLogger.log_event(…)                    [if audit attached]
  │         • Build AuditEvent with all fields populated
  │         • Hash user_id → SHA-256 (never stored raw)
  │         • If gxp_mode=True: compute SHA-256 of full event JSON → store in checksum
  │         • INSERT INTO audit_events (append-only, no UPDATE/DELETE ever)
  │
  └─► [7] Return AgentResult
            • content: str
            • input_tokens: int
            • output_tokens: int
            • cost_usd: float
            • model: str
            • governance_result: GovernanceResult
```

---

## 4. GovernanceLayer internals

### PII detection

PII is detected via compiled regex patterns, evaluated at module load time
(not per-call) for performance:

| PII type     | Pattern captures                                  |
|:-------------|:--------------------------------------------------|
| `email`      | Standard RFC 5322-ish email addresses             |
| `phone`      | US format with optional country code, separators  |
| `ssn`        | `NNN-NN-NNNN` format                              |
| `credit_card`| Visa, MC, Amex, Discover with optional separators |
| `ip_address` | IPv4 dotted-decimal                               |

Matches return `PIIMatch(pii_type, pattern, position)`. The matched text is
stored in `PIIMatch.pattern` — callers who log `GovernanceResult` should
strip or redact `pii_detected` before writing to any external system.

> **What PII detection does not do:** It does not detect names, addresses,
> dates of birth, passport numbers, or any pattern requiring semantic
> understanding. If your use case involves those data types, implement a
> pre-hook that calls a purpose-built PII detection service (AWS Comprehend,
> Azure Text Analytics, or similar).

### Constitutional AI check

When `constitutional_ai=True`, the governance layer runs a self-critique pass
on the model's response. The current implementation checks for the presence
of harmful-content markers that the orchestrator injects into the system
prompt. A future version will run a second Claude call as a critic — this is
left as a hook-based extension point rather than being baked into the core
to avoid doubling API cost without user consent.

### Hook execution order

```
pre_hooks[0](prompt, context)
pre_hooks[1](prompt, context)
…
pre_hooks[n](prompt, context)
    ↓
[API call]
    ↓
post_hooks[0](response, context)
post_hooks[1](response, context)
…
post_hooks[n](response, context)
```

Hooks that raise `GovernanceViolation` abort the chain immediately. Other
exceptions propagate to the caller unchanged. Hooks may be sync or async —
the layer detects coroutines via `inspect.iscoroutinefunction` and awaits
them; plain functions are called directly.

### GxP citation enforcement

In `gxp_mode=True`, the post-check scans every response for at least one
occurrence of `[SOURCE:…]`, `[REF:…]`, or `[CITATION:…]`. The regex is
case-insensitive. If no marker is found, `gxp_compliant` is set to `False`
and `"gxp_flag"` is added to `GovernanceResult.flags`. This is a **warning**
(flag), not a blocking violation, because the library cannot know whether the
absence of a citation is acceptable in context. Upgrade it to a blocking rule
in a post-hook if your SOPs require it.

---

## 5. AgentOrchestrator internals

### Agent registration

Each agent is a `dataclass`-like object holding:

```python
@dataclass
class Agent:
    agent_id: str           # UUID4, assigned at creation
    name: str               # human label
    system_prompt: str      # injected as the "system" turn
    persona: str            # passed to governance + audit
    model: str              # resolved concrete model ID
    tools: list[dict]       # Anthropic tool definitions
    mcp_connectors: list[str]  # connector names → look up in registry
    metadata: dict          # arbitrary caller-supplied context
```

The `tier` parameter (`"default"` | `"batch"` | `"gated"`) maps to concrete
model IDs via a tier table. Callers can override with `model_tier` for
agent-specific model selection without exposing model IDs everywhere:

| Tier      | Default model              |
|:----------|:---------------------------|
| `default` | `claude-sonnet-4-6`        |
| `batch`   | `claude-haiku-4-5`         |
| `gated`   | `claude-opus-5`            |

### Context manager lifecycle

```python
async with AgentOrchestrator(governance=gov, monitor=mon) as orch:
    agent = await orch.create_agent(…)
    result = await agent.run(prompt)
# __aexit__ cancels any pending background tasks
```

The context manager pattern is enforced to ensure background budget-alert
callbacks have a defined lifetime. Constructing an orchestrator outside a
context manager is possible but not recommended — `on_alert` callbacks may
leak if the monitor is not properly torn down.

---

## 6. TokenMonitor internals

### Cost model

Pricing is stored as a `dict[str, tuple[float, float]]` mapping model ID to
`(input_price_per_million_tokens, output_price_per_million_tokens)`. Prices
are in USD and are correct as of library release. They are **not** fetched
from an API at runtime, so they may drift as Anthropic adjusts pricing. A
`cost_usd` column in `usage_records` stores the pre-calculated cost at write
time, preserving accuracy even if the pricing table is later updated.

### Budget check strategy

The monitor maintains an **in-memory cache** of today's spend that is
populated on first access and invalidated at UTC midnight. This avoids a
SQLite read on every `check_budget()` call, keeping pre-call latency at
sub-millisecond levels.

```
check_budget() called
        │
        ├─ cache valid?  ──YES──► return cached BudgetStatus
        │
        └─ NO ──► SELECT SUM(cost_usd) FROM usage_records WHERE date = today
                        │
                        └─► populate cache ──► return BudgetStatus
```

The tradeoff: if two processes share the same SQLite file, each has its own
cache and may each allow spending up to the budget before either detects the
breach. For single-process deployments this is not a concern. For multi-process,
pass a shared database and query `check_budget()` synchronously from a
single writer process, or accept a small overspend window (bounded by the
number of concurrent processes × the cost of a single call).

### Alert delivery

`@monitor.on_alert` callbacks are fired **after** the usage record is written,
not before. This means the model call has already happened by the time the
alert fires. The alert is a notification, not a gate — use `check_budget()`
before the call for hard enforcement.

---

## 7. AuditLogger internals

### Append-only guarantee

The SQLite schema issues no `UPDATE` or `DELETE` statements against the
`audit_events` table. This is enforced by design, not by SQLite triggers,
because the library controls all writes and SQLite does not have a
`CREATE TABLE … AS IMMUTABLE` construct. If you need a stronger guarantee,
wrap the SQLite file with a filesystem ACL that makes it append-only to
your application's OS user.

### SHA-256 checksum construction

When `gxp_mode=True`, the checksum is computed as:

```python
event_dict = event.model_dump(mode="json")
event_dict.pop("checksum", None)          # exclude the field being computed
canonical = json.dumps(event_dict, sort_keys=True, ensure_ascii=False)
checksum = hashlib.sha256(canonical.encode()).hexdigest()
```

`sort_keys=True` ensures the JSON serialisation is deterministic regardless
of dict insertion order. `ensure_ascii=False` preserves Unicode characters
(relevant for non-English system prompts and multilingual responses stored
in metadata).

`verify_checksums()` recomputes this for every stored event and returns a
`dict[str, bool]` mapping `event_id → True/False`. An event that fails
verification has been modified after write — either by direct SQLite
manipulation or file-level tampering.

### User privacy

`user_id` is **never stored raw** in the audit log. The field stored is:

```python
user_hash = hashlib.sha256(user_id.encode()).hexdigest()
```

This is a one-way hash. It allows correlating all events from the same user
without storing PII. If you need to reverse the hash for a specific audit
inquiry, you must maintain your own `user_id → hash` lookup table externally.

### Retention policy

`AuditLogger(retention_days=365)` configures a retention policy, but the
library does **not** run automatic deletion. It provides a `purge_old_events()`
method that must be called by the application (e.g., from a scheduled job).
This is intentional: automatic deletion of compliance records without explicit
invocation is a risk in regulated environments.

---

## 8. AdoptionTracker internals

### Wave state machine

```
created ──► planned ──► active ──► complete
                │
                └── WaveGateError if gate not met
```

A wave transitions `planned → active` only when its gate condition is
satisfied:

```python
gate_progress = activated_count(gate_wave_id) / target_count(gate_wave_id)
if gate_progress < gate_threshold_pct:
    raise WaveGateError(f"Gate wave at {gate_progress:.1%}, need {gate_threshold_pct:.1%}")
```

There is no automatic transition to `complete` — that is left to the
operator, who sets it after verifying adoption metrics.

### Literacy score algorithm

```
score = min(100, log10(call_count + 1) / log10(MAX_CALLS + 1) × 100)
```

Where `MAX_CALLS` is a constant (default: 1000). This gives:

| Calls | Score |
|------:|------:|
| 0     | 0     |
| 1     | 25    |
| 5     | 47    |
| 20    | 65    |
| 100   | 83    |
| 1000  | 100   |

Logarithmic scaling reflects the reality that the marginal value of each
additional call decreases as a user becomes more proficient. The first 20
calls teach the most; calls 200–1000 are refinement.

### `get_inactive_users(days=N)` semantics

Returns user IDs that are activated in any wave but have made no calls in
the last `N` days. At `days=1`, this is "no calls in the last 24 hours."
In a live deployment this surfaces users who signed up but stopped using
the tool. In a demo with all calls happening in the same second, use
`days=1` — only users with `call_count == 0` will appear, because all
recent calls are well within the 1-day window.

---

## 9. SQLite as the persistence layer

Three separate SQLite databases are used by default:

| Component     | Default path    | Tables          |
|:--------------|:----------------|:----------------|
| TokenMonitor  | `monitor.db`    | `usage_records` |
| AuditLogger   | `audit.db`      | `audit_events`  |
| AdoptionTracker| `adoption.db`  | `waves`, `activations`, `user_calls` |

### Why SQLite?

1. **Zero operational overhead.** No daemon to run, no port to open, no
   credentials to rotate. A file on disk is all that's needed.
2. **Portable for auditors.** An SQLite file can be handed to a compliance
   team, opened in DB Browser for SQLite, and queried with standard SQL.
   No proprietary tooling required.
3. **Sufficient for the workload.** A 5,000-user deployment making 10 calls
   per user per day generates 50,000 rows/day in `usage_records`. SQLite
   handles this with ease; at 100× that scale, consider PostgreSQL.
4. **aiosqlite** provides async non-blocking access. Each coroutine gets its
   own connection from the pool, avoiding thread contention.

### Scaling beyond SQLite

When you outgrow SQLite (typically >500,000 events/day or >10 concurrent
writers), the swap path is:

1. Replace the `aiosqlite.connect(db_path)` calls in each module with a
   connection factory that returns an `asyncpg` connection.
2. The SQL dialect is intentionally kept to the common subset (no SQLite
   JSON functions, no `ROWID`, no `STRICT` tables) so queries work
   unmodified on PostgreSQL.
3. Open a GitHub issue if you need this — PRs for a pluggable connection
   backend are welcome.

---

## 10. Async model and thread safety

### Everything is async

Every database write, every hook invocation, every API call is `async`.
The library never calls `asyncio.run()` internally — that is the caller's
responsibility. This means you can embed the library in any async framework
(FastAPI, aiohttp, Starlette) without conflict.

### `aiosqlite` connection-per-call

`aiosqlite` is not a connection pool — each `async with aiosqlite.connect(path)`
opens a new connection and closes it at context exit. For the library's
workload (one or two writes per API call) this is adequate. The overhead is
~1 ms per open/close on a local filesystem.

> **Critical:** `aiosqlite.connect(":memory:")` creates a **separate**
> in-memory database for each connection. Two calls to `connect(":memory:")`
> see two independent empty databases. Always use a real file path in tests
> that span multiple operations. The examples use `tempfile.mkdtemp()` for
> this reason.

### MCPConnectorRegistry thread safety

`MCPConnectorRegistry` is a thread-safe singleton guarded by a
`threading.Lock`. The double-checked locking pattern is used in `__new__`
to avoid lock contention after initialisation:

```python
if cls._instance is None:        # fast path — no lock
    with cls._lock:
        if cls._instance is None:  # slow path — inside lock
            cls._instance = super().__new__(cls)
```

---

## 11. Error hierarchy

```
Exception
└── EnterpriseClaudeError          ← all library errors
    ├── GovernanceViolation        ← policy blocked the call
    │   └── ApprovalRequiredError  ← model requires explicit approval
    ├── BudgetExceededError        ← cost budget exhausted
    ├── WaveGateError              ← wave gate not met
    ├── AgentNotFoundError         ← unknown agent_id
    └── ConnectorNotFoundError     ← unknown connector name
```

**Catching strategy:**

```python
try:
    result = await agent.run(prompt, user_id=user_id)
except ApprovalRequiredError:
    # Model gated — route to approval workflow
except GovernanceViolation as exc:
    # Policy blocked — return exc.violation_type to the caller
except BudgetExceededError:
    # Spend limit hit — return 429-equivalent to the caller
except EnterpriseClaudeError:
    # Catch-all for any other library error
```

Never catch `anthropic.APIError` inside the library — surface it to the caller
so they can implement their own retry logic.

---

## 12. Extension points

### Custom governance rules via hooks

The hook pattern is the primary extension mechanism. A hook can do anything:
call an external API, query a database, raise `GovernanceViolation`.

```python
from enterprise_claude import GovernanceLayer, GovernanceViolation

gov = GovernanceLayer(pii_filter=True)

@gov.register_pre_hook
async def enforce_prompt_language(prompt: str, context: dict) -> None:
    """Block non-English prompts in a UK-only deployment."""
    detected = await language_detect_api(prompt)
    if detected != "en":
        raise GovernanceViolation(
            f"Prompt language '{detected}' not permitted",
            violation_type="language_policy",
        )

@gov.register_post_hook
def log_to_siem(response: str, context: dict) -> None:
    """Forward every response to the corporate SIEM."""
    siem_client.send({
        "source": "enterprise_claude",
        "persona": context.get("persona"),
        "response_length": len(response),
        "timestamp": context.get("timestamp"),
    })
```

### Custom MCP connectors

```python
from enterprise_claude.mcp_connectors import MCPConnectorRegistry, ConnectorConfig, AuthType

registry = MCPConnectorRegistry()
registry.register("internal-erp", ConnectorConfig(
    name="internal-erp",
    display_name="Acme ERP",
    url="@acme/mcp-server-erp",
    auth_type=AuthType.BEARER_TOKEN,
    required_env_vars=["ERP_API_TOKEN"],
    description="Query Acme's internal ERP for inventory and procurement data",
    documentation_url="https://internal.acme.com/erp/mcp",
    tags=["erp", "finance", "procurement"],
))
```

### Custom personas and allowlists

```python
gov = GovernanceLayer(
    allowed_personas=["analyst", "engineer", "clinical_ops"],
)
# Any agent created with a persona not in this list raises GovernanceViolation
```

---

## 13. What this library is not

Understanding the boundaries is as important as understanding the capabilities.

| This library is NOT…                    | What to use instead                              |
|:----------------------------------------|:-------------------------------------------------|
| A RAG framework                         | LlamaIndex, LangChain, or direct embedding calls |
| A vector store                          | Chroma, Pinecone, pgvector                       |
| A prompt management system              | Langfuse, PromptLayer, Helicone                  |
| A model fine-tuning tool                | Anthropic fine-tuning API                        |
| A full observability platform           | Langfuse, LangSmith, Arize Phoenix               |
| A distributed task queue                | Celery, RQ, Temporal                             |
| A multi-tenancy SaaS backend            | Build on top of this library                     |
| A replacement for network-level WAF     | AWS WAF, Cloudflare, Apigee                      |

The library is deliberately narrow. It solves the governance, cost, audit, and
adoption problems and nothing else. Compose it with the tools above to build
a complete enterprise AI platform.
