"""Tailscale Artery configuration for the Telegram bridge.

Maps Sovereign Machina mesh nodes to their Tailscale Artery addresses
and provides a configuration registry for the Telegram Artery integration.

Topology:
  Node B (100.66.173.31) — Director / Strategist workspace
  Node C (100.102.109.81) — Voice / Perception (vision handoff)
  Node D (100.120.225.12) — Hermes Core / Reasoning (token streaming source)

All inter-node traffic flows over the Tailscale Zero-Trust Artery.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, Optional


# ---------------------------------------------------------------------------
# Mesh node registry (Tailnet IPs)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MeshNode:
    """A single node in the Sovereign mesh."""
    name: str
    role: str
    tailnet_ip: str
    port: int
    services: Dict[str, str] = field(default_factory=dict)


# Canonical node definitions — matches AGENTS.md §4
MESH_NODES: Dict[str, MeshNode] = {
    "node_b": MeshNode(
        name="Director",
        role="strategist",
        tailnet_ip="100.66.173.31",
        port=8000,
        services={
            "vsb_router": "http://100.66.173.31:8000/v1",
            "goose": "http://100.66.173.31:8001",
        },
    ),
    "node_c": MeshNode(
        name="Falcon",
        role="perception",
        tailnet_ip="100.102.109.81",
        port=8000,
        services={
            "vibevoice_asr": "http://100.102.109.81:8002/asr",
            "voxcpm2_tts": "http://100.102.109.81:8003/tts",
            "qwen_perception": "http://100.102.109.81:8000/v1",
            "graphify_ast": "http://100.102.109.81:8004",
        },
    ),
    "node_d": MeshNode(
        name="Quaternary",
        role="reasoning",
        tailnet_ip="100.120.225.12",
        port=8000,
        services={
            "hermes_core": "http://100.120.225.12:8000/v1",
            "carnice_v2": "http://100.120.225.12:8000/v1",
            "qwen_coder": "http://100.120.225.12:8001/v1",
            "consensus": "http://100.120.225.12:8002",
        },
    ),
}


# ---------------------------------------------------------------------------
# Artery configuration
# ---------------------------------------------------------------------------

@dataclass
class ArteryConfig:
    """Configuration for the Telegram Artery bridge.

    Attributes:
        bot_token: Telegram bot API token.
        primary_node: The mesh node responsible for token streaming (Node D).
        perception_node: The mesh node for vision/perception handoff (Node C).
        reasoning_model: Model ID on the primary node for reasoning.
        perception_model: Model ID on the perception node.
        stream_chunk_size: Characters per streaming chunk sent to Telegram.
        stream_interval: Seconds between progressive Telegram edits.
        enable_bot_coordination: Whether bot-to-bot handoff is active.
        coordination_timeout: Seconds before a handoff times out.
        monospace_blocks: Whether reasoning blocks use monospace formatting.
        max_message_length: Telegram message length limit (4096).
    """

    # Required
    bot_token: str = ""

    # Node routing
    primary_node: str = "node_d"
    perception_node: str = "node_c"

    # Model IDs
    reasoning_model: str = "carnice-v2-27b"
    perception_model: str = "qwen3.5-0.8b"

    # Streaming parameters
    stream_chunk_size: int = 40
    stream_interval: float = 0.3

    # Bot coordination
    enable_bot_coordination: bool = True
    coordination_timeout: float = 30.0

    # Formatting
    monospace_blocks: bool = True
    max_message_length: int = 4096

    @classmethod
    def from_dict(cls, cfg: dict) -> "ArteryConfig":
        """Build an ArteryConfig from a plugin config dict.

        Values are resolved: explicit config > environment variables > defaults.
        """
        return cls(
            bot_token=cfg.get("bot_token", os.getenv("TELEGRAM_ARTERY_BOT_TOKEN", "")),
            primary_node=cfg.get("primary_node", "node_d"),
            perception_node=cfg.get("perception_node", "node_c"),
            reasoning_model=cfg.get("reasoning_model", "carnice-v2-27b"),
            perception_model=cfg.get("perception_model", "qwen3.5-0.8b"),
            stream_chunk_size=int(cfg.get("stream_chunk_size", 40)),
            stream_interval=float(cfg.get("stream_interval", 0.3)),
            enable_bot_coordination=bool(cfg.get("enable_bot_coordination", True)),
            coordination_timeout=float(cfg.get("coordination_timeout", 30.0)),
            monospace_blocks=bool(cfg.get("monospace_blocks", True)),
            max_message_length=int(cfg.get("max_message_length", 4096)),
        )

    @classmethod
    def from_env(cls) -> "ArteryConfig":
        """Build an ArteryConfig using only environment variables."""
        return cls(
            bot_token=os.getenv("TELEGRAM_ARTERY_BOT_TOKEN", ""),
        )

    def get_primary_node(self) -> MeshNode:
        """Return the primary reasoning node."""
        return MESH_NODES[self.primary_node]

    def get_perception_node(self) -> MeshNode:
        """Return the perception node."""
        return MESH_NODES[self.perception_node]

    def get_reasoning_url(self) -> str:
        """Return the full API URL for the reasoning model."""
        node = self.get_primary_node()
        return node.services.get("hermes_core", f"http://{node.tailnet_ip}:{node.port}/v1")

    def get_perception_url(self) -> str:
        """Return the full API URL for the perception model."""
        node = self.get_perception_node()
        return node.services.get("qwen_perception", f"http://{node.tailnet_ip}:{node.port}/v1")

    def validate(self) -> Optional[str]:
        """Validate configuration and return an error message, or None if valid."""
        if not self.bot_token:
            return "bot_token is required (set TELEGRAM_ARTERY_BOT_TOKEN or configure in plugin)"
        if self.primary_node not in MESH_NODES:
            return f"Unknown primary_node: {self.primary_node}"
        if self.perception_node not in MESH_NODES:
            return f"Unknown perception_node: {self.perception_node}"
        if self.stream_chunk_size < 1:
            return "stream_chunk_size must be >= 1"
        if self.stream_interval < 0.05:
            return "stream_interval must be >= 0.05"
        if self.max_message_length < 1:
            return "max_message_length must be >= 1"
        return None
