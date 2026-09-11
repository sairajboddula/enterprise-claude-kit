"""
Basic Governed Agent Example
============================
Simplest end-to-end usage of enterprise-claude-kit:

  1. Create a GovernanceLayer with PII filtering + blocked keywords
  2. Create a TokenMonitor with a daily budget
  3. Deploy an agent via AgentOrchestrator
  4. Run a query and display a beautiful Rich result

Run:
    python examples/basic_governed_agent.py

Requires:
    ANTHROPIC_API_KEY set in .env (or the environment)
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from datetime import UTC, datetime, timedelta

from dotenv import load_dotenv
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from enterprise_claude import AgentOrchestrator, GovernanceLayer, TokenMonitor
from enterprise_claude.governance import GovernanceViolation

load_dotenv()

console = Console()

# ---------------------------------------------------------------------------
# Demo query
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "You are a concise AI governance consultant helping enterprises deploy "
    "large language models safely.  Provide practical, evidence-based advice."
)

QUERY = (
    "What are the three most important technical safeguards when deploying "
    "large language models in a regulated industry like finance or healthcare?  "
    "Keep your answer to at most 200 words, using a numbered list."
)

DEMO_HEADER = """
  [bold cyan]enterprise-claude-kit[/bold cyan] · Basic Governed Agent Demo
  Governance  ·  Token Monitoring  ·  Cost Tracking
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _governance_badge(passed: bool) -> Text:
    if passed:
        return Text("✅  PASS", style="bold green")
    return Text("❌  FAIL", style="bold red")


def _budget_bar(pct: float, width: int = 36) -> str:
    filled = min(int(width * pct / 100), width)
    empty = width - filled
    if pct < 60:
        colour = "green"
    elif pct < 85:
        colour = "yellow"
    else:
        colour = "red"
    return f"[{colour}]{'█' * filled}[/{colour}]{'░' * empty}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    # ── Banner ────────────────────────────────────────────────────────────────
    console.print(Panel(DEMO_HEADER, border_style="cyan", padding=(0, 2)))
    console.print()

    # ── Pre-flight check ──────────────────────────────────────────────────────
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

    # ── Temporary databases (cleaned up on exit) ───────────────────────────────
    tmpdir = tempfile.mkdtemp(prefix="ecl_demo_")
    monitor_db = os.path.join(tmpdir, "monitor.db")
    try:
        await _run_demo(monitor_db)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def _run_demo(monitor_db: str) -> None:
    # ── 1. Governance layer ───────────────────────────────────────────────────
    governance = GovernanceLayer(
        pii_filter=True,
        blocked_keywords=["classified", "top secret"],
        constitutional_ai=True,
    )

    # ── 2. Token monitor with budget alert ───────────────────────────────────
    monitor = TokenMonitor(daily_budget_usd=5.0, alert_threshold_pct=0.8, db_path=monitor_db)

    alert_fired: list[str] = []

    @monitor.on_alert
    async def _on_budget_alert(status) -> None:  # type: ignore[no-untyped-def]
        alert_fired.append(f"⚠  Budget alert: {status.pct_used:.1f}% of daily limit used")

    # ── 3. Show config summary ────────────────────────────────────────────────
    cfg_table = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
    cfg_table.add_column("Key", style="dim")
    cfg_table.add_column("Value")
    cfg_table.add_row("PII filter", "[green]enabled[/green]")
    cfg_table.add_row("Blocked keywords", "classified, top secret")
    cfg_table.add_row("Daily budget", "$5.00 USD")
    cfg_table.add_row("Alert threshold", "80 %")
    cfg_table.add_row("Model (default tier)", "claude-sonnet-4-6")
    console.print(Panel(cfg_table, title="[bold]Governance & Monitor Config[/bold]", border_style="blue"))
    console.print()

    # ── 4. Deploy agent and run ───────────────────────────────────────────────
    async with AgentOrchestrator(
        governance=governance,
        monitor=monitor,
    ) as orch:

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
            console=console,
        ) as progress:
            task = progress.add_task("Creating agent…", total=None)
            agent = await orch.create_agent(
                name="Governance Consultant",
                system_prompt=SYSTEM_PROMPT,
                persona="analyst",
            )
            progress.update(task, description="Running query…")
            try:
                result = await agent.run(QUERY, user_id="demo_user_001")
            except GovernanceViolation as exc:
                console.print(
                    Panel(
                        f"[bold red]Governance blocked the request:[/bold red]\n{exc}",
                        border_style="red",
                    )
                )
                return

        # ── 5. Response panel ─────────────────────────────────────────────────
        console.print(
            Panel(
                result.content,
                title="[bold cyan]Claude's Response[/bold cyan]",
                border_style="cyan",
                padding=(1, 2),
            )
        )
        console.print()

        # ── 6. Governance result ──────────────────────────────────────────────
        gov = result.governance_result
        gov_table = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
        gov_table.add_column("Field", style="dim")
        gov_table.add_column("Value")
        gov_table.add_row("Status", _governance_badge(gov.passed))
        gov_table.add_row("Persona", gov.persona)
        gov_table.add_row("Model", gov.model)
        gov_table.add_row(
            "PII detected",
            f"[yellow]{len(gov.pii_detected)} hit(s)[/yellow]"
            if gov.pii_detected
            else "[green]none[/green]",
        )
        gov_table.add_row(
            "Flags",
            f"[yellow]{', '.join(gov.flags)}[/yellow]" if gov.flags else "[green]none[/green]",
        )
        gov_table.add_row(
            "Violations",
            f"[red]{', '.join(gov.violations)}[/red]"
            if gov.violations
            else "[green]none[/green]",
        )
        console.print(Panel(gov_table, title="[bold]Governance Result[/bold]", border_style="green"))
        console.print()

        # ── 7. Cost summary table ─────────────────────────────────────────────
        now = datetime.now(tz=UTC)
        summary = await monitor.get_cost_summary(
            start_date=now - timedelta(hours=24),
            end_date=now,
        )

        cost_table = Table(title="Cost Breakdown — Last 24 h", box=box.ROUNDED, header_style="bold cyan")
        cost_table.add_column("Dimension")
        cost_table.add_column("Key")
        cost_table.add_column("Cost (USD)", justify="right")
        cost_table.add_column("% of calls", justify="right")

        for model_id, cost in sorted(summary.by_model.items(), key=lambda x: -x[1]):
            pct_str = f"{summary.model_pct.get(model_id, 0):.1f} %"
            cost_table.add_row("Model", model_id, f"${cost:.6f}", pct_str)
        for persona, cost in sorted(summary.by_persona.items(), key=lambda x: -x[1]):
            cost_table.add_row("Persona", persona, f"${cost:.6f}", "—")

        cost_table.add_section()
        cost_table.add_row(
            "[bold]Total[/bold]", "",
            f"[bold]${summary.total_usd:.6f}[/bold]",
            f"[bold]{summary.record_count} call(s)[/bold]",
        )

        console.print(cost_table)
        console.print()

        # ── 8. Budget progress bar ────────────────────────────────────────────
        budget = await monitor.check_budget()
        bar = _budget_bar(budget.pct_used)
        bar_colour = "green" if budget.pct_used < 60 else ("yellow" if budget.pct_used < 85 else "red")
        budget_lines = [
            f"  {bar}  [{bar_colour}]{budget.pct_used:.1f} %[/{bar_colour}]",
            (
                f"  [bold]Used:[/bold] ${budget.used_usd:.6f}  "
                f"[dim]of[/dim]  [bold]Budget:[/bold] ${budget.budget_usd:.2f}  "
                f"[dim]→[/dim]  [bold]Remaining:[/bold] ${budget.remaining_usd:.6f}"
            ),
        ]
        if alert_fired:
            budget_lines.append(f"\n  [bold yellow]{alert_fired[0]}[/bold yellow]")
        console.print(
            Panel(
                "\n".join(budget_lines),
                title="[bold]Daily Budget Status[/bold]",
                border_style=bar_colour,
                padding=(1, 0),
            )
        )
        console.print()
        console.print(Rule("[dim]Demo complete[/dim]"))


if __name__ == "__main__":
    asyncio.run(main())
