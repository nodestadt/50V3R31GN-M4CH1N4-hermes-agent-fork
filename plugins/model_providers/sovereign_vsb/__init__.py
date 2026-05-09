"""
Sovereign VSB: Virtual Sovereign Bus ModelProvider.

Core design:
- Multi-node dispatch (Tailscale Artery)
- TokenSpeed backend for speed-of-light inference
- UDP pulse synchronization (302-byte deterministic state)
- Load balancing across nodes
"""

import logging
from typing import Any, Optional
from providers import register_provider
from providers.base import ProviderProfile

logger = logging.getLogger("sovereign_vsb")

class SovereignVSBProfile(ProviderProfile):
    """
    Sovereign VSB Profile - Dynamic model discovery across the mesh.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.config = {} # To be populated by loader

    def fetch_models(self, *, api_key: str | None = None, timeout: float = 8.0) -> list[str] | None:
        """
        Dynamically fetch models from config.yaml mesh_nodes.
        """
        try:
            import yaml
            from pathlib import Path
            config_path = Path("~/.hermes/config.yaml").expanduser()
            logger.info(f"VSB: Fetching models from {config_path}")
            if not config_path.exists():
                logger.warning(f"VSB: config.yaml not found at {config_path}")
                return None
            
            with open(config_path) as f:
                config = yaml.safe_load(f)
            
            vsb_config = config.get("model_providers", {}).get("sovereign-vsb", {}).get("config", {})
            nodes = vsb_config.get("mesh_nodes", [])
            logger.info(f"VSB: Found {len(nodes)} mesh nodes in config")
            
            models = []
            for node in nodes:
                node_models = node.get("models", [])
                logger.info(f"VSB: Node {node.get('id')} has models: {node_models}")
                for model in node_models:
                    models.append(model)
            
            unique_models = list(set(models))
            logger.info(f"VSB: Discovered {len(unique_models)} unique models across the mesh")
            return unique_models
        except Exception as e:
            logger.error(f"VSB: Error fetching models from config: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

sovereign_vsb = SovereignVSBProfile(
    name="sovereign-vsb",
    aliases=("vsb", "sovereign"),
    display_name="Sovereign VSB",
    description="Sovereign Model Router - VSB (Virtual Sovereign Bus) for high-performance inference.",
    signup_url="https://github.com/Sovereign-Machina",
    base_url="http://100.120.225.12:8080/v1",
    env_vars=("HERMES_API_TOKEN",),
    auth_type="api_key",
    fallback_models=(
        "falcon", "embedding", 
        "carnice-9b", "qwen3-vl", 
        "voxcpm2-indic-q4", "qwen3.5-0.8b",
        "carnice-v2-27b", "qwen2.5-coder-14b"
    ),
    default_max_tokens=32768,
)

register_provider(sovereign_vsb)

# Import the actual provider class for registration if needed by the loader
from .provider import SovereignVSBProvider
