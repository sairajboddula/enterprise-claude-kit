"""
Governance and policy enforcement layer for enterprise Claude deployments.

This is the most critical module — every Claude API call passes through here.
"""
from __future__ import annotations

import inspect
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exception hierarchy (re-exported from enterprise_claude)
# ---------------------------------------------------------------------------


class EnterpriseClaudeError(Exception):
    """Base exception for all enterprise-claude-kit errors."""

    def __init__(
        self,
        message: str,
        violation_type: str | None = None,
        **details: Any,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.violation_type = violation_type
        self.details = details

    def __str__(self) -> str:
        base = (
            f"[{self.violation_type}] {self.message}"
            if self.violation_type
            else self.message
        )
        if self.details:
            detail_str = ", ".join(f"{k}={v}" for k, v in self.details.items())
            return f"{base} ({detail_str})"
        return base


class GovernanceViolation(EnterpriseClaudeError):
    """Raised when a governance policy check fails."""


class BudgetExceededError(EnterpriseClaudeError):
    """Raised when a cost budget is exceeded."""


class WaveGateError(EnterpriseClaudeError):
    """Raised when a deployment wave gate blocks progression."""


class AgentNotFoundError(EnterpriseClaudeError):
    """Raised when a requested agent cannot be found."""


class ConnectorNotFoundError(EnterpriseClaudeError):
    """Raised when a requested data-source connector cannot be found."""


class ApprovalRequiredError(GovernanceViolation):
    """Raised when human approval is required before proceeding."""


# ---------------------------------------------------------------------------
# PII regex patterns — compiled once at module load for performance
# ---------------------------------------------------------------------------

_PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
        re.IGNORECASE,
    ),
    "phone": re.compile(
        r"\b(\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
    ),
    "ssn": re.compile(
        r"\b\d{3}-\d{2}-\d{4}\b",
    ),
    "credit_card": re.compile(
        # Match common card numbers with optional spaces or hyphens between digit groups
        r"\b(?:4[0-9]{3}|5[1-5][0-9]{2}|3[47][0-9]{2}|6(?:011|5[0-9]{2}))"
        r"[\s\-]?[0-9]{4}[\s\-]?[0-9]{4}[\s\-]?[0-9]{1,4}\b",
    ),
    "ip_address": re.compile(
        r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
    ),
}

# GxP / regulatory citation markers expected in compliant responses
_GXP_CITATION_RE = re.compile(r"\[(?:SOURCE|REF|CITATION):", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class GovernanceConfig(BaseModel):
    """Immutable configuration for the GovernanceLayer."""

    model_config = ConfigDict(frozen=True)

    pii_filter: bool = True
    blocked_keywords: list[str] = Field(default_factory=list)
    max_prompt_length: int | None = None  # None = no limit
    require_system_prompt: bool = False
    allowed_personas: list[str] | None = None  # None = all allowed
    gxp_mode: bool = False  # pharma / regulated-industry mode
    constitutional_ai: bool = False
    log_violations: bool = True


class PIIMatch(BaseModel):
    """Describes a single PII hit within a block of text."""

    pii_type: str  # "email", "phone", "ssn", "credit_card", "ip_address"
    pattern: str  # the matched text (redact before storing in logs)
    position: int  # character offset of match start


class GovernanceResult(BaseModel):
    """Outcome of a post-response governance check."""

    passed: bool
    violations: list[str]
    pii_detected: list[PIIMatch]
    flags: list[str]  # non-blocking warnings
    gxp_compliant: bool | None = None  # only populated when gxp_mode=True
    timestamp: datetime
    persona: str
    model: str


# ---------------------------------------------------------------------------
# GovernanceLayer
# ---------------------------------------------------------------------------


class GovernanceLayer:
    """
    Policy enforcement layer that wraps every Claude API interaction.

    All pre/post checks run synchronously (fast regex + string ops).
    Hook functions may be sync or async — both are handled transparently.

    Example usage::

        gov = GovernanceLayer(pii_filter=True, gxp_mode=True)

        @gov.register_pre_hook
        async def my_hook(prompt, persona, model):
            ...

        await gov.pre_check(prompt, persona="analyst", model="claude-sonnet-4-6",
                            system_prompt="You are a helpful assistant.")
    """

    def __init__(
        self,
        config: GovernanceConfig | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialise governance layer.

        Parameters
        ----------
        config:
            A fully-constructed :class:`GovernanceConfig`.  If *None*, any
            keyword arguments are forwarded to ``GovernanceConfig(**kwargs)``.
        **kwargs:
            Shorthand for GovernanceConfig fields when *config* is omitted.
            ``pre_hooks`` and ``post_hooks`` lists are extracted before
            forwarding remaining kwargs to GovernanceConfig.
        """
        # Extract hook lists before they reach GovernanceConfig (not a model field)
        initial_pre_hooks: list[Callable[..., Any]] = list(kwargs.pop("pre_hooks", None) or [])
        initial_post_hooks: list[Callable[..., Any]] = list(kwargs.pop("post_hooks", None) or [])

        if config is not None:
            self.config = config
        else:
            self.config = GovernanceConfig(**kwargs)

        self._pre_hooks: list[Callable[..., Any]] = initial_pre_hooks
        self._post_hooks: list[Callable[..., Any]] = initial_post_hooks

    # ------------------------------------------------------------------
    # Hook registration
    # ------------------------------------------------------------------

    def register_pre_hook(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """
        Decorator: register a pre-call hook.

        The hook receives ``(prompt: str, context: dict)`` and may be a
        plain function or a coroutine.  Raise :class:`GovernanceViolation`
        inside the hook to block the call.
        """
        self._pre_hooks.append(fn)
        return fn

    def register_post_hook(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """
        Decorator: register a post-call hook.

        The hook receives ``(response_text: str, context: dict)`` and may
        be a plain function or a coroutine.
        """
        self._post_hooks.append(fn)
        return fn

    # ------------------------------------------------------------------
    # Pre-call checks
    # ------------------------------------------------------------------

    async def pre_check(
        self,
        prompt: str,
        persona: str = "default",
        model: str = "default",
        system_prompt: str | None = None,
    ) -> GovernanceResult:
        """
        Run all pre-call governance checks.

        Returns a :class:`GovernanceResult` with ``passed=True`` when all
        checks pass.  Raises :class:`GovernanceViolation` on the first
        failing check (all violations are collected and raised together).

        Parameters
        ----------
        prompt:
            The full user prompt text.
        persona:
            The active agent persona label (e.g. ``"analyst"``).
            Defaults to ``"default"`` when not specified.
        model:
            Claude model identifier (e.g. ``"claude-sonnet-4-6"``).
            Defaults to ``"default"`` when not specified.
        system_prompt:
            Optional system prompt provided alongside the user turn.
        """
        violations: list[str] = []

        # 1. Prompt length
        if (
            self.config.max_prompt_length is not None
            and len(prompt) > self.config.max_prompt_length
        ):
            msg = (
                f"Prompt length {len(prompt)} exceeds maximum "
                f"{self.config.max_prompt_length} characters"
            )
            self._log_violation("PROMPT_TOO_LONG", persona, model, {"length": len(prompt)})
            violations.append(msg)

        # 2. System prompt required
        if self.config.require_system_prompt and not system_prompt:
            msg = "A system prompt is required but was not supplied"
            self._log_violation("MISSING_SYSTEM_PROMPT", persona, model, {})
            violations.append(msg)

        # 3. Allowed personas
        if (
            self.config.allowed_personas is not None
            and persona not in self.config.allowed_personas
        ):
            msg = (
                f"Persona '{persona}' is not in the allowed list: "
                f"{self.config.allowed_personas}"
            )
            self._log_violation("DISALLOWED_PERSONA", persona, model, {"persona": persona})
            violations.append(msg)

        # 4. Blocked keywords (case-insensitive)
        blocked_found = self._check_keywords(prompt)
        if blocked_found:
            msg = f"Prompt contains blocked keyword(s): {blocked_found}"
            self._log_violation(
                "BLOCKED_KEYWORDS", persona, model, {"keywords": blocked_found}
            )
            violations.append(msg)

        # 5. PII detection
        pii_matches: list[PIIMatch] = []
        if self.config.pii_filter:
            pii_matches = self._detect_pii(prompt)
            if pii_matches:
                types_found = list({m.pii_type for m in pii_matches})
                msg = f"PII detected in prompt: {types_found}"
                self._log_violation(
                    "PII_IN_PROMPT",
                    persona,
                    model,
                    {"pii_types": types_found, "count": len(pii_matches)},
                )
                violations.append(msg)

        if violations:
            raise GovernanceViolation(
                "; ".join(violations),
                violation_type="PRE_CHECK_FAILED",
                persona=persona,
                model=model,
            )

        # 6. Run registered pre-hooks — hook signature: (prompt, context_dict)
        context: dict[str, Any] = {
            "persona": persona,
            "model": model,
            "system_prompt": system_prompt,
        }
        for hook in self._pre_hooks:
            if inspect.iscoroutinefunction(hook):
                await hook(prompt, context)
            else:
                hook(prompt, context)

        return GovernanceResult(
            passed=True,
            violations=[],
            pii_detected=pii_matches,
            flags=[],
            gxp_compliant=None,
            timestamp=datetime.now(tz=UTC),
            persona=persona,
            model=model,
        )

    # ------------------------------------------------------------------
    # Post-response checks
    # ------------------------------------------------------------------

    async def post_check(
        self,
        response_text: str,
        persona: str = "default",
        model: str = "default",
    ) -> GovernanceResult:
        """
        Run all post-response governance checks.

        Never raises — issues are captured in the returned
        :class:`GovernanceResult`.  GxP mode checks for citation markers
        (``[SOURCE:]``, ``[REF:]``, or ``[CITATION:]``).

        Parameters
        ----------
        response_text:
            The raw text of the Claude response.
        persona:
            Active agent persona label.
        model:
            Claude model identifier used for the call.

        Returns
        -------
        GovernanceResult
            Full structured outcome including PII hits, violations, and flags.
        """
        violations: list[str] = []
        flags: list[str] = []
        gxp_compliant: bool | None = None

        # PII in response
        pii_matches: list[PIIMatch] = []
        if self.config.pii_filter:
            pii_matches = self._detect_pii(response_text)
            if pii_matches:
                types_found = list({m.pii_type for m in pii_matches})
                flags.append(f"PII detected in response: {types_found}")

        # Blocked keywords in response
        blocked_found = self._check_keywords(response_text)
        if blocked_found:
            violations.append(f"Response contains blocked keyword(s): {blocked_found}")

        # GxP citation check
        if self.config.gxp_mode:
            gxp_compliant = bool(_GXP_CITATION_RE.search(response_text))
            if not gxp_compliant:
                flags.append(
                    "GxP mode: response lacks required citation marker "
                    "([SOURCE:], [REF:], or [CITATION:])"
                )

        # Run registered post-hooks — hook signature: (response, context_dict)
        post_context: dict[str, Any] = {"persona": persona, "model": model}
        for hook in self._post_hooks:
            try:
                if inspect.iscoroutinefunction(hook):
                    await hook(response_text, post_context)
                else:
                    hook(response_text, post_context)
            except GovernanceViolation as exc:
                violations.append(str(exc))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Post-hook %s raised an unexpected error: %s",
                    getattr(hook, "__name__", repr(hook)),
                    exc,
                )

        passed = len(violations) == 0

        return GovernanceResult(
            passed=passed,
            violations=violations,
            pii_detected=pii_matches,
            flags=flags,
            gxp_compliant=gxp_compliant,
            timestamp=datetime.now(tz=UTC),
            persona=persona,
            model=model,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _detect_pii(self, text: str) -> list[PIIMatch]:
        """
        Scan *text* for all known PII patterns.

        Returns
        -------
        list[PIIMatch]
            One entry per individual regex hit, ordered by position.
        """
        matches: list[PIIMatch] = []
        for pii_type, pattern in _PII_PATTERNS.items():
            for m in pattern.finditer(text):
                matches.append(
                    PIIMatch(
                        pii_type=pii_type,
                        pattern=m.group(),
                        position=m.start(),
                    )
                )
        matches.sort(key=lambda x: x.position)
        return matches

    def _check_keywords(self, text: str) -> list[str]:
        """
        Return the subset of :attr:`GovernanceConfig.blocked_keywords` found
        in *text* (case-insensitive).
        """
        lower_text = text.lower()
        return [kw for kw in self.config.blocked_keywords if kw.lower() in lower_text]

    def _log_violation(
        self,
        violation_type: str,
        persona: str,
        model: str,
        details: dict[str, Any],
    ) -> None:
        """
        Emit a structured WARNING log entry for a governance violation.

        Only logs when :attr:`GovernanceConfig.log_violations` is *True*.
        """
        if not self.config.log_violations:
            return
        logger.warning(
            "Governance violation | type=%s | persona=%s | model=%s | ts=%s | details=%s",
            violation_type,
            persona,
            model,
            datetime.now(tz=UTC).isoformat(),
            details,
        )
