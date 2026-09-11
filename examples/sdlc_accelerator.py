"""
SDLC Accelerator — Wave-Based Adoption Simulation
==================================================
Demonstrates wave-based enterprise rollout with AdoptionTracker.

No API key required — this example is entirely local, using an
in-memory SQLite database.

Simulation scenario
-------------------
An engineering organisation rolls out Claude to two groups:

  Wave 1 · Architects    — 10 users, must reach 80 % before Wave 2 opens
  Wave 2 · Developers    — 40 users, gated on Wave 1

Steps shown:
  1. Create waves with a gate rule
  2. Attempt early Wave 2 activation → WaveGateError (expected)
  3. Activate 8 / 10 architects (80 %), simulate call counts
  4. Confirm Wave 2 gate is now met → activate Wave 2
  5. Activate 20 / 40 developers, simulate call counts
  6. Render a full adoption dashboard:
       • Per-wave progress bars
       • Literacy score table per user (sampled)
       • Persona breakdown
       • Inactive user detection

Run:
    python examples/sdlc_accelerator.py
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
)
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from enterprise_claude.adoption_tracker import AdoptionTracker, WaveGateError

console = Console()

DEMO_HEADER = """
  [bold cyan]enterprise-claude-kit[/bold cyan] · SDLC Accelerator Wave Adoption Demo
  No API key needed · In-memory SQLite · Pure AdoptionTracker
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _literacy_bar(score: float, width: int = 10) -> str:
    """Render a simple block bar for a 0-100 literacy score."""
    filled = round(score / 100 * width)
    colour = "green" if score >= 50 else ("yellow" if score >= 20 else "red")
    return f"[{colour}]{'█' * filled}[/{colour}]{'░' * (width - filled)}"


def _status_badge(status: str) -> Text:
    colours = {"planned": "blue", "active": "green", "complete": "dim"}
    return Text(status, style=f"bold {colours.get(status, 'white')}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    console.print(Panel(DEMO_HEADER, border_style="cyan", padding=(0, 2)))
    console.print()

    # ── Init tracker (temp-file SQLite, auto-cleaned on exit) ────────────────
    tmpdir  = tempfile.mkdtemp(prefix="ecl_sdlc_demo_")
    db_path = os.path.join(tmpdir, "adoption.db")
    tracker = AdoptionTracker(db_path=db_path)
    await tracker.initialize()

    # ── 1. Create waves ───────────────────────────────────────────────────────
    wave1 = await tracker.create_wave(
        name="Wave 1 — Architects",
        target_count=10,
        order=1,
    )
    wave2 = await tracker.create_wave(
        name="Wave 2 — Developers",
        target_count=40,
        order=2,
        gate_wave_id=wave1.wave_id,
        gate_threshold_pct=0.80,
    )

    setup_table = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
    setup_table.add_column("Key", style="dim")
    setup_table.add_column("Value")
    setup_table.add_row("Wave 1", f"[bold]{wave1.name}[/bold]  target=10  no gate")
    setup_table.add_row("Wave 2", f"[bold]{wave2.name}[/bold]  target=40  gated at 80 % of Wave 1")
    setup_table.add_row("DB", f"temp SQLite ({db_path})")
    console.print(Panel(setup_table, title="[bold]Wave Configuration[/bold]", border_style="blue"))
    console.print()

    # ── 2. Activate Wave 1 ────────────────────────────────────────────────────
    await tracker.activate_wave(wave1.wave_id)
    console.print(f"  [green]●[/green] Activated: [bold]{wave1.name}[/bold]")

    # ── 3. Try Wave 2 early → WaveGateError ──────────────────────────────────
    console.print(
        f"\n  Attempting [bold]{wave2.name}[/bold] activation before gate is met (0 %)…"
    )
    try:
        await tracker.activate_wave(wave2.wave_id)
        console.print("  [red]ERROR: should have raised WaveGateError[/red]")
    except WaveGateError as exc:
        console.print(
            f"  [yellow]⛔ WaveGateError (expected):[/yellow] {exc}"
        )
    console.print()

    # ── 4. Activate 8/10 Wave 1 users + simulate calls ───────────────────────
    arch_users = [f"arch_{i:03d}" for i in range(1, 11)]

    # call counts modelling real adoption shape: power users → light users → inactive
    arch_calls: dict[str, int] = {
        "arch_001": 42,   # power user
        "arch_002": 28,
        "arch_003": 15,
        "arch_004": 10,
        "arch_005": 5,
        "arch_006": 3,
        "arch_007": 1,
        "arch_008": 0,    # activated but never used → inactive
        # arch_009 and arch_010 NOT activated (Wave 1 at 80 %)
    }

    console.print("  [bold]Activating Wave 1 architects and recording call counts…[/bold]")
    with Progress(
        TextColumn("  [progress.description]{task.description}"),
        BarColumn(bar_width=30),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        console=console,
        transient=True,
    ) as prog:
        task = prog.add_task("Activating Wave 1 users", total=8)
        for uid in arch_users[:8]:
            await tracker.record_activation(wave1.wave_id, uid, persona="architect")
            for _ in range(arch_calls.get(uid, 0)):
                await tracker.record_call(uid)
            prog.advance(task)

    p1 = await tracker.get_wave_progress(wave1.wave_id)
    console.print(
        f"  [green]✅[/green] Wave 1 progress: "
        f"[bold]{p1.activated}/{p1.target}[/bold] "
        f"({p1.completion_pct:.0%})  — gate met!\n"
    )

    # ── 5. Activate Wave 2 ────────────────────────────────────────────────────
    await tracker.activate_wave(wave2.wave_id)
    console.print(f"  [green]●[/green] Activated: [bold]{wave2.name}[/bold]")

    # 20/40 developers, varied call counts
    dev_users = [f"dev_{i:03d}" for i in range(1, 41)]
    dev_calls: list[int] = (
        [25, 18, 12, 10, 8]   # power users  (5)
        + [5, 4, 3, 3, 2]     # moderate      (5)
        + [1] * 8              # light          (8)
        + [0] * 2              # inactive       (2)
        # 20 not activated yet
    )

    console.print("\n  [bold]Activating Wave 2 developers and recording call counts…[/bold]")
    with Progress(
        TextColumn("  [progress.description]{task.description}"),
        BarColumn(bar_width=30),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        console=console,
        transient=True,
    ) as prog:
        task = prog.add_task("Activating Wave 2 users", total=20)
        for i, uid in enumerate(dev_users[:20]):
            await tracker.record_activation(wave2.wave_id, uid, persona="developer")
            for _ in range(dev_calls[i]):
                await tracker.record_call(uid)
            prog.advance(task)

    p2 = await tracker.get_wave_progress(wave2.wave_id)
    console.print(
        f"  [green]✅[/green] Wave 2 progress: "
        f"[bold]{p2.activated}/{p2.target}[/bold] "
        f"({p2.completion_pct:.0%})\n"
    )

    # =========================================================================
    # Dashboard
    # =========================================================================
    console.print(Rule("[bold cyan]Adoption Dashboard[/bold cyan]"))
    console.print()

    # ── Wave progress bars ────────────────────────────────────────────────────
    all_progress = await tracker.get_all_wave_progress()

    wave_table = Table(
        title="Wave Progress",
        box=box.ROUNDED,
        header_style="bold cyan",
    )
    wave_table.add_column("Wave")
    wave_table.add_column("Status", justify="center")
    wave_table.add_column("Activated", justify="right")
    wave_table.add_column("Target", justify="right")
    wave_table.add_column("Progress bar", min_width=24)
    wave_table.add_column("% Complete", justify="right")
    wave_table.add_column("Gate")

    wave_status_map = {
        wave1.wave_id: "active",
        wave2.wave_id: "active",
    }
    wave_gate_map = {
        wave2.wave_id: "≥ 80 % Wave 1",
    }

    for wp in all_progress:
        filled = min(int(wp.completion_pct * 20), 20)
        colour = "green" if wp.completion_pct >= 0.8 else ("yellow" if wp.completion_pct >= 0.4 else "blue")
        bar = f"[{colour}]{'█' * filled}[/{colour}]{'░' * (20 - filled)}"
        status = wave_status_map.get(wp.wave_id, "planned")
        gate_str = wave_gate_map.get(wp.wave_id, "—")
        wave_table.add_row(
            wp.wave_name,
            _status_badge(status),
            str(wp.activated),
            str(wp.target),
            bar,
            f"{wp.completion_pct:.0%}",
            gate_str,
        )

    console.print(wave_table)
    console.print()

    # ── Literacy score sample table ───────────────────────────────────────────
    sampled_users = [
        ("arch_001", "architect"),
        ("arch_003", "architect"),
        ("arch_007", "architect"),
        ("arch_008", "architect"),
        ("dev_001",  "developer"),
        ("dev_005",  "developer"),
        ("dev_012",  "developer"),
        ("dev_018",  "developer"),
    ]

    lit_table = Table(
        title="Literacy Score Sample (selected users)",
        box=box.ROUNDED,
        header_style="bold cyan",
    )
    lit_table.add_column("User ID")
    lit_table.add_column("Persona")
    lit_table.add_column("Calls", justify="right")
    lit_table.add_column("Score /100", justify="right")
    lit_table.add_column("Literacy bar", min_width=14)
    lit_table.add_column("Level")

    call_lookup = {**arch_calls, **{dev_users[i]: dev_calls[i] for i in range(20)}}
    for uid, persona in sampled_users:
        score = await tracker.get_literacy_score(uid)
        calls = call_lookup.get(uid, 0)
        bar   = _literacy_bar(score)
        if score >= 50:
            level = Text("Advanced",    style="bold green")
        elif score >= 20:
            level = Text("Intermediate", style="yellow")
        elif score > 0:
            level = Text("Beginner",    style="blue")
        else:
            level = Text("Inactive",    style="dim red")
        lit_table.add_row(uid, persona, str(calls), f"{score:.0f}", bar, level)

    console.print(lit_table)
    console.print()

    # ── Inactive users ────────────────────────────────────────────────────────
    # days=1 means "no calls in the last 24 hours"; in a fresh demo this is
    # equivalent to "never called", since all calls above happened just now.
    inactive = await tracker.get_inactive_users(days=1)
    inactive_panel_lines: list[str] = []
    if inactive:
        for uid in sorted(inactive)[:10]:
            inactive_panel_lines.append(f"  [yellow]•[/yellow] {uid}")
        if len(inactive) > 10:
            inactive_panel_lines.append(f"  [dim]… and {len(inactive) - 10} more[/dim]")
    else:
        inactive_panel_lines.append("  [green]No inactive users detected.[/green]")

    console.print(
        Panel(
            "\n".join(inactive_panel_lines),
            title=f"[bold]Inactive Users — no calls in last 24 h ({len(inactive)} total)[/bold]",
            border_style="yellow" if inactive else "green",
            padding=(1, 0),
        )
    )
    console.print()

    # ── Overall summary ───────────────────────────────────────────────────────
    total_activated = sum(wp.activated for wp in all_progress)
    total_target    = sum(wp.target    for wp in all_progress)
    overall_pct     = total_activated / total_target if total_target else 0.0

    filled    = min(int(overall_pct * 36), 36)
    bar_colour = "green" if overall_pct >= 0.8 else "yellow"
    overall_bar = f"[{bar_colour}]{'█' * filled}[/{bar_colour}]{'░' * (36 - filled)}"

    summary_table = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
    summary_table.add_column("Key", style="dim")
    summary_table.add_column("Value")
    summary_table.add_row("Total activated",  f"[bold]{total_activated}[/bold] / {total_target} users")
    summary_table.add_row("Overall progress", f"{overall_bar}  [{bar_colour}]{overall_pct:.0%}[/{bar_colour}]")
    summary_table.add_row("Wave 1 status",    "[green]✅ Gate met (80 %+)[/green]")
    summary_table.add_row("Wave 2 status",    "[yellow]⏳ In progress (50 %)[/yellow]")
    summary_table.add_row("Inactive users",   str(len(inactive)))

    console.print(Panel(summary_table, title="[bold]Overall Adoption Summary[/bold]", border_style="cyan"))
    console.print()
    console.print(Rule("[dim]Demo complete[/dim]"))

    # Cleanup temp files
    shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
