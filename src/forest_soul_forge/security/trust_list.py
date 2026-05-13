"""ADR-0061 T4 (Burst 247) — trusted-issuers list for passport
verification.

A passport's cryptographic signature is necessary but not
sufficient. The receiving daemon must also **explicitly trust**
the issuer — otherwise an attacker who generates their own
operator master keypair could mint passports the receiver
would otherwise accept on signature-validity alone.

This module loads the trust list at daemon startup. The list is
the union of:

  1. The **local operator master public key** (always trusted —
     a Forge daemon trusts itself; this is the canonical use
     case of "my own agent runs on my own machine"). Resolved
     via :func:`resolve_operator_keypair`.

  2. **Operator-supplied additional pubkeys** loaded from a
     text file at the path in ``FSF_TRUSTED_OPERATOR_KEYS``
     (env var; defaults to ``data/trusted_operators.txt`` if
     unset). One base64 pubkey per line; ``# comment`` lines
     allowed; blank lines ignored. Missing file is non-fatal
     — the trust list just contains only the local operator
     master.

Operators add trust by copying another operator's
``data/operator_pubkey.txt`` content into their own
``trusted_operators.txt`` file (one line per trusted operator).
A future ADR could automate this via signed first-contact;
v1 is "paste the string."
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from forest_soul_forge.security.operator_key import (
    resolve_operator_keypair,
)


# ---- defaults -------------------------------------------------------------

#: Default path the trust loader looks at when
#: ``FSF_TRUSTED_OPERATOR_KEYS`` is unset. Resolved relative to
#: the daemon's working directory (typically repo root) — the
#: same path-resolution pattern as ``data/audit_chain.jsonl``.
DEFAULT_TRUST_LIST_PATH = Path("data/trusted_operators.txt")

ENV_VAR = "FSF_TRUSTED_OPERATOR_KEYS"


# ---- cache ----------------------------------------------------------------

_CACHE_LOCK = threading.RLock()
_CACHED: dict[str, list[str]] = {}


# ---- public API -----------------------------------------------------------


def load_trusted_operator_pubkeys(
    *,
    path: Path | None = None,
    include_local: bool = True,
    force_reload: bool = False,
) -> list[str]:
    """Return the list of trusted operator master public keys
    (base64-encoded raw 32-byte ed25519 strings).

    Order:
      1. The local operator master (when ``include_local=True``,
         the default — a daemon always trusts itself).
      2. Each line in the trust file at ``path`` (defaults to the
         FSF_TRUSTED_OPERATOR_KEYS env var → DEFAULT_TRUST_LIST_PATH).

    Comments (``# foo``) and blank lines in the file are skipped.
    Duplicate entries are deduped (set semantics) while preserving
    the order of first appearance.

    Process-cached. ``force_reload=True`` bypasses the cache (for
    tests + the operator's ``fsf trust reload`` command).
    """
    cache_key = str(path or "<env-default>") + f":local={include_local}"
    if not force_reload:
        with _CACHE_LOCK:
            if cache_key in _CACHED:
                return list(_CACHED[cache_key])

    resolved_path = path or _resolve_default_path()
    pubkeys: list[str] = []
    seen: set[str] = set()

    def _add(pk: str) -> None:
        if pk and pk not in seen:
            seen.add(pk)
            pubkeys.append(pk)

    if include_local:
        try:
            _, local_pub_b64 = resolve_operator_keypair()
            _add(local_pub_b64)
        except Exception:
            # Local keypair resolution shouldn't fail in normal
            # operation, but if it does, the trust list still
            # functions — operator just can't verify passports
            # for their own agents. Surface via diagnostics; the
            # quarantine path handles None gracefully.
            pass

    # File-supplied trusted pubkeys are additive. Missing file
    # is non-fatal — the operator simply hasn't added any
    # external operators yet.
    try:
        text = resolved_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    except Exception:
        text = ""

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        _add(line)

    with _CACHE_LOCK:
        _CACHED[cache_key] = list(pubkeys)
    return pubkeys


def reset_cache() -> None:
    """Clear the trust-list process cache. Tests + operator
    reload commands use this."""
    with _CACHE_LOCK:
        _CACHED.clear()


# ---- internals -----------------------------------------------------------


def _resolve_default_path() -> Path:
    """Resolve FSF_TRUSTED_OPERATOR_KEYS env var or fall back to
    the default."""
    env_value = os.environ.get(ENV_VAR)
    if env_value:
        return Path(env_value).expanduser()
    return DEFAULT_TRUST_LIST_PATH
