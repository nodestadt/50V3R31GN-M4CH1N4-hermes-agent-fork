"""Bot-to-bot coordination between Sovereign mesh nodes.

Enables autonomous handoff between specialized agents across mesh nodes:
  - Node C (Perception): Vision analysis, image understanding, ASR
  - Node D (Reasoning): Deep reasoning, code generation, analysis

Coordination protocol:
  1. A message arrives on the Sovereign Proxy Telegram bot.
  2. The coordinator classifies the request (perception vs reasoning).
  3. If perception: hand off to Node C's perception model.
  4. If reasoning: route to Node D's reasoning model.
  5. Complex requests may chain both (perceive → reason → respond).

The "Guest Bot" feature allows Node C and Node D models to appear in
the same Telegram thread, creating a seamless multi-agent experience.
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .artery_config import ArteryConfig, MESH_NODES

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RequestType(enum.Enum):
    """Classification of an incoming request."""
    PERCEPTION = "perception"
    REASONING = "reasoning"
    HYBRID = "hybrid"
    UNKNOWN = "unknown"


class HandoffStatus(enum.Enum):
    """Status of a bot-to-bot handoff."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class HandoffRequest:
    """A single handoff request between mesh nodes."""
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    request_type: RequestType = RequestType.UNKNOWN
    source_node: str = ""
    target_node: str = ""
    source_chat_id: int = 0
    source_message_id: int = 0
    payload: str = ""
    status: HandoffStatus = HandoffStatus.PENDING
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    result: Optional[str] = None
    error: Optional[str] = None


@dataclass
class CoordinationSession:
    """Tracks a multi-step coordination session across nodes."""
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    chat_id: int = 0
    steps: List[HandoffRequest] = field(default_factory=list)
    is_active: bool = True
    created_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Request classifier
# ---------------------------------------------------------------------------

# Keywords that suggest perception (vision/image/voice) tasks
_PERCEPTION_KEYWORDS = frozenset({
    "image", "photo", "picture", "see", "look", "visual", "vision",
    "screenshot", "camera", "ocr", "recognize", "face", "object",
    "transcribe", "audio", "voice", "listen", "hear", "speech",
    "asr", "stt", "waveform",
})

# Keywords that suggest deep reasoning tasks
_REASONING_KEYWORDS = frozenset({
    "analyze", "think", "reason", "explain", "why", "how",
    "code", "debug", "fix", "implement", "design", "architect",
    "compare", "evaluate", "synthesize", "derive", "prove",
    "calculate", "compute", "solve",
})


def classify_request(text: str) -> RequestType:
    """Classify an incoming message as perception, reasoning, or hybrid.

    Uses keyword matching against known perception and reasoning cues.

    Args:
        text: The incoming message text.

    Returns:
        RequestType indicating the dominant request category.
    """
    if not text:
        return RequestType.UNKNOWN

    text_lower = text.lower()
    words = set(text_lower.split())

    has_perception = bool(words & _PERCEPTION_KEYWORDS)
    has_reasoning = bool(words & _REASONING_KEYWORDS)

    if has_perception and has_reasoning:
        return RequestType.HYBRID
    if has_perception:
        return RequestType.PERCEPTION
    if has_reasoning:
        return RequestType.REASONING
    return RequestType.REASONING  # Default to reasoning for general queries


# ---------------------------------------------------------------------------
# Bot Coordinator
# ---------------------------------------------------------------------------

class BotCoordinator:
    """Coordinates bot-to-bot handoffs across Sovereign mesh nodes.

    Routes requests between Node C (perception) and Node D (reasoning)
    based on request classification. Supports:
    - Single-node routing (perception OR reasoning)
    - Hybrid routing (perception → reasoning pipeline)
    - Autonomous handoff with timeout handling

    Usage::

        coordinator = BotCoordinator(config, perception_client, reasoning_client)
        result = await coordinator.handle_request(chat_id, message_text)
    """

    def __init__(
        self,
        config: ArteryConfig,
        perception_client: Optional[Callable] = None,
        reasoning_client: Optional[Callable] = None,
    ):
        """
        Args:
            config: ArteryConfig with mesh node addresses.
            perception_client: Async callable(payload) -> str for Node C.
            reasoning_client: Async callable(payload) -> str for Node D.
        """
        self.config = config
        self._perception_client = perception_client
        self._reasoning_client = reasoning_client
        self._sessions: Dict[str, CoordinationSession] = {}
        self._active_handoffs: Dict[str, HandoffRequest] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def handle_request(
        self,
        chat_id: int,
        message_text: str,
        message_id: int = 0,
    ) -> str:
        """Handle an incoming Telegram message via mesh coordination.

        Classifies the request, routes to the appropriate node(s),
        and returns the response text.

        Args:
            chat_id: Telegram chat ID.
            message_text: The incoming message text.
            message_id: Telegram message ID for reference.

        Returns:
            The response text from the appropriate mesh node.

        Raises:
            RuntimeError: If no client is available for the target node.
        """
        request_type = classify_request(message_text)
        logger.info(
            "Handling request chat_id=%s type=%s len=%d",
            chat_id, request_type.value, len(message_text),
        )

        session = CoordinationSession(chat_id=chat_id)
        self._sessions[session.session_id] = session

        try:
            if request_type == RequestType.PERCEPTION:
                result = await self._route_to_perception(
                    session, chat_id, message_text, message_id
                )
            elif request_type == RequestType.REASONING:
                result = await self._route_to_reasoning(
                    session, chat_id, message_text, message_id
                )
            elif request_type == RequestType.HYBRID:
                result = await self._route_hybrid(
                    session, chat_id, message_text, message_id
                )
            else:
                result = await self._route_to_reasoning(
                    session, chat_id, message_text, message_id
                )
        except asyncio.TimeoutError:
            result = "⏱ Request timed out during mesh coordination."
            logger.warning("Timeout for chat_id=%s session=%s", chat_id, session.session_id)
        except Exception as exc:
            result = f"❌ Coordination error: {exc}"
            logger.error("Error for chat_id=%s: %s", chat_id, exc)
        finally:
            session.is_active = False
            # Evict completed session to prevent unbounded growth
            self._sessions.pop(session_id, None)

        return result

    def get_active_sessions(self) -> List[CoordinationSession]:
        """Return all currently active coordination sessions."""
        return [s for s in self._sessions.values() if s.is_active]

    def get_handoff_status(self, request_id: str) -> Optional[HandoffRequest]:
        """Return the status of a specific handoff request."""
        return self._active_handoffs.get(request_id)

    # ------------------------------------------------------------------
    # Routing methods
    # ------------------------------------------------------------------

    async def _route_to_perception(
        self,
        session: CoordinationSession,
        chat_id: int,
        text: str,
        message_id: int,
    ) -> str:
        """Route request to Node C (Perception)."""
        handoff = self._create_handoff(
            session=session,
            request_type=RequestType.PERCEPTION,
            source_node="node_d",
            target_node="node_c",
            chat_id=chat_id,
            message_id=message_id,
            payload=text,
        )

        if self._perception_client is None:
            handoff.status = HandoffStatus.FAILED
            handoff.error = "No perception client configured"
            return "⚠ Perception node unavailable."

        return await self._execute_handoff(handoff, self._perception_client)

    async def _route_to_reasoning(
        self,
        session: CoordinationSession,
        chat_id: int,
        text: str,
        message_id: int,
    ) -> str:
        """Route request to Node D (Reasoning)."""
        handoff = self._create_handoff(
            session=session,
            request_type=RequestType.REASONING,
            source_node="node_b",
            target_node="node_d",
            chat_id=chat_id,
            message_id=message_id,
            payload=text,
        )

        if self._reasoning_client is None:
            handoff.status = HandoffStatus.FAILED
            handoff.error = "No reasoning client configured"
            return "⚠ Reasoning node unavailable."

        return await self._execute_handoff(handoff, self._reasoning_client)

    async def _route_hybrid(
        self,
        session: CoordinationSession,
        chat_id: int,
        text: str,
        message_id: int,
    ) -> str:
        """Route a hybrid request through perception then reasoning.

        Step 1: Send to Node C for perception analysis.
        Step 2: Feed perception result to Node D for reasoning synthesis.
        """
        # Step 1: Perception
        perception_result = await self._route_to_perception(
            session, chat_id, text, message_id
        )

        # Step 2: Augment the original request with perception data
        augmented_payload = (
            f"Original request: {text}\n\n"
            f"Perception analysis (Node C):\n{perception_result}\n\n"
            f"Provide a comprehensive response based on the above."
        )

        reasoning_result = await self._route_to_reasoning(
            session, chat_id, augmented_payload, message_id
        )

        return reasoning_result

    # ------------------------------------------------------------------
    # Handoff execution
    # ------------------------------------------------------------------

    async def _execute_handoff(
        self,
        handoff: HandoffRequest,
        client: Callable,
    ) -> str:
        """Execute a single handoff to a mesh node client.

        Args:
            handoff: The handoff request to execute.
            client: Async callable that processes the request.

        Returns:
            The response text from the target node.
        """
        handoff.status = HandoffStatus.IN_PROGRESS
        self._active_handoffs[handoff.request_id] = handoff

        try:
            result = await asyncio.wait_for(
                client(handoff.payload),
                timeout=self.config.coordination_timeout,
            )
            handoff.status = HandoffStatus.COMPLETED
            handoff.result = result
            handoff.completed_at = time.time()
            return result
        except asyncio.TimeoutError:
            handoff.status = HandoffStatus.TIMED_OUT
            handoff.error = f"Handoff timed out after {self.config.coordination_timeout}s"
            raise
        except Exception as exc:
            handoff.status = HandoffStatus.FAILED
            handoff.error = str(exc)
            raise

    def _create_handoff(
        self,
        session: CoordinationSession,
        request_type: RequestType,
        source_node: str,
        target_node: str,
        chat_id: int,
        message_id: int,
        payload: str,
    ) -> HandoffRequest:
        """Create and register a new handoff request."""
        handoff = HandoffRequest(
            request_type=request_type,
            source_node=source_node,
            target_node=target_node,
            source_chat_id=chat_id,
            source_message_id=message_id,
            payload=payload,
        )
        session.steps.append(handoff)
        return handoff


def get_node_info(node_key: str) -> Optional[Dict[str, Any]]:
    """Get information about a mesh node.

    Args:
        node_key: Node identifier (e.g. "node_b", "node_c", "node_d").

    Returns:
        Dict with node info, or None if node_key is unknown.
    """
    node = MESH_NODES.get(node_key)
    if node is None:
        return None
    return {
        "name": node.name,
        "role": node.role,
        "tailnet_ip": node.tailnet_ip,
        "port": node.port,
        "services": node.services,
    }
