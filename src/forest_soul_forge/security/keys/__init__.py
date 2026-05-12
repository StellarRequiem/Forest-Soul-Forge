"""Per-agent keypair storage (ADR-0049).

This package implements the ``AgentKeyStore`` surface that holds
ed25519 private keys, one per born agent, bound to the agent's
identity at birth (per ADR-0049 Decision 1). The public key lives
in ``agents.public_key`` + the soul.md frontmatter and is freely
shareable; the private key never leaves this store.

## Why a wrapper, not a parallel substrate

ADR-0049 Decision 2 specified a fresh ``KeyStore`` Protocol with
three backends (keychain / encrypted_file / memory_only). ADR-0052
already shipped a ``SecretStoreProtocol`` covering the same three
backends (keychain / file / vaultwarden) for plugin secrets. Rather
than duplicate the substrate, ``AgentKeyStore`` wraps any
``SecretStoreProtocol``:

  - The Protocol expects strings; private keys are bytes. We
    base64-encode at the boundary and decode on fetch.
  - Each agent's private key is stored under the secret name
    ``forest_agent_key:<instance_id>``. The prefix makes the
    keystore-vs-plugin-secret namespace visible at-a-glance in
    backends like macOS Keychain that list secrets by name.

The net effect: when the operator picks ``FSF_SECRET_STORE=keychain``
(default on macOS), agent private keys land in the OS Keychain
alongside plugin secrets; switching to ``file`` puts them in
``~/.forest/secrets/secrets.yaml``. The operator gets a single
surface to think about.

## Threat model boundary

The base64 encoding is NOT encryption — it's a transport encoding
so the bytes survive a key-value-of-strings backend. Confidentiality
of the private key is the backend's responsibility (Keychain's
hardware-backed access controls, an encrypted file with
``chmod 600``, etc.). If the operator chose the ``file`` backend in
production, the private keys are exposed at the same level the
plugin secrets are. The ADR-0052 startup banner warns about this.
"""
from __future__ import annotations

from .agent_key_store import (
    AgentKeyNotFoundError,
    AgentKeyStore,
    AgentKeyStoreError,
    resolve_agent_key_store,
)

__all__ = [
    "AgentKeyNotFoundError",
    "AgentKeyStore",
    "AgentKeyStoreError",
    "resolve_agent_key_store",
]
