"""Mirage VFS Bridge Plugin — Sovereign Machina Virtualized Filesystem Integration.

Provides the Hermes agent with transparent access to mesh-wide data sources
(Redis on Node A, S3-compatible storage) through a FUSE-mounted virtualized
filesystem on Node D.

Hook points:
  - ``on_session_start``: Optionally verifies VFS mount health.

Configuration (in cli-config.yaml under plugins.mirage_vfs):
  - mount_point: FUSE mount path (default: "/mnt/mirage")
  - redis_host: Redis host — Node A IP (default: "100.96.253.114")
  - redis_port: Redis port (default: 6379)
  - s3_endpoint: S3 endpoint (default: "http://100.96.253.114:9000")
  - s3_bucket: S3 bucket name (default: "sovereign-mirage")
  - health_check_on_start: Run health check at session start (default: true)

Tools registered:
  - ``mirage_health_check``: Verify VFS mount is active.
  - ``mirage_read_file``: Read a virtual file (transparently from Redis/S3).
  - ``mirage_list_dir``: List virtual directory contents.
  - ``mirage_write_file``: Write to a virtual file (persists to backend).
"""

from __future__ import annotations

import logging
from typing import Any

from .mirage_config import MirageVfsConfig
from .vfs_bridge import VfsBridge

logger = logging.getLogger(__name__)

PLUGIN_NAME = "mirage-vfs"
PLUGIN_VERSION = "1.0.0"


class MirageVfsPlugin:
    """Main plugin class for the Mirage VFS bridge integration.

    Manages the lifecycle of the VFS bridge and exposes hook callbacks
    and tools for Hermes plugin registration.
    """

    def __init__(self, config: dict):
        """Initialize the Mirage VFS plugin.

        Args:
            config: Plugin configuration dict from cli-config.yaml.
        """
        self.config = MirageVfsConfig.from_dict(config)
        self._bridge: VfsBridge | None = None
        self._initialized = False

    def initialize(self, plugin_api: Any) -> None:
        """Initialize the plugin with the Hermes plugin API.

        Creates the VFS bridge instance.

        Args:
            plugin_api: The Hermes plugin API context.
        """
        self._bridge = VfsBridge(self.config)
        self._initialized = True

        logger.info(
            "%s v%s initialized (mount=%s, redis=%s:%d, s3=%s/%s)",
            PLUGIN_NAME,
            PLUGIN_VERSION,
            self.config.mount_point,
            self.config.redis_host,
            self.config.redis_port,
            self.config.s3_endpoint,
            self.config.s3_bucket,
        )

    # ------------------------------------------------------------------
    # Hook callbacks
    # ------------------------------------------------------------------

    def on_session_start(self, **kwargs) -> None:
        """Hook: on_session_start — optionally verify VFS mount health.

        Performs a lightweight health check against the Mirage mount
        point at session start to surface connectivity issues early.

        Args:
            **kwargs: May include 'session_id', 'platform'.
        """
        if not self._bridge or not self.config.health_check_on_start:
            return

        try:
            result = self._bridge._handle_health_check()
            if result.get("mounted"):
                logger.debug(
                    "Mirage VFS health check passed for session %s "
                    "(%d entries visible)",
                    kwargs.get("session_id", "unknown"),
                    len(result.get("entries", [])),
                )
            else:
                logger.warning(
                    "Mirage VFS health check: mount not active at %s "
                    "(session %s)",
                    self.config.mount_point,
                    kwargs.get("session_id", "unknown"),
                )
        except Exception as exc:
            logger.warning("Mirage VFS health check error: %s", exc)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def bridge(self) -> VfsBridge | None:
        """Return the active VFS bridge instance."""
        return self._bridge


def register(ctx: Any) -> None:
    """Hermes plugin registration entry point.

    Called by the plugin loader when the plugin is discovered and enabled.

    Args:
        ctx: The Hermes plugin API context with config and registration methods.
    """
    # Extract plugin-specific config from the parent context
    plugin_config = {}
    if hasattr(ctx, "config") and isinstance(ctx.config, dict):
        plugin_config = ctx.config.get("mirage_vfs", {})
    elif hasattr(ctx, "config"):
        plugin_config = getattr(ctx.config, "mirage_vfs", {})

    plugin = MirageVfsPlugin(plugin_config)
    plugin.initialize(ctx)

    # Register VFS tools with Hermes
    if plugin.bridge:
        for tool_def in plugin.bridge.get_tool_definitions():
            ctx.register_tool(
                name=tool_def["name"],
                toolset=PLUGIN_NAME,
                schema=tool_def["inputSchema"],
                handler=tool_def["handler"],
                check_fn=lambda: True,
                emoji="💾",
            )

    # Register hook callbacks
    ctx.register_hook("on_session_start", plugin.on_session_start)

    logger.info(
        "[Mirage VFS] v%s registered — virtualized filesystem bridge active",
        PLUGIN_VERSION,
    )
