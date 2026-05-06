"""ADR-0052 T5 (B169) — `fsf secret` CLI tests.

Drives the CLI as a function call (no subprocess) so we can:
  - Inspect stdout/stderr via capsys
  - Mock getpass / input for prompts
  - Force FSF_SECRET_STORE=file with FSF_FILE_SECRETS_PATH pointed
    at a tmp file so each test gets isolated state

The CLI talks directly to the resolved SecretStoreProtocol — does
NOT go through the daemon HTTP surface. So testing is fast + has
no daemon dependency.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from forest_soul_forge.cli.main import main as cli_main
from forest_soul_forge.security.secrets.resolver import _reset_cache_for_tests


@pytest.fixture(autouse=True)
def _isolated_secret_store(tmp_path: Path, monkeypatch):
    """Each test gets its own FileStore at a tmp path. Resets the
    resolver cache so a previous test's resolution doesn't leak."""
    monkeypatch.setenv("FSF_SECRET_STORE", "file")
    monkeypatch.setenv("FSF_FILE_SECRETS_PATH", str(tmp_path / "secrets.yaml"))
    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


# ---------------------------------------------------------------------------
# `fsf secret backend`
# ---------------------------------------------------------------------------

def test_backend_shows_active_store(capsys):
    rc = cli_main(["secret", "backend"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "backend: file" in out
    assert "selected via: explicit" in out


def test_backend_shows_platform_default(monkeypatch, capsys):
    """When FSF_SECRET_STORE isn't set, output identifies the
    selection as the platform default."""
    monkeypatch.delenv("FSF_SECRET_STORE", raising=False)
    _reset_cache_for_tests()
    rc = cli_main(["secret", "backend"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "selected via: platform default" in out


# ---------------------------------------------------------------------------
# `fsf secret put`
# ---------------------------------------------------------------------------

def test_put_via_stdin(capsys, monkeypatch):
    """--from-stdin reads from stdin; trailing newline stripped."""
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("sk-abcdef123\n"))
    rc = cli_main(["secret", "put", "openai_key", "--from-stdin"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "stored 'openai_key'" in out
    assert "backend=file" in out


def test_put_via_prompt(capsys, monkeypatch):
    """Without --from-stdin the CLI calls getpass to prompt
    interactively. Test mocks the prompt to avoid a TTY."""
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "ghp_xyz789")
    rc = cli_main(["secret", "put", "github_pat"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "stored 'github_pat'" in out


def test_put_empty_stdin_rejected(capsys, monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    rc = cli_main(["secret", "put", "x", "--from-stdin"])
    assert rc == 4
    err = capsys.readouterr().err
    assert "stdin was empty" in err


def test_put_empty_prompt_rejected(capsys, monkeypatch):
    """Empty prompt response → reject (don't silently store the
    empty string)."""
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "")
    rc = cli_main(["secret", "put", "x"])
    assert rc == 4
    err = capsys.readouterr().err
    assert "empty value rejected" in err


# ---------------------------------------------------------------------------
# `fsf secret get`
# ---------------------------------------------------------------------------

def test_get_unknown_returns_6(capsys):
    rc = cli_main(["secret", "get", "never_set"])
    assert rc == 6
    err = capsys.readouterr().err
    assert "not stored" in err
    assert "fsf secret put" in err     # actionable hint


def test_get_returns_masked_by_default(capsys, monkeypatch):
    """Default output masks the value. Long secrets show first 4 +
    last 4; short secrets are fully masked."""
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "sk-0123456789abcdef")
    cli_main(["secret", "put", "key"])
    capsys.readouterr()

    rc = cli_main(["secret", "get", "key"])
    assert rc == 0
    out = capsys.readouterr().out
    # Expected mask: first 4 ('sk-0') + ellipsis + last 4 ('cdef')
    assert "sk-0" in out
    assert "cdef" in out
    assert "sk-0123456789abcdef" not in out
    assert "(19 chars)" in out
    assert "--reveal to print" in out


def test_get_with_reveal_prints_plaintext(capsys, monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "supersecret-value")
    cli_main(["secret", "put", "key"])
    capsys.readouterr()

    rc = cli_main(["secret", "get", "key", "--reveal"])
    assert rc == 0
    out = capsys.readouterr().out
    # --reveal writes the value with NO trailing newline so piping
    # stays clean. Trim/equals not just contains: ensures no
    # additional formatting.
    assert out == "supersecret-value"


def test_short_secret_fully_masked(capsys, monkeypatch):
    """≤12 chars → full asterisks (don't expose the bookends of a
    short token)."""
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "shortone")
    cli_main(["secret", "put", "k"])
    capsys.readouterr()

    cli_main(["secret", "get", "k"])
    out = capsys.readouterr().out
    assert "********" in out          # 8 asterisks for 'shortone'
    assert "shortone" not in out


# ---------------------------------------------------------------------------
# `fsf secret delete`
# ---------------------------------------------------------------------------

def test_delete_with_yes_skips_prompt(capsys, monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "v1")
    cli_main(["secret", "put", "to_delete"])
    capsys.readouterr()

    rc = cli_main(["secret", "delete", "to_delete", "--yes"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "deleted 'to_delete'" in out

    # Confirm it's actually gone.
    rc = cli_main(["secret", "get", "to_delete"])
    assert rc == 6


def test_delete_aborted_on_no_confirmation(capsys, monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "v1")
    cli_main(["secret", "put", "still_there"])
    capsys.readouterr()

    monkeypatch.setattr("builtins.input", lambda prompt="": "no")
    rc = cli_main(["secret", "delete", "still_there"])
    assert rc == 0      # not an error — explicit operator choice
    out = capsys.readouterr().out
    assert "aborted" in out

    # Still in store.
    rc = cli_main(["secret", "get", "still_there", "--reveal"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out == "v1"


def test_delete_missing_is_idempotent(capsys):
    """Per ADR-0052 contract, delete-of-absent is a no-op."""
    rc = cli_main(["secret", "delete", "never_existed", "--yes"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "deleted" in out


# ---------------------------------------------------------------------------
# `fsf secret list`
# ---------------------------------------------------------------------------

def test_list_empty_shows_helpful_message(capsys):
    rc = cli_main(["secret", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no secrets stored" in out
    assert "fsf secret put" in out


def test_list_prints_names_sorted(capsys, monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "v")
    cli_main(["secret", "put", "zebra"])
    cli_main(["secret", "put", "alpha"])
    cli_main(["secret", "put", "mango"])
    capsys.readouterr()

    rc = cli_main(["secret", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert lines == ["alpha", "mango", "zebra"]
