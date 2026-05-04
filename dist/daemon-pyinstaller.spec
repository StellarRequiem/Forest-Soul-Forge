# PyInstaller spec for the Forest Soul Forge daemon binary.
#
# ADR-0042 T4 (Burst 101). Produces a single-file binary the
# Tauri desktop shell can bundle as a sidecar resource — eliminates
# the "users must have Python 3.11+ installed" requirement for
# the Tauri-packaged distribution.
#
# Why PyInstaller over PyOxidizer / Nuitka:
#   - PyInstaller: simplest config, well-documented, handles
#     uvicorn's hidden imports + FastAPI's reflection-heavy
#     startup. Single-file output (~50-80MB) is operator-
#     installable and comparable to other Python-bundled apps.
#   - PyOxidizer: smaller binaries (10-30MB) but more complex
#     spec; relies on Rust toolchain; Python stdlib coverage
#     gaps surface as runtime errors.
#   - Nuitka: smallest binaries but slowest build (~10x); compiles
#     Python to C which has stricter import discipline; the
#     daemon's lazy imports + dynamic schema migrations would
#     need refactoring.
#
# Trade-off accepted: ~50-80MB sidecar bundle for v0.5. If the
# Tauri-packaged installer crosses 100MB total, revisit
# PyOxidizer for v0.6+.
#
# Build:
#   cd <repo-root>
#   pip install pyinstaller>=6.0
#   pyinstaller dist/daemon-pyinstaller.spec
#
# Output:
#   dist/build/forest-soul-forge-daemon  (intermediate dir)
#   dist/dist/forest-soul-forge-daemon   (the binary, single file)

# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

# Anchor relative paths to the spec file's directory rather than
# pyinstaller's cwd. The build.command sets cwd; this keeps the
# spec file invocable from anywhere.
SPEC_DIR = Path(SPECPATH).resolve()
REPO_ROOT = SPEC_DIR.parent

# uvicorn + FastAPI rely on dynamic imports that PyInstaller's
# import-graph scanner doesn't always pick up. Hidden imports
# enumerate them explicitly. Add to this list if production runs
# error with ModuleNotFoundError.
hiddenimports = [
    # uvicorn protocols + lifecycle paths
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    # FastAPI's reflection-heavy startup
    "fastapi.openapi",
    "fastapi.openapi.docs",
    "fastapi.openapi.utils",
    # ADR-0027 / 0036 / 0041 dynamic imports inside the daemon
    "forest_soul_forge.daemon.routers",
    "forest_soul_forge.daemon.scheduler.task_types",
    "forest_soul_forge.tools.builtin",
    "forest_soul_forge.skills",
    # YAML loaders
    "yaml.cyaml",
    "yaml.parser",
]

# Bundle config files alongside the binary. The daemon reads
# config/*.yaml at startup; ship them inside the binary so the
# operator doesn't have to install them separately.
datas = [
    (str(REPO_ROOT / "config"), "config"),
    # Skill manifests + soul/constitution templates
    (str(REPO_ROOT / "examples"), "examples"),
]

# Single-file binary. tradeoff: slightly slower startup (~1-2s
# extraction to /tmp on first run) vs. directory-based output
# (faster but uglier distribution). Single-file aligns with the
# "drop the binary into the .app bundle" workflow.
analysis = Analysis(
    [str(REPO_ROOT / "src" / "forest_soul_forge" / "daemon" / "__main__.py")],
    pathex=[str(REPO_ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Test deps don't need to ship in production
        "pytest",
        "pytest_asyncio",
    ],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="forest-soul-forge-daemon",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,         # UPX compression broken on macOS arm64; keep off
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,      # Tauri shell pipes stdout/stderr; needs a real console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,  # Build for current arch; cross-compile via arch-specific spec
    codesign_identity=None,  # T5 / Burst 102-103 wires real signing
    entitlements_file=None,
)
