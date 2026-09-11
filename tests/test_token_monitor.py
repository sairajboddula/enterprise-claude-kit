"""
Tests for enterprise_claude.token_monitor module.

Coverage:
- Cost calculation accuracy per model (Sonnet, Haiku, Opus)
- Budget tracking with SQLite persistence
- Daily scope isolation (yesterday's usage excluded)
- Alert callback firing at threshold
- Cost summary grouping by agent / persona / model
- Model split percentage sum validation
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from enterprise_claude.token_monitor import TokenMonitor

# ── pytest-asyncio auto mode ──────────────────────────────────────────────────
pytestmark = pytest.mark.asyncio


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
async def monitor(tmp_path: Path) -> TokenMonitor:
    """Fresh TokenMonitor backed by a temp SQLite file."""
    m = TokenMonitor(
        daily_budget_usd=10.0,
        alert_threshold_pct=0.8,
        db_path=str(tmp_path / "usage.db"),
    )
    await m.initialize()
    return m


# ─── TestCostCalculation ──────────────────────────────────────────────────────

class TestCostCalculation:
    """Unit tests for calculate_cost() — no I/O, no async."""

    def test_sonnet_input_cost(self) -> None:
        """1 M input tokens on claude-sonnet-4-5 = $3.00 (input price $3/M)."""
        m = TokenMonitor(daily_budget_usd=100.0)
        cost = m.calculate_cost("claude-sonnet-4-5", input_tokens=1_000_000, output_tokens=0)
        assert cost == pytest.approx(3.00, rel=1e-6)

    def test_sonnet_output_cost(self) -> None:
        """1 M output tokens on claude-sonnet-4-5 = $15.00 (output price $15/M)."""
        m = TokenMonitor(daily_budget_usd=100.0)
        cost = m.calculate_cost("claude-sonnet-4-5", input_tokens=0, output_tokens=1_000_000)
        assert cost == pytest.approx(15.00, rel=1e-6)

    def test_haiku_cost_cheaper(self) -> None:
        """Haiku is cheaper than Sonnet for the same token count."""
        m = TokenMonitor(daily_budget_usd=100.0)
        sonnet = m.calculate_cost("claude-sonnet-4-5", input_tokens=100_000, output_tokens=50_000)
        haiku = m.calculate_cost("claude-haiku-4-5", input_tokens=100_000, output_tokens=50_000)
        assert haiku < sonnet

    def test_opus_most_expensive(self) -> None:
        """Opus is more expensive than Sonnet for the same token count."""
        m = TokenMonitor(daily_budget_usd=100.0)
        sonnet = m.calculate_cost("claude-sonnet-4-5", input_tokens=100_000, output_tokens=50_000)
        opus = m.calculate_cost("claude-opus-4-5", input_tokens=100_000, output_tokens=50_000)
        assert opus > sonnet

    def test_unknown_model_raises(self) -> None:
        """An unrecognised model name raises ValueError with 'Unknown model' in message."""
        m = TokenMonitor(daily_budget_usd=100.0)
        with pytest.raises(ValueError, match="[Uu]nknown model"):
            m.calculate_cost("gpt-4-turbo", input_tokens=1_000, output_tokens=500)

    def test_fractional_tokens(self) -> None:
        """500 output tokens on Sonnet = $0.0075  (500 / 1_000_000 * $15)."""
        m = TokenMonitor(daily_budget_usd=100.0)
        cost = m.calculate_cost("claude-sonnet-4-5", input_tokens=0, output_tokens=500)
        assert cost == pytest.approx(0.0075, rel=1e-6)

    def test_zero_tokens(self) -> None:
        """Zero tokens for both directions returns 0.0."""
        m = TokenMonitor(daily_budget_usd=100.0)
        cost = m.calculate_cost("claude-sonnet-4-5", input_tokens=0, output_tokens=0)
        assert cost == 0.0


# ─── TestBudgetTracking ───────────────────────────────────────────────────────

class TestBudgetTracking:
    """Tests for check_budget() and record_usage() against a real SQLite DB."""

    async def test_budget_check_empty(self, monitor: TokenMonitor) -> None:
        """Fresh monitor has zero spend and 0% utilisation."""
        status = await monitor.check_budget()
        assert status.used_usd == pytest.approx(0.0)
        assert status.budget_usd == pytest.approx(10.0)
        assert status.pct_used == pytest.approx(0.0)

    async def test_budget_alert_at_threshold(self, monitor: TokenMonitor) -> None:
        """Recording ≥80% of budget fires the on_alert callback."""
        alerts: list = []

        @monitor.on_alert
        async def on_budget_alert(status) -> None:
            alerts.append(status)

        # $8 worth of Sonnet output tokens: $8 / ($15/M) ≈ 533 333 tokens
        await monitor.record_usage(
            model="claude-sonnet-4-5",
            input_tokens=0,
            output_tokens=533_334,
        )
        await monitor.check_budget()
        assert len(alerts) >= 1
        assert alerts[0].pct_used >= 0.8

    async def test_budget_alert_not_fired_below_threshold(self, monitor: TokenMonitor) -> None:
        """Usage below 80% threshold does NOT fire the alert callback."""
        alerts: list = []

        @monitor.on_alert
        async def on_alert(status) -> None:
            alerts.append(status)

        # ~$0.10 of spend — well below $8 (80% of $10)
        await monitor.record_usage(
            model="claude-sonnet-4-5",
            input_tokens=0,
            output_tokens=6_666,  # 6 666 * $15/M ≈ $0.10
        )
        await monitor.check_budget()
        assert len(alerts) == 0

    async def test_record_usage_persists(self, tmp_path: Path) -> None:
        """Usage recorded in one TokenMonitor instance is visible in a fresh instance
        backed by the same DB file."""
        db_path = str(tmp_path / "persist.db")

        m1 = TokenMonitor(daily_budget_usd=10.0, db_path=db_path)
        await m1.initialize()
        await m1.record_usage("claude-sonnet-4-5", input_tokens=1_000, output_tokens=500)

        # Re-initialise against the same file
        m2 = TokenMonitor(daily_budget_usd=10.0, db_path=db_path)
        await m2.initialize()
        status = await m2.check_budget()
        assert status.used_usd > 0.0

    async def test_daily_budget_scope(self, tmp_path: Path) -> None:
        """Yesterday's usage rows are excluded from today's budget calculation."""
        db_path = str(tmp_path / "daily.db")
        m = TokenMonitor(daily_budget_usd=10.0, db_path=db_path)
        await m.initialize()

        yesterday = (datetime.now(tz=UTC).date() - timedelta(days=1)).isoformat()
        # Insert directly with a historic date via the private helper
        await m._insert_usage_for_date(
            model="claude-sonnet-4-5",
            input_tokens=0,
            output_tokens=1_000_000,
            usage_date=yesterday,
        )

        status = await m.check_budget()
        assert status.used_usd == pytest.approx(0.0)

    async def test_alert_callback_called(self, monitor: TokenMonitor) -> None:
        """on_alert decorator registers a callback that fires when budget is exceeded."""
        fired: list[bool] = []

        @monitor.on_alert
        async def cb(status) -> None:
            fired.append(True)

        # Push spend clearly over $8 (80%) threshold
        await monitor.record_usage(
            "claude-sonnet-4-5", input_tokens=0, output_tokens=600_000
        )
        await monitor.check_budget()
        assert fired, "Alert callback was never called"


# ─── TestCostSummary ──────────────────────────────────────────────────────────

class TestCostSummary:
    """Tests for get_cost_summary() — grouping and percentage rollups."""

    async def test_by_agent_grouping(self, monitor: TokenMonitor) -> None:
        """Summary.by_agent maps each agent_id to its cost."""
        await monitor.record_usage(
            "claude-sonnet-4-5", input_tokens=1_000, output_tokens=500, agent_id="agent-A"
        )
        await monitor.record_usage(
            "claude-sonnet-4-5", input_tokens=1_000, output_tokens=500, agent_id="agent-B"
        )
        summary = await monitor.get_cost_summary()
        assert "agent-A" in summary.by_agent
        assert "agent-B" in summary.by_agent
        assert summary.by_agent["agent-A"] > 0.0
        assert summary.by_agent["agent-B"] > 0.0

    async def test_by_persona_grouping(self, monitor: TokenMonitor) -> None:
        """Summary.by_persona maps each persona label to its cost."""
        await monitor.record_usage(
            "claude-sonnet-4-5", input_tokens=1_000, output_tokens=500, persona="research"
        )
        await monitor.record_usage(
            "claude-sonnet-4-5", input_tokens=1_000, output_tokens=500, persona="clinical_ops"
        )
        summary = await monitor.get_cost_summary()
        assert "research" in summary.by_persona
        assert "clinical_ops" in summary.by_persona

    async def test_by_model_grouping(self, monitor: TokenMonitor) -> None:
        """Summary.by_model maps each model name to its cost."""
        await monitor.record_usage("claude-sonnet-4-5", input_tokens=1_000, output_tokens=500)
        await monitor.record_usage("claude-haiku-4-5", input_tokens=1_000, output_tokens=500)
        summary = await monitor.get_cost_summary()
        assert "claude-sonnet-4-5" in summary.by_model
        assert "claude-haiku-4-5" in summary.by_model

    async def test_model_split_percentages(self, monitor: TokenMonitor) -> None:
        """model_pct values sum to 100.0 (within floating-point tolerance)."""
        await monitor.record_usage("claude-sonnet-4-5", input_tokens=1_000, output_tokens=500)
        await monitor.record_usage("claude-haiku-4-5", input_tokens=1_000, output_tokens=500)
        summary = await monitor.get_cost_summary()
        total_pct = sum(summary.model_pct.values())
        assert total_pct == pytest.approx(100.0, abs=0.1)

    async def test_inactive_license_detection(self, monitor: TokenMonitor) -> None:
        """An agent that has recorded usage appears in by_agent; one with no usage
        should not appear (or appear with zero cost)."""
        await monitor.record_usage(
            "claude-sonnet-4-5",
            input_tokens=0,
            output_tokens=100,
            agent_id="active-agent",
        )
        summary = await monitor.get_cost_summary()
        assert "active-agent" in summary.by_agent
        assert summary.by_agent["active-agent"] > 0.0
        # An agent that never recorded should not appear or be zero
        assert summary.by_agent.get("never-used-agent", 0.0) == pytest.approx(0.0)
