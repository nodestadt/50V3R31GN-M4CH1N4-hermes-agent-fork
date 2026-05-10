"""
VSB ModelProvider - Native Hermes integration.

Core design:
- Implements ModelProvider ABC
- Delegates to VSBRouter for node selection
- Fast inference via TokenSpeed backend
"""
import logging
from agent.model_provider import ModelProvider
from typing import List, Dict, Optional, Any
from .vsb_router import VSBRouter, MESH_NODES

logger = logging.getLogger("vsb_provider")


class SovereignVSBProvider(ModelProvider):
    """
    Sovereign Model Router - Native Hermes ModelProvider.

    Core capabilities:
    - Multi-node dispatch (10.0.0.x subnet)
    - TokenSpeed backend for speed-of-light inference
    - Load balancing and health monitoring
    """

    def __init__(self, config: dict):
        """
        Initialize VSB provider.

        Config:
        - mesh_nodes: List of {id, ip, port, models}
        - tokenspeed_url: TokenSpeed backend URL
        - pulse_enabled: Enable UDP pulse sync (default: True)
        """
        self.config = config
        
        # Parse mesh_nodes from config
        mesh_nodes_config = config.get("mesh_nodes", [])
        nodes = []
        from .vsb_router import Node, VSBRouter
        
        for n in mesh_nodes_config:
            nodes.append(Node(
                id=n["id"],
                ip=n["ip"],
                port=n["port"],
                models=n["models"]
            ))
        
        if not nodes:
            # Fallback to hardcoded for back-compat
            from .vsb_router import MESH_NODES
            nodes = MESH_NODES
            logger.warning("No mesh_nodes found in config, using hardcoded fallback")

        self.router = VSBRouter(nodes)
        self.pulse_enabled = config.get("pulse_enabled", True)

        logger.info(f"Initializing VSB provider with {len(nodes)} nodes (pulse_enabled={self.pulse_enabled})")

        if self.pulse_enabled:
            self.router.start_pulse_sync()

    def list_models(self) -> List[Dict]:
        """
        List all available models across mesh.
        """
        models = []
        for node in self.router.nodes.values():
            for model in node.models:
                models.append({
                    "id": model,
                    "name": f"{node.id}/{model}",
                    "node": node.id,
                    "context_length": 128000,
                    "supports_function_calling": True,
                })
        logger.debug(f"VSB listed {len(models)} models across the mesh")
        return models

    def get_model_info(self, model_id: str) -> Dict:
        """
        Get model info (node-specific).
        """
        # Extract model name (remove node prefix if present)
        model_name = model_id.split("/")[-1] if "/" in model_id else model_id

        node = self.router.select_node(model_name)

        if not node:
            logger.warning(f"Model not found: {model_id}")
            return {"error": f"Model not found: {model_id}"}

        return {
            "id": model_id,
            "name": f"{node.id}/{model_name}",
            "node": node.id,
            "context_length": 128000,
            "supports_function_calling": True,
            "backend": "tokenspeed",
        }

    def generate(
        self,
        model_id: str,
        messages: List[Dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs
    ) -> Dict:
        """
        Generate completion via VSB routing.
        """
        logger.info(f"Generating via VSB for model: {model_id}")
        
        # Extract model name
        model_name = model_id.split("/")[-1] if "/" in model_id else model_id

        # Build prompt from messages
        prompt = self._build_prompt(messages)

        # Route inference
        result = self.router.route_inference(model_name, prompt, **kwargs)

        if "error" in result:
            logger.error(f"Generation error for {model_id}: {result['error']}")
            return result

        logger.debug(f"Generation successful for {model_id} via {result.get('node')}")
        
        # Format as Hermes response
        return {
            "content": result.get("text"),
            "reasoning": result.get("reasoning", ""),
            "tokens": result.get("tokens", 0),
            "model": model_id,
            "node": result.get("node"),
            "finish_reason": "stop",
        }

    def stream(
        self,
        model_id: str,
        messages: List[Dict],
        **kwargs
    ) -> Any:
        """
        Stream completion via VSB routing.
        """
        logger.info(f"Streaming via VSB for model: {model_id}")
        
        # Extract model name
        model_name = model_id.split("/")[-1] if "/" in model_id else model_id

        # Build prompt from messages
        prompt = self._build_prompt(messages)

        # Route inference (request stream)
        kwargs["stream"] = True
        result = self.router.route_inference(model_name, prompt, **kwargs)

        if "error" in result:
            logger.error(f"Streaming error for {model_id}: {result['error']}")
            yield {"error": result["error"]}
            return

        # yield chunks
        for chunk_type, chunk_text in result.get("stream"):
            yield {
                "content": chunk_text if chunk_type == "content" else "",
                "reasoning": chunk_text if chunk_type == "reasoning" else "",
                "model": model_id,
                "node": result.get("node")
            }

    def _build_prompt(self, messages: List[Dict]) -> str:
        """
        Build prompt from messages.
        """
        prompt_parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            prompt_parts.append(f"{role}: {content}")

        return "\n".join(prompt_parts)

    def shutdown(self):
        """Shutdown VSB provider and pulse sync."""
        self.router.stop()
        logger.info("Sovereign Model Router shut down")
