"""
enterprise-claude-kit
=====================

A governance and orchestration framework that wraps the Anthropic Claude API
for enterprise deployments.

Key capabilities
----------------
* **Governance layer** — PII filtering, GxP compliance mode, prompt-length caps,
  model-access gating, and configurable policy hooks that run before every call.
* **Agent orchestrator** — register named agents with pre-configured system
  prompts, tool sets, and MCP connector bindings; dispatch tasks with a single
  ``await orchestrator.run(agent_id, prompt)`` call.
* **Token / cost monitor** — per-user, per-team, and org-wide token accounting
  persisted in SQLite; raises ``BudgetExceededError`` when daily limits are hit.
* **Audit logger** — immutable, append-only event log (SQLite-backed) with
  structured fields, retention policy, and query/filter helpers.
* **Adoption tracker** — wave-based roll-out engine; gates model access by
  persona/group membership and tracks feature-adoption metrics over time.
* **MCP connector registry** — first-class wrappers for GitHub, Jira, Slack,
  Confluence, and SharePoint that surface as MCP tool definitions consumable
  directly by the Anthropic SDK.

Quick start
-----------
>>> import asyncio
>>> from enterprise_claude import GovernanceLayer, GovernanceConfig
>>> cfg = GovernanceConfig(pii_filter=True, max_prompt_length=50_000)
>>> gov = GovernanceLayer(config=cfg)
>>> result = asyncio.run(gov.check("Summarise this document: …"))
>>> print(result.allowed)  # True | False

Environment variables
---------------------
All tunables have ``ECL_`` prefixed env-var equivalents; copy ``.env.example``
and load it with ``python-dotenv`` before importing this package.

Logging
-------
The library attaches a ``NullHandler`` to the ``enterprise_claude`` logger so
that log output is *opt-in*.  In your application call::

    logging.basicConfig(level=logging.INFO)
    logging.getLogger("enterprise_claude").setLevel(logging.DEBUG)
"""

from __future__ import annotations

import logging
from typing import Any

# ---------------------------------------------------------------------------
# Library-level logging — NullHandler so callers must opt in
# ---------------------------------------------------------------------------
logging.getLogger("enterprise_claude").addHandler(logging.NullHandler())

# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class EnterpriseClaudeError(Exception):
    """Base exception for all errors raised by enterprise-claude-kit.

    All library-specific exceptions derive from this class, making it easy for
    callers to write a single broad ``except EnterpriseClaudeError`` handler
    while still being able to narrow to specific sub-types.

    Parameters
    ----------
    message:
        Human-readable description of the error.
    violation_type:
        Optional short code that categorises the error (used primarily by
        governance sub-classes).  ``None`` for non-policy errors.
    **details:
        Arbitrary keyword arguments providing extra structured context.
        They are stored on ``self.details`` and included in ``__str__``.
    """

    def __init__(
        self,
        message: str,
        violation_type: str | None = None,
        **details: Any,
    ) -> None:
        super().__init__(message)
        self.message: str = message
        self.violation_type: str | None = violation_type
        self.details: dict[str, Any] = details

    def __str__(self) -> str:
        parts = [self.message]
        if self.violation_type:
            parts.append(f"[violation_type={self.violation_type}]")
        if self.details:
            kv = ", ".join(f"{k}={v!r}" for k, v in self.details.items())
            parts.append(f"({kv})")
        return " ".join(parts)

    def __repr__(self) -> str:
        cls = type(self).__name__
        return (
            f"{cls}(message={self.message!r}, "
            f"violation_type={self.violation_type!r}, "
            f"details={self.details!r})"
        )


class GovernanceViolation(EnterpriseClaudeError):
    """Raised when a request is blocked by the governance layer.

    This is the base class for all policy-level rejections.  Callers that need
    to distinguish between a budget problem and a content-policy problem should
    catch the more specific sub-classes first.

    Parameters
    ----------
    message:
        Explanation of which policy was violated and why.
    violation_type:
        Short code identifying the specific policy rule, e.g.
        ``"pii_detected"``, ``"prompt_too_long"``, ``"gxp_restricted_model"``.
    **details:
        Extra context such as ``field="patient_id"``, ``model="claude-opus-4"``.
    """

    def __init__(
        self,
        message: str,
        violation_type: str | None = None,
        **details: Any,
    ) -> None:
        super().__init__(message, violation_type=violation_type, **details)


class BudgetExceededError(EnterpriseClaudeError):
    """Raised when a call would breach a configured token / cost budget.

    The ``TokenMonitor`` raises this before forwarding the request to the
    Anthropic API so that no tokens are consumed once a limit is hit.

    Parameters
    ----------
    message:
        Description of which budget was exceeded.
    violation_type:
        Always ``"budget_exceeded"`` by convention; override if needed.
    **details:
        Recommended keys: ``budget_usd``, ``spent_usd``, ``user_id``,
        ``team_id``, ``period``.

    Example
    -------
    >>> raise BudgetExceededError(
    ...     "Daily budget exhausted",
    ...     budget_usd=100.0,
    ...     spent_usd=101.23,
    ...     user_id="alice",
    ... )
    """

    def __init__(
        self,
        message: str,
        violation_type: str | None = "budget_exceeded",
        **details: Any,
    ) -> None:
        super().__init__(message, violation_type=violation_type, **details)


class WaveGateError(EnterpriseClaudeError):
    """Raised when a requested feature/model is not yet active for the caller.

    The adoption-tracker's wave system gates access by persona or user group.
    When a user attempts to use a capability that their wave has not reached,
    this exception is raised instead of forwarding the request.

    Parameters
    ----------
    message:
        Explanation of which wave gate was not met.
    violation_type:
        Always ``"wave_gate"`` by convention.
    **details:
        Recommended keys: ``wave_id``, ``required_persona``, ``user_id``,
        ``feature``.
    """

    def __init__(
        self,
        message: str,
        violation_type: str | None = "wave_gate",
        **details: Any,
    ) -> None:
        super().__init__(message, violation_type=violation_type, **details)


class AgentNotFoundError(EnterpriseClaudeError):
    """Raised when ``AgentOrchestrator.run()`` is called with an unknown agent_id.

    Parameters
    ----------
    message:
        Description of the lookup failure.
    violation_type:
        ``None`` — this is a configuration/programming error, not a policy one.
    **details:
        Recommended keys: ``agent_id``, ``registered_agents``.

    Example
    -------
    >>> raise AgentNotFoundError(
    ...     "No agent registered with id 'code-reviewer'",
    ...     agent_id="code-reviewer",
    ... )
    """

    def __init__(
        self,
        message: str,
        violation_type: str | None = None,
        **details: Any,
    ) -> None:
        super().__init__(message, violation_type=violation_type, **details)


class ConnectorNotFoundError(EnterpriseClaudeError):
    """Raised when ``get_connector()`` is called with an unknown connector name.

    Parameters
    ----------
    message:
        Description of the lookup failure.
    violation_type:
        ``None`` — configuration/programming error.
    **details:
        Recommended keys: ``connector_name``, ``available_connectors``.

    Example
    -------
    >>> raise ConnectorNotFoundError(
    ...     "No connector registered for 'salesforce'",
    ...     connector_name="salesforce",
    ...     available_connectors=["github", "jira", "slack"],
    ... )
    """

    def __init__(
        self,
        message: str,
        violation_type: str | None = None,
        **details: Any,
    ) -> None:
        super().__init__(message, violation_type=violation_type, **details)


class ApprovalRequiredError(GovernanceViolation):
    """Raised when a gated model is requested without the required approval.

    Some models (e.g. ``claude-opus-4``) may be restricted to approved users
    or teams only.  Attempts to use them without approval raise this exception,
    which is a ``GovernanceViolation`` sub-class so governance-level handlers
    catch it automatically.

    Parameters
    ----------
    message:
        Explanation of which model requires approval and how to request it.
    violation_type:
        Always ``"approval_required"`` by convention.
    **details:
        Recommended keys: ``model``, ``user_id``, ``approval_url``.

    Example
    -------
    >>> raise ApprovalRequiredError(
    ...     "Model 'claude-opus-4' requires explicit approval",
    ...     violation_type="approval_required",
    ...     model="claude-opus-4",
    ...     user_id="bob",
    ...     approval_url="https://internal.example.com/ai/request-access",
    ... )
    """

    def __init__(
        self,
        message: str,
        violation_type: str | None = "approval_required",
        **details: Any,
    ) -> None:
        super().__init__(message, violation_type=violation_type, **details)


# ---------------------------------------------------------------------------
# Public API re-exports
# ---------------------------------------------------------------------------

from enterprise_claude.adoption_tracker import AdoptionTracker, Persona, Wave
from enterprise_claude.audit import AuditEvent, AuditFilter, AuditLogger
from enterprise_claude.governance import (
    GovernanceConfig,
    GovernanceLayer,
    GovernanceResult,
)
from enterprise_claude.mcp_connectors import (
    MCPConnectorRegistry,
    get_connector,
    list_connectors,
)
from enterprise_claude.orchestrator import Agent, AgentOrchestrator
from enterprise_claude.token_monitor import MonitorConfig, TokenMonitor

__version__ = "0.1.0"

__all__ = [
    # Adoption / wave management
    "AdoptionTracker",
    # Orchestration
    "Agent",
    # Exceptions
    "AgentNotFoundError",
    "AgentOrchestrator",
    "ApprovalRequiredError",
    # Audit
    "AuditEvent",
    "AuditFilter",
    "AuditLogger",
    "BudgetExceededError",
    "ConnectorNotFoundError",
    "EnterpriseClaudeError",
    # Governance
    "GovernanceConfig",
    "GovernanceLayer",
    "GovernanceResult",
    "GovernanceViolation",
    # MCP connectors
    "MCPConnectorRegistry",
    # Token / cost monitoring
    "MonitorConfig",
    "Persona",
    "TokenMonitor",
    "Wave",
    "WaveGateError",
    "get_connector",
    "list_connectors",
]
