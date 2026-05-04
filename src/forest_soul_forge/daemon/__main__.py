"""Daemon entry point for ``python -m forest_soul_forge.daemon``.

ADR-0042 T4 (Burst 101). Two callers need a single-file
launchable entry point:

1. The Tauri desktop shell (``apps/desktop/src/main.rs``) spawns
   ``python -m forest_soul_forge.daemon --port 7423`` as a
   subprocess in dev mode. Without ``__main__.py`` this errors
   out as "No module named forest_soul_forge.daemon.__main__".
2. PyInstaller (``dist/daemon-pyinstaller.spec``) packages this
   file as the binary's entry point. Production Tauri builds
   spawn the bundled binary instead of ``python3``.

Equivalent to the existing ``run.command``'s
``uvicorn forest_soul_forge.daemon.app:app --host 127.0.0.1 --port 7423``,
but plumbed through Python so PyInstaller has a single function
to bundle. ``run.command`` keeps working unchanged for
developers running from source.
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
        help="Bind port (default 7423; matches frontend's API expectation)",
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
