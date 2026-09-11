"""
Pre-built MCP connector configurations for common enterprise tools.

Each connector returns a typed ConnectorConfig with all required info
for setting up an MCP server connection.
"""

from __future__ import annotations

import logging
import os
import threading
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from enterprise_claude.governance import (
    ConnectorNotFoundError,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums and models
# ---------------------------------------------------------------------------


class AuthType(str, Enum):
    """Authentication strategy required by an MCP connector.

    Attributes:
        BEARER_TOKEN: HTTP Authorization header with a Bearer token.
        BASIC_AUTH: HTTP Basic authentication (username + password).
        OAUTH2: OAuth 2.0 flow (client-credentials or auth-code).
        API_KEY: Static API key sent as a header or query parameter.
        NONE: No authentication required.
    """

    BEARER_TOKEN = "bearer_token"
    BASIC_AUTH = "basic_auth"
    OAUTH2 = "oauth2"
    API_KEY = "api_key"
    NONE = "none"


class ConnectorConfig(BaseModel):
    """Full configuration for a single MCP connector.

    Attributes:
        name: Unique connector identifier (e.g., 'github').
        display_name: Human-readable connector name.
        url: MCP server URL or npm package path.
        auth_type: Authentication strategy.
        required_env_vars: Environment variables that must be set.
        optional_env_vars: Environment variables that may improve behaviour.
        description: Short description of connector capabilities.
        documentation_url: Link to upstream documentation.
        version: Connector version string.
        tags: Searchable topic tags.
    """

    model_config = ConfigDict(strict=False)

    name: str
    display_name: str
    url: str
    auth_type: AuthType
    required_env_vars: list[str]
    optional_env_vars: list[str] = Field(default_factory=list)
    description: str
    documentation_url: str
    version: str = "latest"
    tags: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def name_lowercase(cls, v: str) -> str:
        """Connector names are normalised to lowercase."""
        return v.strip().lower()

    @field_validator("url")
    @classmethod
    def url_not_empty(cls, v: str) -> str:
        """The URL / npm package path must not be blank."""
        if not v.strip():
            raise ValueError("ConnectorConfig.url must not be empty")
        return v.strip()


# ---------------------------------------------------------------------------
# Built-in connector catalogue
# ---------------------------------------------------------------------------

_BUILTIN_CONNECTORS: dict[str, ConnectorConfig] = {
    "github": ConnectorConfig(
        name="github",
        display_name="GitHub",
        url="@modelcontextprotocol/server-github",
        auth_type=AuthType.BEARER_TOKEN,
        required_env_vars=["GITHUB_TOKEN"],
        optional_env_vars=["GITHUB_API_URL"],
        description="Access GitHub repositories, issues, PRs, and code search",
        documentation_url="https://github.com/modelcontextprotocol/servers/tree/main/src/github",
        tags=["code", "devops", "scm"],
    ),
    "jira": ConnectorConfig(
        name="jira",
        display_name="Atlassian Jira",
        url="@modelcontextprotocol/server-atlassian",
        auth_type=AuthType.BASIC_AUTH,
        required_env_vars=["JIRA_URL", "JIRA_TOKEN", "JIRA_EMAIL"],
        description="Manage Jira issues, projects, sprints, and boards",
        documentation_url="https://github.com/sooperset/mcp-atlassian",
        tags=["project-management", "agile", "ticketing"],
    ),
    "slack": ConnectorConfig(
        name="slack",
        display_name="Slack",
        url="@modelcontextprotocol/server-slack",
        auth_type=AuthType.BEARER_TOKEN,
        required_env_vars=["SLACK_BOT_TOKEN"],
        optional_env_vars=["SLACK_TEAM_ID"],
        description="Read Slack channels, send messages, search conversations",
        documentation_url="https://github.com/modelcontextprotocol/servers/tree/main/src/slack",
        tags=["communication", "messaging"],
    ),
    "confluence": ConnectorConfig(
        name="confluence",
        display_name="Confluence",
        url="@modelcontextprotocol/server-atlassian",
        auth_type=AuthType.BASIC_AUTH,
        required_env_vars=["CONFLUENCE_URL", "CONFLUENCE_TOKEN"],
        optional_env_vars=["CONFLUENCE_EMAIL"],
        description="Search and retrieve Confluence pages and spaces",
        documentation_url="https://github.com/sooperset/mcp-atlassian",
        tags=["knowledge-base", "wiki", "documentation"],
    ),
    "sharepoint": ConnectorConfig(
        name="sharepoint",
        display_name="Microsoft SharePoint",
        url="@modelcontextprotocol/server-sharepoint",
        auth_type=AuthType.OAUTH2,
        required_env_vars=[
            "SHAREPOINT_TENANT_ID",
            "SHAREPOINT_CLIENT_ID",
            "SHAREPOINT_CLIENT_SECRET",
        ],
        optional_env_vars=["SHAREPOINT_SITE_URL"],
        description="Access SharePoint documents, lists, and sites",
        documentation_url="https://github.com/microsoft/mcp-for-sharepoint",
        tags=["document-management", "microsoft365"],
    ),
    "postgres": ConnectorConfig(
        name="postgres",
        display_name="PostgreSQL",
        url="@modelcontextprotocol/server-postgres",
        auth_type=AuthType.NONE,
        required_env_vars=["POSTGRES_CONNECTION_STRING"],
        optional_env_vars=["POSTGRES_SCHEMA"],
        description="Query and inspect PostgreSQL databases",
        documentation_url="https://github.com/modelcontextprotocol/servers/tree/main/src/postgres",
        tags=["database", "sql", "data"],
    ),
    "filesystem": ConnectorConfig(
        name="filesystem",
        display_name="Local Filesystem",
        url="@modelcontextprotocol/server-filesystem",
        auth_type=AuthType.NONE,
        required_env_vars=["MCP_FS_ALLOWED_DIRS"],
        optional_env_vars=[],
        description="Read and write files within approved directories",
        documentation_url="https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
        tags=["filesystem", "local"],
    ),
    "servicenow": ConnectorConfig(
        name="servicenow",
        display_name="ServiceNow",
        url="@modelcontextprotocol/server-servicenow",
        auth_type=AuthType.BASIC_AUTH,
        required_env_vars=["SERVICENOW_INSTANCE_URL", "SERVICENOW_USERNAME", "SERVICENOW_PASSWORD"],
        optional_env_vars=["SERVICENOW_CLIENT_ID", "SERVICENOW_CLIENT_SECRET"],
        description="Access ServiceNow incidents, changes, and CMDB records",
        documentation_url="https://github.com/modelcontextprotocol/servers",
        tags=["itsm", "ticketing", "operations"],
    ),
    "salesforce": ConnectorConfig(
        name="salesforce",
        display_name="Salesforce",
        url="@modelcontextprotocol/server-salesforce",
        auth_type=AuthType.OAUTH2,
        required_env_vars=[
            "SALESFORCE_CLIENT_ID",
            "SALESFORCE_CLIENT_SECRET",
            "SALESFORCE_USERNAME",
            "SALESFORCE_PASSWORD",
        ],
        optional_env_vars=["SALESFORCE_INSTANCE_URL"],
        description="Query Salesforce CRM objects, accounts, and opportunities",
        documentation_url="https://github.com/modelcontextprotocol/servers",
        tags=["crm", "sales", "salesforce"],
    ),
    "teams": ConnectorConfig(
        name="teams",
        display_name="Microsoft Teams",
        url="@modelcontextprotocol/server-teams",
        auth_type=AuthType.OAUTH2,
        required_env_vars=[
            "TEAMS_TENANT_ID",
            "TEAMS_CLIENT_ID",
            "TEAMS_CLIENT_SECRET",
        ],
        optional_env_vars=["TEAMS_TEAM_ID"],
        description="Send messages, read channels, and manage Teams conversations",
        documentation_url="https://github.com/microsoft/mcp-for-teams",
        tags=["communication", "messaging", "microsoft365"],
    ),
}


# ---------------------------------------------------------------------------
# MCPConnectorRegistry — thread-safe singleton
# ---------------------------------------------------------------------------


class MCPConnectorRegistry:
    """Registry for custom MCP connectors.

    Thread-safe singleton.  Built-in connectors are always available.
    Custom connectors registered via ``register()`` take precedence over
    built-ins with the same name.

    Typical usage::

        registry = MCPConnectorRegistry()
        registry.register("my-tool", ConnectorConfig(...))
        config = registry.get("my-tool")
    """

    _instance: MCPConnectorRegistry | None = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> MCPConnectorRegistry:  # noqa: PYI034
        """Return the singleton instance, creating it on first call."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:  # double-checked locking
                    instance = super().__new__(cls)
                    instance._custom = {}
                    instance._registry_logger = logging.getLogger(
                        "enterprise_claude.mcp_registry"
                    )
                    cls._instance = instance
        return cls._instance

    # Declare instance attributes for type checkers (populated in __new__)
    _custom: dict[str, ConnectorConfig]
    _registry_logger: logging.Logger

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register(self, name: str, config: ConnectorConfig) -> None:
        """Register a custom connector, overwriting any existing entry with the same name.

        Args:
            name: Unique identifier for the connector.
            config: Full ConnectorConfig for the connector.
        """
        normalised = name.strip().lower()
        with self._lock:
            self._custom[normalised] = config
        self._registry_logger.info("Registered custom connector '%s'", normalised)

    def unregister(self, name: str) -> None:
        """Remove a custom connector from the registry.

        This only affects custom connectors — built-in connectors cannot be
        removed via this method.

        Args:
            name: Name of the custom connector to remove.

        Raises:
            ConnectorNotFoundError: When no custom connector with that name exists.
        """
        normalised = name.strip().lower()
        with self._lock:
            if normalised not in self._custom:
                raise ConnectorNotFoundError(
                    f"Custom connector '{normalised}' not found",
                    connector_name=normalised,
                )
            del self._custom[normalised]
        self._registry_logger.info("Unregistered custom connector '%s'", normalised)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, name: str) -> ConnectorConfig:
        """Return a connector by name, with custom connectors taking precedence.

        Args:
            name: Connector identifier (case-insensitive).

        Returns:
            ConnectorConfig for the requested connector.

        Raises:
            ConnectorNotFoundError: When no connector with that name is known.
        """
        normalised = name.strip().lower()
        with self._lock:
            if normalised in self._custom:
                return self._custom[normalised]
        builtin = _BUILTIN_CONNECTORS.get(normalised)
        if builtin is None:
            available = sorted(self.list_all().keys())
            raise ConnectorNotFoundError(
                f"Connector '{normalised}' not found. Available: {available}",
                connector_name=normalised,
            )
        return builtin

    def list_all(self) -> dict[str, ConnectorConfig]:
        """Return all connectors — built-ins merged with custom (custom wins on collision).

        Returns:
            Dictionary mapping connector name → ConnectorConfig.
        """
        with self._lock:
            merged: dict[str, ConnectorConfig] = {**_BUILTIN_CONNECTORS, **self._custom}
        return merged

    # ------------------------------------------------------------------
    # Testing / reset
    # ------------------------------------------------------------------

    def _reset(self) -> None:
        """Clear all custom connectors.  Intended for use in tests only."""
        with self._lock:
            self._custom.clear()


# ---------------------------------------------------------------------------
# Module-level singleton and convenience functions
# ---------------------------------------------------------------------------

registry: MCPConnectorRegistry = MCPConnectorRegistry()
"""Module-level singleton registry.  Import and use directly for simple cases."""


def get_connector(name: str) -> ConnectorConfig:
    """Return a connector config by name, checking the registry then built-ins.

    Args:
        name: Connector identifier (case-insensitive).

    Returns:
        ConnectorConfig for the requested connector.

    Raises:
        ConnectorNotFoundError: When no connector with that name is known.
    """
    return registry.get(name)


def list_connectors() -> list[dict[str, Any]]:
    """List all available connectors (built-in and registered).

    Each entry includes whether required environment variables are currently
    set, so callers can surface misconfiguration early.

    Returns:
        List of dicts with keys:
        - ``name``: connector identifier
        - ``display_name``: human-readable name
        - ``description``: short capability summary
        - ``tags``: list of topic tags
        - ``auth_type``: authentication strategy
        - ``env_configured``: True when all required env vars are present
    """
    connectors = registry.list_all()
    result: list[dict[str, Any]] = []
    for name, cfg in sorted(connectors.items()):
        env_check = validate_connector(name)
        env_configured = all(env_check.values())
        result.append(
            {
                "name": cfg.name,
                "display_name": cfg.display_name,
                "description": cfg.description,
                "tags": cfg.tags,
                "auth_type": cfg.auth_type.value,
                "env_configured": env_configured,
            }
        )
    return result


def validate_connector(name: str) -> dict[str, bool]:
    """Check that all required environment variables for a connector are set.

    Args:
        name: Connector identifier (case-insensitive).

    Returns:
        Dict mapping each required env var name to True/False (True = set and
        non-empty).

    Raises:
        ConnectorNotFoundError: When no connector with that name is known.
    """
    cfg = registry.get(name)
    return {
        var: bool(os.environ.get(var, "").strip())
        for var in cfg.required_env_vars
    }
