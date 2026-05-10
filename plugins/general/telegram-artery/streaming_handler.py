"""Real-time token streaming to Telegram with monospaced reasoning formatting.

Implements "Thought Visualization" — raw reasoning steps from Node D's
LLM tokens are streamed into Telegram messages as monospaced code blocks
that progressively update via editMessageText.

Streaming protocol:
  1. On first token, send a new message with monospaced reasoning prefix.
  2. Accumulate tokens into a buffer.
  3. On each flush interval, edit the message with the updated content.
  4. On stream end, finalize the message and strip the streaming cursor.

Monospaced formatting wraps reasoning steps in triple backtick code blocks.
Streaming cursor ("▉") is appended during active generation and removed at end.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, Optional

from .artery_config import ArteryConfig

logger = logging.getLogger(__name__)

# Streaming visual markers
CURSOR = " ▉"
REASONING_PREFIX = "💭 Reasoning\n"
CODE_FENCE = "```\n"
CODE_FENCE_END = "\n```"
THOUGHT_DELIMITER = "\n---\n"


@dataclass
class StreamState:
    """Tracks the state of a single streaming session."""
    chat_id: int
    message_id: Optional[int] = None
    buffer: str = ""
    reasoning_steps: Deque[str] = field(default_factory=lambda: __import__("collections").deque())
    last_edit_time: float = 0.0
    last_edit_len: int = 0
    step_count: int = 0
    is_active: bool = True
    is_finalized: bool = False


class StreamingHandler:
    """Manages real-time token streaming to Telegram.

    Hooks into the LLM token stream on Node D and progressively renders
    reasoning steps as monospaced Telegram messages.

    Usage::

        handler = StreamingHandler(config, send_message_func, edit_message_func)
        await handler.start_stream(chat_id=12345)

        # Feed tokens from the LLM
        for token in token_generator:
            await handler.feed_token(token)

        # Signal completion
        await handler.finalize_stream()
    """

    def __init__(
        self,
        config: ArteryConfig,
        send_message: Callable,
        edit_message: Callable,
    ):
        """
        Args:
            config: ArteryConfig with streaming parameters.
            send_message: Async callable(chat_id, text) -> message_id.
            edit_message: Async callable(chat_id, message_id, text) -> None.
        """
        self.config = config
        self._send_message = send_message
        self._edit_message = edit_message
        self._active_streams: Dict[int, StreamState] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start_stream(self, chat_id: int) -> None:
        """Initialize a new streaming session for a Telegram chat.

        Sends the initial message with a reasoning prefix and cursor.

        Args:
            chat_id: Telegram chat ID to stream into.
        """
        state = StreamState(chat_id=chat_id)
        self._active_streams[chat_id] = state

        initial_text = self._format_streaming_content(state)
        try:
            message_id = await self._send_message(chat_id, initial_text)
            state.message_id = message_id
            logger.info("Stream started for chat_id=%s, message_id=%s", chat_id, message_id)
        except Exception as exc:
            logger.error("Failed to start stream for chat_id=%s: %s", chat_id, exc)
            state.is_active = False
            raise

    async def feed_token(self, token: str, chat_id: int) -> None:
        """Feed a single LLM token into the active stream.

        Accumulates the token in the buffer and triggers a Telegram message
        edit when the buffer exceeds the configured chunk size or the edit
        interval has elapsed.

        Args:
            token: A single token string from the LLM.
            chat_id: Telegram chat ID for the active stream.
        """
        state = self._active_streams.get(chat_id)
        if state is None or not state.is_active:
            return

        state.buffer += token

        now = time.monotonic()
        elapsed = now - state.last_edit_time
        buffer_ready = len(state.buffer) - state.last_edit_len >= self.config.stream_chunk_size

        if buffer_ready or elapsed >= self.config.stream_interval:
            await self._flush(state)

    async def feed_tokens(self, tokens: str, chat_id: int) -> None:
        """Feed a batch of tokens. Convenience wrapper around feed_token."""
        for token in tokens:
            await self.feed_token(token, chat_id)

    async def finalize_stream(self, chat_id: int) -> None:
        """Finalize the streaming session.

        Flushes any remaining buffered content, removes the streaming cursor,
        and marks the stream as complete.

        Args:
            chat_id: Telegram chat ID for the stream to finalize.
        """
        state = self._active_streams.get(chat_id)
        if state is None:
            return

        # Final flush — removes cursor
        state.is_active = False
        await self._flush(state, final=True)

        state.is_finalized = True
        logger.info(
            "Stream finalized for chat_id=%s, %d reasoning steps",
            chat_id, state.step_count,
        )

    def get_stream_state(self, chat_id: int) -> Optional[StreamState]:
        """Return the current StreamState for a chat, if any."""
        return self._active_streams.get(chat_id)

    def end_stream(self, chat_id: int) -> None:
        """Remove the stream state for a chat (cleanup)."""
        self._active_streams.pop(chat_id, None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _flush(self, state: StreamState, final: bool = False) -> None:
        """Flush the current buffer to the Telegram message.

        Args:
            state: Active stream state.
            final: If True, remove the cursor and do final formatting.
        """
        if state.message_id is None:
            return

        text = self._format_streaming_content(state, show_cursor=not final)
        text = self._truncate_message(text)

        try:
            await self._edit_message(state.chat_id, state.message_id, text)
            state.last_edit_time = time.monotonic()
            state.last_edit_len = len(state.buffer)
        except Exception as exc:
            # Telegram edit can fail if content unchanged or rate-limited
            logger.debug("Edit failed (chat_id=%s): %s", state.chat_id, exc)

    def _format_streaming_content(
        self, state: StreamState, show_cursor: bool = True
    ) -> str:
        """Format the current buffer into a Telegram-friendly message.

        When monospace_blocks is enabled, reasoning content is wrapped in
        triple-backtick code blocks for monospaced rendering.

        Args:
            state: Active stream state.
            show_cursor: Whether to append the streaming cursor.

        Returns:
            Formatted message string.
        """
        content = state.buffer
        if not content:
            content = "..."

        if self.config.monospace_blocks:
            parts = [REASONING_PREFIX, CODE_FENCE, content]
            if show_cursor:
                parts.append(CURSOR)
            parts.append(CODE_FENCE_END)
        else:
            parts = [content]
            if show_cursor:
                parts.append(CURSOR)

        return "".join(parts)

    def _truncate_message(self, text: str) -> str:
        """Truncate message to fit within Telegram's message length limit.

        Preserves the code fence closing when truncation occurs.

        Args:
            text: Formatted message text.

        Returns:
            Truncated text that fits within max_message_length.
        """
        limit = self.config.max_message_length
        if len(text) <= limit:
            return text

        # Leave room for truncation marker and code fence close
        truncation_marker = "...[truncated]"
        if self.config.monospace_blocks:
            cutoff = limit - len(truncation_marker) - len(CODE_FENCE_END) - 1
            text = text[:cutoff] + truncation_marker + "\n" + CODE_FENCE_END
        else:
            cutoff = limit - len(truncation_marker)
            text = text[:cutoff] + truncation_marker

        return text


def format_reasoning_step(step_text: str, step_number: int) -> str:
    """Format a single reasoning step for Telegram display.

    Args:
        step_text: The raw reasoning text.
        step_number: Step sequence number.

    Returns:
        Formatted string with monospaced step marker.
    """
    marker = f"Step {step_number}:"
    return f"{marker}\n```\n{step_text}\n```"


def format_thought_block(thoughts: str) -> str:
    """Wrap a complete thought block in monospaced formatting.

    Args:
        thoughts: Full thought/reasoning text.

    Returns:
        Triple-backtick-wrapped string.
    """
    return f"{REASONING_PREFIX}```\n{thoughts}\n```"
