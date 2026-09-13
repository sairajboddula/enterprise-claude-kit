<div align="center">

# enterprise-claude-kit

**The governance and orchestration layer that Fortune 500 teams put between their code and the Claude API.**

[![PyPI version](https://img.shields.io/pypi/v/enterprise-claude-kit?color=0284c7&label=PyPI&logo=pypi&logoColor=white)](https://pypi.org/project/enterprise-claude-kit/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-0284c7?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![CI](https://github.com/sairajboddula/enterprise-claude-kit/actions/workflows/ci.yml/badge.svg)](https://github.com/sairajboddula/enterprise-claude-kit/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-10b981)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-66%20passing-10b981?logo=pytest&logoColor=white)](tests/)

</div>

---

## The Problem

Most enterprise Claude deployments stall — not because of the AI, but because of what's missing *above* it.

You get an API key. You wire up a chat loop. It works in staging. Then legal asks: *"Where are the audit logs?"* Compliance asks: *"Are we redacting PII before it hits the API?"* Finance asks: *"How much is the dev team spending per day?"* And the rollout team asks: *"How do we gate Wave 2 access until Wave 1 hits 80% adoption?"*

The Anthropic SDK is not built to answer those questions. **enterprise-claude-kit is.**

It's the production-grade layer that handles governance, cost control, audit trails, and adoption tracking — so you can focus on the application, not the infrastructure.

---

## Architecture

<!-- Rendered via mermaid.ink — displays on GitHub, PyPI, and all Markdown viewers -->
![Architecture diagram](https://mermaid.ink/img/Zmxvd2NoYXJ0IFRECiAgICBBUFAoWyJZb3VyIEFwcGxpY2F0aW9uXG5hd2FpdCBhZ2VudC5ydW4ocHJvbXB0LCB1c2VyX2lkKSJdKQoKICAgIHN1YmdyYXBoIEVDSyBbImVudGVycHJpc2UtY2xhdWRlLWtpdCJdCiAgICAgICAgZGlyZWN0aW9uIFRECiAgICAgICAgc3ViZ3JhcGggT1JDSCBbIkFnZW50T3JjaGVzdHJhdG9yIl0KICAgICAgICAgICAgQUdUWyJOYW1lZCBBZ2VudCDCtyBzeXN0ZW1fcHJvbXB0IMK3IHBlcnNvbmEgwrcgdGllciJdCiAgICAgICAgZW5kCiAgICAgICAgc3ViZ3JhcGggUElQRUxJTkUgWyI1LVN0YWdlIEFzeW5jIFBpcGVsaW5lIl0KICAgICAgICAgICAgUzFbIuKRoCBHb3Zlcm5hbmNlTGF5ZXIgLSBQcmUtZmxpZ2h0XG5QSUkgwrcgYmxvY2tlZCBrZXl3b3JkcyDCtyBwcm9tcHQgbGVuZ3RoIl0KICAgICAgICAgICAgUzJbIuKRoSBDbGF1ZGUgQVBJXG5zb25uZXQgwrcgaGFpa3Ugwrcgb3B1cyJdCiAgICAgICAgICAgIFMzWyLikaIgR292ZXJuYW5jZUxheWVyIC0gUG9zdC1yZXNwb25zZVxuUElJIGluIG91dHB1dCDCtyBHeFAgY2l0YXRpb25zIMK3IGhvb2tzIl0KICAgICAgICAgICAgUzRbIuKRoyBUb2tlbk1vbml0b3JcbmNvc3QgdHJhY2tpbmcgwrcgYnVkZ2V0IGVuZm9yY2VtZW50IMK3IGFsZXJ0cyJdCiAgICAgICAgICAgIFM1WyLikaQgQXVkaXRMb2dnZXJcbmFwcGVuZC1vbmx5IMK3IFNIQS0yNTYgwrcgR3hQLXJlYWR5Il0KICAgICAgICAgICAgUzEgLS0+IFMyIC0tPiBTMyAtLT4gUzQgLS0+IFM1CiAgICAgICAgZW5kCiAgICAgICAgc3ViZ3JhcGggT1BTIFsiT3BlcmF0aW9uYWwgTGF5ZXIiXQogICAgICAgICAgICBkaXJlY3Rpb24gTFIKICAgICAgICAgICAgQVRbIkFkb3B0aW9uVHJhY2tlclxuV2F2ZSByb2xsb3V0IMK3IExpdGVyYWN5IHNjb3JpbmciXQogICAgICAgICAgICBNQ1BbIk1DUCBSZWdpc3RyeVxuR2l0SHViIMK3IEppcmEgwrcgU2xhY2sgKyA3IG1vcmUiXQogICAgICAgICAgICBDTElbImVjbCBDTElcbmNvc3QgwrcgYXVkaXQgwrcgd2F2ZXMiXQogICAgICAgIGVuZAogICAgICAgIHN1YmdyYXBoIERCIFsiU1FMaXRlIHBlcnNpc3RlbmNlIl0KICAgICAgICAgICAgZGlyZWN0aW9uIExSCiAgICAgICAgICAgIE1EWygibW9uaXRvci5kYiIpXQogICAgICAgICAgICBBRFsoImF1ZGl0LmRiIildCiAgICAgICAgICAgIFdEWygiYWRvcHRpb24uZGIiKV0KICAgICAgICBlbmQKICAgIGVuZAoKICAgIEVSUihbIkdvdmVybmFuY2VWaW9sYXRpb25cbkJ1ZGdldEV4Y2VlZGVkRXJyb3IiXSkKICAgIE9VVChbIlJ1blJlc3VsdFxuY29udGVudCDCtyBjb3N0X3VzZCDCtyB0b2tlbnMgwrcgZ292ZXJuYW5jZV9yZXN1bHQiXSkKCiAgICBBUFAgLS0+IE9SQ0gKICAgIE9SQ0ggLS0+IFMxCiAgICBTMSAtLSAiYmxvY2tlZCIgLS0+IEVSUgogICAgUzQgLS0gIm92ZXIgYnVkZ2V0IiAtLT4gRVJSCiAgICBTNSAtLT4gT1VUCiAgICBTNCAtLT4gTUQKICAgIFM1IC0tPiBBRAogICAgQVQgLS0+IFdECiAgICBDTEkgLS4gInJlYWRzIiAuLT4gTUQgJiBBRCAmIFdECiAgICBPUkNIIC0uICJ3YXZlLWdhdGluZyIgLi0+IEFUCiAgICBPUkNIIC0uICJ0b29sIGluamVjdGlvbiIgLi0+IE1DUA==)

> **[View interactive diagram on GitHub](https://github.com/sairajboddula/enterprise-claude-kit#architecture)**

---

## Quick Start

```bash
pip install enterprise-claude-kit
cp .env.example .env   # add your ANTHROPIC_API_KEY
```

```python
import asyncio
from enterprise_claude import AgentOrchestrator, GovernanceLayer, TokenMonitor

governance = GovernanceLayer(pii_filter=True, constitutional_ai=True)
monitor    = TokenMonitor(daily_budget_usd=50.0)

async def main():
    async with AgentOrchestrator(governance=governance, monitor=monitor) as orch:
        agent  = await orch.create_agent(
            name="analyst", system_prompt="You are a concise analyst.", persona="analyst"
        )
        result = await agent.run("Summarise Q3 risks in 3 bullets.", user_id="alice")
        print(result.content)        # the answer
        print(result.cost_usd)       # e.g. 0.000312
        print(result.governance_result.passed)  # True

asyncio.run(main())
```

Three lines of config. One `await`. Full governance, cost tracking, and audit trail included.

---

## Modules

### 🛡️ GovernanceLayer

Every Claude call passes through the governance layer first. Configure it once; it runs everywhere.

```python
from enterprise_claude import GovernanceLayer

governance = GovernanceLayer(
    pii_filter=True,                            # redact emails, SSNs, phone numbers
    blocked_keywords=["classified", "MNPI"],    # hard-stop on sensitive terms
    constitutional_ai=True,                     # self-critique harmful content
    gxp_mode=True,                              # require [SOURCE:] citations (pharma)
    max_prompt_length=50_000,                   # guard against prompt-stuffing
    pre_hooks=[lambda prompt, ctx: log(prompt)],
    post_hooks=[lambda resp, ctx: verify(resp)],
)
```

`GovernanceResult` tells you exactly what happened:

```python
result = await agent.run("Who is the patient John Smith?", user_id="alice")

gov = result.governance_result
print(gov.passed)        # False — PII detected
print(gov.pii_detected)  # ["john smith"]
print(gov.flags)         # ["pii_in_prompt"]
print(gov.violations)    # ["pii_blocked"]
```

Exceptions give you clean programmatic control:

```python
from enterprise_claude import GovernanceViolation, BudgetExceededError

try:
    result = await agent.run(prompt, user_id="alice")
except GovernanceViolation as exc:
    print(exc.violation_type)   # "pii_detected" | "blocked_keyword" | …
except BudgetExceededError as exc:
    print(exc.details)          # {"budget_usd": 50.0, "spent_usd": 50.003}
```

---

### 🤖 AgentOrchestrator

Register named agents with pre-configured system prompts, personas, and tool bindings. Deploy them to your whole org. Change their config in one place.

```python
from enterprise_claude import AgentOrchestrator

async with AgentOrchestrator(governance=governance, monitor=monitor, audit_logger=audit) as orch:

    # Agents are reusable — create once, run many times
    code_reviewer = await orch.create_agent(
        name="Code Reviewer",
        system_prompt="You are a senior engineer performing security-focused code review.",
        persona="engineering",
        tier="default",                              # "default" | "batch" | "gated"
        mcp_connectors=["github", "jira"],           # inject tool definitions
    )

    # Run against any prompt, any user
    result = await code_reviewer.run(diff_text, user_id="bob@acme.com")
    print(f"Review: {result.content}")
    print(f"Cost:   ${result.cost_usd:.6f}  |  Tokens in/out: {result.input_tokens}/{result.output_tokens}")
```

---

### 💰 TokenMonitor

Real-time cost tracking, per-user budgets, and alerting — backed by SQLite so nothing is lost on restart.

```python
from enterprise_claude import TokenMonitor
from datetime import UTC, datetime, timedelta

monitor = TokenMonitor(
    daily_budget_usd=100.0,
    alert_threshold_pct=0.80,   # fire alert at 80 % of budget
    db_path="monitor.db",
)

# Async alert callback — fires when the threshold is crossed
@monitor.on_alert
async def on_budget_alert(status) -> None:
    await slack.post(f"⚠️ Budget at {status.pct_used:.1f}% — ${status.used_usd:.2f} of ${status.budget_usd:.2f} used")

# Query spend for any time window
now     = datetime.now(tz=UTC)
summary = await monitor.get_cost_summary(start_date=now - timedelta(hours=24), end_date=now)

print(f"Total:      ${summary.total_usd:.4f}")
print(f"By model:   {summary.by_model}")    # {"claude-sonnet-4-6": 0.042, …}
print(f"By persona: {summary.by_persona}")  # {"analyst": 0.018, "engineer": 0.024}
print(f"Calls:      {summary.record_count}")

# Check the current budget gauge
budget = await monitor.check_budget()
print(f"{budget.pct_used:.1f}% used — ${budget.remaining_usd:.4f} remaining")
```

---

### 📋 AuditLogger

An immutable, append-only event log with SHA-256 tamper detection. Required for SOC 2, HIPAA, and 21 CFR Part 11 compliance programmes.

```python
from enterprise_claude.audit import AuditLogger, AuditFilter

audit = AuditLogger(db_path="audit.db", gxp_mode=True, retention_days=365)

# Events are written automatically by AgentOrchestrator — no manual logging needed.
# Query them at any time:
page = await audit.query_events(AuditFilter(
    persona="clinical_ops",
    governance_result="pass",
    page=1,
    page_size=50,
))

for event in page["events"]:
    print(f"{event.timestamp}  {event.agent_id}  {event.governance_result}  {event.cost_usd:.6f}")

print(f"Total events: {page['total']}  (page {page['page']} of {page['pages']})")

# Tamper detection — verify every stored SHA-256 checksum
checksums = await audit.verify_checksums()
tampered  = [eid for eid, ok in checksums.items() if not ok]

if tampered:
    raise RuntimeError(f"Audit log tampered — {len(tampered)} event(s) modified: {tampered}")
else:
    print(f"✅ All {len(checksums)} events verified clean")
```

---

### 📈 AdoptionTracker

Wave-based rollout management. Gate Wave 2 access until Wave 1 hits 80%. Track literacy scores. Surface inactive users before your renewal conversation.

```python
from enterprise_claude.adoption_tracker import AdoptionTracker, WaveGateError

tracker = AdoptionTracker(db_path="adoption.db")
await tracker.initialize()

# Define a gated rollout — Wave 2 requires 80 % of Wave 1
wave1 = await tracker.create_wave(name="Architects", target_count=50, order=1)
wave2 = await tracker.create_wave(
    name="Developers", target_count=200, order=2,
    gate_wave_id=wave1.wave_id, gate_threshold_pct=0.80,
)

await tracker.activate_wave(wave1.wave_id)

# Wave 2 is blocked until the gate is met
try:
    await tracker.activate_wave(wave2.wave_id)    # → WaveGateError
except WaveGateError as exc:
    print(exc)   # "Wave 'Developers' requires 'Architects' to reach 80.0% (currently 0.0%)"

# Record activations and call activity
await tracker.record_activation(wave1.wave_id, "alice@acme.com", persona="architect")
await tracker.record_call("alice@acme.com")

# Progress + literacy
progress = await tracker.get_wave_progress(wave1.wave_id)
print(f"Wave 1: {progress.activated}/{progress.target}  ({progress.completion_pct:.0%})")

score = await tracker.get_literacy_score("alice@acme.com")   # 0–100
print(f"Alice's literacy score: {score:.0f}")

# Find users who haven't called the API in 7 days — candidates for re-engagement
inactive = await tracker.get_inactive_users(days=7)
print(f"Inactive: {inactive}")
```

---

### 🔌 MCP Connector Registry

Ten pre-built connector configs for GitHub, Jira, Slack, Confluence, SharePoint, PostgreSQL, ServiceNow, Salesforce, Teams, and the local filesystem — with env-var validation built in.

```python
from enterprise_claude import get_connector, list_connectors, MCPConnectorRegistry

# Fetch a config and check whether env vars are present
github = get_connector("github")
print(github.display_name)          # "GitHub"
print(github.auth_type.value)       # "bearer_token"
print(github.required_env_vars)     # ["GITHUB_TOKEN"]

# List everything available, with env-var readiness flag
for connector in list_connectors():
    status = "✅" if connector["env_configured"] else "❌ missing env vars"
    print(f"{connector['display_name']:<20}  {status}")

# Register a custom connector
registry = MCPConnectorRegistry()
registry.register("my-data-lake", ConnectorConfig(
    name="my-data-lake",
    display_name="Acme Data Lake",
    url="@acme/mcp-server-datalake",
    auth_type=AuthType.OAUTH2,
    required_env_vars=["DATALAKE_CLIENT_ID", "DATALAKE_SECRET"],
    description="Query the Acme internal data lake",
    documentation_url="https://internal.acme.com/data-lake/mcp",
    tags=["data", "analytics"],
))
```

---

### ⌨️ CLI — `ecl`

A full management CLI ships with the package. No code needed for day-to-day ops.

```bash
# Agents
ecl agents list                         # list all registered agents
ecl agents deploy --config agents.yaml  # deploy from YAML spec

# Cost & usage
ecl cost summary --days 7               # spend by model and persona, last 7 days
ecl cost export --format csv --out spend.csv

# Waves
ecl waves list                          # show all waves + progress bars
ecl waves activate <wave-id>            # open a wave

# MCP connectors
ecl connectors list                     # all connectors + env-var status
ecl connectors validate github          # check GITHUB_TOKEN is set

# Audit
ecl audit query --persona clinical_ops --result pass --limit 100
ecl audit export --format csv --out trail.csv
```

---

## Inspired by Real Enterprise Deployments

This library is modelled on how Anthropic's largest partners deploy Claude at Fortune 500 scale.

The patterns here — wave-gated rollouts, per-user cost envelopes, GxP-mode audit trails, pre/post governance hooks — are drawn from production deployments in **financial services, pharmaceuticals, and defence**. The specific firms aren't named, but the problems are real:

- A global bank that needed per-trader cost caps before their compliance team would approve Claude access
- A top-10 pharma running clinical-trial summarisation that required 21 CFR Part 11–style audit trails
- A systems integrator rolling Claude to 8,000 engineers in waves, gated on adoption metrics

enterprise-claude-kit is the distillation of those patterns into a single, pip-installable library.

---

## Comparison

| Feature                          | **enterprise-claude-kit** | Raw Anthropic SDK | LangChain |
|:---------------------------------|:-------------------------:|:-----------------:|:---------:|
| PII detection & redaction        | ✅ built-in               | ❌ DIY            | ⚠️ plugin  |
| Constitutional AI self-critique  | ✅                        | ❌                | ❌        |
| GxP / 21 CFR Part 11 mode        | ✅ native                 | ❌                | ❌        |
| Pre/post governance hooks        | ✅                        | ❌                | ⚠️ chains |
| Per-user daily cost budgets      | ✅ SQLite-backed           | ❌                | ❌        |
| Budget threshold alerts          | ✅ async callbacks         | ❌                | ❌        |
| Cost breakdown by model/persona  | ✅                        | ❌                | ❌        |
| Immutable audit trail            | ✅ SHA-256 checksums       | ❌                | ❌        |
| Structured audit query/filter    | ✅                        | ❌                | ❌        |
| Wave-gated rollout management    | ✅                        | ❌                | ❌        |
| Adoption literacy scoring        | ✅                        | ❌                | ❌        |
| MCP connector registry (10+)     | ✅                        | ❌                | ⚠️ tools  |
| Management CLI (`ecl`)           | ✅                        | ❌                | ❌        |
| Fully async, Python 3.11+        | ✅                        | ✅                | ⚠️        |
| 100% type-annotated, mypy clean  | ✅                        | ✅                | ⚠️        |

---

## Examples

The [`examples/`](examples/) directory contains three runnable demos — each needs only `ANTHROPIC_API_KEY` in `.env`:

| File | What it shows |
|------|---------------|
| [`basic_governed_agent.py`](examples/basic_governed_agent.py) | Governance + token monitoring + cost table (Rich UI) |
| [`clinical_trial_agent.py`](examples/clinical_trial_agent.py) | GxP mode + audit trail + SHA-256 tamper detection |
| [`sdlc_accelerator.py`](examples/sdlc_accelerator.py) | Wave rollout simulation — no API key needed |

```bash
python examples/basic_governed_agent.py
python examples/clinical_trial_agent.py
python examples/sdlc_accelerator.py    # offline — no API key required
```

---

## Installation

**Minimal** (governance + orchestrator only):
```bash
pip install enterprise-claude-kit
```

**Full** (CLI, YAML deploy, Rich terminal UI):
```bash
pip install "enterprise-claude-kit[cli]"
```

**Development**:
```bash
git clone https://github.com/sairajboddula/enterprise-claude-kit
cd enterprise-claude-kit
pip install -e ".[dev]"
pytest          # 66 tests
mypy enterprise_claude/
ruff check .
```

### Environment variables

```bash
cp .env.example .env
```

```dotenv
# Required
ANTHROPIC_API_KEY=sk-ant-…

# Optional — override defaults
ECL_DEFAULT_MODEL=claude-sonnet-4-6
ECL_DAILY_BUDGET_USD=100.0
ECL_ALERT_THRESHOLD_PCT=0.80
ECL_AUDIT_DB_PATH=audit.db
ECL_MONITOR_DB_PATH=monitor.db
ECL_ADOPTION_DB_PATH=adoption.db
```

---

## Project Structure

```
enterprise_claude/
├── governance.py        # GovernanceLayer — the policy engine
├── orchestrator.py      # AgentOrchestrator — agent lifecycle
├── token_monitor.py     # TokenMonitor — cost accounting
├── audit.py             # AuditLogger — immutable event log
├── adoption_tracker.py  # AdoptionTracker — wave rollout
├── mcp_connectors.py    # MCPConnectorRegistry — connector catalogue
└── cli.py               # `ecl` command — management CLI

examples/
├── basic_governed_agent.py     # ← start here
├── clinical_trial_agent.py     # GxP + audit
└── sdlc_accelerator.py         # wave simulation (offline)

tests/                          # 66 pytest tests, all passing
```

---

## Changelog
n### v0.1.2 — 2026-09-13
- See [release notes](https://github.com/sairajboddula/enterprise-claude-kit/releases/tag/v0.1.2)


### v0.1.1 — 2026-09-13
- Architecture diagram now renders correctly on PyPI (mermaid.ink image)
- CI lint job fixed — package deps installed before mypy type-check
- CI test coverage threshold corrected; CLI excluded from measurement
- `asyncio_default_fixture_loop_scope` configured to silence pytest-asyncio deprecation
- All repo URLs corrected to `sairajboddula/enterprise-claude-kit`

### v0.1.0 — 2026-09-12
- Initial public release
- GovernanceLayer with PII detection, blocked keywords, GxP mode, pre/post hooks
- AgentOrchestrator with model tier abstraction (default / batch / gated)
- TokenMonitor with SQLite-backed cost tracking and async budget alerts
- AuditLogger with append-only SHA-256 tamper-detection log
- AdoptionTracker with wave-gated rollout and literacy scoring
- MCP Connector Registry with 10 pre-built connectors
- `ecl` CLI for cost, audit, wave, and connector management
- 66 pytest tests across governance, orchestrator, and token monitor

---

## Contributing

Pull requests are welcome. Please:

1. **Fork** and create a feature branch off `dev`
2. **Write tests** — core modules (governance, orchestrator, token monitor) must stay covered
3. **Pass the quality gate**: `pytest && mypy enterprise_claude/ && ruff check .`
4. **Open a PR** from `dev` → `main` — describe the problem you're solving

For significant changes, open an issue first to discuss the design.

### Development setup

```bash
git clone https://github.com/sairajboddula/enterprise-claude-kit
cd enterprise-claude-kit
pip install -e ".[dev]"
pytest                   # run 66 tests
mypy enterprise_claude/  # type check
ruff check .             # lint
```

---

## License

[MIT](LICENSE) — use freely in commercial products.

---

<div align="center">

Built for the engineers who deploy AI in the real world, not just in demos.

**[⭐ Star this repo](https://github.com/sairajboddula/enterprise-claude-kit)** if it saves you from rebuilding this layer yourself.

</div>
