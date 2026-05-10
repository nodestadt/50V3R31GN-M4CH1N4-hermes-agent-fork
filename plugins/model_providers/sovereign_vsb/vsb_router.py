"""
VSB Router - Multi-node model dispatch with TokenSpeed.

Core design:
- Node selection based on model type and load
- UDP pulse for state sync (302-byte packets)
- TokenSpeed backend for fast inference
"""
import logging
import os
import socket
import struct
import time
import threading
import httpx
import json
import re
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
    ram_usage: float = 0.0
    vram_usage: float = 0.0
    last_seen: float = 0.0


class VSBPulse:
    """
    UDP pulse synchronization (302-byte deterministic state).
    """
    PORT = 7878
    PULSE_INTERVAL = 2.0  # seconds
    PULSE_TIMEOUT = 10.0  # seconds

    def __init__(self, nodes: List[Node], bind_ip: str = "0.0.0.0"):
        self.bind_ip = bind_ip
        self.nodes = nodes
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

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

    def listen(self, timeout: float = 1.0) -> Optional[tuple]:
        """Listen for pulse from any node."""
        self.sock.settimeout(timeout)
        try:
            data, addr = self.sock.recvfrom(302)
            return (data, addr)
        except (socket.timeout, BlockingIOError):
            return None
        except Exception as e:
            logger.error(f"Error receiving pulse: {e}")
            return None

    def recv_pulse(self, data, addr):
        """Unpack pulse and update node metrics."""
        if len(data) < 302:
            return

        try:
            header, version, node_id, load, ram, vram = struct.unpack("!3sBBfff", data[:17])
            if header != b"VSB" or version != 3:
                return

            ip = addr[0]
            for node in self.nodes:
                if node.ip == ip:
                    node.load = load
                    node.ram_usage = ram
                    node.vram_usage = vram
                    node.last_seen = time.time()
                    break
        except Exception as e:
            logger.error(f"Failed to unpack pulse: {e}")


class VSBRouter:
    """
    Multi-node model router with TokenSpeed backend.
    """
    def __init__(self, nodes: List[Node]):
        self.nodes = {n.id: n for n in nodes}
        self.pulse = VSBPulse(nodes)
        self.running = False
        secret = os.getenv("SOVEREIGN_MESH_SECRET")
        if not secret:
            raise EnvironmentError(
                "SOVEREIGN_MESH_SECRET environment variable is required. "
                "Set it to the mesh shared secret before starting the VSB router."
            )
        self.secret_key = secret

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

    def route_inference(self, model: str, prompt: str, **kwargs) -> Dict:
        """
        Route inference to best node via TokenSpeed.
        """
        node = self.select_node(model)

        if not node:
            logger.error(f"Inference routing failed: No node found for model: {model}")
            return {"error": f"No node found for model: {model}"}

        logger.info(f"Routing {model} -> {node.id} ({node.ip})")

        try:
            # Call OpenAI-compatible endpoint on node
            response = self._call_tokenspeed(node, model, prompt, **kwargs)
            return response
        except Exception as e:
            logger.error(f"Routing error to {node.id} for {model}: {e}")
            return {"error": str(e)}

    def _call_tokenspeed(self, node: Node, model: str, prompt: str, **kwargs) -> Dict:
        """
        Call TokenSpeed backend (llama-server) on node.
        """
        
        # Build OpenAI chat completions request
        url = f"http://{node.ip}:{node.port}/v1/chat/completions"
        
        stream = kwargs.get("stream", False)
        
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": kwargs.get("temperature", 0.7),
            "max_tokens": kwargs.get("max_tokens", 4096),
            "stream": stream
        }
        
        headers = {
            "Authorization": f"Bearer {self.secret_key}"
        }
        
        logger.debug(f"Calling VSB backend: {url} (stream={stream})")
        
        # Use longer timeout for CPU inference
        timeout = httpx.Timeout(connect=5.0, read=300.0, write=5.0, pool=10.0)
        
        if stream:
            return self._stream_tokenspeed(url, payload, headers, node)

        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            
            choices = data.get("choices", [])
            if not choices:
                raise ValueError("No choices returned from inference backend")
                
            choice = choices[0]
            message = choice.get("message", {})
            content = message.get("content", "")
            reasoning = message.get("reasoning_content", "")
            
            # If no explicit reasoning_content, check for <think> blocks in content
            if not reasoning and "<think>" in content and "</think>" in content:
                match = re.search(r"<think>(.*?)</think>", content, re.DOTALL)
                if match:
                    reasoning = match.group(1)
                    content = content.replace(match.group(0), "").strip()
            
            usage = data.get("usage", {})
            
            return {
                "text": content,
                "reasoning": reasoning,
                "tokens": usage.get("total_tokens", 0),
                "node": node.id,
                "finish_reason": choice.get("finish_reason", "stop")
            }

    def _stream_tokenspeed(self, url: str, payload: Dict, headers: Dict, node: Node):
        """
        Stream from TokenSpeed backend.
        """

        def gen():
            in_think_block = False
            with httpx.stream("POST", url, json=payload, headers=headers, timeout=300.0) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                        
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        reasoning = delta.get("reasoning_content", "")
                        
                        if reasoning:
                            yield ("reasoning", reasoning)
                        
                        if content:
                            # Basic <think> block detection
                            if "<think>" in content:
                                in_think_block = True
                                parts = content.split("<think>", 1)
                                if parts[0]:
                                    yield ("content", parts[0])
                                content = parts[1]
                            
                            if "</think>" in content:
                                parts = content.split("</think>", 1)
                                if parts[0]:
                                    yield ("reasoning", parts[0])
                                in_think_block = False
                                content = parts[1]
                            
                            if content:
                                if in_think_block:
                                    yield ("reasoning", content)
                                else:
                                    yield ("content", content)

                    except Exception as e:
                        logger.error(f"Error parsing stream chunk: {e}")

        return {
            "stream": gen(),
            "node": node.id
        }

    def start_pulse_sync(self):
        """Start background pulse sync in a daemon thread."""
        self.pulse.start()
        self.running = True

        def _pulse_loop():
            while self.running:
                try:
                    pulse_data = self.pulse.listen(timeout=1.0)
                    if pulse_data:
                        data, addr = pulse_data
                        self.pulse.recv_pulse(data, addr)
                        logger.debug(f"Pulse received from {addr[0]}:{addr[1]}")
                except Exception as e:
                    logger.error(f"Pulse loop error: {e}")

        self._pulse_thread = threading.Thread(target=_pulse_loop, daemon=True, name="vsb-pulse")
        self._pulse_thread.start()
        logger.info("VSB background pulse sync started (daemon thread)")

    def stop(self):
        """Stop router and pulse sync."""
        logger.info("Stopping VSB router and pulse sync")
        self.running = False
        if hasattr(self, '_pulse_thread') and self._pulse_thread is not None:
            self._pulse_thread.join(timeout=3.0)


# Mesh node configuration (Tailscale Artery)
MESH_NODES = [
    Node(id="node-a", ip="100.90.196.70", port=8000, models=["falcon", "embedding"]),
    Node(id="node-b", ip="100.66.173.31", port=9119, models=["carnice-9b", "qwen3-vl"]),
    Node(id="node-c", ip="100.102.109.81", port=8080, models=["voxcpm2", "moshi", "qwen3.5-0.8b"]),
    Node(id="node-d", ip="100.120.225.12", port=8080, models=["carnice-v2-27b", "qwen2.5-coder-14b"]),
]


def main():
    """Test VSB router."""
    logging.basicConfig(level=logging.DEBUG)
    
    if not os.getenv("SOVEREIGN_MESH_SECRET"):
        os.environ["SOVEREIGN_MESH_SECRET"] = "test-secret-for-dev"
        logger.warning("Using development SOVEREIGN_MESH_SECRET. Set the env var in production.")

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
