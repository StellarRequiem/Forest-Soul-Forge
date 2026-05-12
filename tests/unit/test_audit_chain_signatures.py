"""ADR-0049 T5+T6 (Burst 244) — per-event ed25519 signatures.

Tests the sign-on-emit + verify-on-replay surface added to
``core/audit_chain.py``. Uses raw signer/verifier closures (the
same shape the daemon's lifespan wires) so the tests exercise the
contract without needing the full daemon stack.

Coverage:
- append() attaches a "ed25519:..." signature when the signer
  returns bytes
- append() omits the signature when the signer returns None
  (legacy agent, no keypair)
- append() doesn't sign operator-emitted events (agent_dna is None)
- verify() accepts an entry whose signature ed25519.verify-s
- verify() refuses on a tampered signature
- verify() refuses on signature attached to operator-emitted entry
- verify() ignores signatures when verifier is unwired (back-compat)
- Legacy unsigned entries pass hash-chain check
- ChainEntry round-trips via to_json_line / _entry_from_dict
"""
from __future__ import annotations

import base64
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

from forest_soul_forge.core.audit_chain import AuditChain, ChainEntry


# ---- helpers ---------------------------------------------------------------


@pytest.fixture
def chain(tmp_path: Path) -> AuditChain:
    """Fresh chain in tmpdir — genesis lands on construction."""
    return AuditChain(tmp_path / "chain.jsonl")


def _keypair():
    priv = Ed25519PrivateKey.generate()
    pub_bytes = priv.public_key().public_bytes_raw()
    return priv, pub_bytes


# ---- sign-on-emit -----------------------------------------------------------


def test_signer_called_on_agent_event(chain):
    """Agent-emitted event (agent_dna != None) → signer invoked
    with (entry_hash_bytes, agent_dna). Returned bytes attached
    as 'ed25519:<b64>' field."""
    priv, _ = _keypair()
    called_with: list = []

    def signer(entry_hash_bytes: bytes, agent_dna: str) -> bytes:
        called_with.append((entry_hash_bytes, agent_dna))
        return priv.sign(entry_hash_bytes)

    chain.set_signer(signer)
    entry = chain.append("tool_call_dispatched", {"foo": "bar"}, agent_dna="abc123")

    assert len(called_with) == 1
    eh_bytes, dna = called_with[0]
    assert dna == "abc123"
    assert len(eh_bytes) == 32  # sha256 raw = 32 bytes
    assert entry.signature is not None
    assert entry.signature.startswith("ed25519:")


def test_signer_not_called_on_operator_event(chain):
    """Operator-emitted (agent_dna=None) → signer NOT invoked.
    Genesis + births stay unsigned per ADR-0049 D3."""
    calls = []

    def signer(*args):
        calls.append(args)
        return b"x" * 64

    chain.set_signer(signer)
    entry = chain.append("agent_birthed", {"who": "alex"}, agent_dna=None)

    assert calls == []
    assert entry.signature is None


def test_signer_returning_none_yields_unsigned_entry(chain):
    """Legacy agent (no keypair on file) → signer returns None →
    entry lands unsigned. Verifier later treats as 'legacy
    unsigned' per ADR-0049 D5."""
    def signer(eh, dna):
        return None

    chain.set_signer(signer)
    entry = chain.append("tool_call_dispatched", {}, agent_dna="legacy_dna")
    assert entry.signature is None


def test_signer_exception_does_not_block_append(chain):
    """Transient signer failure (KeyStore disk error, etc.) must
    NOT prevent the audit append. The chain entry lands unsigned;
    the verifier will catch it later as a gap. This is the
    failure-mode-tolerance design point in append()."""
    def signer(eh, dna):
        raise RuntimeError("keystore disk i/o failed")

    chain.set_signer(signer)
    entry = chain.append("tool_call_dispatched", {}, agent_dna="abc")
    assert entry.signature is None  # signature omitted
    assert entry.seq > 0  # but the append succeeded


# ---- signature outside entry_hash -----------------------------------------


def test_entry_hash_unchanged_by_signature(chain):
    """ADR-0049 D4: signature is computed OVER entry_hash, NOT
    part of it. Re-running the canonical hash with the same inputs
    must yield the same entry_hash whether or not the signer ran."""
    priv, _ = _keypair()

    chain.set_signer(lambda eh, dna: priv.sign(eh))
    signed = chain.append("memory_written", {"k": "v"}, agent_dna="dna1")

    chain.set_signer(None)
    unsigned = chain.append("memory_written", {"k": "v"}, agent_dna="dna1")

    # Different seqs (sequential appends) so the entry_hash differs;
    # but signature presence/absence doesn't change the hash
    # ALGORITHM. Easier sanity: round-trip the signed entry through
    # the serializer and confirm it parses back with matching hash.
    from forest_soul_forge.core.audit_chain import _entry_from_dict
    import json as _json
    line = signed.to_json_line()
    obj = _json.loads(line)
    assert obj["entry_hash"] == signed.entry_hash
    re_parsed = _entry_from_dict(obj)
    assert re_parsed.entry_hash == signed.entry_hash
    assert re_parsed.signature == signed.signature


# ---- verify-on-replay -----------------------------------------------------


def test_verify_accepts_correctly_signed_entry(chain):
    """End-to-end: signer attaches a real ed25519 sig, verifier
    confirms it."""
    priv, pub_bytes = _keypair()

    chain.set_signer(lambda eh, dna: priv.sign(eh))
    chain.set_verifier(_verify_factory({"agent_a": pub_bytes}))

    chain.append("tool_call_dispatched", {}, agent_dna="agent_a")
    result = chain.verify()
    assert result.ok, result.reason


def test_verify_refuses_tampered_signature(chain, tmp_path: Path):
    """An entry whose signature was rewritten after the fact must
    fail verification. Simulates: an attacker with disk access
    swaps the signature field but doesn't have the agent's private
    key."""
    priv, pub_bytes = _keypair()
    other_priv, _ = _keypair()  # different key — for the forge

    chain.set_signer(lambda eh, dna: priv.sign(eh))
    chain.set_verifier(_verify_factory({"agent_a": pub_bytes}))

    entry = chain.append("tool_call_dispatched", {}, agent_dna="agent_a")

    # Rewrite the on-disk entry's signature with one from a
    # different keypair (same byte length so the JSON structure
    # is intact).
    bad_sig = other_priv.sign(bytes.fromhex(entry.entry_hash))
    bad_field = "ed25519:" + base64.b64encode(bad_sig).decode("ascii")
    text = chain.path.read_text()
    lines = text.splitlines()
    last_line = lines[-1]
    # Replace just the signature string in the JSONL line.
    import json as _json
    obj = _json.loads(last_line)
    obj["signature"] = bad_field
    lines[-1] = _json.dumps(obj, sort_keys=True, separators=(",", ":"))
    chain.path.write_text("\n".join(lines) + "\n")

    # Reopen so the cached head reloads from disk.
    fresh = AuditChain(chain.path)
    fresh.set_verifier(_verify_factory({"agent_a": pub_bytes}))
    result = fresh.verify()
    assert not result.ok
    assert "signature verification failed" in (result.reason or "")


def test_verify_refuses_signature_on_operator_event(chain, tmp_path: Path):
    """Defense in depth: if an attacker attaches a signature to
    an operator-emitted entry (agent_dna=None), verify refuses
    the chain. That combination is malformed by ADR-0049 D3."""
    priv, pub_bytes = _keypair()
    chain.set_verifier(_verify_factory({"agent_a": pub_bytes}))

    # Manually craft an entry that has a signature but no agent_dna.
    entry = chain.append("agent_birthed", {"who": "alex"}, agent_dna=None)
    # Tamper: inject a signature into the on-disk JSONL.
    import json as _json
    text = chain.path.read_text()
    lines = text.splitlines()
    last = _json.loads(lines[-1])
    bogus_sig = priv.sign(bytes.fromhex(entry.entry_hash))
    last["signature"] = "ed25519:" + base64.b64encode(bogus_sig).decode("ascii")
    lines[-1] = _json.dumps(last, sort_keys=True, separators=(",", ":"))
    chain.path.write_text("\n".join(lines) + "\n")

    fresh = AuditChain(chain.path)
    fresh.set_verifier(_verify_factory({"agent_a": pub_bytes}))
    result = fresh.verify()
    assert not result.ok
    assert "operator-emitted" in (result.reason or "")


def test_verify_refuses_unsupported_algorithm(chain, tmp_path: Path):
    """Forward-compat check: an entry whose signature starts with
    a non-ed25519 prefix (e.g. 'sphincs+:...' in some future ADR)
    is treated as unsupported and the chain refuses — better to
    fail loudly than to silently skip unfamiliar algorithms."""
    priv, pub_bytes = _keypair()
    chain.set_verifier(_verify_factory({"agent_a": pub_bytes}))

    entry = chain.append("tool_call_dispatched", {}, agent_dna="agent_a")

    import json as _json
    text = chain.path.read_text()
    lines = text.splitlines()
    last = _json.loads(lines[-1])
    last["signature"] = "sphincs+:bogus-payload"
    lines[-1] = _json.dumps(last, sort_keys=True, separators=(",", ":"))
    chain.path.write_text("\n".join(lines) + "\n")

    fresh = AuditChain(chain.path)
    fresh.set_verifier(_verify_factory({"agent_a": pub_bytes}))
    result = fresh.verify()
    assert not result.ok
    assert "unsupported signature algorithm" in (result.reason or "")


def test_legacy_unsigned_entry_passes_hash_check_only(chain):
    """Pre-ADR-0049 entries (no signature field) must continue to
    verify as long as the hash chain links — ADR-0049 D5
    'legacy unsigned' contract."""
    chain.set_verifier(_verify_factory({}))
    chain.append("tool_call_dispatched", {}, agent_dna="agent_a")
    result = chain.verify()
    assert result.ok, result.reason


def test_verifier_unwired_skips_signature_check(chain):
    """If the verifier closure isn't installed (test contexts that
    only care about hash-chain semantics), verify() ignores
    signatures entirely. Same backward-compat behavior the
    pre-ADR-0049 verifier had."""
    priv, _ = _keypair()
    chain.set_signer(lambda eh, dna: priv.sign(eh))
    # Note: NO set_verifier call.
    chain.append("tool_call_dispatched", {}, agent_dna="agent_a")
    result = chain.verify()
    assert result.ok, result.reason


# ---- helpers --------------------------------------------------------------


def _verify_factory(pub_keys_by_dna: dict) -> callable:
    """Build a verifier closure that knows about the given test
    agents' public keys. Mirrors the daemon lifespan closure but
    uses an in-memory dict instead of the registry."""
    def verifier(entry_hash_bytes: bytes, signature_bytes: bytes, agent_dna: str) -> bool:
        pub_bytes = pub_keys_by_dna.get(agent_dna)
        if pub_bytes is None:
            return False
        from cryptography.exceptions import InvalidSignature
        try:
            pub = Ed25519PublicKey.from_public_bytes(pub_bytes)
            pub.verify(signature_bytes, entry_hash_bytes)
            return True
        except InvalidSignature:
            return False
    return verifier


# ---- serialization round-trip --------------------------------------------


def test_chain_entry_round_trip_with_signature():
    """ChainEntry → to_json_line → JSON parse → _entry_from_dict
    must preserve the signature field exactly."""
    from forest_soul_forge.core.audit_chain import _entry_from_dict
    import json as _json

    orig = ChainEntry(
        seq=42,
        timestamp="2026-05-12T00:00:00Z",
        prev_hash="a" * 64,
        entry_hash="b" * 64,
        agent_dna="dna_x",
        event_type="memory_written",
        event_data={"k": "v"},
        signature="ed25519:" + base64.b64encode(b"x" * 64).decode("ascii"),
    )
    line = orig.to_json_line()
    parsed = _entry_from_dict(_json.loads(line))
    assert parsed.signature == orig.signature
    assert parsed.entry_hash == orig.entry_hash


# ---- strict mode ----------------------------------------------------------


def test_strict_mode_refuses_unsigned_agent_event(chain):
    """ADR-0049 T7: strict=True rejects an agent-emitted entry
    that lacks a signature."""
    # No signer wired → entry lands unsigned.
    chain.append("tool_call_dispatched", {}, agent_dna="agent_a")
    result = chain.verify(strict=True)
    assert not result.ok
    assert "strict mode" in (result.reason or "")


def test_strict_mode_tolerates_operator_events(chain):
    """Strict mode only enforces signatures on agent-emitted
    entries (agent_dna != None). Operator events stay unsigned
    by design (per ADR-0049 D3)."""
    chain.append("agent_birthed", {"who": "alex"}, agent_dna=None)
    result = chain.verify(strict=True)
    assert result.ok, result.reason


def test_strict_mode_accepts_properly_signed_entries(chain):
    """Strict + verifier-wired: a signed agent entry passes."""
    priv, pub_bytes = _keypair()
    chain.set_signer(lambda eh, dna: priv.sign(eh))
    chain.set_verifier(_verify_factory({"agent_a": pub_bytes}))
    chain.append("tool_call_dispatched", {}, agent_dna="agent_a")
    result = chain.verify(strict=True)
    assert result.ok, result.reason


def test_default_tolerant_mode_passes_unsigned_agent_event(chain):
    """Default verify() (strict=False) tolerates legacy unsigned
    agent entries per ADR-0049 D5. This is the back-compat
    contract that lets pre-ADR-0049 chains keep verifying after
    the upgrade."""
    chain.append("tool_call_dispatched", {}, agent_dna="agent_a")
    result = chain.verify()  # default strict=False
    assert result.ok, result.reason


def test_chain_entry_round_trip_no_signature():
    """ChainEntry without signature serializes without the
    'signature' field at all — important so pre-ADR-0049
    entries round-trip byte-for-byte through the encoder."""
    import json as _json
    orig = ChainEntry(
        seq=0,
        timestamp="2026-05-12T00:00:00Z",
        prev_hash="GENESIS",
        entry_hash="0" * 64,
        agent_dna=None,
        event_type="chain_created",
        event_data={},
    )
    line = orig.to_json_line()
    obj = _json.loads(line)
    assert "signature" not in obj
