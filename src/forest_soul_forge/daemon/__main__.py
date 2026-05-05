"""Daemon entry point for ``python -m forest_soul_forge.daemon``.

This is the **kernel's** entry point — `python -m
forest_soul_forge.daemon` boots the FastAPI control plane with no
userspace dependencies. Headless installs (kernel-only consumers,
external integrators, second distributions) use this directly.

ADR-0042 T4 (Burst 101) added the entry to support two specific
SoulUX-distribution callers:

1. The Tauri desktop shell (``apps/desktop/src/main.rs``) spawns
   ``python -m forest_soul_forge.daemon --port 7423`` as a
   subprocess in dev mode.
2. PyInstaller (``dist/daemon-pyinstaller.spec``) packages this
   file as the SoulUX binary's entry point.

Both are SoulUX-distribution concerns (ADR-0044), but the entry
point itself is kernel — anyone can ``pip install
forest-soul-forge[daemon] && python -m forest_soul_forge.daemon``
and have a running kernel. See ``docs/runbooks/headless-install.md``
for the headless install runbook.

Equivalent to ``uvicorn forest_soul_forge.daemon.app:app --host
127.0.0.1 --port 7423``, but plumbed through Python so PyInstaller
has a single function to bundle. ``run.command`` (a SoulUX
operator script) keeps working unchanged for developers running
from source.
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="forest-soul-forge-daemon",
        description=(
            "Forest Soul Forge daemon. Equivalent to the uvicorn "
            "command in run.command, packaged for clean -m and "
            "PyInstaller invocation."
        ),
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host (default 127.0.0.1; use 0.0.0.0 for LAN access)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=7423,
        help=(
            "Bind port (default 7423; the SoulUX reference frontend "
            "expects this port, but any consumer can override)"
        ),
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=("critical", "error", "warning", "info", "debug", "trace"),
        help="uvicorn log level (default info)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Auto-reload on source changes (developer only; never in production)",
    )
    args = parser.parse_args(argv)

    # Lazy uvicorn import keeps --help fast and lets PyInstaller
    # see the import as a normal stdlib-style call instead of a
    # top-level dependency it has to resolve at spec-build time.
    import uvicorn

    uvicorn.run(
        "forest_soul_forge.daemon.app:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        reload=args.reload,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
