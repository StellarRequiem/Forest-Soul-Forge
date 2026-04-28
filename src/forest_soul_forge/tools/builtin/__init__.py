"""Built-in tool implementations — ADR-0019 T1.

These ship with the daemon and are registered at lifespan. They mirror
catalog entries one-for-one. Operator-installed tools live in
``~/.fsf/plugins/`` and get loaded by the .fsf plugin loader (T5).

T1 ships ``timestamp_window.v1`` only — pure function, no I/O, perfect
proving ground for the Tool Protocol contract. Other catalog entries
will get implementations as the corresponding tranches land.
"""
from forest_soul_forge.tools.builtin.anomaly_score import AnomalyScoreTool
from forest_soul_forge.tools.builtin.audit_chain_verify import AuditChainVerifyTool
from forest_soul_forge.tools.builtin.behavioral_baseline import BehavioralBaselineTool
from forest_soul_forge.tools.builtin.canary_token import CanaryTokenTool
from forest_soul_forge.tools.builtin.continuous_verify import ContinuousVerifyTool
from forest_soul_forge.tools.builtin.delegate import DelegateTool
from forest_soul_forge.tools.builtin.dynamic_policy import DynamicPolicyTool
from forest_soul_forge.tools.builtin.evidence_collect import EvidenceCollectTool
from forest_soul_forge.tools.builtin.file_integrity import FileIntegrityTool
from forest_soul_forge.tools.builtin.honeypot_local import HoneypotLocalTool
from forest_soul_forge.tools.builtin.isolate_process import IsolateProcessTool
from forest_soul_forge.tools.builtin.jit_access import JitAccessTool
from forest_soul_forge.tools.builtin.key_inventory import KeyInventoryTool
from forest_soul_forge.tools.builtin.lateral_movement_detect import LateralMovementDetectTool
from forest_soul_forge.tools.builtin.log_aggregate import LogAggregateTool
from forest_soul_forge.tools.builtin.log_correlate import LogCorrelateTool
from forest_soul_forge.tools.builtin.log_scan import LogScanTool
from forest_soul_forge.tools.builtin.memory_disclose import MemoryDiscloseTool
from forest_soul_forge.tools.builtin.memory_recall import MemoryRecallTool
from forest_soul_forge.tools.builtin.memory_write import MemoryWriteTool
from forest_soul_forge.tools.builtin.patch_check import PatchCheckTool
from forest_soul_forge.tools.builtin.port_policy_audit import PortPolicyAuditTool
from forest_soul_forge.tools.builtin.port_scan_local import PortScanLocalTool
from forest_soul_forge.tools.builtin.posture_check import PostureCheckTool
from forest_soul_forge.tools.builtin.software_inventory import SoftwareInventoryTool
from forest_soul_forge.tools.builtin.tamper_detect import TamperDetectTool
from forest_soul_forge.tools.builtin.timestamp_window import TimestampWindowTool
from forest_soul_forge.tools.builtin.traffic_flow_local import TrafficFlowLocalTool
from forest_soul_forge.tools.builtin.triage import TriageTool
from forest_soul_forge.tools.builtin.ueba_track import UebaTrackTool
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
    "BehavioralBaselineTool",
    "AnomalyScoreTool",
    "LogCorrelateTool",
    "LateralMovementDetectTool",
    "UebaTrackTool",
    "PortScanLocalTool",
    "TrafficFlowLocalTool",
    "EvidenceCollectTool",
    "TriageTool",
    "IsolateProcessTool",
    "PostureCheckTool",
    "ContinuousVerifyTool",
    "JitAccessTool",
    "KeyInventoryTool",
    "DynamicPolicyTool",
    "TamperDetectTool",
    "CanaryTokenTool",
    "HoneypotLocalTool",
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
    # ADR-0033 Phase B2 — security_mid pure-python analytics.
    registry.register(BehavioralBaselineTool())
    registry.register(AnomalyScoreTool())
    registry.register(LogCorrelateTool())
    registry.register(LateralMovementDetectTool())
    # ADR-0033 Phase B2 — security_mid telemetry + forensics.
    registry.register(UebaTrackTool())
    registry.register(PortScanLocalTool())
    registry.register(TrafficFlowLocalTool())
    registry.register(EvidenceCollectTool())
    # ADR-0033 Phase B2 — security_mid LLM-driven + privileged.
    registry.register(TriageTool())
    registry.register(IsolateProcessTool())
    # ADR-0033 Phase B3 — security_high posture + verification.
    registry.register(PostureCheckTool())
    registry.register(ContinuousVerifyTool())
    # ADR-0033 Phase B3 — security_high access + key inventory.
    registry.register(JitAccessTool())
    registry.register(KeyInventoryTool())
    # ADR-0033 Phase B3 — security_high privileged (PrivClient).
    registry.register(DynamicPolicyTool())
    registry.register(TamperDetectTool())
    # ADR-0033 Phase B3 — security_high deception layer.
    registry.register(CanaryTokenTool())
    registry.register(HoneypotLocalTool())
