"""Tests for the Telegram Artery plugin.

Covers:
  - artery_config: ArteryConfig construction, validation, node resolution
  - streaming_handler: Stream lifecycle, token feeding, formatting, truncation
  - bot_coordinator: Request classification, routing, handoff lifecycle
  - __init__: Plugin registration, hook callbacks, reasoning detection

Run:
    cd sidecars/hermes-agent-nous
    python -m pytest tests/plugins/test_telegram_artery_plugin.py -v
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Repo root resolution
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO_ROOT / "plugins" / "general" / "telegram-artery"


def _import_module(name: str, path: Path, package: str = ""):
    """Import a single module from an absolute path with proper __module__."""
    mod = sys.modules.get(name)
    if mod is not None:
        return mod
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    mod.__module__ = name
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Ensure parent package exists for relative imports in __init__.py
_parent_pkg = "telegram_artery_test_pkg"
if _parent_pkg not in sys.modules:
    import types
    parent = types.ModuleType(_parent_pkg)
    parent.__path__ = [str(PLUGIN_DIR)]
    parent.__package__ = _parent_pkg
    sys.modules[_parent_pkg] = parent

# Import plugin modules (order matters: deps first)
artery_config_mod = _import_module(
    f"{_parent_pkg}.artery_config", PLUGIN_DIR / "artery_config.py"
)
streaming_handler_mod = _import_module(
    f"{_parent_pkg}.streaming_handler", PLUGIN_DIR / "streaming_handler.py"
)
bot_coordinator_mod = _import_module(
    f"{_parent_pkg}.bot_coordinator", PLUGIN_DIR / "bot_coordinator.py"
)
plugin_init_mod = _import_module(
    f"{_parent_pkg}.init", PLUGIN_DIR / "__init__.py"
)


# Shortcut aliases
ArteryConfig = artery_config_mod.ArteryConfig
MESH_NODES = artery_config_mod.MESH_NODES
StreamingHandler = streaming_handler_mod.StreamingHandler
StreamState = streaming_handler_mod.StreamState
format_thought_block = streaming_handler_mod.format_thought_block
format_reasoning_step = streaming_handler_mod.format_reasoning_step
BotCoordinator = bot_coordinator_mod.BotCoordinator
classify_request = bot_coordinator_mod.classify_request
RequestType = bot_coordinator_mod.RequestType
HandoffStatus = bot_coordinator_mod.HandoffStatus
TelegramArteryPlugin = plugin_init_mod.TelegramArteryPlugin


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def default_config():
    """Default ArteryConfig for testing."""
    return ArteryConfig(bot_token="test-token-12345")


@pytest.fixture
def mock_send():
    return AsyncMock(return_value=42)


@pytest.fixture
def mock_edit():
    return AsyncMock()


@pytest.fixture
def handler(default_config, mock_send, mock_edit):
    return StreamingHandler(default_config, mock_send, mock_edit)


# ===========================================================================
# artery_config tests
# ===========================================================================

class TestArteryConfig:
    """Tests for artery_config.ArteryConfig."""

    def test_from_dict_defaults(self):
        cfg = ArteryConfig.from_dict({})
        assert cfg.bot_token == ""
        assert cfg.primary_node == "node_d"
        assert cfg.perception_node == "node_c"
        assert cfg.stream_chunk_size == 40
        assert cfg.stream_interval == 0.3
        assert cfg.monospace_blocks is True
        assert cfg.max_message_length == 4096

    def test_from_dict_with_values(self):
        cfg = ArteryConfig.from_dict({
            "bot_token": "tok-123",
            "primary_node": "node_b",
            "stream_chunk_size": 100,
            "monospace_blocks": False,
        })
        assert cfg.bot_token == "tok-123"
        assert cfg.primary_node == "node_b"
        assert cfg.stream_chunk_size == 100
        assert cfg.monospace_blocks is False

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_ARTERY_BOT_TOKEN", "env-token")
        cfg = ArteryConfig.from_env()
        assert cfg.bot_token == "env-token"

    def test_validate_valid(self):
        cfg = ArteryConfig(bot_token="tok")
        assert cfg.validate() is None

    def test_validate_missing_token(self):
        cfg = ArteryConfig(bot_token="")
        assert "bot_token" in cfg.validate()

    def test_validate_bad_node(self):
        cfg = ArteryConfig(bot_token="tok", primary_node="node_x")
        assert "primary_node" in cfg.validate()

    def test_validate_bad_chunk_size(self):
        cfg = ArteryConfig(bot_token="tok", stream_chunk_size=0)
        assert "stream_chunk_size" in cfg.validate()

    def test_validate_bad_interval(self):
        cfg = ArteryConfig(bot_token="tok", stream_interval=0.01)
        assert "stream_interval" in cfg.validate()

    def test_get_primary_node(self, default_config):
        node = default_config.get_primary_node()
        assert node.name == "Quaternary"
        assert node.tailnet_ip == "100.120.225.12"

    def test_get_perception_node(self, default_config):
        node = default_config.get_perception_node()
        assert node.name == "Falcon"
        assert node.tailnet_ip == "100.102.109.81"

    def test_get_reasoning_url(self, default_config):
        url = default_config.get_reasoning_url()
        assert "100.120.225.12" in url
        assert "/v1" in url

    def test_get_perception_url(self, default_config):
        url = default_config.get_perception_url()
        assert "100.102.109.81" in url

    def test_mesh_nodes_completeness(self):
        assert "node_b" in MESH_NODES
        assert "node_c" in MESH_NODES
        assert "node_d" in MESH_NODES
        for key, node in MESH_NODES.items():
            assert node.tailnet_ip
            assert node.port > 0
            assert node.services


# ===========================================================================
# streaming_handler tests
# ===========================================================================

class TestStreamingHandler:
    """Tests for streaming_handler.StreamingHandler."""

    @pytest.mark.asyncio
    async def test_start_stream(self, handler, mock_send):
        await handler.start_stream(chat_id=100)
        mock_send.assert_awaited_once()
        args = mock_send.call_args
        assert args[0][0] == 100
        # Initial message should contain reasoning prefix or content
        assert "💭" in args[0][1] or "```" in args[0][1]

    @pytest.mark.asyncio
    async def test_feed_token_accumulates(self, handler, mock_send, mock_edit):
        await handler.start_stream(chat_id=200)
        mock_send.reset_mock()

        # Feed tokens but don't exceed chunk size — no edit yet
        await handler.feed_token("Hello", chat_id=200)
        # Buffer should have accumulated content
        state = handler.get_stream_state(200)
        assert state is not None
        assert "Hello" in state.buffer

    @pytest.mark.asyncio
    async def test_feed_token_triggers_edit(self, handler, mock_send, mock_edit):
        await handler.start_stream(chat_id=300)

        # Feed enough tokens to exceed chunk_size (40 chars)
        for i in range(20):
            await handler.feed_token(f"token-{i:03d} ", chat_id=300)

        # Edit should have been called
        assert mock_edit.await_count > 0

    @pytest.mark.asyncio
    async def test_finalize_stream(self, handler, mock_send, mock_edit):
        await handler.start_stream(chat_id=400)
        await handler.feed_tokens("Some reasoning content", chat_id=400)
        await handler.finalize_stream(chat_id=400)

        state = handler.get_stream_state(400)
        assert state is not None
        assert state.is_finalized is True
        assert state.is_active is False

    @pytest.mark.asyncio
    async def test_finalize_removes_cursor(self, handler, mock_send, mock_edit):
        await handler.start_stream(chat_id=500)
        await handler.feed_tokens("Content here", chat_id=500)
        await handler.finalize_stream(chat_id=500)

        # Last edit should NOT contain cursor
        last_call = mock_edit.call_args_list[-1]
        text = last_call[0][2]
        assert "▉" not in text

    @pytest.mark.asyncio
    async def test_monospace_formatting(self, handler, mock_send, mock_edit):
        await handler.start_stream(chat_id=600)

        # Initial message should use monospace blocks
        initial_text = mock_send.call_args[0][1]
        assert "```" in initial_text

    @pytest.mark.asyncio
    async def test_feed_nonexistent_stream(self, handler):
        """Feeding tokens to a non-existent stream should not raise."""
        await handler.feed_token("test", chat_id=99999)

    @pytest.mark.asyncio
    async def test_finalize_nonexistent_stream(self, handler):
        """Finalizing a non-existent stream should not raise."""
        await handler.finalize_stream(chat_id=99999)

    def test_end_stream_cleans_up(self, handler):
        handler._active_streams[700] = StreamState(chat_id=700)
        handler.end_stream(700)
        assert 700 not in handler._active_streams

    def test_get_stream_state(self, handler):
        handler._active_streams[800] = StreamState(chat_id=800)
        assert handler.get_stream_state(800) is not None
        assert handler.get_stream_state(999) is None


class TestFormatFunctions:
    """Tests for streaming_handler format helpers."""

    def test_format_reasoning_step(self):
        result = format_reasoning_step("analyzing data", 1)
        assert "Step 1:" in result
        assert "```" in result
        assert "analyzing data" in result

    def test_format_thought_block(self):
        result = format_thought_block("deep thought")
        assert "💭" in result
        assert "```" in result
        assert "deep thought" in result


class TestMessageTruncation:
    """Tests for message truncation logic."""

    def test_short_message_not_truncated(self, default_config):
        handler = StreamingHandler(default_config, AsyncMock(), AsyncMock())
        text = "short message"
        assert handler._truncate_message(text) == text

    def test_long_message_truncated(self, default_config):
        handler = StreamingHandler(default_config, AsyncMock(), AsyncMock())
        long_text = "x" * 5000
        result = handler._truncate_message(long_text)
        assert len(result) <= default_config.max_message_length
        assert "truncated" in result

    def test_truncation_preserves_code_fence(self, default_config):
        handler = StreamingHandler(default_config, AsyncMock(), AsyncMock())
        long_text = "```\n" + "x" * 5000 + "\n```"
        result = handler._truncate_message(long_text)
        assert result.endswith("```")


# ===========================================================================
# bot_coordinator tests
# ===========================================================================

class TestClassifyRequest:
    """Tests for bot_coordinator.classify_request."""

    def test_perception_image(self):
        assert classify_request("Look at this image") == RequestType.PERCEPTION

    def test_perception_voice(self):
        assert classify_request("Transcribe this audio") == RequestType.PERCEPTION

    def test_perception_visual(self):
        assert classify_request("What do you see in this photo?") == RequestType.PERCEPTION

    def test_reasoning_analyze(self):
        assert classify_request("Analyze this code") == RequestType.REASONING

    def test_reasoning_debug(self):
        assert classify_request("Debug the issue") == RequestType.REASONING

    def test_hybrid(self):
        result = classify_request("Analyze this image and explain why it matters")
        assert result == RequestType.HYBRID

    def test_empty_string(self):
        assert classify_request("") == RequestType.UNKNOWN

    def test_general_defaults_reasoning(self):
        assert classify_request("What is the weather?") == RequestType.REASONING


class TestBotCoordinator:
    """Tests for bot_coordinator.BotCoordinator."""

    @pytest.fixture
    def coordinator(self, default_config):
        mock_perception = AsyncMock(return_value="perception result")
        mock_reasoning = AsyncMock(return_value="reasoning result")
        return BotCoordinator(default_config, mock_perception, mock_reasoning)

    @pytest.mark.asyncio
    async def test_perception_routing(self, coordinator):
        result = await coordinator.handle_request(100, "Describe this image")
        assert result == "perception result"

    @pytest.mark.asyncio
    async def test_reasoning_routing(self, coordinator):
        result = await coordinator.handle_request(100, "Debug this code")
        assert result == "reasoning result"

    @pytest.mark.asyncio
    async def test_hybrid_routing(self, coordinator):
        result = await coordinator.handle_request(
            100, "Analyze this image and explain why"
        )
        # Hybrid does perception then reasoning
        assert result == "reasoning result"

    @pytest.mark.asyncio
    async def test_unknown_defaults_to_reasoning(self, coordinator):
        result = await coordinator.handle_request(100, "Hello there")
        assert result == "reasoning result"

    @pytest.mark.asyncio
    async def test_no_perception_client(self, default_config):
        coord = BotCoordinator(default_config, None, AsyncMock(return_value="r"))
        result = await coord.handle_request(100, "Describe this image")
        assert "unavailable" in result

    @pytest.mark.asyncio
    async def test_no_reasoning_client(self, default_config):
        coord = BotCoordinator(default_config, AsyncMock(return_value="p"), None)
        result = await coord.handle_request(100, "Analyze this code")
        assert "unavailable" in result

    @pytest.mark.asyncio
    async def test_timeout(self, default_config):
        async def slow_client(payload):
            await asyncio.sleep(100)
            return "never"

        default_config.coordination_timeout = 0.1
        coord = BotCoordinator(default_config, slow_client, slow_client)
        result = await coord.handle_request(100, "Analyze this code")
        assert "timed out" in result.lower()

    @pytest.mark.asyncio
    async def test_client_error(self, default_config):
        async def failing_client(payload):
            raise RuntimeError("node down")

        coord = BotCoordinator(default_config, failing_client, failing_client)
        result = await coord.handle_request(100, "Analyze this code")
        assert "error" in result.lower()

    def test_get_active_sessions(self, coordinator):
        sessions = coordinator.get_active_sessions()
        assert isinstance(sessions, list)

    def test_get_handoff_status_unknown(self, coordinator):
        assert coordinator.get_handoff_status("nonexistent") is None


class TestHandoffLifecycle:
    """Tests for handoff request lifecycle tracking."""

    @pytest.mark.asyncio
    async def test_handoff_completed(self, default_config):
        mock_client = AsyncMock(return_value="done")
        coord = BotCoordinator(default_config, mock_client, mock_client)
        await coord.handle_request(100, "Analyze code")
        # Check handoff was tracked
        sessions = coord.get_active_sessions()
        # Session should no longer be active after handling
        assert len(sessions) == 0


# ===========================================================================
# Plugin init tests
# ===========================================================================

class TestTelegramArteryPlugin:
    """Tests for __init__.TelegramArteryPlugin."""

    def test_initialization(self):
        plugin = TelegramArteryPlugin({"bot_token": "test-token"})
        mock_api = MagicMock()
        mock_api.config = {"telegram_artery": {"bot_token": "test-token"}}
        plugin.initialize(mock_api)
        assert plugin._initialized is True
        assert plugin.streaming_handler is not None
        assert plugin.coordinator is not None

    def test_initialization_without_token(self):
        plugin = TelegramArteryPlugin({})
        mock_api = MagicMock()
        mock_api.config = {}
        plugin.initialize(mock_api)
        assert plugin._initialized is True

    def test_transform_llm_output_telegram_platform(self):
        plugin = TelegramArteryPlugin({"bot_token": "t"})
        mock_api = MagicMock()
        mock_api.config = {}
        plugin.initialize(mock_api)

        result = plugin.on_transform_llm_output(
            platform="telegram",
            response_text="Step 1: Let me think about this",
        )
        # Should format reasoning content for telegram
        assert result is not None
        assert "```" in result

    def test_transform_llm_output_non_telegram(self):
        plugin = TelegramArteryPlugin({"bot_token": "t"})
        mock_api = MagicMock()
        mock_api.config = {}
        plugin.initialize(mock_api)

        result = plugin.on_transform_llm_output(
            platform="cli",
            response_text="Step 1: Let me think",
        )
        assert result is None

    def test_transform_llm_output_non_reasoning(self):
        plugin = TelegramArteryPlugin({"bot_token": "t"})
        mock_api = MagicMock()
        mock_api.config = {}
        plugin.initialize(mock_api)

        result = plugin.on_transform_llm_output(
            platform="telegram",
            response_text="Just a simple response with no reasoning markers.",
        )
        # Not reasoning content — should return None
        assert result is None

    def test_transform_llm_output_empty(self):
        plugin = TelegramArteryPlugin({"bot_token": "t"})
        mock_api = MagicMock()
        mock_api.config = {}
        plugin.initialize(mock_api)

        result = plugin.on_transform_llm_output(
            platform="telegram",
            response_text="",
        )
        assert result is None

    def test_reasoning_detection(self):
        assert TelegramArteryPlugin._is_reasoning_content("Step 1: First step")
        assert TelegramArteryPlugin._is_reasoning_content("Reasoning: let me think")
        assert not TelegramArteryPlugin._is_reasoning_content("Hello world")

    def test_session_hooks(self):
        """Session hooks should not raise."""
        plugin = TelegramArteryPlugin({"bot_token": "t"})
        mock_api = MagicMock()
        mock_api.config = {}
        plugin.initialize(mock_api)

        plugin.on_session_start(session_id="s1", platform="telegram")
        plugin.on_session_end(session_id="s1")
        plugin.on_post_tool_call(tool_name="terminal", tool_result="ok")


class TestPluginRegistration:
    """Tests for the register() entry point."""

    def test_register_calls_hooks(self):
        ctx = MagicMock()
        ctx.config = {"telegram_artery": {"bot_token": "test-token"}}

        plugin_init_mod.register(ctx)

        # Should have registered 4 hooks
        assert ctx.register_hook.call_count == 4
        hook_names = [call[0][0] for call in ctx.register_hook.call_args_list]
        assert "transform_llm_output" in hook_names
        assert "on_session_start" in hook_names
        assert "on_session_end" in hook_names
        assert "post_tool_call" in hook_names

    def test_register_with_no_config(self):
        ctx = MagicMock()
        ctx.config = {}

        # Should not raise
        plugin_init_mod.register(ctx)
        assert ctx.register_hook.call_count == 4


# ===========================================================================
# Node info helper tests
# ===========================================================================

class TestNodeInfo:
    """Tests for bot_coordinator.get_node_info."""

    def test_valid_node(self):
        info = bot_coordinator_mod.get_node_info("node_d")
        assert info is not None
        assert info["name"] == "Quaternary"
        assert info["tailnet_ip"] == "100.120.225.12"

    def test_invalid_node(self):
        info = bot_coordinator_mod.get_node_info("node_x")
        assert info is None
