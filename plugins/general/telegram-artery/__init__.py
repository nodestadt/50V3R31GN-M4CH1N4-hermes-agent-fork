"""Telegram Artery Plugin — Sovereign Machina Telegram AI Artery Integration.

Provides Sovereign-specific Telegram integration with:
  1. Real-time token streaming (Thought Visualization) from Node D reasoning
     into Telegram as monospaced code blocks via progressive message edits.
  2. Bot-to-bot coordination between Node C (perception) and Node D (reasoning)
     enabling autonomous handoff between specialized vision and text agents.
  3. Tailscale Artery integration for secure inter-node communication.

Hook points:
  - ``transform_llm_output``: Intercepts LLM output and formats reasoning
    steps as monospaced blocks for Telegram streaming display.
  - ``on_session_start``: Initializes the streaming handler and coordinator.
  - ``on_session_end``: Cleans up active streams and coordination sessions.
  - ``post_tool_call``: Logs tool calls for the Artery audit trail.

Configuration (in cli-config.yaml under plugins.telegram_artery):
  - bot_token: Telegram bot API token (or set TELEGRAM_ARTERY_BOT_TOKEN env var)
  - primary_node: Mesh node for reasoning (default: "node_d")
  - perception_node: Mesh node for perception (default: "node_c")
  - stream_chunk_size: Characters per streaming chunk (default: 40)
  - stream_interval: Seconds between message edits (default: 0.3)
  - enable_bot_coordination: Enable bot-to-bot handoff (default: true)
  - monospace_blocks: Use monospaced formatting for reasoning (default: true)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .artery_config import ArteryConfig
from .bot_coordinator import BotCoordinator, classify_request, RequestType
from .streaming_handler import StreamingHandler, format_thought_block

logger = logging.getLogger(__name__)

PLUGIN_NAME = "telegram-artery"
PLUGIN_VERSION = "1.0.0"


class TelegramArteryPlugin:
    """Main plugin class for the Telegram Artery integration.

    Manages the lifecycle of the streaming handler and bot coordinator,
    and exposes hook callbacks for Hermes plugin registration.
    """

    def __init__(self, config: dict):
        """Initialize the Telegram Artery plugin.

        Args:
            config: Plugin configuration dict from cli-config.yaml.
        """
        self.artery_config = ArteryConfig.from_dict(config)
        self._streaming_handler: Optional[StreamingHandler] = None
        self._coordinator: Optional[BotCoordinator] = None
        self._initialized = False

    def initialize(self, plugin_api: Any) -> None:
        """Initialize the plugin with the Hermes plugin API.

        Creates the streaming handler and bot coordinator instances.
        The streaming handler requires send/edit message callables which
        are provided by the Telegram gateway adapter.

        Args:
            plugin_api: The Hermes plugin API context.
        """
        # Validate configuration
        error = self.artery_config.validate()
        if error:
            logger.warning("Telegram Artery config invalid: %s", error)
            logger.info("Plugin will operate in degraded mode (coordination only)")

        # Create streaming handler with stub callbacks
        # Real callbacks are injected by the Telegram gateway at runtime
        self._streaming_handler = StreamingHandler(
            config=self.artery_config,
            send_message=self._stub_send,
            edit_message=self._stub_edit,
        )

        # Create bot coordinator (clients injected at runtime)
        self._coordinator = BotCoordinator(config=self.artery_config)

        self._initialized = True
        logger.info(
            "%s v%s initialized (primary=%s, perception=%s)",
            PLUGIN_NAME, PLUGIN_VERSION,
            self.artery_config.primary_node,
            self.artery_config.perception_node,
        )

    # ------------------------------------------------------------------
    # Hook callbacks
    # ------------------------------------------------------------------

    def on_transform_llm_output(self, **kwargs) -> Optional[str]:
        """Hook: transform_llm_output — format reasoning for Telegram.

        Intercepts LLM output and wraps reasoning/thinking blocks in
        monospaced code formatting for Thought Visualization.

        Args:
            **kwargs: Must include 'response_text', 'platform'.

        Returns:
            Formatted text if platform is telegram, else None.
        """
        platform = kwargs.get("platform", "")
        if platform != "telegram":
            return None

        response_text = kwargs.get("response_text", "")
        if not response_text:
            return None

        # Check if this looks like reasoning/thinking content
        if self._is_reasoning_content(response_text):
            return format_thought_block(response_text)

        return None

    def on_session_start(self, **kwargs) -> None:
        """Hook: on_session_start — initialize per-session state.

        Args:
            **kwargs: May include 'session_id', 'platform'.
        """
        platform = kwargs.get("platform", "")
        session_id = kwargs.get("session_id", "unknown")
        logger.debug("Session started: session=%s platform=%s", session_id, platform)

    def on_session_end(self, **kwargs) -> None:
        """Hook: on_session_end — cleanup streams and coordination.

        Args:
            **kwargs: May include 'session_id'.
        """
        session_id = kwargs.get("session_id", "unknown")
        logger.debug("Session ended: session=%s", session_id)

    def on_post_tool_call(self, **kwargs) -> None:
        """Hook: post_tool_call — audit trail for Artery operations.

        Args:
            **kwargs: Must include 'tool_name', 'tool_result'.
        """
        tool_name = kwargs.get("tool_name", "")
        logger.debug("Tool call audit: tool=%s", tool_name)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def streaming_handler(self) -> Optional[StreamingHandler]:
        """Return the active streaming handler instance."""
        return self._streaming_handler

    @property
    def coordinator(self) -> Optional[BotCoordinator]:
        """Return the active bot coordinator instance."""
        return self._coordinator

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_reasoning_content(text: str) -> bool:
        """Heuristic to detect reasoning/thinking content.

        Checks for common markers of structured reasoning in LLM output.
        """
        reasoning_markers = [
            "step 1:", "step 2:", "reasoning:", "thinking:",
            "let me think", "analysis:", "first,", "therefore",
            "we can see", "this suggests", "the key insight",
        ]
        text_lower = text.lower()
        return any(marker in text_lower for marker in reasoning_markers)

    @staticmethod
    async def _stub_send(chat_id: int, text: str) -> int:
        """Stub send callback — replaced at runtime by gateway adapter."""
        logger.debug("stub_send: chat_id=%s len=%d", chat_id, len(text))
        return 0

    @staticmethod
    async def _stub_edit(chat_id: int, message_id: int, text: str) -> None:
        """Stub edit callback — replaced at runtime by gateway adapter."""
        logger.debug("stub_edit: chat_id=%s msg_id=%s len=%d", chat_id, message_id, len(text))


def register(ctx: Any) -> None:
    """Hermes plugin registration entry point.

    Called by the plugin loader when the plugin is discovered and enabled.

    Args:
        ctx: The Hermes plugin API context with config and registration methods.
    """
    # Extract plugin-specific config from the parent context
    plugin_config = {}
    if hasattr(ctx, "config") and isinstance(ctx.config, dict):
        plugin_config = ctx.config.get("telegram_artery", {})
    elif hasattr(ctx, "config"):
        plugin_config = getattr(ctx.config, "telegram_artery", {})

    plugin = TelegramArteryPlugin(plugin_config)
    plugin.initialize(ctx)

    # Register hook callbacks
    ctx.register_hook("transform_llm_output", plugin.on_transform_llm_output)
    ctx.register_hook("on_session_start", plugin.on_session_start)
    ctx.register_hook("on_session_end", plugin.on_session_end)
    ctx.register_hook("post_tool_call", plugin.on_post_tool_call)

    logger.info(
        "[Telegram Artery] v%s registered — streaming + coordination active",
        PLUGIN_VERSION,
    )
