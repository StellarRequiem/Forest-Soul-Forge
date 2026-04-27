"""Built-in tool implementations — ADR-0019 T1.

These ship with the daemon and are registered at lifespan. They mirror
catalog entries one-for-one. Operator-installed tools live in
``~/.fsf/plugins/`` and get loaded by the .fsf plugin loader (T5).

T1 ships ``timestamp_window.v1`` only — pure function, no I/O, perfect
proving ground for the Tool Protocol contract. Other catalog entries
will get implementations as the corresponding tranches land.
"""
from forest_soul_forge.tools.builtin.memory_disclose import MemoryDiscloseTool
from forest_soul_forge.tools.builtin.memory_recall import MemoryRecallTool
from forest_soul_forge.tools.builtin.memory_write import MemoryWriteTool
from forest_soul_forge.tools.builtin.timestamp_window import TimestampWindowTool

__all__ = [
    "TimestampWindowTool",
    "MemoryRecallTool",
    "MemoryWriteTool",
    "MemoryDiscloseTool",
]


def register_builtins(registry) -> None:  # noqa: ANN001 — circular import dance
    """Register every built-in tool into the supplied registry.

    Called from daemon lifespan. Centralizing the registration here
    means adding a new built-in is one line in one file rather than
    a hunt across the lifespan code.
    """
    registry.register(TimestampWindowTool())
    registry.register(MemoryRecallTool())
    registry.register(MemoryWriteTool())
    registry.register(MemoryDiscloseTool())
