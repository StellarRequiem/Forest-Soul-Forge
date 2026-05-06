"""ADR-0052 T2 (B168) — KeychainStore tests.

KeychainStore wraps the macOS ``security`` CLI; the conformance
contract from test_secret_store_conformance.py applies to it the
same as FileStore. But KeychainStore can only be constructed on
Darwin — its __init__ raises SecretStoreError on non-Darwin hosts.

For CI / Linux-sandbox coverage we mock ``platform.system`` and
``subprocess.run`` so the parse paths + argv shape are exercised
without needing real Keychain access. The tests on a real Mac
(when the operator runs them) will exercise the actual security
CLI integration; that path is gated behind a manual skipif and
never fires in CI.
"""
from __future__ import annotations

import platform
from unittest import mock

import pytest

from forest_soul_forge.security.secrets import (
    KeychainStore,
    SecretStoreError,
    SecretStoreProtocol,
)
from forest_soul_forge.security.secrets.keychain_store import (
    ACCOUNT,
    SERVICE_PREFIX,
    _valid_name,
)


# ---------------------------------------------------------------------------
# Constructor — platform gating
# ---------------------------------------------------------------------------

def test_constructor_refuses_non_darwin(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    with pytest.raises(SecretStoreError) as exc:
        KeychainStore()
    assert "macOS-only" in str(exc.value)


def test_constructor_succeeds_on_darwin(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    store = KeychainStore()
    assert isinstance(store, SecretStoreProtocol)
    assert store.name == "keychain"


# ---------------------------------------------------------------------------
# Helper — name validation
# ---------------------------------------------------------------------------

def test_valid_name_allowlist():
    """The allowlist is alnum + _ - . — refuse anything else to
    keep argv parsing predictable + side-step shell escaping."""
    assert _valid_name("openai_key")
    assert _valid_name("github-pat")
    assert _valid_name("api.token.v2")
    assert _valid_name("a")

    assert not _valid_name("")
    assert not _valid_name("name with space")
    assert not _valid_name("dollar$sign")
    assert not _valid_name("forward/slash")
    assert not _valid_name("path\\backslash")
    assert not _valid_name("inject;command")
    assert not _valid_name("backtick`")


# ---------------------------------------------------------------------------
# Mocked subprocess paths — wire-format coverage on Linux
# ---------------------------------------------------------------------------

@pytest.fixture
def darwin_store(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    return KeychainStore()


def _proc(rc: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    """Build a CompletedProcess-like return for subprocess.run."""
    m = mock.MagicMock()
    m.returncode = rc
    m.stdout = stdout
    m.stderr = stderr
    return m


def test_get_constructs_security_argv_correctly(darwin_store):
    """The argv to security must be:
       ['security', 'find-generic-password', '-a', ACCOUNT,
        '-s', SERVICE_PREFIX + name, '-w']
    so the operator can grep the Keychain for prefix-scoped Forest
    entries via Keychain Access."""
    with mock.patch("subprocess.run", return_value=_proc(stdout=b"hello\n")) as m:
        result = darwin_store.get("openai_key")
    assert result == "hello"
    args, _kwargs = m.call_args
    argv = args[0]
    assert argv[0] == "security"
    assert argv[1] == "find-generic-password"
    assert "-a" in argv
    assert ACCOUNT in argv
    assert "-s" in argv
    assert SERVICE_PREFIX + "openai_key" in argv
    assert "-w" in argv


def test_get_returns_none_on_security_44(darwin_store):
    """44 = SecKeychainItemNotFound. Per the conformance contract,
    unknown name → None (not raise)."""
    with mock.patch("subprocess.run", return_value=_proc(rc=44)):
        assert darwin_store.get("never_set") is None


def test_get_raises_on_other_nonzero_rc(darwin_store):
    """Any other nonzero rc (locked keychain, permission denied,
    etc.) → SecretStoreError carrying the captured stderr."""
    with mock.patch("subprocess.run", return_value=_proc(rc=1, stderr=b"locked")):
        with pytest.raises(SecretStoreError) as exc:
            darwin_store.get("openai_key")
    assert "rc=1" in str(exc.value)
    assert "locked" in str(exc.value)


def test_get_strips_trailing_newline(darwin_store):
    """security -w outputs `value\\n`; strip the trailing newline
    so callers get the raw secret."""
    with mock.patch("subprocess.run", return_value=_proc(stdout=b"sk-abcdef\n")):
        assert darwin_store.get("openai_key") == "sk-abcdef"


def test_get_rejects_invalid_name_before_subprocess(darwin_store):
    """A name with a dollar-sign or shell metachar must be rejected
    BEFORE invoking security — defense-in-depth against an
    operator-misconfigured constitution that lets through a
    malicious name from agent input."""
    with mock.patch("subprocess.run") as m:
        with pytest.raises(SecretStoreError) as exc:
            darwin_store.get("$(rm -rf /)")
    assert m.call_count == 0
    assert "unsupported characters" in str(exc.value)


def test_put_constructs_argv_with_upsert_flag(darwin_store):
    """The -U flag MUST be present so put() upserts (overwrites
    existing) rather than failing on a duplicate."""
    with mock.patch("subprocess.run", return_value=_proc()) as m:
        darwin_store.put("openai_key", "sk-abcdef")
    argv = m.call_args[0][0]
    assert argv[0] == "security"
    assert argv[1] == "add-generic-password"
    assert "-U" in argv
    # The value goes on argv after -w. Documented mitigation in
    # keychain_store.py — operators concerned about argv exposure
    # use VaultWardenStore.
    w_idx = argv.index("-w")
    assert argv[w_idx + 1] == "sk-abcdef"


def test_put_raises_on_subprocess_failure(darwin_store):
    with mock.patch("subprocess.run", return_value=_proc(rc=2, stderr=b"weird")):
        with pytest.raises(SecretStoreError) as exc:
            darwin_store.put("openai_key", "x")
    assert "rc=2" in str(exc.value)


def test_put_rejects_non_string_value(darwin_store):
    with pytest.raises(SecretStoreError):
        darwin_store.put("openai_key", 12345)        # type: ignore[arg-type]


def test_put_rejects_empty_name(darwin_store):
    with pytest.raises(SecretStoreError):
        darwin_store.put("", "value")


def test_delete_treats_44_as_noop(darwin_store):
    """Per ADR-0052 §contract, delete-of-absent is a no-op (NOT
    raises). 44 = SecKeychainItemNotFound from security CLI."""
    with mock.patch("subprocess.run", return_value=_proc(rc=44)):
        darwin_store.delete("never_set")             # must not raise


def test_delete_succeeds_on_zero(darwin_store):
    with mock.patch("subprocess.run", return_value=_proc(rc=0)):
        darwin_store.delete("openai_key")


def test_delete_raises_on_other_nonzero(darwin_store):
    with mock.patch("subprocess.run", return_value=_proc(rc=1, stderr=b"locked")):
        with pytest.raises(SecretStoreError):
            darwin_store.delete("openai_key")


def test_list_names_filters_on_service_prefix(darwin_store):
    """dump-keychain output is verbose; we extract just the entries
    whose svce attribute starts with SERVICE_PREFIX. The fixture
    output below includes one Forest entry, one non-Forest entry,
    and one entry with no svce (the <NULL> shape)."""
    dump = (
        b'keychain: "/Users/op/Library/Keychains/login.keychain-db"\n'
        b'class: "genp"\n'
        b'attributes:\n'
        b'    "svce"<blob>="forest-soul-forge:openai_key"\n'
        b'    "acct"<blob>="forest-soul-forge"\n'
        b'class: "genp"\n'
        b'attributes:\n'
        b'    "svce"<blob>="some.other.app"\n'
        b'    "acct"<blob>="anyone"\n'
        b'class: "genp"\n'
        b'attributes:\n'
        b'    "svce"<blob>=<NULL>\n'
        b'class: "genp"\n'
        b'attributes:\n'
        b'    "svce"<blob>="forest-soul-forge:github-pat"\n'
    )
    with mock.patch("subprocess.run", return_value=_proc(stdout=dump)):
        names = darwin_store.list_names()
    assert sorted(names) == ["github-pat", "openai_key"]


def test_list_names_raises_on_dump_failure(darwin_store):
    with mock.patch("subprocess.run", return_value=_proc(rc=1, stderr=b"locked")):
        with pytest.raises(SecretStoreError):
            darwin_store.list_names()


# ---------------------------------------------------------------------------
# Live-on-macOS tests (skipped on Linux sandbox; run only when
# exercised on operator's Mac)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    platform.system() != "Darwin",
    reason="Keychain integration tests run only on the operator's Mac",
)
class TestKeychainStoreLive:
    """Real-Keychain conformance smoke. Run manually on the
    operator's Mac. Each test cleans up after itself."""

    SECRET_NAME = "fsf_test_keychain_smoke"

    def setup_method(self):
        self.store = KeychainStore()
        # Best-effort cleanup of any prior smoke residue.
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

    def test_unknown_returns_none(self):
        assert self.store.get("never_set_smoke_unique_x") is None

    def test_delete_removes(self):
        self.store.put(self.SECRET_NAME, "to-delete")
        self.store.delete(self.SECRET_NAME)
        assert self.store.get(self.SECRET_NAME) is None

    def test_list_includes_added(self):
        self.store.put(self.SECRET_NAME, "x")
        names = self.store.list_names()
        assert self.SECRET_NAME in names
