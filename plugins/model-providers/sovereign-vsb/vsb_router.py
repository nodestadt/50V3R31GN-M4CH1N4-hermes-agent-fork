"""
VSB Router - Multi-node model dispatch with TokenSpeed.

Core design:
- Node selection based on model type and load
- UDP pulse for state sync (302-byte packets)
- TokenSpeed backend for fast inference
"""
import logging
import socket
import struct
import time
import asyncio
from typing import List, Dict, Optional
from dataclasses import dataclass

logger = logging.getLogger("vsb")

@dataclass
class Node:
    """Mesh node in VSB."""
    id: str
    ip: str
    port: int
    models: List[str]
    load: float = 0.0
    last_pulse: float = 0.0


class VSBPulse:
    """
    UDP pulse synchronization (302-byte deterministic state).
    """
    PORT = 7878
    PULSE_INTERVAL = 2.0  # seconds
    PULSE_TIMEOUT = 10.0  # seconds

    def __init__(self, bind_ip: str = "0.0.0.0"):
        self.bind_ip = bind_ip
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.nodes: Dict[str, Node] = {}

    def start(self):
        """Start pulse listener."""
        try:
            self.sock.bind((self.bind_ip, self.PORT))
            self.sock.setblocking(False)
            logger.info(f"Pulse listening on {self.bind_ip}:{self.PORT}")
        except Exception as e:
            logger.error(f"Failed to start pulse listener on {self.bind_ip}:{self.PORT}: {e}")
            raise

    def send_pulse(self, target_ip: str, payload: bytes):
        """Send 302-byte pulse to node."""
        if len(payload) != 302:
            logger.error(f"Pulse invalid size: {len(payload)} bytes (must be exactly 302)")
            raise ValueError("Pulse must be exactly 302 bytes")

        try:
            self.sock.sendto(payload, (target_ip, self.PORT))
            logger.debug(f"Pulse sent to {target_ip}:{self.PORT}")
        except Exception as e:
            logger.error(f"Error sending pulse to {target_ip}: {e}")

    def recv_pulse(self, timeout: float = 1.0) -> Optional[tuple]:
        """Receive pulse from any node."""
        self.sock.settimeout(timeout)
        try:
            data, addr = self.sock.recvfrom(302)
            return (data, addr)
        except socket.timeout:
            return None
        except Exception as e:
            logger.error(f"Error receiving pulse: {e}")
            return None


class VSBRouter:
    """
    Multi-node model router with TokenSpeed backend.
    """
    def __init__(self, nodes: List[Node]):
        self.nodes = {n.id: n for n in nodes}
        self.pulse = VSBPulse()
        self.running = False

    def select_node(self, model: str) -> Optional[Node]:
        """
        Select best node for model based on load and availability.
        """
        candidates = [n for n in self.nodes.values() if model in n.models]

        if not candidates:
            logger.warning(f"No node candidates found for model: {model}")
            return None

        # Select node with lowest load
        selected = min(candidates, key=lambda n: n.load)
        logger.debug(f"Selected node {selected.id} for model {model} (load: {selected.load})")
        return selected

    def route_inference(self, model: str, prompt: str) -> Dict:
        """
        Route inference to best node via TokenSpeed.
        """
        node = self.select_node(model)

        if not node:
            logger.error(f"Inference routing failed: No node found for model: {model}")
            return {"error": f"No node found for model: {model}"}

        logger.info(f"Routing {model} -> {node.id} ({node.ip})")

        try:
            # TokenSpeed backend (simplified - actual call via HTTP)
            # This is the speed-of-light inference path
            response = self._call_tokenspeed(node, model, prompt)
            return response
        except Exception as e:
            logger.error(f"Routing error to {node.id} for {model}: {e}")
            return {"error": str(e)}

    def _call_tokenspeed(self, node: Node, model: str, prompt: str) -> Dict:
        """
        Call TokenSpeed backend on node.
        """
        # Simplified HTTP call to TokenSpeed endpoint
        # Real implementation: httpx.post(f"http://{node.ip}:8000/infer", ...)
        # This is placeholder - actual TokenSpeed SDK integration needed

        return {
            "text": f"[TokenSpeed response for {model}]",
            "tokens": len(prompt) // 4,
            "node": node.id,
        }

    def start_pulse_sync(self):
        """Start background pulse sync."""
        self.pulse.start()
        self.running = True

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _pulse_loop():
            while self.running:
                try:
                    # Check for pulses from all nodes
                    pulse = self.pulse.recv_pulse(timeout=1.0)
                    if pulse:
                        data, addr = pulse
                        # Parse 302-byte pulse (simplified)
                        # Real implementation: struct unpack with node_id, load, models
                        logger.debug(f"Pulse received from {addr[0]}:{addr[1]}")
                except Exception as e:
                    logger.error(f"Pulse loop error: {e}")

                await asyncio.sleep(0.5)

        logger.info("VSB background pulse sync started")
        loop.run_until_complete(_pulse_loop())

    def stop(self):
        """Stop router and pulse sync."""
        logger.info("Stopping VSB router and pulse sync")
        self.running = False


# Mesh node configuration (from Phase 1)
MESH_NODES = [
    Node(id="node-a", ip="10.0.0.10", port=8000, models=["falcon", "embedding"]),
    Node(id="node-b", ip="10.0.0.11", port=9119, models=["carnice-9b", "qwen3-vl"]),
    Node(id="node-c", ip="10.0.0.12", port=8080, models=["voxcpm2", "moshi", "qwen3.5-0.8b"]),
    Node(id="node-d", ip="10.0.0.13", port=8000, models=["carnice-v2-27b", "qwen2.5-coder-14b"]),
]


def main():
    """Test VSB router."""
    logging.basicConfig(level=logging.DEBUG)
    router = VSBRouter(MESH_NODES)

    # Start pulse sync
    router.start_pulse_sync()

    # Test routing
    logger.info("Testing inference routing...")

    # Route to Node D (heavy reasoning)
    result = router.route_inference("carnice-v2-27b", "Test prompt for reasoning")
    logger.info(f"Result: {result}")

    # Route to Node B (vision)
    result = router.route_inference("carnice-9b", "Describe this image")
    logger.info(f"Result: {result}")

    router.stop()


if __name__ == "__main__":
    main()
