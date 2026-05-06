"""ADR-0052 T3 (B174) — VaultWardenStore tests.

VaultWardenStore wraps the Bitwarden ``bw`` CLI. Its behavior
depends on `bw` being installed + an unlocked vault — so for CI
+ Linux-sandbox coverage we mock ``shutil.which`` and
``subprocess.run`` against captured bw output shapes.

Live integration tests gated behind a manual skipif fire only on
operator's machine when bw is installed + a session is unlocked.

What's covered (mocked, runs everywhere):
  - Constructor refuses when `bw` isn't on PATH
  - Constructor warns to stderr when BW_SESSION isn't set
  - Name allowlist (alnum + _-.) — same as KeychainStore
  - get() argv shape; rc=4 → None; "Vault is locked." → SecretStoreError
  - get() parses login.password from item JSON; falls back to
    notes; returns None when neither is set
  - put() detects existing item via list+filter; creates when
    absent, edits when present; payload is base64-encoded JSON
    with type=1 (Login) and password field set
  - put() rejects non-string values + empty names + bad name chars
  - delete() finds item id, calls bw delete; idempotent on absent
  - list_names() filters server-side via --search + strips prefix
"""
from __future__ import annotations

import base64
import json
import os
import platform
from unittest import mock

import pytest

from forest_soul_forge.security.secrets import (
    SecretStoreError,
    VaultWardenStore,
)
from forest_soul_forge.security.secrets.vaultwarden_store import (
    SERVICE_PREFIX,
    _valid_name,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _proc(rc: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    m = mock.MagicMock()
    m.returncode = rc
    m.stdout = stdout
    m.stderr = stderr
    return m


@pytest.fixture
def store(monkeypatch):
    """A VaultWardenStore instance with `bw` on PATH (mocked).
    Tests patch subprocess.run individually to control responses."""
    monkeypatch.setattr(
        "forest_soul_forge.security.secrets.vaultwarden_store.shutil.which",
        lambda name: "/usr/local/bin/bw" if name == "bw" else None,
    )
    monkeypatch.setenv("BW_SESSION", "fake-session-token")
    return VaultWardenStore()


# ---------------------------------------------------------------------------
# Constructor — bw availability
# ---------------------------------------------------------------------------

def test_constructor_refuses_when_bw_missing(monkeypatch):
    monkeypatch.setattr(
        "forest_soul_forge.security.secrets.vaultwarden_store.shutil.which",
        lambda name: None,
    )
    with pytest.raises(SecretStoreError) as exc:
        VaultWardenStore()
    msg = str(exc.value).lower()
    assert "bw" in msg
    assert "install" in msg


def test_constructor_warns_when_bw_session_unset(monkeypatch, capsys):
    monkeypatch.setattr(
        "forest_soul_forge.security.secrets.vaultwarden_store.shutil.which",
        lambda name: "/usr/local/bin/bw",
    )
    monkeypatch.delenv("BW_SESSION", raising=False)
    VaultWardenStore()
    err = capsys.readouterr().err
    assert "BW_SESSION" in err
    assert "bw unlock" in err


# ---------------------------------------------------------------------------
# Name allowlist
# ---------------------------------------------------------------------------

def test_valid_name_allowlist():
    """Same allowlist as KeychainStore so operators switching
    backends never have to rename their secrets."""
    assert _valid_name("openai_key")
    assert _valid_name("github-pat")
    assert _valid_name("api.token.v2")
    assert not _valid_name("")
    assert not _valid_name("name with space")
    assert not _valid_name("path/separator")
    assert not _valid_name("inject;cmd")


# ---------------------------------------------------------------------------
# get()
# ---------------------------------------------------------------------------

def test_get_argv_shape_and_password_extraction(store):
    """get() invokes `bw get item <prefix><name>` and extracts the
    login.password field from the JSON response."""
    item = {
        "id": "abc-uuid",
        "name": SERVICE_PREFIX + "openai_key",
        "type": 1,
        "login": {"username": "forest-soul-forge", "password": "sk-secret-value"},
    }
    with mock.patch(
        "subprocess.run",
        return_value=_proc(stdout=json.dumps(item).encode("utf-8")),
    ) as m:
        result = store.get("openai_key")
    assert result == "sk-secret-value"
    args, _kwargs = m.call_args
    argv = args[0]
    assert argv[0] == "bw"
    assert argv[1] == "get"
    assert argv[2] == "item"
    assert argv[3] == SERVICE_PREFIX + "openai_key"


def test_get_returns_none_on_rc_4(store):
    """rc=4 = Bitwarden's `Not found.` exit code. Per the
    SecretStoreProtocol contract, unknown name → None."""
    with mock.patch("subprocess.run", return_value=_proc(rc=4, stderr=b"Not found.")):
        assert store.get("never_set") is None


def test_get_returns_none_on_not_found_in_stderr(store):
    """Some bw versions exit nonzero with `Not found.` in stderr
    instead of rc=4. Tolerate both shapes."""
    with mock.patch("subprocess.run", return_value=_proc(rc=1, stderr=b"Not found.")):
        assert store.get("never_set") is None


def test_get_raises_on_locked_vault(store):
    with mock.patch(
        "subprocess.run",
        return_value=_proc(rc=1, stderr=b"Vault is locked."),
    ):
        with pytest.raises(SecretStoreError) as exc:
            store.get("openai_key")
    msg = str(exc.value)
    assert "vault is locked" in msg.lower()
    assert "bw unlock" in msg


def test_get_falls_back_to_notes_field(store):
    """An operator who hand-edited an item to put the value in
    notes (no login section) still works."""
    item = {
        "name": SERVICE_PREFIX + "x",
        "type": 1,
        "login": {},
        "notes": "fallback-value",
    }
    with mock.patch(
        "subprocess.run",
        return_value=_proc(stdout=json.dumps(item).encode("utf-8")),
    ):
        assert store.get("x") == "fallback-value"


def test_get_returns_none_when_neither_password_nor_notes(store):
    item = {"name": SERVICE_PREFIX + "x", "type": 1, "login": {}, "notes": None}
    with mock.patch(
        "subprocess.run",
        return_value=_proc(stdout=json.dumps(item).encode("utf-8")),
    ):
        assert store.get("x") is None


def test_get_rejects_invalid_name_before_subprocess(store):
    with mock.patch("subprocess.run") as m:
        with pytest.raises(SecretStoreError):
            store.get("$(rm -rf /)")
    assert m.call_count == 0


# ---------------------------------------------------------------------------
# put()
# ---------------------------------------------------------------------------

def test_put_creates_when_absent(store):
    """When _find_item_id returns None (no existing item), put()
    invokes `bw create item <base64-payload>`. Payload includes
    type=1 + login.password."""
    # First subprocess call: list items (find existing) — empty.
    # Second: create item — success.
    # Third: sync — success.
    calls = [
        _proc(stdout=b"[]"),                        # list items, empty
        _proc(),                                    # create item, ok
        _proc(),                                    # sync, ok
    ]
    with mock.patch("subprocess.run", side_effect=calls) as m:
        store.put("openai_key", "sk-secret-xyz")
    # 3 subprocess calls in order list / create / sync.
    assert m.call_count == 3
    create_argv = m.call_args_list[1][0][0]
    assert create_argv[0] == "bw"
    assert create_argv[1] == "create"
    assert create_argv[2] == "item"
    # Payload is base64 — decode and inspect.
    encoded = create_argv[3]
    decoded = json.loads(base64.b64decode(encoded).decode("utf-8"))
    assert decoded["type"] == 1
    assert decoded["name"] == SERVICE_PREFIX + "openai_key"
    assert decoded["login"]["password"] == "sk-secret-xyz"


def test_put_edits_when_present(store):
    """When an existing item is found, put() invokes `bw edit item
    <id> <base64-payload>`."""
    existing = [{"id": "uuid-existing", "name": SERVICE_PREFIX + "openai_key"}]
    calls = [
        _proc(stdout=json.dumps(existing).encode("utf-8")),     # list items
        _proc(),                                                # edit item
        _proc(),                                                # sync
    ]
    with mock.patch("subprocess.run", side_effect=calls) as m:
        store.put("openai_key", "new-value")
    edit_argv = m.call_args_list[1][0][0]
    assert edit_argv[1] == "edit"
    assert edit_argv[3] == "uuid-existing"


def test_put_rejects_non_string_value(store):
    with pytest.raises(SecretStoreError):
        store.put("k", 12345)              # type: ignore[arg-type]


def test_put_rejects_empty_name(store):
    with pytest.raises(SecretStoreError):
        store.put("", "value")


def test_put_rejects_bad_name_chars(store):
    with pytest.raises(SecretStoreError):
        store.put("name with space", "value")


def test_put_raises_on_locked_vault(store):
    """Backend failure surfaces with the 'bw unlock' hint."""
    calls = [
        _proc(stdout=b"[]"),                                       # list, empty
        _proc(rc=1, stderr=b"Vault is locked."),                   # create fails
    ]
    with mock.patch("subprocess.run", side_effect=calls):
        with pytest.raises(SecretStoreError) as exc:
            store.put("k", "v")
    assert "bw unlock" in str(exc.value)


# ---------------------------------------------------------------------------
# delete()
# ---------------------------------------------------------------------------

def test_delete_idempotent_on_absent(store):
    """No existing item → delete is a no-op (no bw delete call)."""
    with mock.patch("subprocess.run", return_value=_proc(stdout=b"[]")) as m:
        store.delete("never_set")
    # Only the find_item_id list-items call; no delete invocation.
    assert m.call_count == 1
    assert m.call_args_list[0][0][0][1] == "list"


def test_delete_invokes_bw_delete_when_present(store):
    existing = [{"id": "uuid-x", "name": SERVICE_PREFIX + "k"}]
    calls = [
        _proc(stdout=json.dumps(existing).encode("utf-8")),
        _proc(),                                                # delete
        _proc(),                                                # sync
    ]
    with mock.patch("subprocess.run", side_effect=calls) as m:
        store.delete("k")
    delete_argv = m.call_args_list[1][0][0]
    assert delete_argv[1] == "delete"
    assert delete_argv[3] == "uuid-x"


def test_delete_idempotent_on_race(store):
    """If bw delete fails with `Not found.` (someone else deleted
    it concurrently), still treat as success — we wanted absent
    and absent is what we have."""
    existing = [{"id": "uuid-x", "name": SERVICE_PREFIX + "k"}]
    calls = [
        _proc(stdout=json.dumps(existing).encode("utf-8")),
        _proc(rc=1, stderr=b"Not found."),
    ]
    with mock.patch("subprocess.run", side_effect=calls):
        store.delete("k")           # must not raise


# ---------------------------------------------------------------------------
# list_names()
# ---------------------------------------------------------------------------

def test_list_names_strips_service_prefix(store):
    items = [
        {"name": SERVICE_PREFIX + "openai_key", "id": "1"},
        {"name": SERVICE_PREFIX + "github-pat", "id": "2"},
        # Defensive: an item whose name contains the prefix
        # mid-string should NOT match (server-side --search is
        # substring; we filter strictly).
        {"name": "user-named-it-forest-soul-forge:other", "id": "3"},
    ]
    with mock.patch(
        "subprocess.run",
        return_value=_proc(stdout=json.dumps(items).encode("utf-8")),
    ):
        names = store.list_names()
    assert sorted(names) == ["github-pat", "openai_key"]


def test_list_names_raises_on_locked_vault(store):
    with mock.patch(
        "subprocess.run",
        return_value=_proc(rc=1, stderr=b"Vault is locked."),
    ):
        with pytest.raises(SecretStoreError):
            store.list_names()


# ---------------------------------------------------------------------------
# Live tests — only run on operator's host where bw is installed +
# logged in. Skipped in CI / Linux sandbox.
# ---------------------------------------------------------------------------

import shutil as _shutil

@pytest.mark.skipif(
    _shutil.which("bw") is None or not os.environ.get("BW_SESSION"),
    reason="bw CLI not available + unlocked; live tests gated to operator's host",
)
class TestVaultWardenStoreLive:
    """Real-vault conformance smoke. Run manually when the operator
    has an unlocked Bitwarden session."""

    SECRET_NAME = "fsf_test_vw_smoke"

    def setup_method(self):
        self.store = VaultWardenStore()
        try:
            self.store.delete(self.SECRET_NAME)
        except Exception:                            # noqa: BLE001
            pass

    def teardown_method(self):
        try:
            self.store.delete(self.SECRET_NAME)
        except Exception:                            # noqa: BLE001
            pass

    def test_put_get_roundtrip(self):
        self.store.put(self.SECRET_NAME, "smoke-value-123")
        assert self.store.get(self.SECRET_NAME) == "smoke-value-123"

    def test_list_includes_added(self):
        self.store.put(self.SECRET_NAME, "x")
        assert self.SECRET_NAME in self.store.list_names()
