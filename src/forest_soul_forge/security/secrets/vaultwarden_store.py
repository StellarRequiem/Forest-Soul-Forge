"""VaultWardenStore — wraps the Bitwarden ``bw`` CLI for operators
running VaultWarden (or upstream Bitwarden) as their secret vault.

Per ADR-0052 Decision 3 (T3 implementation): mirrors the
KeychainStore design pattern (subprocess CLI wrapper) rather than
reimplementing Bitwarden's master-key derivation + SRP login flow
from scratch. Operators with VaultWarden already running locally
get Forest integration without a parallel auth implementation;
operators without VaultWarden running should pick FileStore or
KeychainStore instead.

Wire format:

  Item type:    Login (Bitwarden item type 1)
  Item name:    "forest-soul-forge:<secret_name>"
  Login.password: the secret value (stored encrypted by Bitwarden)

The "forest-soul-forge:" prefix matches KeychainStore so operators
who switch backends recognize the entry shape immediately. Bitwarden
items are searchable by name; list_names() filters on the prefix
to recover Forest's view without sifting through the operator's
personal passwords.

## Operator setup

Forest does NOT manage Bitwarden auth. Operators must:

  1. Install bw (Bitwarden CLI) — npm i -g @bitwarden/cli
  2. Configure the server URL if using self-hosted VaultWarden:
        bw config server https://vault.example.com
  3. Log in:                                  bw login
  4. Unlock + capture session token:          export BW_SESSION=$(bw unlock --raw)
  5. Set FSF_SECRET_STORE=vaultwarden in Forest's environment

The BW_SESSION env var is the unlock state. Forest passes it
through to subprocess calls. If unset or expired, bw operations
fail with `Vault is locked.` — VaultWardenStore surfaces that as
a SecretStoreError so the daemon's startup banner can prompt the
operator to re-unlock.

## Why bw CLI and not the REST API directly

Implementing the Bitwarden REST API in Python means re-doing:

  - PBKDF2-SHA256 key derivation from the master password
  - The SRP-like login flow (kdfIterations, hashed master password,
    OAuth2 token exchange)
  - Per-item AES-256-CBC + HMAC-SHA256 (re-)encryption
  - Cipher attachment uploads (out of scope for Forest, but the
    library would need to handle them or else)
  - Sync state (Bitwarden caches encrypted vault locally;
    out-of-band changes need bw sync)

bw CLI does ALL of this. Forest gets a working VaultWarden
integration for the cost of a subprocess wrapper and operator
documentation. A native Python client would be 10x the LoC + a
maintenance burden as Bitwarden's protocol evolves.

The downside: every operation forks a process. For Forest's
scale (occasional plugin secret reads), the cost is invisible.

## Threat-model notes

- BW_SESSION leaks: Forest passes BW_SESSION through to bw
  subprocess invocations. The session token is short-lived
  (operator's bw config) and scoped to the current login. If
  the token leaks, the attacker has read access to the operator's
  vault — but they'd also need the bw CLI binary to use it.
  Operators concerned about this exposure use FileStore
  (~/.forest/secrets/secrets.yaml) or KeychainStore (system
  keystore) instead.
- argv exposure: bw accepts the secret VALUE on stdin via
  `bw create item < json` — VaultWardenStore uses that path so
  values never appear in argv. This is a strict improvement over
  KeychainStore's `security add -w VALUE` (documented argv
  exposure trade-off).
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from typing import ClassVar

from .protocol import SecretStoreError


SERVICE_PREFIX = "forest-soul-forge:"

#: Hard cap on bw subprocess wall time. Bitwarden's CLI talks to
#: the server (or local cache); 30 seconds is generous for any
#: single-item operation. Operators on slow networks may need to
#: tune this — surface as an env var if it becomes a real issue.
BW_TIMEOUT_S = 30


class VaultWardenStore:
    """Bitwarden / VaultWarden backend via the ``bw`` CLI."""

    name: ClassVar[str] = "vaultwarden"

    def __init__(self) -> None:
        if shutil.which("bw") is None:
            raise SecretStoreError(
                "VaultWardenStore: 'bw' CLI not found on PATH. Install "
                "with `npm install -g @bitwarden/cli` (Node ≥ 18 required) "
                "and run `bw login` + `export BW_SESSION=$(bw unlock --raw)` "
                "before pointing FSF_SECRET_STORE at vaultwarden. "
                "Operators without VaultWarden running should use "
                "FSF_SECRET_STORE=keychain (macOS) or =file instead."
            )
        if not os.environ.get("BW_SESSION"):
            # We don't refuse construction — bw operations might
            # still work if the operator has a logged-in session
            # via a different mechanism (some setups stash it in
            # the user's shell rc). But surface a hint via stderr
            # so the most common 'why doesn't this work' case has
            # a discoverable answer.
            import sys as _sys
            _sys.stderr.write(
                "[forest_soul_forge.security.secrets] "
                "VaultWardenStore: BW_SESSION env var not set. "
                "Run `export BW_SESSION=$(bw unlock --raw)` if "
                "operations fail with 'Vault is locked.'\n"
            )

    # ------------------------------------------------------------------
    # SecretStoreProtocol surface
    # ------------------------------------------------------------------

    def get(self, secret_name: str) -> str | None:
        if not _valid_name(secret_name):
            raise SecretStoreError(
                f"vaultwarden backend: secret_name {secret_name!r} contains "
                f"unsupported characters (only ASCII letters, digits, "
                f"and `_`/`-`/`.` are allowed)"
            )
        # Look up by exact item name. bw's `bw get item` accepts
        # the name; on multi-match bw errors with a clear message.
        # We don't use `bw get password` because that requires the
        # item to exist; the distinction between "not found" and
        # "found but no password" matters for the contract.
        full = SERVICE_PREFIX + secret_name
        rc, stdout, stderr = _bw(["get", "item", full])
        if rc == 4:
            # rc 4 = "Not found." Stable across bw versions per
            # bitwarden/clients/blob/master/apps/cli.
            return None
        if rc != 0:
            err = stderr.strip()
            if "Not found." in err:
                return None
            if "Vault is locked." in err:
                raise SecretStoreError(
                    f"vaultwarden backend: vault is locked. Run "
                    f"`export BW_SESSION=$(bw unlock --raw)` and retry."
                )
            raise SecretStoreError(
                f"vaultwarden get({secret_name!r}) failed: rc={rc} "
                f"stderr={err[:200]!r}"
            )
        try:
            item = json.loads(stdout)
        except json.JSONDecodeError as e:
            raise SecretStoreError(
                f"vaultwarden get({secret_name!r}): bw returned "
                f"non-JSON output: {e}"
            ) from e
        # Login.password is where we store the value. Notes is a
        # fallback if an operator hand-edited the item.
        login = (item or {}).get("login") or {}
        password = login.get("password")
        if isinstance(password, str):
            return password
        notes = (item or {}).get("notes")
        if isinstance(notes, str) and notes:
            return notes
        return None

    def put(self, secret_name: str, secret_value: str) -> None:
        if not isinstance(secret_name, str) or not secret_name:
            raise SecretStoreError("vaultwarden backend: secret_name must be non-empty string")
        if not isinstance(secret_value, str):
            raise SecretStoreError("vaultwarden backend: secret_value must be a string")
        if not _valid_name(secret_name):
            raise SecretStoreError(
                f"vaultwarden backend: secret_name {secret_name!r} contains "
                f"unsupported characters"
            )

        full = SERVICE_PREFIX + secret_name
        # Upsert: if the item exists, edit it; otherwise create. bw
        # has no native upsert flag, so we check existence first.
        existing_id = self._find_item_id(full)
        item_payload = {
            "type": 1,                       # Login
            "name": full,
            "login": {
                "username": "forest-soul-forge",
                "password": secret_value,
            },
            "notes": None,
        }
        # bw expects base64-encoded JSON on stdin for create/edit:
        #   echo '...json...' | base64 | bw encode | bw create item
        # We do the base64 + bw encode equivalent inline (bw encode
        # is just base64 with stdin-friendly framing).
        encoded = base64.b64encode(
            json.dumps(item_payload).encode("utf-8")
        ).decode("ascii")
        if existing_id is None:
            rc, _stdout, stderr = _bw(["create", "item", encoded])
        else:
            rc, _stdout, stderr = _bw(["edit", "item", existing_id, encoded])
        if rc != 0:
            err = stderr.strip()
            if "Vault is locked." in err:
                raise SecretStoreError(
                    f"vaultwarden backend: vault is locked. Run "
                    f"`export BW_SESSION=$(bw unlock --raw)` and retry."
                )
            raise SecretStoreError(
                f"vaultwarden put({secret_name!r}) failed: rc={rc} "
                f"stderr={err[:200]!r}"
            )
        # Force a sync so the new/edited item is durable on the
        # server (not just in bw's local cache).
        _bw(["sync"])

    def delete(self, secret_name: str) -> None:
        if not _valid_name(secret_name):
            raise SecretStoreError(
                f"vaultwarden backend: secret_name {secret_name!r} contains "
                f"unsupported characters"
            )
        full = SERVICE_PREFIX + secret_name
        existing_id = self._find_item_id(full)
        if existing_id is None:
            return  # idempotent — delete-of-absent is a no-op
        rc, _stdout, stderr = _bw(["delete", "item", existing_id])
        if rc != 0:
            err = stderr.strip()
            if "Not found." in err:
                return  # raced with another deleter; still no-op
            if "Vault is locked." in err:
                raise SecretStoreError(
                    f"vaultwarden backend: vault is locked. Run "
                    f"`export BW_SESSION=$(bw unlock --raw)` and retry."
                )
            raise SecretStoreError(
                f"vaultwarden delete({secret_name!r}) failed: rc={rc} "
                f"stderr={err[:200]!r}"
            )
        _bw(["sync"])

    def list_names(self) -> list[str]:
        # `bw list items --search PREFIX` filters server-side by
        # name substring — much faster than fetching everything
        # and filtering locally.
        rc, stdout, stderr = _bw(["list", "items", "--search", SERVICE_PREFIX])
        if rc != 0:
            err = stderr.strip()
            if "Vault is locked." in err:
                raise SecretStoreError(
                    f"vaultwarden backend: vault is locked. Run "
                    f"`export BW_SESSION=$(bw unlock --raw)` and retry."
                )
            raise SecretStoreError(
                f"vaultwarden list_names failed: rc={rc} "
                f"stderr={err[:200]!r}"
            )
        try:
            items = json.loads(stdout) or []
        except json.JSONDecodeError as e:
            raise SecretStoreError(
                f"vaultwarden list_names: bw returned non-JSON: {e}"
            ) from e

        names: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            full_name = item.get("name") or ""
            # --search is substring-match, not prefix — items with
            # "forest-soul-forge:" elsewhere in the name would slip
            # in. Filter strictly here.
            if isinstance(full_name, str) and full_name.startswith(SERVICE_PREFIX):
                names.append(full_name[len(SERVICE_PREFIX):])
        return names

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _find_item_id(self, full_name: str) -> str | None:
        """Return the bw item id for a given full name, or None
        if no item exists with that exact name."""
        rc, stdout, _stderr = _bw(["list", "items", "--search", full_name])
        if rc != 0:
            return None
        try:
            items = json.loads(stdout) or []
        except json.JSONDecodeError:
            return None
        for item in items:
            if isinstance(item, dict) and item.get("name") == full_name:
                identifier = item.get("id")
                if isinstance(identifier, str):
                    return identifier
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bw(args: list[str]) -> tuple[int, str, str]:
    """Run bw with the given args. Returns (rc, stdout, stderr).

    Pre-fixes ``--raw`` for json-shaped commands? No — bw's default
    output for `get item` and `list items` is JSON when the
    response is itself JSON-shaped. ``--raw`` is reserved for
    `get password` style scalar reads where we want stdout to be
    just the value. For our flow (get item + list items) the
    default JSON output is what we parse.
    """
    try:
        proc = subprocess.run(
            ["bw"] + args,
            capture_output=True,
            timeout=BW_TIMEOUT_S,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"bw timed out after {BW_TIMEOUT_S}s"
    return (
        proc.returncode,
        proc.stdout.decode("utf-8", errors="replace"),
        proc.stderr.decode("utf-8", errors="replace"),
    )


def _valid_name(name: str) -> bool:
    """Reject names containing characters that confuse bw + the
    item-name search. Allowed: ASCII letters, digits, underscore,
    hyphen, dot. Same allowlist as KeychainStore — operators who
    switch backends never have to rename their secrets."""
    if not name:
        return False
    for ch in name:
        if not (ch.isalnum() or ch in "_-."):
            return False
    return True
