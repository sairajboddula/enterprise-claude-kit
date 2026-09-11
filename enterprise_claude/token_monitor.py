"""
Real-time token usage tracking and cost alerting.

Uses SQLite (via aiosqlite) for persistence across restarts.
All monetary values in USD.
"""
from __future__ import annotations

import inspect
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pricing table — per 1 million tokens, USD
# ---------------------------------------------------------------------------

MODEL_COSTS: dict[str, dict[str, float]] = {
    "claude-opus-4-6":   {"input": 15.00, "output": 75.00},
    "claude-opus-4-5":   {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-6": {"input": 3.00,  "output": 15.00},
    "claude-sonnet-4-5": {"input": 3.00,  "output": 15.00},
    "claude-haiku-4-5":  {"input": 0.80,  "output": 4.00},
    "claude-haiku-3-5":  {"input": 0.80,  "output": 4.00},
}

# DDL executed once on first connection
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS usage_records (
    record_id    TEXT PRIMARY KEY,
    timestamp    TEXT NOT NULL,
    agent_id     TEXT NOT NULL,
    persona      TEXT NOT NULL,
    model        TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd     REAL NOT NULL,
    session_id   TEXT
);
CREATE INDEX IF NOT EXISTS idx_timestamp ON usage_records(timestamp);
CREATE INDEX IF NOT EXISTS idx_agent     ON usage_records(agent_id);
CREATE INDEX IF NOT EXISTS idx_persona   ON usage_records(persona);
"""


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class MonitorConfig(BaseModel):
    """Immutable configuration for :class:`TokenMonitor`."""

    model_config = ConfigDict(frozen=True)

    daily_budget_usd: float = 100.0
    alert_threshold_pct: float = 0.8
    model_costs: dict[str, dict[str, float]] = Field(
        default_factory=lambda: MODEL_COSTS.copy()
    )
    reclaim_inactive_after_days: int = 7
    db_path: str = "./monitor.db"


class UsageRecord(BaseModel):
    """A single recorded API call with token counts and computed cost."""

    record_id: str          # UUID4
    timestamp: datetime
    agent_id: str
    persona: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    session_id: str | None = None


class CostSummary(BaseModel):
    """Aggregated cost breakdown over a time window."""

    by_agent: dict[str, float]      # agent_id  → total USD
    by_persona: dict[str, float]    # persona   → total USD
    by_model: dict[str, float]      # model     → total USD
    model_pct: dict[str, float]     # model     → % of total call volume (0-100)
    total_usd: float
    period_start: datetime
    period_end: datetime
    record_count: int


class BudgetStatus(BaseModel):
    """Current daily budget consumption snapshot."""

    used_usd: float
    budget_usd: float
    pct_used: float
    alert: bool
    remaining_usd: float


# ---------------------------------------------------------------------------
# TokenMonitor
# ---------------------------------------------------------------------------


class TokenMonitor:
    """
    Tracks token usage and costs across all agents.

    Persists records to SQLite for durability across process restarts.
    Fires alert callbacks when the daily budget threshold is crossed.

    Usage::

        monitor = TokenMonitor(daily_budget_usd=50.0, db_path="./usage.db")
        await monitor.initialize()

        @monitor.on_alert
        async def handle_alert(status: BudgetStatus):
            ...

        record = await monitor.record_usage(
            agent_id="ag-001", persona="analyst",
            model="claude-sonnet-4-6",
            input_tokens=1500, output_tokens=300,
        )
    """

    def __init__(
        self,
        config: MonitorConfig | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialise the monitor.

        Parameters
        ----------
        config:
            A fully-constructed :class:`MonitorConfig`.  If *None*, keyword
            arguments are forwarded to ``MonitorConfig(**kwargs)``.
        **kwargs:
            Shorthand for MonitorConfig fields when *config* is omitted.
        """
        self.config: MonitorConfig = config if config is not None else MonitorConfig(**kwargs)
        self._alert_callbacks: list[Callable[..., Any]] = []
        self._db_path = self.config.db_path

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """
        Create the database schema if it does not already exist.

        Must be called before any other coroutine on this instance.
        """
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA_SQL)
            await db.commit()
        logger.info("TokenMonitor initialised — db=%s", self._db_path)

    # ------------------------------------------------------------------
    # Hook registration
    # ------------------------------------------------------------------

    def on_alert(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """
        Decorator: register a callback fired when the budget alert threshold
        is crossed.

        The callback receives a single :class:`BudgetStatus` argument and
        may be sync or async.
        """
        self._alert_callbacks.append(fn)
        return fn

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    async def record_usage(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        agent_id: str | None = None,
        persona: str | None = None,
        session_id: str | None = None,
    ) -> UsageRecord:
        """
        Persist a single API call's token usage and calculate its cost.

        If recording this call pushes today's spend past the alert threshold,
        all registered alert callbacks are fired asynchronously.

        Parameters
        ----------
        model:
            Claude model identifier (first positional argument).
        input_tokens:
            Number of prompt tokens consumed.
        output_tokens:
            Number of completion tokens generated.
        agent_id:
            Optional unique identifier of the calling agent.
        persona:
            Optional active agent persona label.
        session_id:
            Optional session correlation identifier.

        Returns
        -------
        UsageRecord
            The persisted record with ``cost_usd`` filled in.
        """
        cost = self.calculate_cost(model, input_tokens, output_tokens)
        record = UsageRecord(
            record_id=str(uuid.uuid4()),
            timestamp=datetime.now(tz=UTC),
            agent_id=agent_id or "unknown",
            persona=persona or "unknown",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            session_id=session_id,
        )

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO usage_records
                    (record_id, timestamp, agent_id, persona, model,
                     input_tokens, output_tokens, cost_usd, session_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.record_id,
                    record.timestamp.isoformat(),
                    record.agent_id,
                    record.persona,
                    record.model,
                    record.input_tokens,
                    record.output_tokens,
                    record.cost_usd,
                    record.session_id,
                ),
            )
            await db.commit()

        logger.debug(
            "Usage recorded | agent=%s | model=%s | tokens_in=%d | tokens_out=%d | cost=%.6f",
            agent_id, model, input_tokens, output_tokens, cost,
        )

        # Check budget and fire alerts if threshold crossed
        status = await self.check_budget()
        if status.alert:
            await self._fire_alerts(status)

        return record

    # ------------------------------------------------------------------
    # Cost calculation
    # ------------------------------------------------------------------

    def calculate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """
        Calculate the USD cost for a single API call.

        Parameters
        ----------
        model:
            Claude model identifier.
        input_tokens:
            Number of prompt tokens.
        output_tokens:
            Number of completion tokens.

        Returns
        -------
        float
            Cost in USD, rounded to 8 decimal places.

        Raises
        ------
        ValueError
            If *model* is not present in the pricing table.
        """
        if model not in self.config.model_costs:
            raise ValueError(
                f"Unknown model '{model}'. Available models: "
                f"{list(self.config.model_costs)}"
            )
        prices = self.config.model_costs[model]
        cost = (input_tokens * prices["input"] + output_tokens * prices["output"]) / 1_000_000
        return round(cost, 8)

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    async def get_cost_summary(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> CostSummary:
        """
        Return cost breakdown by agent, persona, and model over a time window.

        If no date range is provided the window defaults to the current UTC day
        (midnight → now).

        Parameters
        ----------
        start_date:
            Inclusive lower bound (UTC).  Defaults to today midnight UTC.
        end_date:
            Inclusive upper bound (UTC).  Defaults to now.

        Returns
        -------
        CostSummary
            Aggregated cost figures and the exact period used.
        """
        now = datetime.now(tz=UTC)
        if start_date is None:
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if end_date is None:
            end_date = now

        start_iso = start_date.isoformat()
        end_iso = end_date.isoformat()

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row

            # By agent
            by_agent: dict[str, float] = {}
            async with db.execute(
                "SELECT agent_id, SUM(cost_usd) AS total "
                "FROM usage_records WHERE timestamp >= ? AND timestamp <= ? "
                "GROUP BY agent_id",
                (start_iso, end_iso),
            ) as cur:
                async for row in cur:
                    by_agent[row["agent_id"]] = round(row["total"], 8)

            # By persona
            by_persona: dict[str, float] = {}
            async with db.execute(
                "SELECT persona, SUM(cost_usd) AS total "
                "FROM usage_records WHERE timestamp >= ? AND timestamp <= ? "
                "GROUP BY persona",
                (start_iso, end_iso),
            ) as cur:
                async for row in cur:
                    by_persona[row["persona"]] = round(row["total"], 8)

            # By model
            by_model: dict[str, float] = {}
            async with db.execute(
                "SELECT model, SUM(cost_usd) AS total "
                "FROM usage_records WHERE timestamp >= ? AND timestamp <= ? "
                "GROUP BY model",
                (start_iso, end_iso),
            ) as cur:
                async for row in cur:
                    by_model[row["model"]] = round(row["total"], 8)

            # Total + count
            total_usd: float = 0.0
            record_count: int = 0
            async with db.execute(
                "SELECT SUM(cost_usd) AS total, COUNT(*) AS cnt "
                "FROM usage_records WHERE timestamp >= ? AND timestamp <= ?",
                (start_iso, end_iso),
            ) as cur:
                total_row = await cur.fetchone()
                if total_row is not None:
                    total_usd = round(total_row["total"] or 0.0, 8)
                    record_count = int(total_row["cnt"] or 0)

            # Model split percentage (by call count)
            model_pct: dict[str, float] = {}
            if record_count > 0:
                async with db.execute(
                    "SELECT model, COUNT(*) AS cnt "
                    "FROM usage_records WHERE timestamp >= ? AND timestamp <= ? "
                    "GROUP BY model",
                    (start_iso, end_iso),
                ) as cur:
                    async for row in cur:
                        model_pct[row["model"]] = round(
                            (row["cnt"] / record_count) * 100, 2
                        )

        return CostSummary(
            by_agent=by_agent,
            by_persona=by_persona,
            by_model=by_model,
            model_pct=model_pct,
            total_usd=total_usd,
            period_start=start_date,
            period_end=end_date,
            record_count=record_count,
        )

    async def check_budget(self) -> BudgetStatus:
        """
        Check today's cumulative spend against the configured daily budget.

        Returns
        -------
        BudgetStatus
            Current snapshot including alert flag.
        """
        now = datetime.now(tz=UTC)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT COALESCE(SUM(cost_usd), 0.0) AS total "
                "FROM usage_records WHERE timestamp >= ?",
                (day_start.isoformat(),),
            ) as cur:
                row = await cur.fetchone()
                used: float = round(row["total"] if row is not None else 0.0, 8)

        budget = self.config.daily_budget_usd
        pct = used / budget if budget > 0 else 0.0
        alert = pct >= self.config.alert_threshold_pct

        return BudgetStatus(
            used_usd=used,
            budget_usd=budget,
            pct_used=round(pct * 100, 2),
            alert=alert,
            remaining_usd=round(max(budget - used, 0.0), 8),
        )

    async def get_inactive_licenses(
        self,
        users: list[dict[str, str]],
        days: int | None = None,
    ) -> list[dict[str, str]]:
        """
        Identify users with zero API activity in the look-back window.

        Parameters
        ----------
        users:
            Each dict must have ``"user_id"`` and ``"persona"`` keys.
        days:
            Look-back window in days.  Defaults to
            :attr:`MonitorConfig.reclaim_inactive_after_days`.

        Returns
        -------
        list[dict[str, str]]
            Subset of *users* with no recorded usage in the window.
        """
        cutoff_days = days if days is not None else self.config.reclaim_inactive_after_days
        now = datetime.now(tz=UTC)
        cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
        # Subtract days manually to avoid dateutil dependency
        from datetime import timedelta
        cutoff = cutoff - timedelta(days=cutoff_days)
        cutoff_iso = cutoff.isoformat()

        inactive: list[dict[str, str]] = []
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            for user in users:
                user_id = user.get("user_id", "")
                persona = user.get("persona", "")
                async with db.execute(
                    "SELECT COUNT(*) AS cnt FROM usage_records "
                    "WHERE agent_id = ? AND persona = ? AND timestamp >= ?",
                    (user_id, persona, cutoff_iso),
                ) as cur:
                    row = await cur.fetchone()
                    if row is None or row["cnt"] == 0:
                        inactive.append(user)

        return inactive

    async def get_model_split(self) -> dict[str, float]:
        """
        Return percentage of total calls per model for today (UTC).

        Example return value::

            {"claude-sonnet-4-6": 85.2, "claude-haiku-4-5": 14.8}

        Returns
        -------
        dict[str, float]
            Model identifier mapped to percentage of today's call volume.
            Empty dict if no calls recorded today.
        """
        now = datetime.now(tz=UTC)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute(
                "SELECT COUNT(*) AS total FROM usage_records WHERE timestamp >= ?",
                (day_start,),
            ) as cur:
                row = await cur.fetchone()
                total: int = int(row["total"] or 0) if row is not None else 0

            if total == 0:
                return {}

            split: dict[str, float] = {}
            async with db.execute(
                "SELECT model, COUNT(*) AS cnt FROM usage_records "
                "WHERE timestamp >= ? GROUP BY model",
                (day_start,),
            ) as cur:
                async for row in cur:
                    split[row["model"]] = round((row["cnt"] / total) * 100, 2)

        return split

    # ------------------------------------------------------------------
    # Test / internal helpers
    # ------------------------------------------------------------------

    async def _insert_usage_for_date(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        usage_date: str,
        agent_id: str = "test",
        persona: str = "test",
    ) -> None:
        """
        Insert a usage record with an explicit date string (for testing).

        Parameters
        ----------
        model:
            Claude model identifier.
        input_tokens / output_tokens:
            Token counts.
        usage_date:
            ISO-format date string (e.g. ``"2025-01-15"``).  Time defaults
            to midnight UTC.
        agent_id / persona:
            Optional identifiers; default to ``"test"``.
        """
        import uuid as _uuid
        cost = self.calculate_cost(model, input_tokens, output_tokens)
        # Build a full ISO timestamp from the date string
        timestamp = f"{usage_date}T00:00:00+00:00"
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO usage_records
                    (record_id, timestamp, agent_id, persona, model,
                     input_tokens, output_tokens, cost_usd, session_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(_uuid.uuid4()),
                    timestamp,
                    agent_id,
                    persona,
                    model,
                    input_tokens,
                    output_tokens,
                    cost,
                    None,
                ),
            )
            await db.commit()

    async def _fire_alerts(self, status: BudgetStatus) -> None:
        """
        Invoke all registered alert callbacks with *status*.

        Callbacks may be sync or async.  Exceptions are logged and swallowed
        so that a broken callback never prevents the caller from continuing.
        """
        for cb in self._alert_callbacks:
            try:
                if inspect.iscoroutinefunction(cb):
                    await cb(status)
                else:
                    cb(status)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Alert callback %s raised: %s",
                    getattr(cb, "__name__", repr(cb)),
                    exc,
                )
