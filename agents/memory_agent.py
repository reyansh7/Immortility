"""Deprecated shim — use ``memory.memory_manager.MemoryManager`` directly.

Kept so old ``from agents.memory_agent import MemoryAgent`` imports still resolve.
"""

from __future__ import annotations

from memory.memory_manager import MemoryManager

# Back-compat alias: MemoryAgent used to wrap MemoryManager; now they are the same.
MemoryAgent = MemoryManager

__all__ = ["MemoryAgent", "MemoryManager"]
