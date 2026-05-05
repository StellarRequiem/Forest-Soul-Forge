"""Conformance §6 — CLI surface.

Spec: docs/spec/kernel-api-v0.6.md §6.

These tests invoke the ``fsf`` CLI as a subprocess and assert
documented behavior. The CLI is part of the kernel API per spec §6,
so any kernel build must ship a working CLI.

If the CLI isn't installed (the daemon-under-test is a non-Python
build, say), these tests skip cleanly rather than fail.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest


def _have_fsf() -> bool:
    return shutil.which("fsf") is not None


# ----- §6.1 — subcommand tree -------------------------------------------


@pytest.mark.skipif(not _have_fsf(), reason="fsf CLI not on PATH")
def test_section6_fsf_help_succeeds() -> None:
    """§6: ``fsf --help`` exits 0 with a usage banner."""
    result = subprocess.run(
        ["fsf", "--help"], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, (
        f"fsf --help exited {result.returncode}; spec §6.3 says 0 on success.\n"
        f"stdout: {result.stdout[:400]}\nstderr: {result.stderr[:400]}"
    )
    assert "usage:" in result.stdout.lower() or "fsf" in result.stdout.lower()


@pytest.mark.skipif(not _have_fsf(), reason="fsf CLI not on PATH")
@pytest.mark.parametrize("subcommand", ["plugin", "agent", "chronicle"])
def test_section6_subcommands_present(subcommand: str) -> None:
    """§6.1: documented top-level subcommands are present.

    Per spec §6.1 the subcommand tree includes plugin / agent /
    chronicle / forge / install / triune. We probe a representative
    subset; any missing one fails the conformance check.
    """
    result = subprocess.run(
        ["fsf", subcommand, "--help"], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, (
        f"fsf {subcommand} --help exited {result.returncode}; "
        f"spec §6.1 documents this subcommand. "
        f"stderr: {result.stderr[:400]}"
    )


# ----- §6.3 — exit codes -------------------------------------------------


@pytest.mark.skipif(not _have_fsf(), reason="fsf CLI not on PATH")
def test_section6_unknown_subcommand_exits_nonzero() -> None:
    """§6.3: unknown subcommand exits non-zero (Click default 2)."""
    result = subprocess.run(
        ["fsf", "definitely-not-a-real-subcommand"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0, (
        f"fsf with unknown subcommand should exit non-zero per spec §6.3; "
        f"got {result.returncode}"
    )
