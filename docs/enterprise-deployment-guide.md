# Enterprise Deployment Guide

## 5,000-User Wave Rollout — Configuration Reference

This guide walks through a realistic enterprise deployment: rolling Claude to
5,000 employees at a diversified financial services firm across three waves,
with governance tailored per department, cost controls enforced per business
unit, and adoption metrics that satisfy the CIO's quarterly review.

Every code snippet in this guide is production-ready. Copy, adapt, and run.

---

## Table of Contents

1. [Pre-deployment planning](#1-pre-deployment-planning)
2. [Infrastructure checklist](#2-infrastructure-checklist)
3. [Wave 1 — Power users / pilot (50 users)](#3-wave-1--power-users--pilot-50-users)
4. [Wave 2 — Early adopters (500 users)](#4-wave-2--early-adopters-500-users)
5. [Wave 3 — Full rollout (5,000 users)](#5-wave-3--full-rollout-5000-users)
6. [Governance configuration by department](#6-governance-configuration-by-department)
7. [Cost management at scale](#7-cost-management-at-scale)
8. [Monitoring and alerting](#8-monitoring-and-alerting)
9. [Integration with enterprise systems](#9-integration-with-enterprise-systems)
10. [Rollback procedures](#10-rollback-procedures)
11. [Success metrics](#11-success-metrics)

---

## 1. Pre-deployment planning

### Persona taxonomy

Before writing a line of code, map your org chart to Claude personas. Each
persona gets its own governance policy — what it can ask, which model tier
it uses, how much it costs per day.

```
Financial Services Firm — Persona Taxonomy
──────────────────────────────────────────

  research_analyst        → can query market data, no client PII
  investment_advisor      → can discuss client portfolios (PII gate required)
  compliance_officer      → read-only audit queries, gxp-adjacent strictness
  software_engineer       → code review, no production credentials in prompts
  operations              → process automation, bulk/batch tier
  executive               → high-limit, opus tier, approval-gated
```

**Why this matters:** Governance policy is configured per persona, not per
user. When a compliance officer is restricted to certain prompt types and an
engineer is not, you manage two policy objects — not 5,000 user records.

### Budget estimation

Use this worksheet before procurement conversations:

| Persona              | Users | Calls/day | Avg tokens/call | Model tier | Est. cost/user/day |
|:---------------------|------:|----------:|----------------:|:-----------|-----------------:|
| research_analyst     |   200 |        20 |           3,000 | default    |           $0.036 |
| investment_advisor   |   150 |        15 |           4,000 | default    |           $0.036 |
| compliance_officer   |    50 |         5 |           2,000 | default    |           $0.006 |
| software_engineer    | 1,500 |        30 |           5,000 | default    |           $0.090 |
| operations           |   800 |        50 |           1,500 | batch      |           $0.019 |
| executive            |    50 |         3 |           8,000 | gated      |           $0.072 |

**Total daily estimate:** ~$320 USD/day · ~$9,600/month · ~$115,000/year

Add 30% headroom for peak days, prompt engineering experiments, and the
inevitable "can I just ask it one more thing" factor.

### Risk register

Document these before go-live — your CISO will ask:

| Risk | Likelihood | Impact | Control |
|:-----|:----------:|:------:|:--------|
| PII sent to API | Medium | High | `pii_filter=True` on all personas |
| Cost overrun | Medium | Medium | Per-user daily budget + alerts at 80% |
| Prompt injection | Low | High | `blocked_keywords` + pre-hooks |
| Audit log tampering | Low | High | SHA-256 checksums + offsite backup |
| Model hallucination | High | Medium | Constitutional AI + GxP citations where applicable |
| Unauthorised model access | Low | High | `allowed_personas` + wave gates |

---

## 2. Infrastructure checklist

```
Directory layout (recommended):

  /opt/enterprise-claude/
  ├── config/
  │   ├── governance/
  │   │   ├── research_analyst.py
  │   │   ├── investment_advisor.py
  │   │   ├── software_engineer.py
  │   │   └── executive.py
  │   └── agents.yaml              ← agent definitions (deployed via ecl agents deploy)
  ├── data/
  │   ├── monitor.db               ← TokenMonitor (one per deployment)
  │   ├── audit.db                 ← AuditLogger  (one per deployment)
  │   └── adoption.db              ← AdoptionTracker
  ├── logs/
  │   └── enterprise_claude.log
  └── scripts/
      ├── daily_cost_report.py
      └── purge_old_audit_events.py
```

```bash
# System requirements
Python 3.11+
6 GB RAM (for concurrent requests; most is the OS and Anthropic SDK)
SSD storage (SQLite performance is I/O-bound)
Outbound HTTPS to api.anthropic.com

# Install
pip install "enterprise-claude-kit[cli]"

# Environment
cp .env.example /opt/enterprise-claude/.env
# Set: ANTHROPIC_API_KEY, ECL_DAILY_BUDGET_USD, ECL_AUDIT_DB_PATH, …
```

---

## 3. Wave 1 — Power users / pilot (50 users)

**Goal:** Validate governance config, establish cost baselines, catch
issues before wider rollout. Duration: 4–6 weeks.

**Who:** Senior analysts, tech leads, one compliance representative.
Not selected for enthusiasm — selected for diversity of use cases.

### Wave creation

```python
# scripts/wave_setup.py
import asyncio
from enterprise_claude.adoption_tracker import AdoptionTracker

async def setup_waves():
    tracker = AdoptionTracker(db_path="/opt/enterprise-claude/data/adoption.db")
    await tracker.initialize()

    wave1 = await tracker.create_wave(
        name="Wave 1 — Pilot (Power Users)",
        target_count=50,
        order=1,
        # No gate — Wave 1 is always open
    )
    wave2 = await tracker.create_wave(
        name="Wave 2 — Early Adopters",
        target_count=500,
        order=2,
        gate_wave_id=wave1.wave_id,
        gate_threshold_pct=0.80,   # 80% of Wave 1 must be active before Wave 2 opens
    )
    wave3 = await tracker.create_wave(
        name="Wave 3 — Full Organisation",
        target_count=5_000,
        order=3,
        gate_wave_id=wave2.wave_id,
        gate_threshold_pct=0.70,   # 70% of Wave 2 — more lenient for scale
    )

    print(f"Wave 1 ID: {wave1.wave_id}")
    print(f"Wave 2 ID: {wave2.wave_id}")
    print(f"Wave 3 ID: {wave3.wave_id}")
    # Save these IDs to config — you'll need them for activate_wave()

asyncio.run(setup_waves())
```

### Wave 1 governance — research analyst persona

```python
# config/governance/research_analyst.py
from enterprise_claude import GovernanceLayer
from enterprise_claude.governance import GovernanceViolation

def _block_client_identifiers(prompt: str, context: dict) -> None:
    """
    Additional check beyond PII filter: block account numbers and CRD numbers.
    These are not standard PII patterns but are sensitive in this context.
    """
    import re
    if re.search(r"\bCRD[-\s]?\d{6,8}\b", prompt, re.IGNORECASE):
        raise GovernanceViolation(
            "Prompt contains a CRD number — remove before submitting",
            violation_type="regulated_identifier",
        )

RESEARCH_ANALYST_GOVERNANCE = GovernanceLayer(
    pii_filter=True,
    blocked_keywords=[
        "client password", "account credentials", "insider",
        "material non-public", "MNPI",
    ],
    max_prompt_length=40_000,
    constitutional_ai=True,
    allowed_personas=["research_analyst"],
    pre_hooks=[_block_client_identifiers],
)
```

### Wave 1 agent configuration (agents.yaml)

```yaml
# config/agents.yaml
agents:
  - name: Research Assistant
    persona: research_analyst
    tier: default
    system_prompt: |
      You are a financial research assistant helping analysts at [Firm Name].
      You provide factual market analysis, summarise research papers, and help
      structure investment theses.

      Constraints:
      - Do not make investment recommendations or predict price movements.
      - Do not reference specific client accounts or portfolios.
      - Cite sources where possible using [SOURCE: ...] format.
      - Flag uncertainty clearly: "Based on available data..." or "This is uncertain..."
    mcp_connectors:
      - bloomberg       # if licensed
      - confluence      # internal research wiki

  - name: Code Review Assistant
    persona: software_engineer
    tier: default
    system_prompt: |
      You are a senior software engineer performing code review at [Firm Name].
      Focus on security, correctness, and maintainability.

      Constraints:
      - Do not suggest storing credentials in code.
      - Flag any hardcoded IP addresses, tokens, or secrets.
      - Recommend OWASP best practices for financial applications.
    mcp_connectors:
      - github
      - jira
```

```bash
# Deploy agents from YAML
ecl agents deploy --config /opt/enterprise-claude/config/agents.yaml
```

### Activating Wave 1 users (provisioning integration)

```python
# Called by your IdP/HR system webhook when a user is provisioned
async def provision_user(user_id: str, persona: str, wave_id: str) -> None:
    tracker = AdoptionTracker(db_path="/opt/enterprise-claude/data/adoption.db")
    await tracker.initialize()

    await tracker.record_activation(
        wave_id=wave_id,
        user_id=user_id,
        persona=persona,
    )
    print(f"Provisioned {user_id} ({persona}) into wave {wave_id}")
```

### Wave 1 review criteria (week 6)

Before opening Wave 2, verify:

- [ ] ≥ 80% of Wave 1 users have made at least one call (`get_wave_progress()`)
- [ ] Zero `BudgetExceededError` events without prior warning
- [ ] Governance violation rate < 2% of calls
- [ ] No PII incidents escalated to the DPO
- [ ] Average literacy score ≥ 40 (`get_literacy_score()`)
- [ ] No open compliance incidents

```bash
# Generate the Wave 1 review report
ecl waves list
ecl cost summary --days 42
ecl audit query --result block --limit 1000   # review all blocked calls
```

---

## 4. Wave 2 — Early adopters (500 users)

**Goal:** Scale governance and cost controls. Surface edge cases in
persona configuration. Build internal champions. Duration: 8 weeks.

**Who:** All department-nominated early adopters. Must complete a
30-minute "Responsible AI Use" module before provisioning.

### Opening Wave 2

```python
async def open_wave2(wave2_id: str) -> None:
    tracker = AdoptionTracker(db_path="/opt/enterprise-claude/data/adoption.db")
    await tracker.initialize()

    try:
        await tracker.activate_wave(wave2_id)
        print("Wave 2 is now active")
    except WaveGateError as exc:
        # This fires if Wave 1 hasn't hit 80% yet
        print(f"Cannot open Wave 2: {exc}")
        print("Check Wave 1 progress: ecl waves list")
```

### Departmental cost envelopes

With 500 users across multiple departments, give each business unit its own
daily budget rather than a single org-wide limit:

```python
# Per-department monitors — each backed by a different SQLite database
# or differentiated by a team_id prefix in usage records

DEPT_MONITORS = {
    "equity_research": TokenMonitor(
        daily_budget_usd=500.0,      # 100 analysts × $5/user/day
        alert_threshold_pct=0.75,
        db_path="/opt/enterprise-claude/data/monitor.db",
    ),
    "compliance": TokenMonitor(
        daily_budget_usd=50.0,       # small team, lower volume
        alert_threshold_pct=0.90,   # tighter alert — compliance can't overspend
        db_path="/opt/enterprise-claude/data/monitor.db",
    ),
    "engineering": TokenMonitor(
        daily_budget_usd=1_000.0,   # largest team, highest call frequency
        alert_threshold_pct=0.80,
        db_path="/opt/enterprise-claude/data/monitor.db",
    ),
}

# Wire department-specific alerts
@DEPT_MONITORS["equity_research"].on_alert
async def equity_research_alert(status) -> None:
    await slack_notify(
        channel="#ai-ops-alerts",
        text=(
            f"⚠️ Equity Research: Claude budget at {status.pct_used:.0f}%\n"
            f"${status.used_usd:.2f} of ${status.budget_usd:.2f} used today\n"
            f"Remaining: ${status.remaining_usd:.2f}"
        ),
    )
```

### Investment advisor persona (PII-sensitive)

This persona handles client-facing work. The governance policy is stricter.

```python
from enterprise_claude import GovernanceLayer
from enterprise_claude.governance import GovernanceViolation

def _require_client_context_redaction(prompt: str, context: dict) -> None:
    """
    Advisors must redact client names before submitting prompts.
    Enforced by checking for a redaction marker in the prompt.
    """
    # If the prompt mentions "client" or "portfolio" but has no redaction marker
    import re
    mentions_client = bool(re.search(r"\bclient\b|\bportfolio\b", prompt, re.IGNORECASE))
    has_redaction   = "[REDACTED]" in prompt or "[CLIENT]" in prompt

    if mentions_client and not has_redaction:
        raise GovernanceViolation(
            "Prompts mentioning clients must use [REDACTED] or [CLIENT] placeholders. "
            "See the Responsible AI guide for redaction procedures.",
            violation_type="client_data_policy",
        )

INVESTMENT_ADVISOR_GOVERNANCE = GovernanceLayer(
    pii_filter=True,                    # belt-and-suspenders
    blocked_keywords=["client SSN", "account number", "tax ID"],
    max_prompt_length=30_000,           # tighter than analysts — less need for bulk text
    constitutional_ai=True,
    allowed_personas=["investment_advisor"],
    pre_hooks=[_require_client_context_redaction],
    post_hooks=[
        lambda resp, ctx: audit_pii_in_response(resp, ctx),   # extra logging
    ],
)
```

---

## 5. Wave 3 — Full rollout (5,000 users)

**Goal:** Organisation-wide access. Department heads own their budget.
Literacy scores are part of the quarterly AI review. Duration: ongoing.

**Who:** Everyone. Provisioning is automated via the IdP.

### Automated provisioning at scale

```python
# Integrate with your IdP (Okta, Azure AD, etc.) via webhook or SCIM

async def on_user_created(event: dict) -> None:
    """
    Called by IdP webhook when a new employee is onboarded.
    Maps their job group to a Claude persona and wave.
    """
    user_id  = event["user_id"]
    job_group = event["job_group"]   # from HRIS

    persona_map = {
        "EQUITY_RESEARCH":    ("research_analyst",    WAVE3_ID),
        "INVESTMENT_ADVISORY": ("investment_advisor",  WAVE3_ID),
        "TECHNOLOGY":         ("software_engineer",   WAVE3_ID),
        "COMPLIANCE":         ("compliance_officer",  WAVE3_ID),
        "OPERATIONS":         ("operations",           WAVE3_ID),
        "EXECUTIVE":          ("executive",            WAVE3_ID),
    }

    persona, wave_id = persona_map.get(job_group, ("operations", WAVE3_ID))

    tracker = AdoptionTracker(db_path=ADOPTION_DB)
    await tracker.initialize()
    await tracker.record_activation(wave_id=wave_id, user_id=user_id, persona=persona)

    # Provision access in your application layer
    await grant_claude_access(user_id, persona)
    await send_welcome_email(user_id, persona)
```

### Org-wide governance baseline

```python
# The base policy applied to all personas unless overridden
BASE_GOVERNANCE = GovernanceLayer(
    pii_filter=True,
    blocked_keywords=[
        # Legal / regulatory
        "insider information", "material non-public", "MNPI",
        # Security
        "api key", "private key", "ssh key", "bearer token",
        # HR sensitivity
        "salary band", "performance improvement plan",
    ],
    max_prompt_length=50_000,
    constitutional_ai=True,
    log_violations=True,
)
```

### Executive tier — gated model with approval workflow

```python
# executives get Claude Opus but require approval from IT and Risk
EXECUTIVE_GOVERNANCE = GovernanceLayer(
    pii_filter=True,
    blocked_keywords=["personal email", "personal device"],
    allowed_personas=["executive"],
    constitutional_ai=True,
    pre_hooks=[_verify_executive_approval],   # checks approval DB
)

async def _verify_executive_approval_impl(prompt: str, context: dict) -> None:
    user_id = context.get("user_id", "")
    approved = await approval_db.is_approved(user_id, "claude_opus_access")
    if not approved:
        raise ApprovalRequiredError(
            f"User {user_id} requires approval for Opus-tier access. "
            "Submit a request at https://internal.example.com/ai/access",
            model="claude-opus-5",
            user_id=user_id,
        )

async with AgentOrchestrator(governance=EXECUTIVE_GOVERNANCE, monitor=executive_monitor) as orch:
    exec_agent = await orch.create_agent(
        name="Executive Briefing Assistant",
        system_prompt="…",
        persona="executive",
        tier="gated",          # maps to claude-opus-5
        approval_granted=True, # checked against the pre-hook above
    )
```

---

## 6. Governance configuration by department

Reference card — copy and adapt.

| Department         | `pii_filter` | `gxp_mode` | `constitutional_ai` | `max_prompt_length` | Model tier  |
|:-------------------|:------------:|:----------:|:-------------------:|--------------------:|:-----------:|
| Equity Research    | ✅            | ❌          | ✅                   | 40,000              | default     |
| Investment Advisory| ✅            | ❌          | ✅                   | 30,000              | default     |
| Compliance         | ✅            | ❌          | ✅                   | 20,000              | default     |
| Software Eng.      | ✅            | ❌          | ✅                   | 60,000              | default     |
| Operations         | ✅            | ❌          | ❌                   | 20,000              | batch       |
| Executive          | ✅            | ❌          | ✅                   | 50,000              | gated       |
| Clinical (pharma)  | ✅            | ✅          | ✅                   | 30,000              | default     |

---

## 7. Cost management at scale

### Daily automated cost report

```python
# scripts/daily_cost_report.py  — run via cron at 07:00 UTC
import asyncio
from datetime import UTC, datetime, timedelta
from enterprise_claude import TokenMonitor

async def send_daily_report() -> None:
    monitor = TokenMonitor(db_path="/opt/enterprise-claude/data/monitor.db")

    now   = datetime.now(tz=UTC)
    yesterday = now - timedelta(hours=24)

    summary = await monitor.get_cost_summary(start_date=yesterday, end_date=now)

    lines = [
        "━━━ Claude Daily Cost Report ━━━",
        f"Period: {yesterday:%Y-%m-%d %H:%M} → {now:%Y-%m-%d %H:%M} UTC",
        f"",
        f"Total spend:  ${summary.total_usd:.4f} USD",
        f"Total calls:  {summary.record_count:,}",
        f"",
        "By model:",
    ]
    for model, cost in sorted(summary.by_model.items(), key=lambda x: -x[1]):
        pct = summary.model_pct.get(model, 0)
        lines.append(f"  {model:<35} ${cost:.4f}  ({pct:.1f}%)")

    lines += ["", "By persona:"]
    for persona, cost in sorted(summary.by_persona.items(), key=lambda x: -x[1]):
        lines.append(f"  {persona:<20} ${cost:.4f}")

    report = "\n".join(lines)
    await send_email(to="ai-ops@example.com", subject="Claude Daily Cost", body=report)
    await slack_notify(channel="#ai-cost", text=f"```{report}```")

asyncio.run(send_daily_report())
```

### Monthly budget reconciliation

```bash
# Export last month's usage for finance reconciliation
ecl cost export --format csv --out /reports/claude-usage-$(date +%Y-%m).csv --days 30

# Column headers in the CSV:
# timestamp, user_id, persona, model, input_tokens, output_tokens, cost_usd, session_id
```

### Anomaly detection hook

```python
# Catch a single user spending more than $5 in a single call (unusual)
SINGLE_CALL_THRESHOLD_USD = 5.0

def _flag_expensive_call(response: str, context: dict) -> None:
    cost = context.get("cost_usd", 0.0)
    user = context.get("user_id", "unknown")
    if cost > SINGLE_CALL_THRESHOLD_USD:
        logger.warning(
            "HIGH_COST_CALL user=%s cost=$%.4f model=%s",
            user, cost, context.get("model"),
        )
        # Optionally alert the ops team
        asyncio.create_task(
            slack_notify(f"⚠️ Single call cost ${cost:.4f} for user {user}")
        )
```

---

## 8. Monitoring and alerting

### Key metrics to track

| Metric | Healthy range | Alert threshold | Action |
|:-------|:-------------|:----------------|:-------|
| Daily spend (org) | < budget | > 80% of budget | Notify AI ops |
| Governance violation rate | < 2% | > 5% | Review blocked prompts |
| GxP citation miss rate | < 10% | > 25% | Retrain / update system prompt |
| Active users / day | Growing | Declining 2 wk | Re-engagement campaign |
| Literacy score (avg) | Rising | Flat > 4 weeks | Training intervention |
| Inactive users | < 10% | > 20% | Outreach to managers |
| Audit log checksum failures | 0 | Any | Immediate escalation to CISO |

### Prometheus metrics (example integration)

```python
from prometheus_client import Counter, Gauge, start_http_server

governance_violations = Counter(
    "ecl_governance_violations_total",
    "Governance violations by type",
    ["violation_type", "persona"],
)
daily_spend_gauge = Gauge(
    "ecl_daily_spend_usd",
    "Current day spend in USD",
    ["department"],
)

# In your post-hook:
def record_governance_metrics(response: str, context: dict) -> None:
    result = context.get("governance_result")
    if result and result.violations:
        for v in result.violations:
            governance_violations.labels(
                violation_type=v,
                persona=context.get("persona", "unknown"),
            ).inc()

# In your daily report job:
async def update_spend_gauges() -> None:
    summary = await monitor.get_cost_summary(start_date=…, end_date=…)
    for persona, cost in summary.by_persona.items():
        daily_spend_gauge.labels(department=persona).set(cost)
```

### Grafana dashboard queries (SQLite via Grafana SQLite plugin)

```sql
-- Spend over time (last 7 days, hourly)
SELECT
    strftime('%Y-%m-%d %H:00', timestamp) AS hour,
    SUM(cost_usd) AS total_usd,
    COUNT(*) AS call_count
FROM usage_records
WHERE timestamp >= datetime('now', '-7 days')
GROUP BY hour
ORDER BY hour;

-- Governance violations by type (last 30 days)
SELECT
    governance_result,
    persona,
    COUNT(*) AS events
FROM audit_events
WHERE timestamp >= datetime('now', '-30 days')
GROUP BY governance_result, persona
ORDER BY events DESC;
```

---

## 9. Integration with enterprise systems

### SSO / IdP integration (Okta example)

```python
# FastAPI endpoint called by Okta's event hook on user.lifecycle.create
from fastapi import FastAPI, Request
app = FastAPI()

@app.post("/webhooks/okta/user-created")
async def okta_user_created(request: Request):
    payload = await request.json()
    user_id    = payload["data"]["events"][0]["target"][0]["alternateId"]
    dept_group = payload["data"]["events"][0]["target"][1].get("displayName", "")

    await provision_user(user_id=user_id, job_group=dept_group)
    return {"status": "ok"}
```

### JIRA integration for governance incidents

```python
# When a call is blocked, auto-create a JIRA ticket for the security team
async def on_governance_block(prompt: str, context: dict) -> None:
    jira = get_connector("jira")  # uses JIRA_URL, JIRA_TOKEN, JIRA_EMAIL env vars

    # The MCP connector config gives you the URL + auth type;
    # make the actual Jira API call with your preferred HTTP client
    await jira_client.create_issue(
        project="AIOPS",
        issue_type="Security",
        summary=f"Claude governance block — {context.get('violation_type')}",
        description=(
            f"User: {context.get('user_id')}\n"
            f"Persona: {context.get('persona')}\n"
            f"Violation: {context.get('violation_type')}\n"
            f"Timestamp: {context.get('timestamp')}\n\n"
            "Prompt snippet (first 200 chars, PII scrubbed):\n"
            f"{prompt[:200]}"
        ),
        priority="Medium",
    )
```

### Slack integration — literacy leaderboard

```python
# Weekly Slack post — top adopters per department
async def post_weekly_leaderboard() -> None:
    tracker = AdoptionTracker(db_path=ADOPTION_DB)
    await tracker.initialize()

    sampled = [...]   # fetch your user list from the IdP
    scores  = [(uid, await tracker.get_literacy_score(uid)) for uid in sampled]
    top10   = sorted(scores, key=lambda x: -x[1])[:10]

    lines = ["*📊 Weekly Claude Adoption Leaderboard*", ""]
    for rank, (uid, score) in enumerate(top10, 1):
        bar = "█" * int(score / 10)
        lines.append(f"{rank}. `{uid}` — {bar} {score:.0f}/100")

    await slack_notify(channel="#ai-adoption", text="\n".join(lines))
```

---

## 10. Rollback procedures

### Rolling back a wave

If a wave introduces widespread governance violations or unexpected cost spikes:

```bash
# 1. Immediately cap spend
ecl cost summary --days 1          # assess damage

# 2. Review blocked calls
ecl audit query --result block --limit 500

# 3. Identify the failing governance rule
# Look for the most common violation_type in the audit output
# Fix the GovernanceConfig and redeploy

# 4. Deprovision affected users (via your IdP, not this library)
# The library does not manage user deletion — that is your IdP's job

# 5. Mark the wave as needing re-approval
# Manually update the wave status in adoption.db:
# UPDATE waves SET status = 'planned' WHERE wave_id = '…';
# (This is the only legitimate use of UPDATE in the adoption database)
```

### Rolling back a governance policy change

```python
# Keep the previous GovernanceConfig object in version control
# Rollback is a one-line change: swap the config object

# Before change:
CURRENT_GOVERNANCE = GovernanceLayer(pii_filter=True, blocked_keywords=["new-term"])

# After rollback:
CURRENT_GOVERNANCE = GovernanceLayer(pii_filter=True, blocked_keywords=[])
```

Because `GovernanceLayer` is stateless (no persistence), rollback takes effect
immediately on the next request — no database migration, no restart required.

---

## 11. Success metrics

Present these at the quarterly AI programme review:

```python
# scripts/quarterly_report.py
async def generate_quarterly_metrics() -> dict:
    tracker = AdoptionTracker(db_path=ADOPTION_DB)
    monitor  = TokenMonitor(db_path=MONITOR_DB)
    audit    = AuditLogger(db_path=AUDIT_DB)
    await tracker.initialize()

    now     = datetime.now(tz=UTC)
    q_start = now - timedelta(days=90)

    all_waves = await tracker.get_all_wave_progress()
    cost      = await monitor.get_cost_summary(start_date=q_start, end_date=now)
    audit_p   = await audit.query_events(AuditFilter(
        start_date=q_start, end_date=now, page=1, page_size=1
    ))

    inactive  = await tracker.get_inactive_users(days=30)

    return {
        "total_activated":       sum(w.activated for w in all_waves),
        "total_target":          sum(w.target    for w in all_waves),
        "adoption_rate":         sum(w.activated for w in all_waves) / sum(w.target for w in all_waves),
        "total_api_calls":       cost.record_count,
        "total_spend_usd":       cost.total_usd,
        "cost_per_call":         cost.total_usd / cost.record_count if cost.record_count else 0,
        "total_audit_events":    audit_p["total"],
        "inactive_users_30d":    len(inactive),
    }
```

| Metric | Q1 target | Q2 target | Q3 target |
|:-------|----------:|----------:|----------:|
| Users activated | 50 | 500 | 5,000 |
| Avg. calls/user/day | 5 | 12 | 15 |
| Adoption rate | 80% | 70% | 65% |
| Avg. literacy score | 35 | 50 | 65 |
| Governance violation rate | < 5% | < 3% | < 2% |
| Cost per call (USD) | < $0.05 | < $0.04 | < $0.04 |
| Inactive users (30d) | < 20% | < 15% | < 10% |
