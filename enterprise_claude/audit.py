"""
Immutable audit trail for compliance and regulatory requirements.

Append-only SQLite storage. No UPDATE or DELETE operations permitted.
When gxp_mode=True, each event gets a SHA-256 checksum for tamper detection.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import math
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import aiosqlite
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DDL — append-only; no UPDATE or DELETE ever issued against this table
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS audit_events (
    event_id           TEXT PRIMARY KEY,
    timestamp          TEXT NOT NULL,
    agent_id           TEXT NOT NULL,
    persona            TEXT NOT NULL,
    model_used         TEXT NOT NULL,
    input_token_count  INTEGER NOT NULL,
    output_token_count INTEGER NOT NULL,
    governance_result  TEXT NOT NULL,
    hook_triggered     INTEGER NOT NULL,
    session_id         TEXT NOT NULL,
    user_hash          TEXT NOT NULL,
    checksum           TEXT,
    metadata           TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_persona   ON audit_events(persona);
CREATE INDEX IF NOT EXISTS idx_audit_gov       ON audit_events(governance_result);
"""

# Fields excluded when computing the GxP checksum so the hash is stable
_CHECKSUM_EXCLUDE = {"checksum"}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class AuditEvent(BaseModel):
    """A single immutable audit record for one Claude API call."""

    event_id: str               # UUID4
    timestamp: datetime
    agent_id: str
    persona: str
    model_used: str
    input_token_count: int
    output_token_count: int
    governance_result: Literal["pass", "flag", "block"]
    hook_triggered: bool
    session_id: str
    user_hash: str              # SHA-256 of user_id — never raw PII
    checksum: str | None = None  # SHA-256 of event JSON; set when gxp_mode=True
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuditFilter(BaseModel):
    """Parameters for a paginated audit query."""

    start_date: datetime | None = None
    end_date: datetime | None = None
    agent_id: str | None = None
    persona: str | None = None
    model_used: str | None = None
    governance_result: Literal["pass", "flag", "block"] | None = None
    page: int = 1
    page_size: int = 100


class AuditStats(BaseModel):
    """Aggregated statistics for a time period."""

    period: str                          # "day", "week", or "month"
    total_events: int
    by_governance_result: dict[str, int]
    by_persona: dict[str, int]
    by_model: dict[str, int]
    hook_triggered_count: int
    unique_agents: int


# ---------------------------------------------------------------------------
# AuditLogger
# ---------------------------------------------------------------------------


class AuditLogger:
    """
    Append-only audit log for every Claude API call.

    Enforces immutability — no UPDATE or DELETE is ever issued.  In GxP mode
    every event is SHA-256 checksummed so that offline tamper detection is
    possible via :meth:`verify_checksums`.

    Usage::

        logger = AuditLogger(db_path="./audit.db", gxp_mode=True)
        await logger.initialize()

        event = AuditEvent(
            event_id=str(uuid.uuid4()),
            timestamp=datetime.now(tz=timezone.utc),
            agent_id="ag-001",
            persona="analyst",
            model_used="claude-sonnet-4-6",
            input_token_count=500,
            output_token_count=120,
            governance_result="pass",
            hook_triggered=False,
            session_id="sess-xyz",
            user_hash=AuditLogger.hash_user_id("user@example.com"),
        )
        await logger.log_event(event)
    """

    def __init__(
        self,
        db_path: str = "./audit.db",
        gxp_mode: bool = False,
    ) -> None:
        """
        Initialise the audit logger.

        Parameters
        ----------
        db_path:
            Filesystem path for the SQLite database file.
        gxp_mode:
            When *True*, each event is checksummed for tamper detection and
            :meth:`verify_checksums` becomes meaningful.
        """
        self.db_path = db_path
        self.gxp_mode = gxp_mode

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def hash_user_id(user_id: str) -> str:
        """
        Return the SHA-256 hex digest of *user_id*.

        Call this before constructing :class:`AuditEvent` — raw user IDs
        must never be stored in the audit log.

        Parameters
        ----------
        user_id:
            The raw, un-hashed user identifier (e.g. email address or UUID).

        Returns
        -------
        str
            64-character hex string.
        """
        return hashlib.sha256(user_id.encode()).hexdigest()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """
        Create the audit schema if it does not already exist.

        Must be called once before any other coroutine on this instance.
        Idempotent — safe to call multiple times.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_SCHEMA_SQL)
            await db.commit()
        logger.info("AuditLogger initialised — db=%s gxp_mode=%s", self.db_path, self.gxp_mode)

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    async def log_event(self, event: AuditEvent) -> None:
        """
        Append *event* to the audit log.

        If :attr:`gxp_mode` is enabled, the checksum is computed and written
        into the event before persisting.  Existing records are never mutated.

        Parameters
        ----------
        event:
            The :class:`AuditEvent` to persist.  Construct via
            ``AuditEvent.model_validate(...)`` or directly.
        """
        if self.gxp_mode:
            checksum = self._compute_checksum(event)
            # Pydantic v2: model_copy returns a new instance with patched fields
            event = event.model_copy(update={"checksum": checksum})

        metadata_json = json.dumps(event.metadata, default=str)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO audit_events (
                    event_id, timestamp, agent_id, persona, model_used,
                    input_token_count, output_token_count, governance_result,
                    hook_triggered, session_id, user_hash, checksum, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.timestamp.isoformat(),
                    event.agent_id,
                    event.persona,
                    event.model_used,
                    event.input_token_count,
                    event.output_token_count,
                    event.governance_result,
                    int(event.hook_triggered),
                    event.session_id,
                    event.user_hash,
                    event.checksum,
                    metadata_json,
                ),
            )
            await db.commit()

        logger.debug(
            "Audit event logged | event_id=%s | agent=%s | result=%s",
            event.event_id,
            event.agent_id,
            event.governance_result,
        )

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    async def query_events(self, filters: AuditFilter) -> dict[str, Any]:
        """
        Return a paginated list of audit events matching *filters*.

        Parameters
        ----------
        filters:
            :class:`AuditFilter` specifying date range, optional equality
            constraints, and pagination parameters.

        Returns
        -------
        dict
            ``{"events": list[AuditEvent], "total": int,
               "page": int, "pages": int}``
        """
        conditions: list[str] = []
        params: list[Any] = []

        if filters.start_date is not None:
            conditions.append("timestamp >= ?")
            params.append(filters.start_date.isoformat())
        if filters.end_date is not None:
            conditions.append("timestamp <= ?")
            params.append(filters.end_date.isoformat())
        if filters.agent_id is not None:
            conditions.append("agent_id = ?")
            params.append(filters.agent_id)
        if filters.persona is not None:
            conditions.append("persona = ?")
            params.append(filters.persona)
        if filters.model_used is not None:
            conditions.append("model_used = ?")
            params.append(filters.model_used)
        if filters.governance_result is not None:
            conditions.append("governance_result = ?")
            params.append(filters.governance_result)

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        offset = (filters.page - 1) * filters.page_size

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # Total count
            async with db.execute(
                f"SELECT COUNT(*) AS cnt FROM audit_events {where_clause}",
                params,
            ) as cur:
                row = await cur.fetchone()
                total = row["cnt"] if row is not None else 0

            # Paginated rows
            async with db.execute(
                f"SELECT * FROM audit_events {where_clause} "
                "ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                params + [filters.page_size, offset],
            ) as cur:
                rows = await cur.fetchall()

        events = [_row_to_event(row) for row in rows]
        pages = math.ceil(total / filters.page_size) if filters.page_size else 1

        return {
            "events": events,
            "total": total,
            "page": filters.page,
            "pages": max(pages, 1),
        }

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    async def export_csv(
        self,
        start_date: datetime,
        end_date: datetime,
        output_path: str = "./audit_export.csv",
    ) -> str:
        """
        Export audit events in *[start_date, end_date]* to a CSV file.

        Parameters
        ----------
        start_date:
            Inclusive lower bound (UTC).
        end_date:
            Inclusive upper bound (UTC).
        output_path:
            Filesystem path for the output CSV file.

        Returns
        -------
        str
            Absolute path of the written file.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM audit_events "
                "WHERE timestamp >= ? AND timestamp <= ? "
                "ORDER BY timestamp ASC",
                (start_date.isoformat(), end_date.isoformat()),
            ) as cur:
                rows = list(await cur.fetchall())

        fieldnames = [
            "event_id", "timestamp", "agent_id", "persona", "model_used",
            "input_token_count", "output_token_count", "governance_result",
            "hook_triggered", "session_id", "user_hash", "checksum", "metadata",
        ]

        with open(output_path, "w", newline="", encoding="utf-8") as fh:  # noqa: ASYNC230
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(dict(row))

        logger.info(
            "Audit CSV exported | path=%s | rows=%d | start=%s | end=%s",
            output_path,
            len(rows),
            start_date.isoformat(),
            end_date.isoformat(),
        )
        return output_path

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    async def get_stats(
        self,
        period: Literal["day", "week", "month"] = "day",
    ) -> AuditStats:
        """
        Return aggregated statistics for the most recent *period*.

        Parameters
        ----------
        period:
            ``"day"`` → last 24 hours, ``"week"`` → last 7 days,
            ``"month"`` → last 30 days (all relative to now UTC).

        Returns
        -------
        AuditStats
            Counts broken down by governance result, persona, model, etc.
        """
        now = datetime.now(tz=UTC)
        delta_map: dict[str, timedelta] = {
            "day":   timedelta(days=1),
            "week":  timedelta(weeks=1),
            "month": timedelta(days=30),
        }
        since = (now - delta_map[period]).isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute(
                "SELECT COUNT(*) AS cnt FROM audit_events WHERE timestamp >= ?",
                (since,),
            ) as cur:
                _cnt_row = await cur.fetchone()
                total_events = _cnt_row["cnt"] if _cnt_row is not None else 0

            by_gov: dict[str, int] = {}
            async with db.execute(
                "SELECT governance_result, COUNT(*) AS cnt "
                "FROM audit_events WHERE timestamp >= ? GROUP BY governance_result",
                (since,),
            ) as cur:
                async for row in cur:
                    by_gov[row["governance_result"]] = row["cnt"]

            by_persona: dict[str, int] = {}
            async with db.execute(
                "SELECT persona, COUNT(*) AS cnt "
                "FROM audit_events WHERE timestamp >= ? GROUP BY persona",
                (since,),
            ) as cur:
                async for row in cur:
                    by_persona[row["persona"]] = row["cnt"]

            by_model: dict[str, int] = {}
            async with db.execute(
                "SELECT model_used, COUNT(*) AS cnt "
                "FROM audit_events WHERE timestamp >= ? GROUP BY model_used",
                (since,),
            ) as cur:
                async for row in cur:
                    by_model[row["model_used"]] = row["cnt"]

            async with db.execute(
                "SELECT COUNT(*) AS cnt FROM audit_events "
                "WHERE timestamp >= ? AND hook_triggered = 1",
                (since,),
            ) as cur:
                _hook_row = await cur.fetchone()
                hook_count = _hook_row["cnt"] if _hook_row is not None else 0

            async with db.execute(
                "SELECT COUNT(DISTINCT agent_id) AS cnt "
                "FROM audit_events WHERE timestamp >= ?",
                (since,),
            ) as cur:
                _agents_row = await cur.fetchone()
                unique_agents = _agents_row["cnt"] if _agents_row is not None else 0

        return AuditStats(
            period=period,
            total_events=total_events,
            by_governance_result=by_gov,
            by_persona=by_persona,
            by_model=by_model,
            hook_triggered_count=hook_count,
            unique_agents=unique_agents,
        )

    # ------------------------------------------------------------------
    # GxP tamper detection
    # ------------------------------------------------------------------

    async def verify_checksums(self) -> dict[str, bool]:
        """
        Re-compute the checksum for every event and compare against the stored value.

        This method is meaningful only when :attr:`gxp_mode` is *True*.  It
        reads all events that have a non-NULL checksum and verifies them.

        Returns
        -------
        dict[str, bool]
            ``{event_id: True}`` if the stored checksum matches the recomputed
            one; ``{event_id: False}`` if the record appears tampered.
        """
        if not self.gxp_mode:
            logger.warning("verify_checksums called but gxp_mode is False — returning empty dict")
            return {}

        results: dict[str, bool] = {}

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM audit_events WHERE checksum IS NOT NULL"
            ) as cur:
                rows = await cur.fetchall()

        for row in rows:
            event = _row_to_event(row)
            stored = event.checksum
            # Strip the stored checksum, recompute, compare
            event_without_checksum = event.model_copy(update={"checksum": None})
            expected = self._compute_checksum(event_without_checksum)
            results[event.event_id] = stored == expected

        tampered = [eid for eid, ok in results.items() if not ok]
        if tampered:
            logger.error("Tampered audit events detected: %s", tampered)

        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_checksum(self, event: AuditEvent) -> str:
        """
        Compute a SHA-256 checksum of *event* serialised as JSON.

        The ``checksum`` field itself is excluded so the hash is stable
        regardless of whether the field is set.

        Parameters
        ----------
        event:
            :class:`AuditEvent` whose data forms the hash input.

        Returns
        -------
        str
            64-character lowercase hex digest.
        """
        data = event.model_dump(exclude=_CHECKSUM_EXCLUDE)
        # Ensure datetime objects are serialised deterministically
        canonical = json.dumps(data, default=_json_default, sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _json_default(obj: Any) -> str:
    """JSON serialiser fallback — converts datetime to ISO-8601."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serialisable")


def _row_to_event(row: aiosqlite.Row) -> AuditEvent:
    """
    Convert a raw SQLite row into an :class:`AuditEvent` model instance.

    Parameters
    ----------
    row:
        A row returned with ``aiosqlite.Row`` as the row factory.

    Returns
    -------
    AuditEvent
        Validated Pydantic model.
    """
    metadata_raw = row["metadata"] or "{}"
    try:
        metadata = json.loads(metadata_raw)
    except json.JSONDecodeError:
        metadata = {}

    return AuditEvent.model_validate(
        {
            "event_id": row["event_id"],
            "timestamp": datetime.fromisoformat(row["timestamp"]),
            "agent_id": row["agent_id"],
            "persona": row["persona"],
            "model_used": row["model_used"],
            "input_token_count": row["input_token_count"],
            "output_token_count": row["output_token_count"],
            "governance_result": row["governance_result"],
            "hook_triggered": bool(row["hook_triggered"]),
            "session_id": row["session_id"],
            "user_hash": row["user_hash"],
            "checksum": row["checksum"],
            "metadata": metadata,
        }
    )
