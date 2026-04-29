"""SecretsTable — accessor for ``agent_secrets`` (ADR-003X Phase C1).

Audit emission lives in the calling layer (dispatcher / endpoint for
writes, ToolContext.secrets accessor for reads) so the table stays a
pure SQL surface. The audit chain receives secret_set /
secret_revealed / secret_blocked / secret_revoked events with the
(instance_id, name) pair only — never the value.

R4: extracted from registry.py.
"""
from __future__ import annotations

import sqlite3

from forest_soul_forge.registry.tables._helpers import transaction, utc_now_iso


class SecretsTable:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def set_secret(
        self,
        instance_id: str,
        name: str,
        plaintext: str,
        *,
        master_key,  # MasterKey instance from core.secrets
        when: str | None = None,
    ) -> None:
        """Encrypt + persist a secret. Replaces any existing (instance_id, name).

        ``master_key`` is required — the table never reads the env var
        itself. Caller (lifespan) passes the loaded MasterKey through.
        AAD pins (instance_id, name) so a stolen ciphertext cannot be
        re-attached to a different row without the AEAD tag check
        failing.

        Audit emission is the caller's responsibility — this method
        only does the SQL + crypto.
        """
        from forest_soul_forge.core.secrets import encrypt, aad_for
        ciphertext, nonce = encrypt(
            master_key, plaintext, associated=aad_for(instance_id, name)
        )
        with transaction(self._conn):
            self._conn.execute(
                """
                INSERT OR REPLACE INTO agent_secrets
                    (instance_id, name, ciphertext, nonce, created_at, last_revealed_at)
                VALUES (?, ?, ?, ?, ?, NULL);
                """,
                (instance_id, name, ciphertext, nonce, when or utc_now_iso()),
            )

    def get_secret(
        self,
        instance_id: str,
        name: str,
        *,
        master_key,
        when: str | None = None,
    ) -> str:
        """Return the plaintext for ``(instance_id, name)``.

        Raises :class:`UnknownSecretError` if the row doesn't exist.
        Bumps ``last_revealed_at`` so an operator can spot dormant
        secrets that no agent ever reads.

        Audit emission (``secret_revealed``) is the caller's
        responsibility — see ToolContext.secrets accessor.
        """
        from forest_soul_forge.core.secrets import decrypt, aad_for, UnknownSecretError
        row = self._conn.execute(
            "SELECT ciphertext, nonce FROM agent_secrets WHERE instance_id=? AND name=?;",
            (instance_id, name),
        ).fetchone()
        if row is None:
            raise UnknownSecretError(f"no secret {name!r} for {instance_id}")
        plaintext = decrypt(
            master_key,
            row["ciphertext"],
            row["nonce"],
            associated=aad_for(instance_id, name),
        )
        # Touch last_revealed_at so an operator can see dormant secrets.
        with transaction(self._conn):
            self._conn.execute(
                "UPDATE agent_secrets SET last_revealed_at=? WHERE instance_id=? AND name=?;",
                (when or utc_now_iso(), instance_id, name),
            )
        return plaintext

    def list_secret_names(self, instance_id: str) -> list[str]:
        """Return the list of secret names for an agent — values never disclosed."""
        rows = self._conn.execute(
            "SELECT name FROM agent_secrets WHERE instance_id=? ORDER BY name;",
            (instance_id,),
        ).fetchall()
        return [r["name"] for r in rows]

    def delete_secret(self, instance_id: str, name: str) -> bool:
        """Remove a secret. Returns True if a row was deleted, False if it didn't exist.

        Audit emission (``secret_revoked``) is the caller's responsibility.
        """
        with transaction(self._conn):
            cur = self._conn.execute(
                "DELETE FROM agent_secrets WHERE instance_id=? AND name=?;",
                (instance_id, name),
            )
            return cur.rowcount > 0
