"""Built-in tool implementations — ADR-0019 T1.

These ship with the daemon and are registered at lifespan. They mirror
catalog entries one-for-one. Operator-installed tools live in
``~/.fsf/plugins/`` and get loaded by the .fsf plugin loader (T5).

T1 ships ``timestamp_window.v1`` only — pure function, no I/O, perfect
proving ground for the Tool Protocol contract. Other catalog entries
will get implementations as the corresponding tranches land.
"""
from forest_soul_forge.tools.builtin.audit_chain_verify import AuditChainVerifyTool
from forest_soul_forge.tools.builtin.delegate import DelegateTool
from forest_soul_forge.tools.builtin.file_integrity import FileIntegrityTool
from forest_soul_forge.tools.builtin.log_aggregate import LogAggregateTool
from forest_soul_forge.tools.builtin.log_scan import LogScanTool
from forest_soul_forge.tools.builtin.memory_disclose import MemoryDiscloseTool
from forest_soul_forge.tools.builtin.memory_recall import MemoryRecallTool
from forest_soul_forge.tools.builtin.memory_write import MemoryWriteTool
from forest_soul_forge.tools.builtin.patch_check import PatchCheckTool
from forest_soul_forge.tools.builtin.port_policy_audit import PortPolicyAuditTool
from forest_soul_forge.tools.builtin.software_inventory import SoftwareInventoryTool
from forest_soul_forge.tools.builtin.timestamp_window import TimestampWindowTool
from forest_soul_forge.tools.builtin.usb_device_audit import UsbDeviceAuditTool

__all__ = [
    "TimestampWindowTool",
    "MemoryRecallTool",
    "MemoryWriteTool",
    "MemoryDiscloseTool",
    "DelegateTool",
    "AuditChainVerifyTool",
    "FileIntegrityTool",
    "LogScanTool",
    "LogAggregateTool",
    "PatchCheckTool",
    "SoftwareInventoryTool",
    "PortPolicyAuditTool",
    "UsbDeviceAuditTool",
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
    registry.register(DelegateTool())
    # ADR-0033 Phase B1 — security_low pure-python tools.
    registry.register(AuditChainVerifyTool())
    registry.register(FileIntegrityTool())
    registry.register(LogScanTool())
    registry.register(LogAggregateTool())
    # ADR-0033 Phase B1 — security_low OS-shellout tools.
    registry.register(PatchCheckTool())
    registry.register(SoftwareInventoryTool())
    registry.register(PortPolicyAuditTool())
    registry.register(UsbDeviceAuditTool())
