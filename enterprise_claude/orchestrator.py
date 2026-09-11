"""
Central orchestrator for managing Claude agent lifecycle.

All agents are created, tracked, and shut down through this class.
Model policy is enforced automatically — no agent can escape governance.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

import anthropic
from pydantic import BaseModel, ConfigDict, Field, field_validator

from enterprise_claude.audit import AuditEvent, AuditLogger
from enterprise_claude.governance import (
    AgentNotFoundError,
    ApprovalRequiredError,
    EnterpriseClaudeError,
    GovernanceLayer,
    GovernanceResult,
)
from enterprise_claude.token_monitor import MonitorConfig, TokenMonitor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model policy
# ---------------------------------------------------------------------------

SUPPORTED_MODELS: dict[str, str] = {
    "default": "claude-sonnet-4-6",
    "batch": "claude-haiku-4-5",
    "gated": "claude-opus-4-6",  # requires approval
}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class AgentConfig(BaseModel):
    """Configuration for a single agent.

    Attributes:
        name: Human-readable agent name.
        system_prompt: The system-level instruction passed to the model.
        persona: Named role the agent plays (used by governance).
        tools: Optional list of Anthropic tool definitions.
        mcp_connectors: Connector names from MCPConnectorRegistry.
        model_tier: Which model tier to use (default/batch/gated).
        approval_granted: Must be True when model_tier='gated'.
        metadata: Arbitrary key/value pairs for caller use.
    """

    model_config = ConfigDict(strict=False)

    name: str
    system_prompt: str
    persona: str
    tools: list[dict[str, Any]] = Field(default_factory=list)
    mcp_connectors: list[str] = Field(default_factory=list)
    model_tier: Literal["default", "batch", "gated"] = "default"
    approval_granted: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        """Ensure the agent name is non-empty."""
        if not v.strip():
            raise ValueError("Agent name must not be empty")
        return v.strip()

    @field_validator("system_prompt")
    @classmethod
    def system_prompt_not_empty(cls, v: str) -> str:
        """Ensure the system prompt is non-empty."""
        if not v.strip():
            raise ValueError("system_prompt must not be empty")
        return v.strip()

    @field_validator("persona")
    @classmethod
    def persona_not_empty(cls, v: str) -> str:
        """Ensure the persona is non-empty."""
        if not v.strip():
            raise ValueError("persona must not be empty")
        return v.strip()


class AgentStatus(BaseModel):
    """Live status snapshot for an agent.

    Attributes:
        agent_id: Unique identifier for this agent instance.
        name: Human-readable name from AgentConfig.
        persona: Role name from AgentConfig.
        model: Resolved model ID (e.g., 'claude-sonnet-4-6').
        status: Current lifecycle state.
        created_at: When the agent was instantiated.
        last_active: Timestamp of the most recent run() call, or None.
        total_calls: Cumulative number of successful run() calls.
        total_cost_usd: Cumulative estimated spend in USD.
        session_id: Stable session UUID for this agent lifetime.
    """

    model_config = ConfigDict(strict=False)

    agent_id: str
    name: str
    persona: str
    model: str
    status: Literal["active", "idle", "shutdown"]
    created_at: datetime
    last_active: datetime | None
    total_calls: int
    total_cost_usd: float
    session_id: str


class RunResult(BaseModel):
    """Result of a single agent.run() call.

    Attributes:
        content: Text returned by the model.
        model: Model ID used for the call.
        input_tokens: Number of tokens in the request.
        output_tokens: Number of tokens in the response.
        cost_usd: Estimated cost for this call in USD.
        governance_result: Full governance evaluation output.
        agent_id: The agent that produced this result.
        session_id: Session the call belongs to.
        timestamp: When the result was produced.
    """

    model_config = ConfigDict(strict=False, arbitrary_types_allowed=True)

    content: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    governance_result: GovernanceResult
    agent_id: str
    session_id: str
    timestamp: datetime


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class Agent:
    """A single governed Claude agent instance.

    Do not instantiate directly — use AgentOrchestrator.create_agent().
    All calls are routed through GovernanceLayer automatically.

    The run() method executes the full governance pipeline:
      1. Pre-check (prompt / system-prompt validation)
      2. Anthropic API call
      3. Post-check (response content validation)
      4. Token usage recording
      5. Audit log emission
    """

    def __init__(
        self,
        agent_id: str,
        config: AgentConfig,
        model: str,
        governance: GovernanceLayer,
        monitor: TokenMonitor,
        audit_logger: AuditLogger,
        anthropic_client: Any,  # anthropic.AsyncAnthropic
    ) -> None:
        """Initialise the agent.

        Args:
            agent_id: Unique UUID string assigned by the orchestrator.
            config: Immutable configuration snapshot.
            model: Resolved model ID (e.g., 'claude-sonnet-4-6').
            governance: Shared GovernanceLayer instance.
            monitor: Shared TokenMonitor instance.
            audit_logger: Shared AuditLogger instance.
            anthropic_client: An initialised anthropic.AsyncAnthropic client.
        """
        self.agent_id = agent_id
        self.config = config
        self.model = model
        self.governance = governance
        self.monitor = monitor
        self.audit_logger = audit_logger
        self._client = anthropic_client
        self.created_at: datetime = datetime.now(UTC)
        self.last_active: datetime | None = None
        self.total_calls: int = 0
        self._total_cost_usd: float = 0.0
        self.session_id: str = str(uuid4())
        self._status: Literal["active", "idle", "shutdown"] = "active"
        self._logger = logging.getLogger(f"enterprise_claude.agent.{agent_id[:8]}")

    @property
    def name(self) -> str:
        """Human-readable name from AgentConfig."""
        return self.config.name

    @property
    def status(self) -> str:
        """Current lifecycle status string."""
        return self._status

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        prompt: str,
        user_id: str | None = None,
        stream: bool = False,
    ) -> RunResult:
        """Run a prompt through this agent with full governance and monitoring.

        Executes the five-step pipeline:

        1. ``governance.pre_check`` — validates prompt, persona, model,
           and system prompt.
        2. Anthropic API call via ``anthropic_client.messages.create``.
        3. ``governance.post_check`` — validates the model response.
        4. ``monitor.record_usage`` — records token counts and cost.
        5. ``audit_logger.log_event`` — writes a tamper-evident audit record.

        Args:
            prompt: User message to send to the model.
            user_id: Optional plain-text user identifier (stored hashed).
            stream: Reserved for future streaming support; currently ignored.

        Returns:
            RunResult containing the model response and all metadata.

        Raises:
            EnterpriseClaudeError: If the agent has been shut down.
            GovernanceViolation: If pre_check detects a policy violation.
        """
        if self._status == "shutdown":
            raise EnterpriseClaudeError(
                "Agent has been shut down",
                agent_id=self.agent_id,
            )

        self._logger.debug(
            "Running prompt on agent %s (model=%s)", self.agent_id[:8], self.model
        )

        # Step 1 — governance pre-check
        await self.governance.pre_check(
            prompt=prompt,
            persona=self.config.persona,
            model=self.model,
            system_prompt=self.config.system_prompt,
        )

        # Step 2 — Anthropic API call
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        create_kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 4096,
            "system": self.config.system_prompt,
            "messages": messages,
        }
        if self.config.tools:
            create_kwargs["tools"] = self.config.tools

        response = await self._client.messages.create(**create_kwargs)

        response_text: str = (
            response.content[0].text if response.content else ""
        )
        input_tokens: int = response.usage.input_tokens
        output_tokens: int = response.usage.output_tokens

        self._logger.debug(
            "Received response (%d input, %d output tokens)",
            input_tokens,
            output_tokens,
        )

        # Step 3 — governance post-check
        gov_result: GovernanceResult = await self.governance.post_check(
            response_text=response_text,
            persona=self.config.persona,
            model=self.model,
        )

        # Step 4 — record usage (model is now first positional arg)
        usage = await self.monitor.record_usage(
            self.model,
            input_tokens,
            output_tokens,
            agent_id=self.agent_id,
            persona=self.config.persona,
            session_id=self.session_id,
        )

        # Determine governance outcome label
        gov_status: Literal["pass", "flag", "block"]
        if not gov_result.passed:
            gov_status = "block" if gov_result.violations else "flag"
        elif gov_result.flags:
            gov_status = "flag"
        else:
            gov_status = "pass"

        # Step 5 — audit log
        user_hash = AuditLogger.hash_user_id(user_id or "anonymous")
        event = AuditEvent(
            event_id=str(uuid4()),
            timestamp=datetime.now(UTC),
            agent_id=self.agent_id,
            persona=self.config.persona,
            model_used=self.model,
            input_token_count=input_tokens,
            output_token_count=output_tokens,
            governance_result=gov_status,
            hook_triggered=bool(
                getattr(self.governance, "_pre_hooks", [])
                or getattr(self.governance, "_post_hooks", [])
            ),
            session_id=self.session_id,
            user_hash=user_hash,
        )
        await self.audit_logger.log_event(event)

        # Update state
        self.total_calls += 1
        self._total_cost_usd += usage.cost_usd
        self.last_active = datetime.now(UTC)
        self._status = "active"

        return RunResult(
            content=response_text,
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=usage.cost_usd,
            governance_result=gov_result,
            agent_id=self.agent_id,
            session_id=self.session_id,
            timestamp=datetime.now(UTC),
        )

    def get_status(self) -> AgentStatus:
        """Return a current status snapshot for this agent.

        Returns:
            AgentStatus with all live metrics populated.
        """
        return AgentStatus(
            agent_id=self.agent_id,
            name=self.config.name,
            persona=self.config.persona,
            model=self.model,
            status=self._status,
            created_at=self.created_at,
            last_active=self.last_active,
            total_calls=self.total_calls,
            total_cost_usd=self._total_cost_usd,
            session_id=self.session_id,
        )

    def shutdown(self) -> None:
        """Mark this agent as shut down.

        After calling shutdown(), any subsequent run() invocation will raise
        EnterpriseClaudeError. This method is idempotent.
        """
        if self._status != "shutdown":
            self._status = "shutdown"
            self._logger.info("Agent %s shut down", self.agent_id)


# ---------------------------------------------------------------------------
# AgentOrchestrator
# ---------------------------------------------------------------------------


class AgentOrchestrator:
    """Central manager for all Claude agents in an enterprise deployment.

    Enforces model policy, wires governance, monitoring, and audit to every
    agent, and provides fleet-level visibility (list, cost, status).

    Typical usage::

        async with AgentOrchestrator(api_key="...") as orch:
            agent = await orch.create_agent(
                name="MyAgent",
                system_prompt="You are a helpful assistant.",
                persona="analyst",
            )
            result = await agent.run("Summarise this report: ...")
    """

    def __init__(
        self,
        model_policy: dict[str, str] | None = None,
        governance: GovernanceLayer | None = None,
        monitor: TokenMonitor | None = None,
        audit_logger: AuditLogger | None = None,
        api_key: str | None = None,
    ) -> None:
        """Initialise the orchestrator.

        Args:
            model_policy: Maps tier names to model IDs.  Defaults to
                SUPPORTED_MODELS when None.
            governance: GovernanceLayer instance.  Created with defaults
                when None.
            monitor: TokenMonitor instance.  Created with defaults when None.
            audit_logger: AuditLogger instance.  Created with defaults when
                None.
            api_key: Anthropic API key.  Falls back to the
                ``ANTHROPIC_API_KEY`` environment variable when None.
        """
        # Merge user policy on top of built-in defaults so all tiers always resolve
        effective_policy = dict(SUPPORTED_MODELS)
        if model_policy:
            effective_policy.update(model_policy)
        self._model_policy: dict[str, str] = effective_policy
        self.governance: GovernanceLayer = governance or GovernanceLayer()
        self.monitor: TokenMonitor = monitor or TokenMonitor(
            config=MonitorConfig()
        )
        self.audit_logger: AuditLogger = audit_logger or AuditLogger()
        self._agents: dict[str, Agent] = {}
        self._logger = logging.getLogger("enterprise_claude.orchestrator")

        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._client = anthropic.AsyncAnthropic(api_key=resolved_key)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Initialize DB connections for monitor and audit_logger.

        Must be called once at startup before creating agents.  The async
        context manager ``__aenter__`` calls this automatically.
        """
        self._logger.info("Initializing AgentOrchestrator")
        if hasattr(self.monitor, "initialize"):
            await self.monitor.initialize()
        if hasattr(self.audit_logger, "initialize"):
            await self.audit_logger.initialize()

    async def __aenter__(self) -> AgentOrchestrator:  # noqa: PYI034
        """Enter the async context manager, initializing all subsystems."""
        await self.initialize()
        return self

    async def __aexit__(self, *args: object) -> None:
        """Exit the async context manager, shutting down all live agents."""
        for agent in self._agents.values():
            if agent._status != "shutdown":
                agent.shutdown()

    # ------------------------------------------------------------------
    # Agent factory
    # ------------------------------------------------------------------

    async def create_agent(
        self,
        name: str,
        system_prompt: str,
        persona: str,
        tools: list[dict[str, Any]] | None = None,
        mcp_connectors: list[str] | None = None,
        tier: Literal["default", "batch", "gated"] = "default",
        model_tier: Literal["default", "batch", "gated"] | None = None,  # backward compat
        approval_granted: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> Agent:
        """Create and register a new governed agent.

        Args:
            name: Human-readable agent name.
            system_prompt: Instruction passed as the system message.
            persona: Named role used by the governance layer.
            tools: Optional Anthropic tool definitions.
            mcp_connectors: Connector names resolved via MCPConnectorRegistry.
            model_tier: One of 'default', 'batch', or 'gated'.
            approval_granted: Must be True when model_tier='gated'.
            metadata: Arbitrary caller metadata.

        Returns:
            A ready-to-use Agent instance.

        Raises:
            ApprovalRequiredError: When model_tier='gated' and
                approval_granted is False.
        """
        # Support both 'tier' (new) and 'model_tier' (backward compat) param names
        effective_tier: Literal["default", "batch", "gated"] = (
            model_tier if model_tier is not None else tier
        )

        if effective_tier == "gated" and not approval_granted:
            raise ApprovalRequiredError(
                "claude-opus model requires explicit approval",
                violation_type="gated_model_no_approval",
                model_tier=effective_tier,
                persona=persona,
            )

        model = self._resolve_model(effective_tier)
        agent_id = str(uuid4())

        config = AgentConfig(
            name=name,
            system_prompt=system_prompt,
            persona=persona,
            tools=tools or [],
            mcp_connectors=mcp_connectors or [],
            model_tier=effective_tier,
            approval_granted=approval_granted,
            metadata=metadata or {},
        )

        agent = Agent(
            agent_id=agent_id,
            config=config,
            model=model,
            governance=self.governance,
            monitor=self.monitor,
            audit_logger=self.audit_logger,
            anthropic_client=self._client,
        )

        self._agents[agent_id] = agent
        self._logger.info(
            "Created agent '%s' id=%s model=%s persona=%s",
            name,
            agent_id[:8],
            model,
            persona,
        )
        return agent

    # ------------------------------------------------------------------
    # Fleet management
    # ------------------------------------------------------------------

    async def list_agents(self) -> list[AgentStatus]:
        """Return a status snapshot for every registered agent.

        Returns:
            List of AgentStatus objects ordered by creation time.
        """
        statuses = [a.get_status() for a in self._agents.values()]
        statuses.sort(key=lambda s: s.created_at)
        return statuses

    async def get_agent(self, agent_id: str) -> Agent:
        """Return an Agent by its ID.

        Args:
            agent_id: UUID string assigned at creation time.

        Returns:
            The matching Agent instance.

        Raises:
            AgentNotFoundError: When no agent with that ID exists.
        """
        agent = self._agents.get(agent_id)
        if agent is None:
            raise AgentNotFoundError(
                f"No agent found with id '{agent_id}'",
                agent_id=agent_id,
            )
        return agent

    async def shutdown_agent(self, agent_id: str) -> None:
        """Gracefully shut down a specific agent.

        Args:
            agent_id: UUID string of the agent to shut down.

        Raises:
            AgentNotFoundError: When no agent with that ID exists.
        """
        agent = await self.get_agent(agent_id)
        agent.shutdown()
        self._logger.info("Shut down agent %s", agent_id[:8])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_model(self, tier: str) -> str:
        """Map a tier name to a concrete model ID using the active policy.

        Args:
            tier: One of the keys in SUPPORTED_MODELS (or the custom policy).

        Returns:
            Model ID string (e.g., 'claude-sonnet-4-6').

        Raises:
            EnterpriseClaudeError: When the tier is not in the policy.
        """
        model = self._model_policy.get(tier)
        if model is None:
            valid = list(self._model_policy.keys())
            raise EnterpriseClaudeError(
                f"Unknown model tier '{tier}'. Valid tiers: {valid}"
            )
        return model
