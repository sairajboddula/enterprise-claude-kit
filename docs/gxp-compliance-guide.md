# GxP Compliance Guide

## Using enterprise-claude-kit in Regulated Pharma Environments

This guide is written for **Quality Assurance engineers, validation specialists,
and regulatory affairs teams** deploying Claude in environments governed by
21 CFR Part 11, EU Annex 11, ICH E6(R2), or equivalent GxP frameworks.

It covers what `gxp_mode=True` actually enforces, what it deliberately does not,
how to export audit trails for FDA/EMA submissions, and how to build the
validation documentation your Quality Management System requires.

---

## Table of Contents

1. [Regulatory context](#1-regulatory-context)
2. [What gxp\_mode enforces](#2-what-gxp_mode-enforces)
3. [What gxp\_mode does not enforce](#3-what-gxp_mode-does-not-enforce)
4. [Audit trail architecture](#4-audit-trail-architecture)
5. [SHA-256 tamper detection](#5-sha-256-tamper-detection)
6. [Setting up a GxP deployment](#6-setting-up-a-gxp-deployment)
7. [Exporting audit trails for submissions](#7-exporting-audit-trails-for-submissions)
8. [Routine integrity verification](#8-routine-integrity-verification)
9. [Validation documentation (IQ/OQ/PQ)](#9-validation-documentation-iqoqpq)
10. [GAMP 5 category and risk classification](#10-gamp-5-category-and-risk-classification)
11. [Use case: adverse event summarisation](#11-use-case-adverse-event-summarisation)
12. [Use case: clinical trial protocol review](#12-use-case-clinical-trial-protocol-review)
13. [Regulatory submission checklist](#13-regulatory-submission-checklist)

---

## 1. Regulatory context

### 21 CFR Part 11 (FDA — USA)

21 CFR Part 11 governs electronic records and electronic signatures in FDA-regulated
activities. The key requirements relevant to an AI system:

| § | Requirement | Library controls |
|:--|:------------|:----------------|
| 11.10(a) | Systems must be validated to ensure accuracy, reliability, consistent intended performance | IQ/OQ/PQ documentation; 66-test automated suite |
| 11.10(b) | The ability to generate accurate and complete copies of records | `AuditLogger.export_csv()` — complete, human-readable export |
| 11.10(c) | Protection of records to enable their accurate and ready retrieval throughout the records retention period | Append-only SQLite; configurable `retention_days` |
| 11.10(e) | Use of secure, computer-generated, time-stamped audit trails | UTC timestamps + SHA-256 checksums on every event |
| 11.10(g) | Use of authority checks to ensure that only authorised individuals can use the system | Wave gates + persona allowlists in GovernanceLayer |
| 11.10(k) | Use of appropriate controls over systems documentation | This document + version-controlled configuration |
| 11.50 | Signed records shall contain: printed name, date/time, meaning of signature | Human-review workflow required; the library records `user_hash` |

### EU Annex 11 (EMA — Europe)

Annex 11 covers computerised systems used in GxP activities in the EU. Key
items aligned with the library:

| Clause | Requirement | Library controls |
|:-------|:------------|:----------------|
| 4.8 | Data should be checked for integrity | SHA-256 checksums + `verify_checksums()` |
| 7.1 | Data entered manually into a computer should be checked | PII filter + pre-hooks |
| 9 | Audit trails | Append-only `audit_events` table with `governance_result` |
| 12.4 | Electronic signatures | Human approval workflow; library records user_hash |
| 13 | Printouts | `export_csv()` produces human-readable record |

### ICH E6(R2) — Good Clinical Practice

GCP requires that clinical trial records be attributable, legible, contemporaneous,
original, and accurate (ALCOA). The library's audit trail supports:

- **Attributable:** `user_hash` field — SHA-256 of `user_id`; reversible with your user directory
- **Legible:** structured fields in SQLite; exportable to CSV/JSON
- **Contemporaneous:** `timestamp` is set at API call time, stored as UTC ISO-8601
- **Original:** append-only — no UPDATE/DELETE ever issued
- **Accurate:** `checksum` field detects post-write modification

---

## 2. What gxp\_mode enforces

When `gxp_mode=True` is set on `GovernanceLayer` and `AuditLogger`, the
following controls activate automatically.

### GovernanceLayer — response citation check

Every Claude response is scanned for at least one regulatory citation marker:

```
Accepted formats (case-insensitive):
  [SOURCE: ICH E6(R2) Section 4.9]
  [REF: 21 CFR §312.32]
  [CITATION: FDA Guidance for Industry — Safety Reporting Requirements]
```

If no marker is found, `GovernanceResult.gxp_compliant` is set to `False`
and `"gxp_flag"` is added to `GovernanceResult.flags`.

**This is a flag, not a block by default.** Upgrade it to a block in a post-hook
if your SOPs require that non-cited responses are rejected:

```python
from enterprise_claude import GovernanceLayer
from enterprise_claude.governance import GovernanceViolation

def _require_gxp_citation(response: str, context: dict) -> None:
    """Promote gxp_flag from warning to blocking violation."""
    gov_result = context.get("governance_result")
    if gov_result and "gxp_flag" in gov_result.flags:
        raise GovernanceViolation(
            "Response does not contain a regulatory citation marker. "
            "Responses in GxP context must cite [SOURCE:], [REF:], or [CITATION:].",
            violation_type="missing_gxp_citation",
        )

governance = GovernanceLayer(
    gxp_mode=True,
    pii_filter=True,
    post_hooks=[_require_gxp_citation],   # ← promotes flag to block
)
```

### AuditLogger — SHA-256 checksum on every event

When `gxp_mode=True` is set on `AuditLogger`, every written event gets a
`checksum` field:

```python
audit = AuditLogger(db_path="audit.db", gxp_mode=True)
```

The checksum is:

```python
sha256(json.dumps(event_fields, sort_keys=True, ensure_ascii=False).encode())
```

All event fields are included except `checksum` itself. `sort_keys=True` makes
the serialisation deterministic across Python versions and platforms.

### AuditLogger — user privacy by design

The `user_id` passed to `agent.run(prompt, user_id="alice@pharma.com")` is
**never stored raw** in the audit database. Instead:

```python
user_hash = sha256("alice@pharma.com".encode()).hexdigest()
# stored as: "2bd806c97f0e00af1a1fc3328fa763a9269723c8db8fac4f93af71db186d6e90"
```

This satisfies GDPR data minimisation requirements while preserving the ability
to correlate all events from the same user. To reverse a hash during an audit
inquiry, cross-reference against your identity directory.

---

## 3. What gxp\_mode does not enforce

Understanding what the library does *not* do is critical for your risk assessment.

### ❌ Electronic signatures (21 CFR §11.50)

The library records `user_hash` but does not implement an electronic signature
workflow. If your SOPs require a signature (review, approval, or release steps),
you must implement that in your application layer. The `user_hash` can serve as
the "signatory record" in your e-sig workflow.

### ❌ Access control and authentication

The library does not authenticate users. It receives a `user_id` from your
application and trusts it. Access control (who can log in, which roles they
have, password requirements) is the responsibility of your identity provider
(Okta, Azure AD, PingFederate, etc.).

### ❌ Network-level data isolation

The library does not prevent prompts from leaving your network — it calls
`api.anthropic.com` over HTTPS. If your data classification policy prohibits
sending data to a third-party API, you cannot use this library without a
Data Processing Agreement (DPA) with Anthropic. Verify the Anthropic DPA
covers your data types before deployment.

### ❌ Response accuracy validation

The library checks that Claude's response *contains a citation marker* —
it does not verify that the cited regulation *actually exists* or that the
cited text is *correctly quoted*. Humans must review responses before they
inform clinical decisions, regulatory submissions, or SOPs.

### ❌ Audit log deletion prevention at the OS level

The library issues no `DELETE` or `UPDATE` against the audit database, but
it cannot prevent a database administrator with OS-level access from deleting
the SQLite file. For production GxP deployments, combine with:

- File system ACLs restricting write access to the app user only
- OS-level audit logging of file modifications (auditd, Windows Event Log)
- Nightly offsite backup to immutable storage (S3 Object Lock, Azure Blob Immutable)

---

## 4. Audit trail architecture

### Database schema

```sql
CREATE TABLE audit_events (
    event_id           TEXT PRIMARY KEY,          -- UUID4
    timestamp          TEXT NOT NULL,             -- UTC ISO-8601: "2025-03-14T09:26:53.589000+00:00"
    agent_id           TEXT NOT NULL,             -- UUID4 of the agent that made the call
    persona            TEXT NOT NULL,             -- e.g. "clinical_ops"
    model_used         TEXT NOT NULL,             -- e.g. "claude-sonnet-4-6"
    input_token_count  INTEGER NOT NULL,          -- tokens in the prompt
    output_token_count INTEGER NOT NULL,          -- tokens in the response
    governance_result  TEXT NOT NULL,             -- "pass" | "flag" | "block"
    hook_triggered     INTEGER NOT NULL,          -- 0 or 1 (boolean)
    session_id         TEXT NOT NULL,             -- UUID4, groups calls in a session
    user_hash          TEXT NOT NULL,             -- SHA-256(user_id) — no raw PII
    checksum           TEXT,                      -- SHA-256 of event JSON (gxp_mode only)
    metadata           TEXT DEFAULT '{}'          -- JSON blob for custom fields
);

CREATE INDEX idx_audit_timestamp ON audit_events(timestamp);
CREATE INDEX idx_audit_persona   ON audit_events(persona);
CREATE INDEX idx_audit_gov       ON audit_events(governance_result);
```

### What each field means for auditors

| Field | Regulatory meaning |
|:------|:------------------|
| `event_id` | Unique record identifier — reference in deviation reports |
| `timestamp` | When the Claude API call was made (UTC) |
| `agent_id` | Which configured agent handled the request |
| `persona` | Which role/function made the request |
| `model_used` | The exact AI model version — important for reproducibility |
| `input_token_count` | Measure of prompt complexity |
| `output_token_count` | Measure of response length |
| `governance_result` | `pass` = compliant; `flag` = warning; `block` = rejected |
| `hook_triggered` | Whether a custom governance hook fired |
| `session_id` | Groups related queries in a single user session |
| `user_hash` | Anonymised user identifier — reversible with identity directory |
| `checksum` | Integrity proof — failure indicates tampering |
| `metadata` | Custom fields (e.g., `study_id`, `protocol_version`) |

### Attaching study metadata

For clinical trial use, attach GCP-required context via `metadata`:

```python
# Pass study-specific context through the agent
result = await agent.run(
    prompt,
    user_id="dr.chen@sponsor.com",
    metadata={
        "study_id":         "PROTOCOL-2025-001",
        "site_id":          "SITE-042",
        "indication":       "Type 2 Diabetes",
        "protocol_version": "v3.1",
    },
)
# The metadata dict is stored verbatim in audit_events.metadata (JSON)
```

---

## 5. SHA-256 tamper detection

### How verification works

```python
from enterprise_claude.audit import AuditLogger

audit = AuditLogger(db_path="audit.db", gxp_mode=True)

# Returns: dict[event_id, bool]
# True  = checksum matches → event is intact
# False = checksum mismatch → event has been modified after write
checksums = await audit.verify_checksums()

intact   = {eid: ok for eid, ok in checksums.items() if ok}
tampered = {eid: ok for eid, ok in checksums.items() if not ok}

if tampered:
    # This is a GxP deviation — must be logged in your quality system
    raise RuntimeError(
        f"CRITICAL: Audit log integrity failure.\n"
        f"Tampered event IDs: {list(tampered.keys())}\n"
        f"Escalate immediately to Quality Assurance."
    )

print(f"Verified {len(intact)} events — all intact")
```

### What tamper detection covers

- Direct SQLite `UPDATE` or `DELETE` commands
- Hex-editor modification of the SQLite file
- Replacement of the file with an older backup

### What tamper detection does not cover

- Deletion of the entire SQLite file (no file = no checksums = nothing to verify)
- Replay attacks (inserting a valid historical record at a new position)
- Modification of the `checksum` field itself *and* re-computation to match

For the last scenario, `verify_checksums()` recomputes the hash from the stored
event fields. If an attacker modifies both a field *and* the stored checksum
to match, the verification will pass — this is the fundamental limitation of
self-referential checksums stored in the same database. For defence-in-depth,
ship checksums to an external immutable log (AWS CloudTrail, Azure Monitor,
a dedicated LIMS) and cross-verify.

---

## 6. Setting up a GxP deployment

### Full configuration example

```python
import asyncio
import os
from enterprise_claude import AgentOrchestrator, GovernanceLayer, TokenMonitor
from enterprise_claude.audit import AuditLogger
from enterprise_claude.governance import GovernanceViolation

# ── 1. GxP governance layer ───────────────────────────────────────────────────

def _post_hook_gxp_block(response: str, context: dict) -> None:
    """Promote missing citation from warning to hard block."""
    gov = context.get("governance_result")
    if gov and "gxp_flag" in gov.flags:
        raise GovernanceViolation(
            "Response lacks a regulatory citation marker ([SOURCE:], [REF:], [CITATION:]).",
            violation_type="missing_gxp_citation",
        )

def _pre_hook_log_query(prompt: str, context: dict) -> None:
    """Rich / logging hook — log query initiation with study context."""
    import logging
    logging.getLogger("gxp").info(
        "QUERY_INITIATED persona=%s study=%s",
        context.get("persona"),
        context.get("metadata", {}).get("study_id", "N/A"),
    )

governance = GovernanceLayer(
    pii_filter=True,
    gxp_mode=True,                              # citation check on every response
    constitutional_ai=True,
    blocked_keywords=["patient name", "subject ID"],   # belt-and-suspenders
    max_prompt_length=30_000,
    allowed_personas=["clinical_ops", "regulatory_affairs", "medical_writing"],
    pre_hooks=[_pre_hook_log_query],
    post_hooks=[_post_hook_gxp_block],          # hard block on missing citation
)

# ── 2. Audit logger ───────────────────────────────────────────────────────────
audit = AuditLogger(
    db_path="/opt/gxp-claude/data/audit.db",
    gxp_mode=True,             # enable SHA-256 checksums
    retention_days=3_650,      # 10 years — typical GxP requirement
)

# ── 3. Token monitor ─────────────────────────────────────────────────────────
monitor = TokenMonitor(
    daily_budget_usd=200.0,
    alert_threshold_pct=0.80,
    db_path="/opt/gxp-claude/data/monitor.db",
)

@monitor.on_alert
async def budget_alert(status) -> None:
    await notify_qa_team(
        f"Claude budget at {status.pct_used:.0f}% — ${status.used_usd:.2f} used today"
    )

# ── 4. System prompt (GCP-compliant framing) ─────────────────────────────────
CLINICAL_SYSTEM_PROMPT = """
You are a clinical operations specialist supporting GxP-regulated research at [Sponsor Name].

Regulatory citation requirement (MANDATORY):
For every factual claim about regulatory requirements, guidelines, or scientific data,
you MUST include a citation using exactly one of these formats:
  [SOURCE: <regulation or guideline name and section>]
  [REF: <document name §section>]
  [CITATION: <author, publication, year>]

Responses that do not include at least one such marker are non-compliant with our
Quality Management System and will be rejected.

Scope limitations:
- You provide information to support human decision-making; you do not make decisions.
- You do not provide patient-specific treatment recommendations.
- You do not interpret individual subject data without explicit study context.
- All outputs must be reviewed by a qualified person before use in regulated activities.
""".strip()

# ── 5. Run ────────────────────────────────────────────────────────────────────
async def main() -> None:
    async with AgentOrchestrator(
        governance=governance,
        monitor=monitor,
        audit_logger=audit,
    ) as orch:

        agent = await orch.create_agent(
            name="Clinical Operations Assistant",
            system_prompt=CLINICAL_SYSTEM_PROMPT,
            persona="clinical_ops",
        )

        try:
            result = await agent.run(
                "What are the expedited reporting timelines for SUSARs under EudraVigilance?",
                user_id="dr.schmidt@sponsor.com",
                metadata={
                    "study_id": "PROTOCOL-2025-001",
                    "site_id":  "EU-SITE-007",
                },
            )
        except GovernanceViolation as exc:
            print(f"Blocked: {exc}")
            return

        print(result.content)
        print(f"\nGxP compliant: {result.governance_result.gxp_compliant}")
        print(f"Cost: ${result.cost_usd:.6f}")

asyncio.run(main())
```

---

## 7. Exporting audit trails for submissions

### CSV export (most common for FDA submissions)

```python
from enterprise_claude.audit import AuditLogger, AuditFilter
from datetime import UTC, datetime

async def export_study_audit_trail(
    study_id: str,
    start_date: datetime,
    end_date: datetime,
    output_path: str,
) -> None:
    """
    Export the complete audit trail for a study period.

    The output CSV is suitable for inclusion in a regulatory submission
    as an electronic record supporting 21 CFR Part 11 compliance.
    """
    audit = AuditLogger(db_path="/opt/gxp-claude/data/audit.db", gxp_mode=True)

    # Export full date range — the metadata column will contain study_id
    await audit.export_csv(
        output_path=output_path,
        audit_filter=AuditFilter(
            start_date=start_date,
            end_date=end_date,
            persona="clinical_ops",     # or remove to export all personas
        ),
    )
    print(f"Exported audit trail to {output_path}")
```

### CSV column reference (for submission cover page)

```
Column            Type      Description
─────────────────────────────────────────────────────────────────
event_id          TEXT      Unique record identifier (UUID4)
timestamp         TEXT      UTC ISO-8601 datetime of the API call
agent_id          TEXT      Identifier of the configured AI agent
persona           TEXT      Operator role/function
model_used        TEXT      AI model version
input_token_count INTEGER   Tokens in the query
output_token_count INTEGER  Tokens in the response
governance_result TEXT      "pass" | "flag" | "block"
hook_triggered    INTEGER   1 if a custom governance hook fired
session_id        TEXT      Groups queries in a single session (UUID4)
user_hash         TEXT      SHA-256(user_id) — see identity log for mapping
checksum          TEXT      SHA-256 of all other fields — integrity proof
metadata          TEXT      JSON blob (contains study_id, site_id, etc.)
```

### Submitting to the FDA — practical guidance

The FDA does not prescribe a specific format for AI system audit logs. Use the
following approach for Module 5 (Clinical Study Reports) submissions:

1. **Export the CSV** for the study period using `export_study_audit_trail()`
2. **Export the checksum verification** as a separate document (see §8)
3. **Include a system description** (one paragraph, see template below)
4. **Include the IQ/OQ/PQ summary** (see §9)
5. **Attach all four documents** as Appendix N (or your sponsor's numbering convention)

**System description template (for regulatory submissions):**

> The clinical operations AI system (enterprise-claude-kit v0.1.0) was used
> to support [describe activity, e.g., "summarisation of incoming adverse event
> reports for triage prioritisation"]. The system is configured in GxP mode,
> which enforces: (a) regulatory citation requirements on every AI response,
> (b) PII filtering on all inputs, and (c) SHA-256 tamper-evident checksumming
> of all audit records. All AI outputs were reviewed by a qualified clinical
> operations specialist before use in regulated activities. The system audit
> trail, exported as a CSV file, is provided as Appendix N-1. Checksum
> verification results are provided as Appendix N-2. The system qualification
> documentation (IQ/OQ/PQ) is provided as Appendix N-3.

---

## 8. Routine integrity verification

### Daily verification job

```python
# scripts/daily_integrity_check.py
# Run via cron at 06:00 UTC, before business hours

import asyncio
import logging
from datetime import UTC, datetime
from enterprise_claude.audit import AuditLogger

logger = logging.getLogger("gxp.integrity")

async def run_daily_integrity_check() -> None:
    audit = AuditLogger(db_path="/opt/gxp-claude/data/audit.db", gxp_mode=True)

    logger.info("Starting daily integrity check at %s", datetime.now(tz=UTC).isoformat())

    checksums = await audit.verify_checksums()

    intact   = sum(1 for ok in checksums.values() if ok)
    tampered = [eid for eid, ok in checksums.items() if not ok]

    logger.info(
        "Integrity check complete: %d events verified, %d failures",
        len(checksums), len(tampered),
    )

    if tampered:
        # This is a GxP deviation — must be investigated and documented
        logger.critical(
            "AUDIT LOG INTEGRITY FAILURE — %d tampered events: %s",
            len(tampered), tampered,
        )
        # Open a CAPA (Corrective and Preventive Action) in your QMS
        await open_quality_deviation(
            severity="Critical",
            title="AI Audit Log Integrity Failure",
            description=(
                f"Daily integrity check detected {len(tampered)} tampered audit events.\n"
                f"Tampered event IDs: {', '.join(tampered)}\n"
                f"Detected at: {datetime.now(tz=UTC).isoformat()}"
            ),
        )
        raise SystemExit(1)   # non-zero exit code alerts the monitoring system

    logger.info("All %d audit events verified intact.", intact)
    print(f"✅ Integrity check passed — {intact} events verified")

asyncio.run(run_daily_integrity_check())
```

### Offsite checksum backup

Ship checksums to an external immutable store nightly:

```python
# After verify_checksums(), write the results to S3 Object Lock
import json, boto3

async def backup_checksums_to_s3(checksums: dict[str, bool]) -> None:
    s3 = boto3.client("s3")
    key = f"audit-checksums/{datetime.now(tz=UTC):%Y/%m/%d}/checksums.json"
    s3.put_object(
        Bucket="your-immutable-audit-bucket",
        Key=key,
        Body=json.dumps({
            "generated_at": datetime.now(tz=UTC).isoformat(),
            "system": "enterprise-claude-kit",
            "checksums": checksums,
        }).encode(),
        ObjectLockMode="COMPLIANCE",
        ObjectLockRetainUntilDate=datetime(2034, 12, 31, tzinfo=UTC),  # 10 years
    )
```

---

## 9. Validation documentation (IQ/OQ/PQ)

This section provides the structure and test evidence for your QMS. Execute
these tests in your validated environment and record the results.

### IQ — Installation Qualification

Objective: Confirm the software is correctly installed and configured.

```bash
# IQ-01: Verify correct package version
pip show enterprise-claude-kit | grep Version
# Expected: Version: 0.1.0 (or the version being qualified)

# IQ-02: Verify Python version compatibility
python --version
# Expected: Python 3.11.x or 3.12.x

# IQ-03: Verify dependencies are present at the required versions
pip show anthropic pydantic aiosqlite
# Expected: anthropic >= 0.40.0, pydantic >= 2.0, aiosqlite >= 0.19

# IQ-04: Verify package imports without error
python -c "import enterprise_claude; print(enterprise_claude.__version__)"
# Expected: 0.1.0 (no ImportError)

# IQ-05: Verify environment variable configuration
python -c "
from dotenv import load_dotenv; load_dotenv()
import os; print('ANTHROPIC_API_KEY set:', bool(os.environ.get('ANTHROPIC_API_KEY')))
"
# Expected: ANTHROPIC_API_KEY set: True
```

### OQ — Operational Qualification

Objective: Confirm the software operates as specified under defined conditions.

```bash
# OQ-01: Full automated test suite passes
python -m pytest tests/ --tb=short -q
# Expected: 66 passed, 0 failed (or more if tests have been added)

# OQ-02: Type annotations are correct (mypy)
python -m mypy enterprise_claude/ --python-version 3.12 --ignore-missing-imports
# Expected: Success: no issues found in 8 source files

# OQ-03: Code quality checks pass (ruff)
python -m ruff check .
# Expected: All checks passed!
```

```python
# OQ-04: PII filter correctly intercepts
import asyncio
from enterprise_claude import GovernanceLayer
from enterprise_claude.governance import GovernanceViolation

async def test_pii_filter():
    gov = GovernanceLayer(pii_filter=True)
    # Email in prompt must be detected
    result = await gov.pre_check(
        prompt="Please analyse data for patient john.doe@hospital.com",
        persona="clinical_ops",
        model="claude-sonnet-4-6",
    )
    assert len(result.pii_detected) >= 1
    assert any(m.pii_type == "email" for m in result.pii_detected)
    print("OQ-04 PASS: PII filter detects email addresses")

asyncio.run(test_pii_filter())
```

```python
# OQ-05: GxP citation check correctly flags non-compliant responses
async def test_gxp_flag():
    gov = GovernanceLayer(gxp_mode=True)
    result = await gov.post_check(
        response="Adverse events should be reported within 15 days.",   # no citation
        persona="clinical_ops",
        model="claude-sonnet-4-6",
    )
    assert result.gxp_compliant is False
    assert "gxp_flag" in result.flags
    print("OQ-05 PASS: GxP mode flags response without citation marker")

asyncio.run(test_gxp_flag())
```

```python
# OQ-06: SHA-256 tamper detection identifies modified records
import asyncio, aiosqlite, tempfile, os
from enterprise_claude.audit import AuditLogger, AuditEvent, AuditFilter
from datetime import UTC, datetime
from uuid import uuid4

async def test_tamper_detection():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "audit.db")
        audit   = AuditLogger(db_path=db_path, gxp_mode=True)

        event = AuditEvent(
            event_id=str(uuid4()),
            timestamp=datetime.now(tz=UTC),
            agent_id=str(uuid4()),
            persona="clinical_ops",
            model_used="claude-sonnet-4-6",
            input_token_count=100,
            output_token_count=200,
            governance_result="pass",
            hook_triggered=False,
            session_id=str(uuid4()),
            user_hash="abc123",
        )
        await audit.log_event(event)

        # Tamper with the record directly
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "UPDATE audit_events SET governance_result = 'block' WHERE event_id = ?",
                (event.event_id,),
            )
            await db.commit()

        # Verify detects the tamper
        checksums = await audit.verify_checksums()
        assert checksums[event.event_id] is False
        print("OQ-06 PASS: Tamper detection identifies modified governance_result")

asyncio.run(test_tamper_detection())
```

### PQ — Performance Qualification

Objective: Confirm the system performs correctly in the production-equivalent environment.

```python
# PQ-01: GxP configuration processes a realistic clinical query
# Run this against the production (or production-equivalent) environment

import asyncio
from enterprise_claude import AgentOrchestrator, GovernanceLayer, TokenMonitor
from enterprise_claude.audit import AuditLogger

async def pq_gxp_end_to_end():
    """
    Acceptance criterion:
    1. Query completes without exception
    2. governance_result.gxp_compliant is True (Claude includes a citation)
    3. AuditLogger records 1 event
    4. Checksum verifies as intact
    """
    import tempfile, os, shutil
    tmpdir = tempfile.mkdtemp()
    try:
        gov   = GovernanceLayer(pii_filter=True, gxp_mode=True)
        mon   = TokenMonitor(daily_budget_usd=10.0, db_path=os.path.join(tmpdir, "m.db"))
        audit = AuditLogger(db_path=os.path.join(tmpdir, "a.db"), gxp_mode=True)

        async with AgentOrchestrator(governance=gov, monitor=mon, audit_logger=audit) as orch:
            agent = await orch.create_agent(
                name="PQ Test Agent",
                system_prompt=(
                    "You are a clinical specialist. "
                    "ALWAYS include [SOURCE:], [REF:], or [CITATION:] markers."
                ),
                persona="clinical_ops",
            )
            result = await agent.run(
                "What are SUSAR reporting timelines under EudraVigilance?",
                user_id="pq_test_user",
            )

        # Assertions
        assert result.content, "Response is empty"
        assert result.governance_result.gxp_compliant, "GxP citation missing"

        from enterprise_claude.audit import AuditFilter
        page = await audit.query_events(AuditFilter(page=1, page_size=10))
        assert page["total"] == 1, f"Expected 1 audit event, got {page['total']}"

        checksums = await audit.verify_checksums()
        assert all(checksums.values()), "Checksum verification failed"

        print("PQ-01 PASS: GxP end-to-end query completed successfully")
        print(f"  Response length:   {len(result.content)} chars")
        print(f"  GxP compliant:     {result.governance_result.gxp_compliant}")
        print(f"  Cost:              ${result.cost_usd:.6f}")
        print(f"  Audit events:      {page['total']}")
        print(f"  Checksum intact:   {all(checksums.values())}")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

asyncio.run(pq_gxp_end_to_end())
```

---

## 10. GAMP 5 category and risk classification

GAMP 5 classifies computerised systems into categories:

| GAMP 5 Category | Description | This library |
|:----------------|:------------|:-------------|
| 1 | Infrastructure | No |
| 3 | Non-configured software (COTS, fixed function) | Partial — the core library |
| 4 | Configured software | Yes — your governance configuration |
| 5 | Custom software | Yes — any custom hooks you write |

**Recommended classification:** **Category 4** for the deployed system
(configured library + your governance config + system prompt).

**Risk level:** Medium to High, depending on:
- Whether AI outputs directly inform patient safety decisions → High
- Whether AI outputs are reviewed by a qualified person before use → Medium

**Validation depth appropriate for Category 4 / Medium risk:**
- IQ: Installation and configuration checks (§9 IQ above)
- OQ: Test suite + functional tests per OQ above
- PQ: End-to-end GxP test with production-equivalent data (§9 PQ above)
- Periodic review: Annual revalidation or after each version upgrade

---

## 11. Use case: adverse event summarisation

```python
AE_SYSTEM_PROMPT = """
You are a pharmacovigilance specialist supporting adverse event (AE) triage
at [Sponsor Name].

Your role:
- Summarise incoming AE reports to assist qualified clinicians with triage
- Extract: onset date, severity (mild/moderate/severe/life-threatening/fatal),
  causality assessment, and outcome
- Flag any expedited reporting criteria (death, life-threatening, hospitalisation,
  significant disability, congenital anomaly)

Mandatory citation requirement:
For any statement about reporting requirements or causality criteria, include
[SOURCE: <regulation>] or [REF: <guideline §section>].

Critical constraint:
You are a decision-support tool. A qualified pharmacovigilance physician must
review and approve all outputs before any regulatory action is taken.
""".strip()
```

**Expedited reporting trigger — post-hook example:**

```python
import re

def _flag_expedited_reporting(response: str, context: dict) -> None:
    """
    Detect if Claude's summary suggests expedited reporting criteria.
    Adds a flag to the governance result for the pharmacovigilance team.
    """
    expedited_keywords = [
        "fatal", "death", "life-threatening", "hospitalisation",
        "significant disability", "congenital anomaly", "15-day",
    ]
    if any(kw in response.lower() for kw in expedited_keywords):
        context.setdefault("flags", []).append("potential_expedited_reporting")
        # This surfaces in the Rich UI / logs; does NOT block the response
```

---

## 12. Use case: clinical trial protocol review

```python
PROTOCOL_REVIEW_SYSTEM_PROMPT = """
You are a clinical operations specialist reviewing study protocols for
compliance with ICH E6(R2) Good Clinical Practice.

For each protocol section you review, you must:
1. Identify any gaps relative to ICH E6(R2) requirements
2. Cite the specific ICH E6(R2) section using [REF: ICH E6(R2) §X.X]
3. Provide a recommended remediation for each gap
4. Rate gap severity: Minor / Major / Critical

Output format for each finding:
  Finding N: [Description]
  Regulation: [REF: ...]
  Severity: Minor | Major | Critical
  Recommendation: [Specific remediation]

Do not provide a finding for sections that are compliant — only flag gaps.
""".strip()
```

---

## 13. Regulatory submission checklist

Before including AI-assisted outputs in a regulatory submission:

**System controls (confirm at deployment):**
- [ ] `gxp_mode=True` confirmed on both `GovernanceLayer` and `AuditLogger`
- [ ] `pii_filter=True` confirmed
- [ ] `retention_days` set to ≥ 10 years (or per your SOPs)
- [ ] Offsite backup of `audit.db` configured and verified
- [ ] Daily integrity check script running and alerts configured
- [ ] System qualification (IQ/OQ/PQ) executed and documented
- [ ] Validation report reviewed and signed by QA

**Per-submission (confirm for each document that cites AI assistance):**
- [ ] Export `audit.db` CSV for the relevant study period
- [ ] Run `verify_checksums()` and export the results
- [ ] Confirm all relevant audit events show `governance_result = "pass"` or `"flag"` (not `"block"`)
- [ ] Confirm all AI outputs were reviewed by a qualified person (recorded in your QMS)
- [ ] Confirm no unresolved GxP flags from the daily integrity check for the study period
- [ ] Include system description paragraph in the submission cover
- [ ] Attach: audit CSV (Appendix N-1), checksum verification (N-2), IQ/OQ/PQ summary (N-3)
- [ ] Confirm the Anthropic DPA is current and covers the data types submitted to the API
