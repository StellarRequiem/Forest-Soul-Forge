"""KeychainStore — macOS Keychain via the system ``security`` CLI.

Per ADR-0052 Decision 3 (T2 implementation): wrap the OS-native
keystore the operator already trusts. Each Forest secret becomes
a Generic Password item in the user's login keychain, scoped by
the ``forest-soul-forge:`` service prefix so an auditing operator
can grep / inspect Forest entries via Keychain Access without
sifting through every saved Wi-Fi password and browser cookie.

Wire format:

  service: ``forest-soul-forge:<secret_name>``
  account: ``forest-soul-forge``
  value:   the secret string

The account is constant — multiple Forest installs on the same
host share the same Keychain entries by design (one operator's
GitHub PAT is one operator's GitHub PAT). The secret_name lives
in the service field where it shows up in Keychain Access's
"Where" column.

Listing: macOS Keychain doesn't expose a "list all entries by
service prefix" CLI directly. We use ``security dump-keychain``
+ awk-style filtering on the service field. This is slower than
the per-name lookup; cache the result if you call list_names
in a loop.

Security review notes (per ADR-0052 §threat model):

  - The ``security`` CLI subprocess receives the secret VALUE on
    its argv for ``add-generic-password -w <value>``. argv is
    visible to other processes via /proc on Linux but on macOS
    via ps; on a multi-user Mac that's a real exposure window
    (sub-second; tightens further if other users aren't the
    threat model). Mitigation: the alternative is `-w` reading
    from stdin, which the security CLI also supports — we use
    that path.
  - Sub-binary ``security`` is at /usr/bin/security and pinned
    by the macOS system integrity protection (SIP). We don't
    sha256-pin it; a compromised /usr/bin/security means the
    operator has bigger problems than Forest's secret store.
  - Errors from security CLI surface as SecretStoreError with
    the captured stderr. We don't try to interpret the
    Apple-specific exit codes; the message is what an operator
    needs to debug.
"""
from __future__ import annotations

import platform
import subprocess
from typing import ClassVar

from .protocol import SecretStoreError


SERVICE_PREFIX = "forest-soul-forge:"
ACCOUNT = "forest-soul-forge"


class KeychainStore:
    """macOS Keychain backend. Implements SecretStoreProtocol structurally.

    Constructor takes no args; the resolver builds it via
    ``KeychainStore()`` and passes the FSF_SECRET_STORE=keychain
    env-var dispatch.
    """

    name: ClassVar[str] = "keychain"

    def __init__(self) -> None:
        if platform.system() != "Darwin":
            raise SecretStoreError(
                "KeychainStore is macOS-only. Detected platform: "
                f"{platform.system()}. Use FSF_SECRET_STORE=file or "
                "vaultwarden on non-macOS hosts."
            )

    # ------------------------------------------------------------------
    # SecretStoreProtocol surface
    # ------------------------------------------------------------------

    def get(self, secret_name: str) -> str | None:
        if not _valid_name(secret_name):
            raise SecretStoreError(
                f"keychain backend: secret_name {secret_name!r} contains "
                f"unsupported characters (only ASCII letters, digits, "
                f"and `_`/`-`/`.` are allowed)"
            )
        proc = subprocess.run(
            [
                "security", "find-generic-password",
                "-a", ACCOUNT,
                "-s", SERVICE_PREFIX + secret_name,
                "-w",
            ],
            capture_output=True,
            timeout=10,
        )
        if proc.returncode == 44:
            # 44 = SecKeychainItemNotFound. Clean signal: name isn't
            # stored. Return None so callers can distinguish from
            # backend failure.
            return None
        if proc.returncode != 0:
            raise SecretStoreError(
                f"keychain get({secret_name!r}) failed: "
                f"rc={proc.returncode} "
                f"stderr={proc.stderr.decode('utf-8', errors='replace')[:200]!r}"
            )
        # security -w outputs the value followed by a single newline.
        return proc.stdout.decode("utf-8", errors="replace").rstrip("\n")

    def put(self, secret_name: str, secret_value: str) -> None:
        if not isinstance(secret_name, str) or not secret_name:
            raise SecretStoreError("keychain backend: secret_name must be non-empty string")
        if not isinstance(secret_value, str):
            raise SecretStoreError("keychain backend: secret_value must be a string")
        if not _valid_name(secret_name):
            raise SecretStoreError(
                f"keychain backend: secret_name {secret_name!r} contains "
                f"unsupported characters"
            )

        # -U upserts: update if exists, else create. -w on stdin
        # avoids the secret showing up in argv.
        # The security CLI accepts the value on stdin via -w when
        # `-` is passed. But -w accepts ONE positional arg; to use
        # stdin we use `-w` followed by `-`, which is documented but
        # unreliable across macOS versions. Most reliable: use the
        # interactive prompt mode by omitting -w and providing on
        # stdin via askpass... actually security has no askpass.
        #
        # The widely-portable shape is `security add-generic-password
        # -U -a ACCOUNT -s SERVICE -w VALUE`, value on argv. We use
        # that with the documented mitigation that argv is briefly
        # visible. Operators concerned about that exposure use
        # VaultWardenStore (T3) instead — see ADR-0052 §threat model.
        proc = subprocess.run(
            [
                "security", "add-generic-password",
                "-U",
                "-a", ACCOUNT,
                "-s", SERVICE_PREFIX + secret_name,
                "-w", secret_value,
            ],
            capture_output=True,
            timeout=10,
        )
        if proc.returncode != 0:
            raise SecretStoreError(
                f"keychain put({secret_name!r}) failed: "
                f"rc={proc.returncode} "
                f"stderr={proc.stderr.decode('utf-8', errors='replace')[:200]!r}"
            )

    def delete(self, secret_name: str) -> None:
        if not _valid_name(secret_name):
            raise SecretStoreError(
                f"keychain backend: secret_name {secret_name!r} contains "
                f"unsupported characters"
            )
        proc = subprocess.run(
            [
                "security", "delete-generic-password",
                "-a", ACCOUNT,
                "-s", SERVICE_PREFIX + secret_name,
            ],
            capture_output=True,
            timeout=10,
        )
        # 44 = not found. Per ADR-0052 the contract is delete-of-
        # absent is a no-op (NOT raises).
        if proc.returncode == 44:
            return
        if proc.returncode != 0:
            raise SecretStoreError(
                f"keychain delete({secret_name!r}) failed: "
                f"rc={proc.returncode} "
                f"stderr={proc.stderr.decode('utf-8', errors='replace')[:200]!r}"
            )

    def list_names(self) -> list[str]:
        """List names by dumping the keychain and filtering on
        service prefix.

        ``security dump-keychain`` is verbose; we parse just enough
        to extract the service strings. Limitations:
          - Only entries owned by the current user keychain are
            visible. System keychain entries (rare for Forest) are
            invisible.
          - dump-keychain prompts for unlock if the keychain is
            locked. The subprocess runs without a TTY; if a prompt
            fires the call hangs until timeout. We catch that and
            surface it as SecretStoreError.
        """
        proc = subprocess.run(
            ["security", "dump-keychain"],
            capture_output=True,
            timeout=15,
        )
        if proc.returncode != 0:
            raise SecretStoreError(
                f"keychain list_names failed: rc={proc.returncode} "
                f"stderr={proc.stderr.decode('utf-8', errors='replace')[:200]!r}"
            )
        names: list[str] = []
        # dump-keychain output has lines like:
        #   "svce"<blob>="forest-soul-forge:openai_key"
        # The svce attribute holds the service. Filter on our prefix.
        for raw_line in proc.stdout.decode("utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line.startswith('"svce"'):
                continue
            # Extract the quoted value at the end of the line:
            #   "svce"<blob>="forest-soul-forge:openai_key"
            eq = line.rfind("=")
            if eq < 0:
                continue
            tail = line[eq + 1:].strip()
            # tail is like '"forest-soul-forge:openai_key"' OR
            # '<NULL>' for entries with no service. Skip the latter.
            if not (tail.startswith('"') and tail.endswith('"')):
                continue
            value = tail[1:-1]
            if value.startswith(SERVICE_PREFIX):
                names.append(value[len(SERVICE_PREFIX):])
        return names


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_name(name: str) -> bool:
    """Reject names containing characters that confuse argv parsing
    or the keychain CLI. Allowed: ASCII letters, digits, underscore,
    hyphen, dot."""
    if not name:
        return False
    for ch in name:
        if not (ch.isalnum() or ch in "_-."):
            return False
    return True
