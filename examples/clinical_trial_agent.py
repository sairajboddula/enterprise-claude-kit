"""
Clinical Trial Agent — GxP Mode
================================
Demonstrates enterprise-claude-kit configured for regulated pharma environments.

Key features shown:
  • gxp_mode=True  — every Claude response must include a [SOURCE:] / [REF:] /
                     [CITATION:] marker; flagged when absent
  • Persona: "clinical_ops"
  • Pre-hook: Rich log when a query is initiated
  • Post-hook: Rich compliance check on the response
  • Audit trail: queried via AuditLogger after the run
  • Tamper detection: SHA-256 checksum verification

Run:
    python examples/clinical_trial_agent.py

Requires:
    ANTHROPIC_API_KEY set in .env (or the environment)
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from datetime import UTC, datetime

from dotenv import load_dotenv
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from enterprise_claude import AgentOrchestrator, GovernanceLayer, TokenMonitor
from enterprise_claude.audit import AuditFilter, AuditLogger
from enterprise_claude.governance import GovernanceViolation

load_dotenv()

console = Console()

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a clinical operations specialist with deep expertise in ICH guidelines, "
    "FDA regulations, and GCP requirements.  "
    "IMPORTANT: For every factual claim, you MUST include a citation using one of these "
    "exact formats: [SOURCE: <regulation name>], [REF: <document §section>], or "
    "[CITATION: <document>].  Responses missing citations are non-compliant."
)

QUERY = (
    "What are the key requirements for adverse event (AE) reporting in Phase III "
    "clinical trials?  Include specific reporting timelines, responsible parties, "
    "and the relevant regulatory framework.  "
    "(Reminder: cite all regulatory sources using [SOURCE:] or [REF:] markers.)"
)

DEMO_HEADER = """
  [bold cyan]enterprise-claude-kit[/bold cyan] · Clinical Trial Agent (GxP Mode)
  Pharma compliance  ·  Audit trail  ·  Tamper detection
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compliance_badge(compliant: bool | None) -> Text:
    if compliant is True:
        return Text("✅  COMPLIANT — source markers present", style="bold green")
    if compliant is False:
        return Text("⚠   NON-COMPLIANT — no source marker found", style="bold yellow")
    return Text("—  GxP check not applicable", style="dim")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    console.print(Panel(DEMO_HEADER, border_style="cyan", padding=(0, 2)))
    console.print()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        console.print(
            Panel(
                "[bold red]ANTHROPIC_API_KEY is not set.[/bold red]\n"
                "Copy [cyan].env.example[/cyan] → [cyan].env[/cyan] and fill in your key.",
                title="[bold red]Missing API Key[/bold red]",
                border_style="red",
            )
        )
        return

    tmpdir = tempfile.mkdtemp(prefix="ecl_gxp_demo_")
    monitor_db = os.path.join(tmpdir, "monitor.db")
    audit_db   = os.path.join(tmpdir, "audit.db")
    try:
        await _run_demo(monitor_db, audit_db)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def _run_demo(monitor_db: str, audit_db: str) -> None:
    # ── Pre-hook: Rich console log ─────────────────────────────────────────────
    def pre_hook(prompt: str, context: dict) -> None:  # type: ignore[type-arg]
        ts = datetime.now(tz=UTC).strftime("%H:%M:%S UTC")
        console.print(
            f"  [dim]{ts}[/dim]  "
            f"[cyan]▶ QUERY INITIATED[/cyan]  "
            f"persona=[bold]{context.get('persona', '?')}[/bold]"
        )

    # ── Post-hook: GxP compliance check ───────────────────────────────────────
    def post_hook(response: str, context: dict) -> None:  # type: ignore[type-arg]
        ts = datetime.now(tz=UTC).strftime("%H:%M:%S UTC")
        has_marker = "[SOURCE:" in response or "[REF:" in response or "[CITATION:" in response
        badge = "[green]✅ COMPLIANT[/green]" if has_marker else "[yellow]⚠ MISSING MARKER[/yellow]"
        console.print(
            f"  [dim]{ts}[/dim]  "
            f"[cyan]◀ RESPONSE RECEIVED[/cyan]  "
            f"GxP={badge}"
        )

    # ── Governance: GxP mode + hooks ──────────────────────────────────────────
    governance = GovernanceLayer(
        pii_filter=True,
        gxp_mode=True,
        pre_hooks=[pre_hook],
        post_hooks=[post_hook],
    )

    monitor = TokenMonitor(daily_budget_usd=10.0, db_path=monitor_db)
    audit   = AuditLogger(db_path=audit_db, gxp_mode=True)

    # ── Config summary ────────────────────────────────────────────────────────
    cfg_table = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
    cfg_table.add_column("Key", style="dim")
    cfg_table.add_column("Value")
    cfg_table.add_row("GxP mode",       "[green]enabled[/green] — requires [SOURCE:] / [REF:] / [CITATION:]")
    cfg_table.add_row("PII filter",     "[green]enabled[/green]")
    cfg_table.add_row("Audit logger",   "[green]enabled[/green] + SHA-256 checksums")
    cfg_table.add_row("Persona",        "clinical_ops")
    cfg_table.add_row("Model tier",     "default → claude-sonnet-4-6")
    console.print(Panel(cfg_table, title="[bold]GxP Configuration[/bold]", border_style="blue"))
    console.print()

    # ── Show the query ────────────────────────────────────────────────────────
    console.print(
        Panel(QUERY, title="[bold]Clinical Trial Query[/bold]", border_style="dim", padding=(1, 2))
    )
    console.print()
    console.print(Rule("[dim]hook log[/dim]"))

    # ── Run the agent ─────────────────────────────────────────────────────────
    async with AgentOrchestrator(
        governance=governance,
        monitor=monitor,
        audit_logger=audit,
    ) as orch:

        agent = await orch.create_agent(
            name="Clinical Ops Assistant",
            system_prompt=SYSTEM_PROMPT,
            persona="clinical_ops",
        )

        try:
            result = await agent.run(QUERY, user_id="clinical_ops_001")
        except GovernanceViolation as exc:
            console.print(Rule())
            console.print(
                Panel(
                    f"[bold red]Governance blocked the request:[/bold red]\n{exc}",
                    border_style="red",
                )
            )
            return

    console.print(Rule("[dim]end hook log[/dim]"))
    console.print()

    # ── Response panel ─────────────────────────────────────────────────────────
    gov = result.governance_result
    console.print(
        Panel(
            result.content,
            title="[bold cyan]Claude's Response[/bold cyan]",
            border_style="cyan",
            padding=(1, 2),
        )
    )
    console.print()

    # ── Governance / GxP result ────────────────────────────────────────────────
    gov_table = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
    gov_table.add_column("Field", style="dim")
    gov_table.add_column("Value")
    gov_table.add_row("Overall status", "[green]✅ PASSED[/green]" if gov.passed else "[red]❌ FAILED[/red]")
    gov_table.add_row("GxP compliance", _compliance_badge(gov.gxp_compliant))
    gov_table.add_row(
        "PII in response",
        f"[yellow]{len(gov.pii_detected)} hit(s)[/yellow]" if gov.pii_detected else "[green]none[/green]",
    )
    gov_table.add_row(
        "Flags",
        "\n".join(f"[yellow]• {f}[/yellow]" for f in gov.flags) if gov.flags else "[green]none[/green]",
    )
    gov_table.add_row("Input tokens",  str(result.input_tokens))
    gov_table.add_row("Output tokens", str(result.output_tokens))
    gov_table.add_row("Cost",          f"${result.cost_usd:.6f} USD")

    console.print(Panel(gov_table, title="[bold]Governance & GxP Result[/bold]", border_style="green"))
    console.print()

    # ── Audit trail ────────────────────────────────────────────────────────────
    page = await audit.query_events(AuditFilter(page=1, page_size=20))
    events = page["events"]

    if events:
        audit_table = Table(
            title=f"Audit Trail ({page['total']} event(s))",
            box=box.ROUNDED,
            header_style="bold cyan",
        )
        audit_table.add_column("Timestamp", style="dim", max_width=22)
        audit_table.add_column("Agent ID",  max_width=12)
        audit_table.add_column("Persona",   max_width=16)
        audit_table.add_column("Model",     max_width=22)
        audit_table.add_column("Gov result", justify="center")
        audit_table.add_column("In", justify="right")
        audit_table.add_column("Out", justify="right")

        gov_styles = {"pass": "green", "flag": "yellow", "block": "red"}
        for ev in events:
            ts_str = ev.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            short_id = ev.agent_id[:10] + "…"
            result_style = gov_styles.get(ev.governance_result, "white")
            audit_table.add_row(
                ts_str,
                short_id,
                ev.persona,
                ev.model_used,
                Text(ev.governance_result, style=f"bold {result_style}"),
                str(ev.input_token_count),
                str(ev.output_token_count),
            )
        console.print(audit_table)
        console.print()

    # ── Tamper detection ───────────────────────────────────────────────────────
    checksums = await audit.verify_checksums()
    all_valid = all(checksums.values()) if checksums else True
    tampered  = [eid for eid, ok in checksums.items() if not ok]

    tamper_icon   = "✅" if all_valid else "❌"
    tamper_status = (
        "[green]All checksums valid — no tampering detected[/green]"
        if all_valid
        else f"[bold red]{len(tampered)} tampered event(s) detected![/bold red]"
    )
    tamper_lines = [
        f"  {tamper_icon}  {tamper_status}",
        f"  [dim]Events verified: {len(checksums)}[/dim]",
    ]
    if tampered:
        tamper_lines.append("  [red]Tampered IDs: " + ", ".join(tampered[:3]) + "[/red]")

    console.print(
        Panel(
            "\n".join(tamper_lines),
            title="[bold]SHA-256 Tamper Detection[/bold]",
            border_style="green" if all_valid else "red",
            padding=(1, 0),
        )
    )
    console.print()
    console.print(Rule("[dim]Demo complete[/dim]"))


if __name__ == "__main__":
    asyncio.run(main())
