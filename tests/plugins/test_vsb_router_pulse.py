import struct
import socket
import time
import pytest
from plugins.model_providers.sovereign_vsb.vsb_router import VSBPulse, Node

def test_pulse_unpacking():
    # Note: We might need to adjust Node arguments if the dataclass changes
    try:
        node = Node(id="node-d", ip="100.120.225.12", port=8080, models=[])
    except TypeError:
        # Fallback for the simpler Node structure if it's already been changed or if the user expects it
        node = Node("node-d", "100.120.225.12")
        
    pulse = VSBPulse([node])
    
    # Pack a 302-byte pulse packet (v3.2 schema)
    # Header: 'VSB' (3), Version: 3 (1), NodeID: 4 (1), Load: 0.75 (4), RAM: 0.5 (4), VRAM: 0.8 (4)
    # Rest is padding (285 bytes)
    packet = struct.pack("!3sBBfff", b"VSB", 3, 4, 0.75, 0.5, 0.8) + b"\x00" * 285
    
    pulse.recv_pulse(packet, ("100.120.225.12", 7878))
    
    from pytest import approx
    assert getattr(node, "load", 0) == approx(0.75)
    assert getattr(node, "ram_usage", 0) == approx(0.5)
    assert getattr(node, "vram_usage", 0) == approx(0.8)
