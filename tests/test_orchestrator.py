"""
Tests for AgentOrchestrator and AdoptionTracker.

Coverage:
AgentOrchestrator:
- Agent creation with default / batch / gated model tiers
- Approval gate for restricted models
- AgentNotFoundError on bad ID
- Agent shutdown changes status
- list_agents() returns all created agents
- GovernanceViolation from pre_check propagates out of agent.run()
- Async context manager protocol

AdoptionTracker:
- Wave creation and activation
- WaveGateError when gate threshold not met
- Wave gate passes at exactly 80%
- User activation recording and wave progress
- Literacy score at 0 / 1 / 10 / 100+ calls
- Inactive user detection
- Wave completion percentage
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from enterprise_claude import AgentOrchestrator, GovernanceLayer, TokenMonitor
from enterprise_claude.adoption_tracker import AdoptionTracker, WaveGateError
from enterprise_claude.governance import GovernanceViolation
from enterprise_claude.orchestrator import AgentNotFoundError, ApprovalRequiredError

# ── pytest-asyncio auto mode ──────────────────────────────────────────────────
pytestmark = pytest.mark.asyncio


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_anthropic():
    """Patch the anthropic module used by orchestrator so no real API calls are made."""
    with patch("enterprise_claude.orchestrator.anthropic") as mock:
        mock_client = AsyncMock()
        mock.AsyncAnthropic.return_value = mock_client

        # Build a realistic-looking response object
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Mocked response")]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50

        mock_client.messages.create = AsyncMock(return_value=mock_response)
        yield mock_client


@pytest.fixture
async def orchestrator(mock_anthropic) -> AgentOrchestrator:
    """Ready-to-use AgentOrchestrator with governance and budget monitor."""
    orch = AgentOrchestrator(
        model_policy={
            "default": "claude-sonnet-4-5",
            "batch": "claude-haiku-4-5",
        },
        governance=GovernanceLayer(pii_filter=False),
        monitor=TokenMonitor(daily_budget_usd=100.0),
    )
    await orch.__aenter__()
    yield orch
    await orch.__aexit__(None, None, None)


@pytest.fixture
async def tracker(tmp_path: Path) -> AdoptionTracker:
    """Initialised AdoptionTracker backed by a temp SQLite database."""
    t = AdoptionTracker(db_path=str(tmp_path / "test_adoption.db"))
    await t.initialize()
    return t


# ─── TestAgentOrchestrator ────────────────────────────────────────────────────

class TestAgentOrchestrator:
    async def test_create_agent_default_model(self, orchestrator: AgentOrchestrator) -> None:
        """Agents created without a tier use the 'default' model from model_policy."""
        agent = await orchestrator.create_agent(
            name="Test Agent",
            system_prompt="You are helpful.",
            persona="research",
        )
        assert agent.model == "claude-sonnet-4-5"
        assert agent.name == "Test Agent"
        assert agent.agent_id is not None and len(agent.agent_id) > 0

    async def test_create_agent_batch_model(self, orchestrator: AgentOrchestrator) -> None:
        """Agents created with tier='batch' use the 'batch' model from model_policy."""
        agent = await orchestrator.create_agent(
            name="Batch Agent",
            system_prompt="You are helpful.",
            persona="batch",
            tier="batch",
        )
        assert agent.model == "claude-haiku-4-5"

    async def test_gated_model_requires_approval(self, orchestrator: AgentOrchestrator) -> None:
        """Requesting tier='gated' without approval_granted raises ApprovalRequiredError."""
        with pytest.raises(ApprovalRequiredError):
            await orchestrator.create_agent(
                name="Opus Agent",
                system_prompt="You are helpful.",
                persona="research",
                tier="gated",
            )

    async def test_gated_model_with_approval(self, orchestrator: AgentOrchestrator) -> None:
        """Requesting tier='gated' with approval_granted=True succeeds."""
        agent = await orchestrator.create_agent(
            name="Approved Agent",
            system_prompt="You are helpful.",
            persona="research",
            tier="gated",
            approval_granted=True,
        )
        assert agent is not None
        assert agent.agent_id is not None

    async def test_agent_not_found(self, orchestrator: AgentOrchestrator) -> None:
        """get_agent() with a non-existent ID raises AgentNotFoundError."""
        with pytest.raises(AgentNotFoundError):
            await orchestrator.get_agent("non-existent-id-00000000")

    async def test_shutdown_agent(self, orchestrator: AgentOrchestrator) -> None:
        """After shutdown_agent(), the agent's status becomes 'shutdown'."""
        agent = await orchestrator.create_agent(
            name="Temp Agent",
            system_prompt="You are helpful.",
            persona="research",
        )
        await orchestrator.shutdown_agent(agent.agent_id)
        retrieved = await orchestrator.get_agent(agent.agent_id)
        assert retrieved.status == "shutdown"

    async def test_list_agents(self, orchestrator: AgentOrchestrator) -> None:
        """list_agents() returns all agents that have been created."""
        a1 = await orchestrator.create_agent(
            name="Agent A", system_prompt="Helpful.", persona="research"
        )
        a2 = await orchestrator.create_agent(
            name="Agent B", system_prompt="Helpful.", persona="research"
        )
        agents = await orchestrator.list_agents()
        ids = {a.agent_id for a in agents}
        assert a1.agent_id in ids
        assert a2.agent_id in ids

    async def test_governance_pre_check_blocks(self, mock_anthropic) -> None:
        """A GovernanceViolation raised during pre_check propagates out of agent.run()."""
        blocking_layer = GovernanceLayer(
            pii_filter=False,
            blocked_keywords=["forbidden"],
        )
        orch = AgentOrchestrator(
            model_policy={"default": "claude-sonnet-4-5"},
            governance=blocking_layer,
            monitor=TokenMonitor(daily_budget_usd=100.0),
        )
        async with orch:
            agent = await orch.create_agent(
                name="Blocked Agent",
                system_prompt="You are helpful.",
                persona="research",
            )
            with pytest.raises(GovernanceViolation):
                await agent.run("This is a forbidden query", user_id="user001")

    async def test_context_manager(self, mock_anthropic) -> None:
        """AgentOrchestrator works correctly as an async context manager."""
        orch = AgentOrchestrator(
            model_policy={"default": "claude-sonnet-4-5"},
            governance=GovernanceLayer(pii_filter=False),
            monitor=TokenMonitor(daily_budget_usd=100.0),
        )
        async with orch as o:
            agent = await o.create_agent(
                name="CM Agent",
                system_prompt="You are helpful.",
                persona="research",
            )
            result = await agent.run("Hello", user_id="user001")

        assert result.content == "Mocked response"


# ─── TestAdoptionTracker ──────────────────────────────────────────────────────

class TestAdoptionTracker:
    async def test_create_wave(self, tracker: AdoptionTracker) -> None:
        """create_wave() returns a Wave with the supplied name and target count."""
        wave = await tracker.create_wave(name="Wave 1", target_count=10, order=1)
        assert wave.name == "Wave 1"
        assert wave.target_count == 10
        assert wave.wave_id is not None

    async def test_activate_wave_first(self, tracker: AdoptionTracker) -> None:
        """The first wave can be activated unconditionally (no gate to check)."""
        wave = await tracker.create_wave(name="Wave 1", target_count=10, order=1)
        await tracker.activate_wave(wave.wave_id)
        retrieved = await tracker.get_wave(wave.wave_id)
        assert retrieved.status == "active"

    async def test_wave_gate_blocks_early(self, tracker: AdoptionTracker) -> None:
        """Wave 2 raises WaveGateError when Wave 1 is only 50% complete (gate=80%)."""
        w1 = await tracker.create_wave(name="Wave 1", target_count=10, order=1)
        w2 = await tracker.create_wave(
            name="Wave 2",
            target_count=40,
            order=2,
            gate_wave_id=w1.wave_id,
            gate_threshold_pct=0.80,
        )
        await tracker.activate_wave(w1.wave_id)

        # Activate only 5/10 = 50%, below the 80% gate
        for i in range(5):
            await tracker.record_activation(w1.wave_id, f"user_{i:03d}")

        with pytest.raises(WaveGateError):
            await tracker.activate_wave(w2.wave_id)

    async def test_wave_gate_passes_at_80pct(self, tracker: AdoptionTracker) -> None:
        """Wave 2 activates successfully once Wave 1 reaches exactly 80% (8/10)."""
        w1 = await tracker.create_wave(name="Wave 1", target_count=10, order=1)
        w2 = await tracker.create_wave(
            name="Wave 2",
            target_count=40,
            order=2,
            gate_wave_id=w1.wave_id,
            gate_threshold_pct=0.80,
        )
        await tracker.activate_wave(w1.wave_id)

        # Activate exactly 8/10 = 80%
        for i in range(8):
            await tracker.record_activation(w1.wave_id, f"user_{i:03d}")

        await tracker.activate_wave(w2.wave_id)  # Must not raise
        w2_state = await tracker.get_wave(w2.wave_id)
        assert w2_state.status == "active"

    async def test_record_activation(self, tracker: AdoptionTracker) -> None:
        """Recording a user activation increments the wave's activated count by 1."""
        wave = await tracker.create_wave(name="Wave 1", target_count=10, order=1)
        await tracker.activate_wave(wave.wave_id)
        await tracker.record_activation(wave.wave_id, "user_001", persona="architect")
        progress = await tracker.get_wave_progress(wave.wave_id)
        assert progress.activated == 1

    async def test_literacy_score_zero(self, tracker: AdoptionTracker) -> None:
        """A user with 0 calls has a literacy score of 0."""
        wave = await tracker.create_wave(name="Wave 1", target_count=10, order=1)
        await tracker.activate_wave(wave.wave_id)
        await tracker.record_activation(wave.wave_id, "user_zero")
        score = await tracker.get_literacy_score("user_zero")
        assert score == 0

    async def test_literacy_score_one_call(self, tracker: AdoptionTracker) -> None:
        """A user with exactly 1 call has a literacy score of 10."""
        wave = await tracker.create_wave(name="Wave 1", target_count=10, order=1)
        await tracker.activate_wave(wave.wave_id)
        await tracker.record_activation(wave.wave_id, "user_one")
        await tracker.record_call("user_one")
        score = await tracker.get_literacy_score("user_one")
        assert score == 10

    async def test_literacy_score_ten_calls(self, tracker: AdoptionTracker) -> None:
        """A user with exactly 10 calls has a literacy score of 50."""
        wave = await tracker.create_wave(name="Wave 1", target_count=10, order=1)
        await tracker.activate_wave(wave.wave_id)
        await tracker.record_activation(wave.wave_id, "user_ten")
        for _ in range(10):
            await tracker.record_call("user_ten")
        score = await tracker.get_literacy_score("user_ten")
        assert score == 50

    async def test_literacy_score_hundred_plus(self, tracker: AdoptionTracker) -> None:
        """A user with 100+ calls reaches the maximum literacy score of 100."""
        wave = await tracker.create_wave(name="Wave 1", target_count=10, order=1)
        await tracker.activate_wave(wave.wave_id)
        await tracker.record_activation(wave.wave_id, "user_hundred")
        for _ in range(100):
            await tracker.record_call("user_hundred")
        score = await tracker.get_literacy_score("user_hundred")
        assert score == 100

    async def test_inactive_users_detected(self, tracker: AdoptionTracker) -> None:
        """A user activated more than 7 days ago with no calls appears in inactive list."""
        wave = await tracker.create_wave(name="Wave 1", target_count=10, order=1)
        await tracker.activate_wave(wave.wave_id)
        await tracker.record_activation(wave.wave_id, "inactive_user")

        # Backdate the activation record so the user looks 8 days old
        await tracker._backdate_activation("inactive_user", days=8)

        inactive = await tracker.get_inactive_users(days=7)
        assert "inactive_user" in inactive

    async def test_wave_progress_completion_pct(self, tracker: AdoptionTracker) -> None:
        """WaveProgress.completion_pct == activated / target_count."""
        wave = await tracker.create_wave(name="Wave 1", target_count=10, order=1)
        await tracker.activate_wave(wave.wave_id)

        for i in range(4):
            await tracker.record_activation(wave.wave_id, f"user_{i:03d}")

        progress = await tracker.get_wave_progress(wave.wave_id)
        assert progress.completion_pct == pytest.approx(0.40)
        assert progress.activated == 4
        assert progress.target == 10
