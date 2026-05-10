"""n8n-MCP Bridge Plugin — Sovereign Machina n8n Workflow Integration.

Provides MCP-to-n8n bridge allowing the Hermes agent to:
  1. Check n8n instance health on Node B (Director's Forge).
  2. List available n8n workflows.
  3. Execute n8n workflows with parameters.
  4. Return workflow execution results.

Hook points:
  - ``on_session_start``: Validates n8n connectivity at session start.
  - ``on_session_end``: Cleans up any pending execution handles.

Configuration (in cli-config.yaml under plugins.n8n_mcp):
  - n8n_base_url: n8n instance URL (default: "http://100.66.173.31:5678")
  - n8n_api_key: API key for n8n REST API (or set N8N_API_KEY env var)
  - timeout_seconds: Request timeout in seconds (default: 30)
  - max_retries: Maximum retries for transient failures (default: 3)
  - verify_ssl: Verify SSL certificates (default: true)
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .n8n_config import N8nConfig
from .n8n_client import N8nClient
from .mcp_bridge import N8nMcpBridge

logger = logging.getLogger(__name__)

PLUGIN_NAME = "n8n-mcp"
PLUGIN_VERSION = "1.0.0"


class N8nMcpPlugin:
    """Main plugin class for the n8n-MCP bridge integration.

    Manages the lifecycle of the n8n REST client and MCP bridge,
    and exposes hook callbacks for Hermes plugin registration.
    """

    def __init__(self, config: dict):
        """Initialize the n8n-MCP plugin.

        Args:
            config: Plugin configuration dict from cli-config.yaml.
        """
        self.config = N8nConfig.from_dict(config)
        self._client: Optional[N8nClient] = None
        self._mcp_bridge: Optional[N8nMcpBridge] = None
        self._initialized = False

    def initialize(self, plugin_api: Any) -> None:
        """Initialize the plugin with the Hermes plugin API.

        Creates the n8n REST client and MCP bridge instances.

        Args:
            plugin_api: The Hermes plugin API context.
        """
        self._client = N8nClient(self.config)
        self._mcp_bridge = N8nMcpBridge(self._client)
        self._initialized = True

        logger.info(
            "%s v%s initialized (endpoint=%s)",
            PLUGIN_NAME,
            PLUGIN_VERSION,
            self.config.base_url,
        )

    # ------------------------------------------------------------------
    # Hook callbacks
    # ------------------------------------------------------------------

    def on_session_start(self, **kwargs) -> None:
        """Hook: on_session_start — validate n8n connectivity.

        Performs a lightweight health check against the n8n instance
        at session start to surface connectivity issues early.

        Args:
            **kwargs: May include 'session_id', 'platform'.
        """
        if not self._client:
            return

        try:
            healthy = self._client.health_check()
            if healthy:
                logger.debug(
                    "n8n health check passed for session %s",
                    kwargs.get("session_id", "unknown"),
                )
            else:
                logger.warning(
                    "n8n health check failed for session %s",
                    kwargs.get("session_id", "unknown"),
                )
        except Exception as exc:
            logger.warning("n8n health check error: %s", exc)

    def on_session_end(self, **kwargs) -> None:
        """Hook: on_session_end — cleanup.

        Args:
            **kwargs: May include 'session_id'.
        """
        session_id = kwargs.get("session_id", "unknown")
        logger.debug("Session ended: session=%s", session_id)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def client(self) -> Optional[N8nClient]:
        """Return the active n8n REST client instance."""
        return self._client

    @property
    def mcp_bridge(self) -> Optional[N8nMcpBridge]:
        """Return the active MCP bridge instance."""
        return self._mcp_bridge


def register(ctx: Any) -> None:
    """Hermes plugin registration entry point.

    Called by the plugin loader when the plugin is discovered and enabled.

    Args:
        ctx: The Hermes plugin API context with config and registration methods.
    """
    # Extract plugin-specific config from the parent context
    plugin_config = {}
    if hasattr(ctx, "config") and isinstance(ctx.config, dict):
        plugin_config = ctx.config.get("n8n_mcp", {})
    elif hasattr(ctx, "config"):
        plugin_config = getattr(ctx.config, "n8n_mcp", {})

    plugin = N8nMcpPlugin(plugin_config)
    plugin.initialize(ctx)

    # Register hook callbacks
    ctx.register_hook("on_session_start", plugin.on_session_start)
    ctx.register_hook("on_session_end", plugin.on_session_end)

    logger.info(
        "[n8n-MCP] v%s registered — n8n workflow bridge active",
        PLUGIN_VERSION,
    )
