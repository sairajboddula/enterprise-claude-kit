"""
MCP server entry point for enterprise-claude-kit.

Exposes the connector registry, governance metadata, and connector
validation as MCP tools — consumable by any MCP-compatible client
(Claude Desktop, Cursor, Claude Code, etc.).

Run via::

    python -m enterprise_claude.mcp_server

Or (after ``pip install enterprise-claude-kit[mcp-server]``)::

    ecl-mcp

The server communicates over stdio using the Model Context Protocol.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy import guard — mcp is an optional dependency
# ---------------------------------------------------------------------------

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp import types as mcp_types

    _MCP_AVAILABLE = True
except ImportError:  # pragma: no cover
    _MCP_AVAILABLE = False
    Server = None  # type: ignore[assignment,misc]
    stdio_server = None  # type: ignore[assignment]
    mcp_types = None  # type: ignore[assignment]

from enterprise_claude.mcp_connectors import (
    get_connector,
    list_connectors,
    validate_connector,
    registry,
)

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_enterprise_connectors",
        "description": (
            "List all available enterprise connectors pre-configured in "
            "enterprise-claude-kit. Returns connector name, display name, "
            "description, tags, auth type, and whether required environment "
            "variables are currently set."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_connector_config",
        "description": (
            "Get the full configuration for a specific enterprise connector, "
            "including required/optional environment variables, MCP server URL, "
            "auth type, documentation link, and tags."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "connector_name": {
                    "type": "string",
                    "description": (
                        "Connector identifier (case-insensitive). "
                        "Examples: github, jira, slack, confluence, "
                        "sharepoint, postgres, servicenow, salesforce, "
                        "teams, filesystem"
                    ),
                }
            },
            "required": ["connector_name"],
        },
    },
    {
        "name": "validate_connector_env",
        "description": (
            "Check whether all required environment variables for a given "
            "connector are set in the current environment. "
            "Returns a dict mapping each required env var to True/False."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "connector_name": {
                    "type": "string",
                    "description": "Connector identifier (case-insensitive).",
                }
            },
            "required": ["connector_name"],
        },
    },
    {
        "name": "get_governance_features",
        "description": (
            "Return a summary of the governance features available in "
            "enterprise-claude-kit: PII filtering, audit logging, token "
            "monitoring, wave-based rollout, and GxP compliance mode."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]

# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def _handle_list_enterprise_connectors() -> str:
    """Return all connectors as a JSON array."""
    connectors = list_connectors()
    return json.dumps(connectors, indent=2)


def _handle_get_connector_config(connector_name: str) -> str:
    """Return full ConnectorConfig for a single connector."""
    cfg = get_connector(connector_name)
    return json.dumps(cfg.model_dump(), indent=2)


def _handle_validate_connector_env(connector_name: str) -> str:
    """Return env-var presence dict for a connector."""
    result = validate_connector(connector_name)
    all_ok = all(result.values())
    output = {
        "connector": connector_name,
        "all_configured": all_ok,
        "env_vars": result,
    }
    if not all_ok:
        missing = [k for k, v in result.items() if not v]
        output["missing"] = missing
    return json.dumps(output, indent=2)


def _handle_get_governance_features() -> str:
    """Return static governance feature catalogue."""
    features = {
        "library": "enterprise-claude-kit",
        "version": "0.1.1",
        "pypi": "https://pypi.org/project/enterprise-claude-kit",
        "github": "https://github.com/sairajboddula/enterprise-claude-kit",
        "features": {
            "GovernanceLayer": {
                "description": (
                    "Pre- and post-response policy engine. "
                    "Detects PII (email, SSN, phone, credit card, IPv4) using "
                    "compiled regex — deterministic and auditable by regulators."
                ),
                "capabilities": [
                    "pii_filter (blocks API call if PII detected in prompt)",
                    "blocked_keywords (raises GovernanceViolation)",
                    "max_prompt_length guard",
                    "gxp_mode (GxP citation check on responses)",
                    "pre_hooks / post_hooks (sync or async)",
                ],
                "raises": "GovernanceViolation",
            },
            "TokenMonitor": {
                "description": (
                    "SQLite-backed cost accounting and daily budget enforcement. "
                    "Budget state survives process restarts."
                ),
                "capabilities": [
                    "daily_budget_usd with hard stop",
                    "alert_threshold_pct with async callback",
                    "cost breakdown by model and persona",
                    "persistent SQLite storage (monitor.db)",
                ],
                "raises": "BudgetExceededError",
            },
            "AuditLogger": {
                "description": (
                    "Append-only audit log with SHA-256 tamper-evident checksums. "
                    "Satisfies 21 CFR Part 11, SOC 2, and HIPAA audit trail requirements."
                ),
                "capabilities": [
                    "Append-only (no UPDATE or DELETE)",
                    "SHA-256 per-event checksum",
                    "User identity hashed (SHA-256(user_id))",
                    "verify_checksums() — detect tampering",
                    "GxP citation validation",
                    "CSV/JSON export",
                ],
            },
            "AdoptionTracker": {
                "description": (
                    "Wave-gated rollout state machine. "
                    "Wave 2 is BLOCKED in code until Wave 1 hits threshold."
                ),
                "capabilities": [
                    "create_wave() with target_count and gate_threshold_pct",
                    "WaveGateError if gate not met (not just a process guideline)",
                    "Log-scale literacy scoring (0-100)",
                    "SQLite persistence",
                ],
                "raises": "WaveGateError",
            },
            "MCPConnectorRegistry": {
                "description": "10 pre-built enterprise connector configurations.",
                "connectors": [
                    c["name"] for c in list_connectors()
                ],
            },
        },
    }
    return json.dumps(features, indent=2)


# ---------------------------------------------------------------------------
# Server bootstrap
# ---------------------------------------------------------------------------


async def _run_server() -> None:
    """Bootstrap and run the MCP stdio server."""
    if not _MCP_AVAILABLE:
        print(
            "ERROR: 'mcp' package not installed.\n"
            "Install it with: pip install 'enterprise-claude-kit[mcp-server]'",
            file=sys.stderr,
        )
        sys.exit(1)

    server: Server = Server("enterprise-claude-kit")

    @server.list_tools()  # type: ignore[misc]
    async def list_tools() -> list[mcp_types.Tool]:  # type: ignore[valid-type]
        return [
            mcp_types.Tool(
                name=t["name"],
                description=t["description"],
                inputSchema=t["inputSchema"],
            )
            for t in _TOOLS
        ]

    @server.call_tool()  # type: ignore[misc]
    async def call_tool(
        name: str, arguments: dict[str, Any]
    ) -> list[mcp_types.TextContent]:  # type: ignore[valid-type]
        try:
            if name == "list_enterprise_connectors":
                text = _handle_list_enterprise_connectors()
            elif name == "get_connector_config":
                text = _handle_get_connector_config(arguments["connector_name"])
            elif name == "validate_connector_env":
                text = _handle_validate_connector_env(arguments["connector_name"])
            elif name == "get_governance_features":
                text = _handle_get_governance_features()
            else:
                text = json.dumps({"error": f"Unknown tool: {name}"})
        except Exception as exc:  # pragma: no cover
            text = json.dumps({"error": str(exc)})

        return [mcp_types.TextContent(type="text", text=text)]

    async with stdio_server() as (read_stream, write_stream):  # type: ignore[misc]
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    """CLI entry point — called by the ``ecl-mcp`` script."""
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    asyncio.run(_run_server())


if __name__ == "__main__":
    main()
