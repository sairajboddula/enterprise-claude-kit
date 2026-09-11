"""
Tests for enterprise_claude.governance module.

Coverage:
- PII detection (email, phone, SSN, credit card)
- Blocked keyword enforcement
- Prompt length limits
- GxP mode source marker validation
- pre/post hooks
- GovernanceConfig validation
- Custom exception hierarchy
"""
from __future__ import annotations

import pytest

from enterprise_claude.governance import (
    GovernanceConfig,
    GovernanceLayer,
    GovernanceViolation,
)

# ── pytest-asyncio auto mode ──────────────────────────────────────────────────
pytestmark = pytest.mark.asyncio


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def basic_layer() -> GovernanceLayer:
    """Layer with PII filter and two blocked keywords."""
    return GovernanceLayer(
        pii_filter=True,
        blocked_keywords=["confidential", "top secret"],
    )


@pytest.fixture
def gxp_layer() -> GovernanceLayer:
    """Layer with GxP mode enabled (no PII filter to keep tests simple)."""
    return GovernanceLayer(pii_filter=False, gxp_mode=True)


@pytest.fixture
def permissive_layer() -> GovernanceLayer:
    """Layer with all checks disabled — baseline for hook tests."""
    return GovernanceLayer(pii_filter=False, blocked_keywords=[], gxp_mode=False)


# ─── TestPIIDetection ─────────────────────────────────────────────────────────

class TestPIIDetection:
    async def test_email_detected(self, basic_layer: GovernanceLayer) -> None:
        """Email address in prompt raises GovernanceViolation with 'PII' in message."""
        with pytest.raises(GovernanceViolation, match="PII"):
            await basic_layer.pre_check("Contact john.doe@example.com for details")

    async def test_phone_detected(self, basic_layer: GovernanceLayer) -> None:
        """US-format phone number in prompt raises GovernanceViolation."""
        with pytest.raises(GovernanceViolation, match="PII"):
            await basic_layer.pre_check("Call me at 555-867-5309")

    async def test_ssn_detected(self, basic_layer: GovernanceLayer) -> None:
        """SSN in NNN-NN-NNNN format raises GovernanceViolation."""
        with pytest.raises(GovernanceViolation, match="PII"):
            await basic_layer.pre_check("My SSN is 123-45-6789")

    async def test_credit_card_detected(self, basic_layer: GovernanceLayer) -> None:
        """16-digit credit card number (with spaces) raises GovernanceViolation."""
        with pytest.raises(GovernanceViolation, match="PII"):
            await basic_layer.pre_check("Card number: 4111 1111 1111 1111")

    async def test_no_pii_passes(self, basic_layer: GovernanceLayer) -> None:
        """Clean prompt with no PII returns passed=True and does not raise."""
        result = await basic_layer.pre_check("What is the capital of France?")
        assert result.passed is True

    async def test_pii_filter_disabled(self) -> None:
        """When pii_filter=False, emails in the prompt are silently ignored."""
        layer = GovernanceLayer(pii_filter=False)
        result = await layer.pre_check("Email: test@example.com")
        assert result.passed is True

    async def test_multiple_pii_types(self, basic_layer: GovernanceLayer) -> None:
        """Prompt with several PII types still raises on the first match."""
        with pytest.raises(GovernanceViolation, match="PII"):
            await basic_layer.pre_check(
                "John Smith, SSN 123-45-6789, card 4111-1111-1111-1111"
            )

    async def test_pii_in_response_flagged(self, basic_layer: GovernanceLayer) -> None:
        """PII found in a *response* is flagged but does not raise (warn, don't block)."""
        result = await basic_layer.post_check("Contact support@corp.com for help")
        # Response is still passed=True — only flagged, not blocked
        assert result.passed is True
        assert any("PII" in flag for flag in result.flags)


# ─── TestKeywordBlocking ──────────────────────────────────────────────────────

class TestKeywordBlocking:
    async def test_blocked_keyword_raises(self, basic_layer: GovernanceLayer) -> None:
        """Exact blocked keyword in prompt raises GovernanceViolation."""
        with pytest.raises(GovernanceViolation, match="[Bb]locked"):
            await basic_layer.pre_check("This is confidential information")

    async def test_blocked_keyword_case_insensitive(self, basic_layer: GovernanceLayer) -> None:
        """Blocked keyword matching is case-insensitive."""
        with pytest.raises(GovernanceViolation, match="[Bb]locked"):
            await basic_layer.pre_check("This is CONFIDENTIAL information")

    async def test_unblocked_keyword_passes(self, basic_layer: GovernanceLayer) -> None:
        """A keyword that is not in the blocked list does not raise."""
        result = await basic_layer.pre_check("This is public information only")
        assert result.passed is True

    async def test_empty_blocked_list_passes(self) -> None:
        """With an empty blocked_keywords list, any content passes keyword check."""
        layer = GovernanceLayer(pii_filter=False, blocked_keywords=[])
        result = await layer.pre_check("confidential top secret classified")
        assert result.passed is True

    async def test_multiple_blocked_keywords(self) -> None:
        """Each keyword in a custom blocked list is enforced independently."""
        layer = GovernanceLayer(
            pii_filter=False,
            blocked_keywords=["alpha", "beta", "gamma"],
        )
        for keyword in ["alpha", "beta", "gamma"]:
            with pytest.raises(GovernanceViolation):
                await layer.pre_check(f"The {keyword} project is ongoing")


# ─── TestGxPMode ──────────────────────────────────────────────────────────────

class TestGxPMode:
    async def test_gxp_response_with_source_passes(self, gxp_layer: GovernanceLayer) -> None:
        """Response containing [SOURCE: ...] marker sets gxp_compliant=True."""
        result = await gxp_layer.post_check(
            "Adverse events must be reported within 15 days. [SOURCE: ICH E6(R2) §4.11]"
        )
        assert result.gxp_compliant is True
        assert not any("GxP" in f for f in result.flags)

    async def test_gxp_response_without_source_flags(self, gxp_layer: GovernanceLayer) -> None:
        """Response without any source marker sets gxp_compliant=False and adds flag."""
        result = await gxp_layer.post_check(
            "Adverse events must be reported within 15 days."
        )
        assert result.gxp_compliant is False
        assert any("GxP" in f or "source" in f.lower() for f in result.flags)

    async def test_gxp_disabled_no_check(self, permissive_layer: GovernanceLayer) -> None:
        """When gxp_mode=False, gxp_compliant is None (check is skipped entirely)."""
        result = await permissive_layer.post_check("No source marker here at all")
        assert result.gxp_compliant is None

    async def test_ref_marker_accepted(self, gxp_layer: GovernanceLayer) -> None:
        """[REF: ...] marker is accepted as a valid GxP citation."""
        result = await gxp_layer.post_check(
            "See [REF: FDA 21 CFR Part 312] for AE reporting timelines."
        )
        assert result.gxp_compliant is True

    async def test_citation_marker_accepted(self, gxp_layer: GovernanceLayer) -> None:
        """[CITATION: ...] marker is accepted as a valid GxP citation."""
        result = await gxp_layer.post_check(
            "This follows the protocol. [CITATION: ICH Q10 §3.2]"
        )
        assert result.gxp_compliant is True


# ─── TestHooks ────────────────────────────────────────────────────────────────

class TestHooks:
    async def test_pre_hook_called(self) -> None:
        """Synchronous pre-hook receives the prompt and is called before pre_check returns."""
        called: list[str] = []

        def my_hook(prompt: str, context: dict) -> None:
            called.append(prompt)

        layer = GovernanceLayer(pii_filter=False, pre_hooks=[my_hook])
        await layer.pre_check("Hello world")
        assert called == ["Hello world"]

    async def test_async_pre_hook_supported(self) -> None:
        """Async pre-hook is awaited correctly."""
        called: list[str] = []

        async def async_hook(prompt: str, context: dict) -> None:
            called.append("async")

        layer = GovernanceLayer(pii_filter=False, pre_hooks=[async_hook])
        await layer.pre_check("Test prompt")
        assert called == ["async"]

    async def test_post_hook_called(self) -> None:
        """Synchronous post-hook receives the response text."""
        captured: list[str] = []

        def my_hook(response: str, context: dict) -> None:
            captured.append(response)

        layer = GovernanceLayer(pii_filter=False, post_hooks=[my_hook])
        await layer.post_check("Hello response")
        assert captured == ["Hello response"]

    async def test_pre_hook_can_raise(self) -> None:
        """A hook that raises GovernanceViolation propagates out of pre_check."""
        def blocking_hook(prompt: str, context: dict) -> None:
            raise GovernanceViolation("Custom hook rejection")

        layer = GovernanceLayer(pii_filter=False, pre_hooks=[blocking_hook])
        with pytest.raises(GovernanceViolation, match="Custom hook rejection"):
            await layer.pre_check("Any prompt")

    async def test_multiple_hooks_all_called(self) -> None:
        """All hooks in a list are called in order, even if mixed sync/async."""
        call_log: list[int] = []

        def hook1(prompt: str, context: dict) -> None:
            call_log.append(1)

        def hook2(prompt: str, context: dict) -> None:
            call_log.append(2)

        async def hook3(prompt: str, context: dict) -> None:
            call_log.append(3)

        layer = GovernanceLayer(pii_filter=False, pre_hooks=[hook1, hook2, hook3])
        await layer.pre_check("Test")
        assert call_log == [1, 2, 3]


# ─── TestConfig ───────────────────────────────────────────────────────────────

class TestConfig:
    def test_default_config(self) -> None:
        """GovernanceConfig initialises with documented defaults."""
        config = GovernanceConfig()
        assert config.pii_filter is True
        assert config.blocked_keywords == []
        assert config.gxp_mode is False
        assert config.require_system_prompt is False
        assert config.constitutional_ai is False
        assert config.max_prompt_length is None
        assert config.allowed_personas is None

    async def test_persona_restriction(self) -> None:
        """When allowed_personas is set, an unlisted persona raises GovernanceViolation."""
        layer = GovernanceLayer(
            pii_filter=False,
            allowed_personas=["research", "clinical_ops"],
        )
        with pytest.raises(GovernanceViolation, match="[Pp]ersona"):
            await layer.pre_check("Hello", persona="marketing")

        # Allowed persona passes without raising
        result = await layer.pre_check("Hello", persona="research")
        assert result.passed is True

    async def test_max_prompt_length(self) -> None:
        """Prompt exceeding max_prompt_length raises GovernanceViolation."""
        layer = GovernanceLayer(pii_filter=False, max_prompt_length=50)
        with pytest.raises(GovernanceViolation, match="[Ll]ength|[Tt]oo long"):
            await layer.pre_check("A" * 51)

        # Exactly at the limit passes
        result = await layer.pre_check("A" * 50)
        assert result.passed is True

    async def test_system_prompt_required(self) -> None:
        """When require_system_prompt=True, calling without system_prompt raises."""
        layer = GovernanceLayer(pii_filter=False, require_system_prompt=True)
        with pytest.raises(GovernanceViolation, match="[Ss]ystem"):
            await layer.pre_check("Hello", system_prompt=None)

        # Providing a system prompt passes the check
        result = await layer.pre_check("Hello", system_prompt="You are helpful.")
        assert result.passed is True

    def test_frozen_config(self) -> None:
        """GovernanceConfig is immutable after construction (frozen Pydantic model).

        Pydantic v2 raises ValidationError; dataclasses raise TypeError;
        plain slots raise AttributeError — accept all three.
        """
        from pydantic import ValidationError as PydanticValidationError
        config = GovernanceConfig(pii_filter=True)
        with pytest.raises((AttributeError, TypeError, PydanticValidationError)):
            config.pii_filter = False  # type: ignore[misc]
