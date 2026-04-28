"""``evidence_collect.v1`` — snapshot process state into a tarball.

ADR-0033 Phase B2. ResponseRogue's forensic-capture surface — when
an alert fires, snapshot enough state for the operator (and the
chain reader) to reconstruct what was running on the machine at
trigger time. Designed to run **before** isolate_process so the
evidence is captured while the process is still alive.

Each invocation produces one tar.gz archive containing:

  * ``ps_snapshot.txt`` — output of ``ps -ef`` (or ``ps auxww`` on
                            macOS) at capture time
  * ``listening_ports.txt`` — output of port_policy_audit-style
                                ``lsof -i -P -n -sTCP:LISTEN``
  * ``flow_table.txt`` — output of ``ss -tanp`` or ``lsof -i -P -n``
                          (current flow table; uses
                          traffic_flow_local under the hood)
  * ``selected_pid_<pid>/`` — for each PID the operator names:
                                cmdline, environ (env vars on Linux,
                                ``ps -ww -p <PID> -o command`` on
                                macOS), open file descriptors, cwd
  * ``manifest.json`` — index of files + capture metadata
                        (timestamp, hostname, caller_instance_id)

Tarball is written to ``settings.evidence_dir`` (the daemon
configures this; tests inject via constraints['evidence_dir']) so
operator can inspect it after the fact. The tool returns the path
+ sha256 of the archive so the audit chain entry can record the
fingerprint without storing the contents.

side_effects=read_only — every command run is query-only. The
write of the tarball lands inside the daemon's evidence dir,
which is artifact-tracked the same way soul.md and constitution.yaml
are.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_TIMEOUT_SECONDS = 30
_MAX_PIDS = 50


class EvidenceCollectTool:
    """Capture a forensic snapshot to a tarball.

    Args:
      pids (list[int], optional): PIDs to capture per-process detail
        for. Each must be > 1. Default: empty (system-level snapshot
        only).
      label (str, optional): operator-supplied label baked into the
        filename + manifest. Used to correlate the tarball with the
        triggering alert. ≤ 64 chars, [A-Za-z0-9_-].

    Output:
      {
        "archive_path":    str,
        "archive_size":    int,
        "archive_sha256":  str,
        "files_included":  [str, ...],
        "pid_count":       int,
        "skipped_pids":    [{pid, reason}, ...],
        "captured_at":     str   # ISO 8601
      }
    """

    name = "evidence_collect"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        pids = args.get("pids")
        if pids is not None:
            if not isinstance(pids, list):
                raise ToolValidationError("pids must be a list of integers")
            if len(pids) > _MAX_PIDS:
                raise ToolValidationError(
                    f"pids must be ≤ {_MAX_PIDS}; got {len(pids)}"
                )
            for p in pids:
                if not isinstance(p, int) or isinstance(p, bool) or p <= 1:
                    raise ToolValidationError(
                        f"every pid must be an integer > 1; got {p!r}"
                    )
        label = args.get("label")
        if label is not None:
            if not isinstance(label, str) or not label:
                raise ToolValidationError("label must be a non-empty string")
            if len(label) > 64:
                raise ToolValidationError("label must be ≤ 64 chars")
            if not all(c.isalnum() or c in "_-" for c in label):
                raise ToolValidationError(
                    "label must match [A-Za-z0-9_-]+"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        evidence_dir = _resolve_evidence_dir(ctx)
        evidence_dir.mkdir(parents=True, exist_ok=True)
        pids: list[int] = list(args.get("pids") or [])
        label = args.get("label") or "evidence"
        captured_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        archive_name = f"{label}-{captured_at}-{ctx.instance_id[:8]}.tar.gz"
        archive_path = evidence_dir / archive_name

        files_included: list[str] = []
        skipped_pids: list[dict[str, Any]] = []

        # Build the tarball in memory first so a failure mid-capture
        # doesn't leave a partial archive on disk.
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            # 1. ps snapshot
            ps_data = _run_ps()
            _add_text(tar, "ps_snapshot.txt", ps_data)
            files_included.append("ps_snapshot.txt")

            # 2. listening ports
            ports_data = _run_listening()
            _add_text(tar, "listening_ports.txt", ports_data)
            files_included.append("listening_ports.txt")

            # 3. flow table
            flow_data = _run_flow_table()
            _add_text(tar, "flow_table.txt", flow_data)
            files_included.append("flow_table.txt")

            # 4. per-PID detail
            for pid in pids:
                detail, reason = _capture_pid(pid)
                if detail is None:
                    skipped_pids.append({"pid": pid, "reason": reason})
                    continue
                for fname, content in detail.items():
                    arcname = f"selected_pid_{pid}/{fname}"
                    _add_text(tar, arcname, content)
                    files_included.append(arcname)

            # 5. manifest
            manifest = {
                "captured_at":         datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "hostname":            platform.node(),
                "platform":            platform.platform(),
                "caller_instance_id":  ctx.instance_id,
                "label":               label,
                "files":               files_included,
                "pids_requested":      pids,
                "pids_captured":       [
                    p for p in pids if not any(s["pid"] == p for s in skipped_pids)
                ],
                "skipped_pids":        skipped_pids,
            }
            _add_text(tar, "manifest.json",
                      json.dumps(manifest, indent=2, sort_keys=True))
            files_included.append("manifest.json")

        # Now write the tarball atomically.
        archive_bytes = buf.getvalue()
        archive_path.write_bytes(archive_bytes)
        digest = "sha256:" + hashlib.sha256(archive_bytes).hexdigest()

        return ToolResult(
            output={
                "archive_path":   str(archive_path),
                "archive_size":   len(archive_bytes),
                "archive_sha256": digest,
                "files_included": files_included,
                "pid_count":      len(pids) - len(skipped_pids),
                "skipped_pids":   skipped_pids,
                "captured_at":    manifest["captured_at"],
            },
            metadata={
                "label":             label,
                "files_in_archive":  len(files_included),
                "platform":          platform.system(),
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"captured {len(files_included)} files "
                f"({len(archive_bytes):,} bytes) → {archive_name}"
            ),
        )


def _resolve_evidence_dir(ctx: ToolContext) -> Path:
    d = (ctx.constraints or {}).get("evidence_dir")
    if d is not None:
        return Path(d)
    d = getattr(ctx, "evidence_dir", None)
    if d is not None:
        return Path(d)
    raise ToolValidationError(
        "evidence_collect.v1: no evidence_dir bound to ctx (daemon "
        "wiring missing). The daemon must populate the evidence "
        "directory path before dispatching."
    )


def _add_text(tar: tarfile.TarFile, arcname: str, content: str) -> None:
    data = content.encode("utf-8", errors="replace")
    info = tarfile.TarInfo(name=arcname)
    info.size = len(data)
    info.mode = 0o644
    tar.addfile(info, io.BytesIO(data))


def _run_ps() -> str:
    """ps -ef on Linux, ps auxww on macOS. Returns stdout (or
    error string)."""
    if platform.system() == "Darwin":
        cmd = ["ps", "auxww"]
    else:
        cmd = ["ps", "-ef"]
    try:
        proc = subprocess.run(cmd, capture_output=True,
                              timeout=_TIMEOUT_SECONDS, check=False)
        return proc.stdout.decode("utf-8", errors="replace") or \
               f"# ps exit={proc.returncode}: {proc.stderr.decode(errors='replace').strip()}"
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return f"# ps unavailable: {e}"


def _run_listening() -> str:
    binary = shutil.which("lsof")
    if binary is None:
        binary = shutil.which("ss")
        if binary is None:
            return "# no lsof or ss on PATH"
        cmd = [binary, "-tlnp"]
    else:
        cmd = [binary, "-i", "-P", "-n", "-sTCP:LISTEN"]
    try:
        proc = subprocess.run(cmd, capture_output=True,
                              timeout=_TIMEOUT_SECONDS, check=False)
        return proc.stdout.decode("utf-8", errors="replace") or \
               f"# {binary} exit={proc.returncode}"
    except subprocess.TimeoutExpired:
        return "# listening probe timed out"


def _run_flow_table() -> str:
    binary = shutil.which("ss")
    if binary is not None:
        cmd = [binary, "-tanp"]
    else:
        binary = shutil.which("lsof")
        if binary is None:
            return "# no ss or lsof on PATH"
        cmd = [binary, "-i", "-P", "-n"]
    try:
        proc = subprocess.run(cmd, capture_output=True,
                              timeout=_TIMEOUT_SECONDS, check=False)
        return proc.stdout.decode("utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return "# flow table probe timed out"


def _capture_pid(pid: int) -> tuple[dict | None, str | None]:
    """Capture per-PID detail. Returns (files_dict, None) on success,
    (None, reason) on skip."""
    detail: dict[str, str] = {}
    if platform.system() == "Linux":
        proc_root = Path("/proc") / str(pid)
        if not proc_root.exists():
            return None, "no /proc entry"
        for fname, src in [
            ("cmdline", "cmdline"),
            ("environ", "environ"),
            ("status",  "status"),
            ("cwd",     "cwd"),
        ]:
            srcp = proc_root / src
            try:
                if src == "cwd":
                    detail[fname] = os.readlink(srcp)
                else:
                    detail[fname] = srcp.read_text(errors="replace").replace("\x00", "\n")
            except OSError as e:
                detail[fname] = f"# read failed: {e}"
        # Open FDs
        fds_dir = proc_root / "fd"
        try:
            fd_lines = []
            for fd in sorted(fds_dir.iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else 0):
                try:
                    target = os.readlink(fd)
                except OSError:
                    target = "<unreadable>"
                fd_lines.append(f"{fd.name} -> {target}")
            detail["open_fds.txt"] = "\n".join(fd_lines) + "\n"
        except OSError as e:
            detail["open_fds.txt"] = f"# fds unavailable: {e}"
    else:
        # macOS / BSD path: use ps + lsof for the same shape.
        try:
            ps = subprocess.run(["ps", "-ww", "-p", str(pid),
                                 "-o", "pid,user,command"],
                                capture_output=True, timeout=_TIMEOUT_SECONDS,
                                check=False)
            if ps.returncode != 0 or not ps.stdout.strip():
                return None, f"ps exit={ps.returncode}"
            detail["ps.txt"] = ps.stdout.decode("utf-8", errors="replace")
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return None, f"ps failed: {e}"
        binary = shutil.which("lsof")
        if binary:
            try:
                lsof = subprocess.run([binary, "-p", str(pid)],
                                      capture_output=True,
                                      timeout=_TIMEOUT_SECONDS, check=False)
                detail["lsof.txt"] = lsof.stdout.decode("utf-8", errors="replace")
            except subprocess.TimeoutExpired:
                detail["lsof.txt"] = "# lsof timed out"
    return detail, None
