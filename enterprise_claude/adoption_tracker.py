"""
Wave-based adoption rollout tracking — the enterprise deployment model.

Tracks user activation across deployment waves and license utilization.
Enforces wave gates: a wave cannot be activated until its gate wave
reaches the configured threshold (default 80%).
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

import aiosqlite
from pydantic import BaseModel, ConfigDict

from enterprise_claude.governance import EnterpriseClaudeError, WaveGateError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class Wave(BaseModel):
    """Represents a single deployment wave.

    Attributes:
        wave_id: Unique UUID string.
        name: Human-readable wave name.
        target_count: Number of users to activate in this wave.
        order: Sort key — lower numbers activate first.
        status: Lifecycle state.
        gate_wave_id: ID of the wave that gates this one, or None.
        gate_threshold_pct: Fraction (0-1) of gate wave needed before this
            activates (e.g. 0.80 means 80%).
        activated_count: Current number of activated users.
        created_at: When this wave was created.
        activated_at: When the wave was set to 'active', or None.
    """

    model_config = ConfigDict(strict=False)

    wave_id: str
    name: str
    target_count: int
    order: int = 0
    status: Literal["planned", "active", "complete"] = "planned"
    gate_wave_id: str | None = None
    gate_threshold_pct: float | None = None
    activated_count: int = 0
    created_at: datetime
    activated_at: datetime | None = None


class WaveProgress(BaseModel):
    """Progress snapshot for a single wave.

    Attributes:
        wave_id: Wave identifier.
        wave_name: Human-readable name.
        activated: Number of users activated so far.
        target: Total target activation count.
        completion_pct: activated / target as a fraction in [0.0, 1.0].
    """

    model_config = ConfigDict(strict=False)

    wave_id: str
    wave_name: str
    activated: int
    target: int
    completion_pct: float


class Persona(BaseModel):
    """Lightweight persona stats kept for backward-compat re-exports."""

    model_config = ConfigDict(strict=False)

    name: str
    active_users: int = 0
    literacy_score: float = 0.0


# ---------------------------------------------------------------------------
# DB schema
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS waves (
    wave_id           TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    target_count      INTEGER NOT NULL,
    order_idx         INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'planned',
    gate_wave_id      TEXT,
    gate_threshold_pct REAL,
    activated_count   INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    activated_at      TEXT
);

CREATE TABLE IF NOT EXISTS activations (
    user_id      TEXT NOT NULL,
    wave_id      TEXT NOT NULL,
    persona      TEXT,
    activated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, wave_id)
);

CREATE TABLE IF NOT EXISTS user_calls (
    user_id      TEXT PRIMARY KEY,
    call_count   INTEGER NOT NULL DEFAULT 0,
    last_call_at TEXT
);
"""


# ---------------------------------------------------------------------------
# AdoptionTracker
# ---------------------------------------------------------------------------


class AdoptionTracker:
    """Tracks enterprise rollout waves and user adoption metrics.

    Enforces wave gates (a wave cannot activate until its gate wave
    reaches the configured threshold), tracks per-user call counts, and
    computes literacy scores.  All I/O is async via aiosqlite.

    Typical usage::

        tracker = AdoptionTracker()
        await tracker.initialize()

        wave = await tracker.create_wave("Architects", target_count=10, order=1)
        await tracker.activate_wave(wave.wave_id)
        await tracker.record_activation(wave.wave_id, "alice@acme.com")
    """

    def __init__(self, db_path: str = "./adoption.db") -> None:
        """Initialise the tracker.

        Args:
            db_path: Path to the SQLite database file. Created if absent.
        """
        self._db_path = db_path
        self._logger = logging.getLogger("enterprise_claude.adoption_tracker")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Create the DB schema if it does not already exist.

        Must be called once before any other method.
        """
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_DDL)
            await db.commit()
        self._logger.info("AdoptionTracker initialized (db=%s)", self._db_path)

    # ------------------------------------------------------------------
    # Wave management
    # ------------------------------------------------------------------

    async def create_wave(
        self,
        name: str,
        target_count: int,
        order: int = 0,
        gate_wave_id: str | None = None,
        gate_threshold_pct: float | None = None,
    ) -> Wave:
        """Create a new planned wave.

        Args:
            name: Human-readable wave name.
            target_count: Number of users to activate in this wave.
            order: Sort key for ordering waves (lower = activates first).
            gate_wave_id: Optional wave that gates this one.
            gate_threshold_pct: Fraction of gate wave needed (e.g. 0.80).

        Returns:
            The newly created Wave record (status='planned').
        """
        wave = Wave(
            wave_id=str(uuid4()),
            name=name,
            target_count=target_count,
            order=order,
            status="planned",
            gate_wave_id=gate_wave_id,
            gate_threshold_pct=gate_threshold_pct,
            activated_count=0,
            created_at=datetime.now(UTC),
        )
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO waves
                    (wave_id, name, target_count, order_idx, status,
                     gate_wave_id, gate_threshold_pct, activated_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    wave.wave_id,
                    wave.name,
                    wave.target_count,
                    wave.order,
                    wave.status,
                    wave.gate_wave_id,
                    wave.gate_threshold_pct,
                    wave.created_at.isoformat(),
                ),
            )
            await db.commit()
        self._logger.info("Created wave '%s' id=%s", name, wave.wave_id[:8])
        return wave

    async def activate_wave(self, wave_id: str) -> Wave:
        """Activate a planned wave.

        If the wave has a ``gate_wave_id``, the gate wave must have reached
        its ``gate_threshold_pct`` activation fraction first (raises
        :class:`WaveGateError` otherwise).

        Args:
            wave_id: UUID of the wave to activate.

        Returns:
            Updated Wave with status='active'.

        Raises:
            EnterpriseClaudeError: When the wave does not exist.
            WaveGateError: When the gate wave has not met its threshold.
        """
        wave = await self.get_wave(wave_id)

        if wave.gate_wave_id is not None:
            gate_progress = await self.get_wave_progress(wave.gate_wave_id)
            threshold = wave.gate_threshold_pct or 0.80
            if gate_progress.completion_pct < threshold:
                raise WaveGateError(
                    f"Wave gate not met: '{gate_progress.wave_name}' is at "
                    f"{gate_progress.completion_pct:.0%} "
                    f"(need {threshold:.0%})",
                    gate_wave_id=wave.gate_wave_id,
                    completion_pct=gate_progress.completion_pct,
                    required_pct=threshold,
                )

        now = datetime.now(UTC)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE waves SET status=?, activated_at=? WHERE wave_id=?",
                ("active", now.isoformat(), wave_id),
            )
            await db.commit()

        wave.status = "active"
        wave.activated_at = now
        self._logger.info("Activated wave '%s'", wave.name)
        return wave

    async def complete_wave(self, wave_id: str) -> Wave:
        """Mark a wave as complete.

        Args:
            wave_id: UUID of the wave to complete.

        Returns:
            Updated Wave with status='complete'.
        """
        wave = await self.get_wave(wave_id)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE waves SET status=? WHERE wave_id=?",
                ("complete", wave_id),
            )
            await db.commit()
        wave.status = "complete"
        return wave

    async def get_wave(self, wave_id: str) -> Wave:
        """Fetch a single wave by ID.

        Args:
            wave_id: UUID of the wave.

        Returns:
            Wave record.

        Raises:
            EnterpriseClaudeError: When no wave with that ID exists.
        """
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT wave_id, name, target_count, order_idx, status,
                       gate_wave_id, gate_threshold_pct, activated_count,
                       created_at, activated_at
                FROM waves WHERE wave_id=?
                """,
                (wave_id,),
            ) as cur:
                row = await cur.fetchone()

        if row is None:
            raise EnterpriseClaudeError(f"Wave '{wave_id}' not found")

        return Wave(
            wave_id=row["wave_id"],
            name=row["name"],
            target_count=row["target_count"],
            order=row["order_idx"],
            status=row["status"],
            gate_wave_id=row["gate_wave_id"],
            gate_threshold_pct=row["gate_threshold_pct"],
            activated_count=row["activated_count"],
            created_at=_parse_dt(row["created_at"]) or datetime.now(UTC),
            activated_at=_parse_dt(row["activated_at"]),
        )

    # ------------------------------------------------------------------
    # User activation
    # ------------------------------------------------------------------

    async def record_activation(
        self,
        wave_id: str,
        user_id: str,
        persona: str | None = None,
    ) -> None:
        """Record a user activation within a wave.

        Inserts an activation record and increments the wave's
        ``activated_count``.  Idempotent — re-activating the same user
        in the same wave is a no-op.

        Args:
            wave_id: UUID of the wave this user is being activated in.
            user_id: Plain-text user identifier.
            persona: Optional persona tag for this user.
        """
        now = datetime.now(UTC)
        async with aiosqlite.connect(self._db_path) as db:
            # Check if already activated (idempotency)
            async with db.execute(
                "SELECT 1 FROM activations WHERE user_id=? AND wave_id=?",
                (user_id, wave_id),
            ) as cur:
                already = await cur.fetchone()

            if already is None:
                await db.execute(
                    """
                    INSERT INTO activations (user_id, wave_id, persona, activated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (user_id, wave_id, persona, now.isoformat()),
                )
                await db.execute(
                    "UPDATE waves SET activated_count = activated_count + 1 WHERE wave_id=?",
                    (wave_id,),
                )
                await db.commit()

        self._logger.debug("Activated user '%s' in wave %s", user_id, wave_id[:8])

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    async def get_wave_progress(self, wave_id: str) -> WaveProgress:
        """Return activation progress for a single wave.

        Args:
            wave_id: UUID of the wave to inspect.

        Returns:
            WaveProgress with activated count, target, and completion fraction.
        """
        wave = await self.get_wave(wave_id)
        target = wave.target_count
        activated = wave.activated_count
        completion_pct = (activated / target) if target > 0 else 0.0
        return WaveProgress(
            wave_id=wave.wave_id,
            wave_name=wave.name,
            activated=activated,
            target=target,
            completion_pct=round(completion_pct, 6),
        )

    async def get_all_wave_progress(self) -> list[WaveProgress]:
        """Return progress for every wave, ordered by wave order index.

        Returns:
            List of WaveProgress objects.
        """
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT wave_id, name, target_count, activated_count
                FROM waves ORDER BY order_idx, created_at
                """
            ) as cur:
                rows = await cur.fetchall()

        results: list[WaveProgress] = []
        for row in rows:
            activated = row["activated_count"]
            target = row["target_count"]
            completion_pct = (activated / target) if target > 0 else 0.0
            results.append(
                WaveProgress(
                    wave_id=row["wave_id"],
                    wave_name=row["name"],
                    activated=activated,
                    target=target,
                    completion_pct=round(completion_pct, 6),
                )
            )
        return results

    # ------------------------------------------------------------------
    # Literacy scoring
    # ------------------------------------------------------------------

    async def record_call(self, user_id: str) -> None:
        """Increment the call count for a user.

        Used internally to track how deeply a user has engaged with the
        Claude tools, feeding into the literacy score.

        Args:
            user_id: Plain-text user identifier.
        """
        now = datetime.now(UTC)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO user_calls (user_id, call_count, last_call_at)
                VALUES (?, 1, ?)
                ON CONFLICT(user_id) DO UPDATE
                    SET call_count   = call_count + 1,
                        last_call_at = excluded.last_call_at
                """,
                (user_id, now.isoformat()),
            )
            await db.commit()

    async def get_literacy_score(self, user_id: str) -> float:
        """Return literacy score (0-100) for a user based on call count.

        Scoring breakpoints (log-scale interpolation):
        - 0 calls  → 0
        - 1 call   → 10
        - 10 calls → 50
        - 100+ calls → 100

        Args:
            user_id: Plain-text user identifier.

        Returns:
            Literacy score in [0.0, 100.0].
        """
        async with aiosqlite.connect(self._db_path) as db, db.execute(
            "SELECT call_count FROM user_calls WHERE user_id=?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()

        count = row[0] if row else 0
        return self._compute_literacy(count)

    def _compute_literacy(self, call_count: int) -> float:
        """Compute literacy score from a raw call count (log-scale)."""
        if call_count <= 0:
            return 0.0
        if call_count >= 100:
            return 100.0
        if call_count >= 10:
            return 50.0 + 50.0 * math.log10(call_count / 10) / math.log10(10)
        return 10.0 + 40.0 * math.log10(call_count) / math.log10(10)

    # ------------------------------------------------------------------
    # Inactive user detection
    # ------------------------------------------------------------------

    async def get_inactive_users(self, days: int = 7) -> list[str]:
        """Return user IDs that have been activated but made no calls.

        A user is considered inactive if they appear in the activations
        table but have never recorded a call (or have not called since the
        given number of days ago).

        Args:
            days: Look-back window in calendar days.

        Returns:
            List of plain-text user_id strings.
        """
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        # Users activated, with no call record OR whose last call is older than the cutoff
        async with (
            aiosqlite.connect(self._db_path) as db,
            db.execute(
                """
                SELECT DISTINCT a.user_id
                FROM activations a
                LEFT JOIN user_calls c ON a.user_id = c.user_id
                WHERE c.user_id IS NULL
                   OR c.last_call_at < ?
                """,
                (cutoff,),
            ) as cur,
        ):
            rows = await cur.fetchall()

        return [row[0] for row in rows]

    # ------------------------------------------------------------------
    # Test helpers (private)
    # ------------------------------------------------------------------

    async def _backdate_activation(self, user_id: str, days: int) -> None:
        """Backdate all activation records for a user by N days.

        Used in tests to simulate a user that was activated in the past.

        Args:
            user_id: Plain-text user identifier.
            days: Number of days to subtract from current activation timestamps.
        """
        new_ts = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE activations SET activated_at=? WHERE user_id=?",
                (new_ts, user_id),
            )
            await db.commit()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO-format datetime string, returning None for null values."""
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return None
