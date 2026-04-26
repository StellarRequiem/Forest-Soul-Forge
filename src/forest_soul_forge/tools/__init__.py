"""Tool execution runtime — ADR-0019 T1.

The runtime turns a tool-call request from an agent's session into a
governed dispatch: constraint resolution, audit-chain entry, optional
approval gate, optional accounting. This module ships the foundation
(Protocol, ToolContext, ToolResult, Registry) plus a single reference
tool — ``timestamp_window.v1`` — to prove the contract.

T2+ (fast-path dispatcher, approval queue, schema v3, etc.) layer on
top of these building blocks.
"""
from forest_soul_forge.tools.base import (
    Tool,
    ToolContext,
    ToolError,
    ToolRegistry,
    ToolResult,
    ToolValidationError,
)

__all__ = [
    "Tool",
    "ToolContext",
    "ToolError",
    "ToolRegistry",
    "ToolResult",
    "ToolValidationError",
]
