# ADR-0052 — Pluggable Secrets Storage Backend

**Status:** Proposed (2026-05-06). Closes ADR-0043 follow-up #4
("plugin secrets storage backend"). Userspace-only delivery — uses
existing kernel ABI surfaces (ADR-0043 plugin protocol, ADR-0019
tool dispatch) without modifying any of them.

## Context

ADR-0043 introduced MCP plugins. Plugins declare `required_secrets`
in `plugin.yaml`, and the loader resolves each secret at server-
launch time before setting `FSF_MCP_AUTH` (or per-secret env vars)
on the spawned subprocess. The where-do-secrets-live question was
deferred to follow-up #4. As of B161 it was still open.

The 2026-05-06 decision: **operator picks the backend.** Different
Forest operators have different threat models and infrastructure
preferences:

- A power-user with VaultWarden already running for their
  passwords wants Forest's plugin secrets to live in the same
  vault — one place, one master password
- A casual operator on macOS wants Keychain integration — the
  built-in keystore they already trust for browser passwords +
  SSH keys
- An operator inside a regulated environment may have a dedicated
  HSM-backed vault (HashiCorp Vault, AWS Secrets Manager, 1Password
  Connect, etc.) and wants Forest to plug into it
- An operator running entirely offline + isolated may accept the
  plaintext-file fallback under `~/.forest/secrets/` (chmod 600)
  with an explicit "this is insecure" warning

Hard-coding a single backend forces every operator to adopt the
chosen one or run unprotected. Pluggability matches the existing
optional-substrate pattern from ADR-0042 T5 (signing — opt-in via
`FSF_SIGN_ENABLED`), ADR-0049 (KeyStore Protocol for per-event
signatures — operator picks the key source), ADR-0051 (per-tool
sandbox modes — `FSF_TOOL_SANDBOX={off,strict,permissive}`).

## Decision

Add a **`SecretStoreProtocol`** abstraction in
`src/forest_soul_forge/security/secrets/` and ship 3 reference
implementations. Operator selects the active backend via
`FSF_SECRET_STORE` env var; defaults to `keychain` on macOS,
`file` on Linux. A fourth `module:path.to.Class` form lets
operators load their own implementation without modifying Forest.

### Decision 1 — Userspace-only delivery; substrate stays additive

This work ships as:

- `SecretStoreProtocol` Python ABC in
  `src/forest_soul_forge/security/secrets/protocol.py`
- 3 reference implementations under
  `src/forest_soul_forge/security/secrets/{keychain,vaultwarden,file}.py`
- A resolver `resolve_secret_store()` that reads `FSF_SECRET_STORE`
  + dispatches to the right implementation
- One new env var: `FSF_SECRET_STORE`
- Optional second env var: `FSF_SECRET_STORE_CONFIG_PATH` (for
  vaultwarden's URL + token-file path; for the file backend the
  base path is implicit at `~/.forest/secrets/`)

**ZERO changes to kernel ABI.** The seven v1.0 surfaces (ADR-0044
D3) — tool dispatch, audit chain, plugin manifest, constitution
schema, HTTP API, CLI, schema migrations — all stay unchanged.
Plugin manifests still declare `required_secrets: [name, ...]`;
the loader's secret-resolution path now goes through the protocol
instead of a hard-coded backend.

### Decision 2 — Protocol shape

```python
class SecretStoreProtocol(Protocol):
    """Read/write/delete named secrets. Implementation chooses
    where storage lives; Forest only needs the Protocol surface."""

    name: ClassVar[str]
    """Backend identifier — surfaced in audit-chain
    secret_resolved events so an auditor can see WHICH backend
    served the secret without leaking the value."""

    def get(self, secret_name: str) -> str | None:
        """Return the secret value, or None if not present.
        Raises SecretStoreError on backend failure (network down,
        permission denied, etc.) — distinct from None so the loader
        can decide whether to retry vs fall back to a default."""

    def put(self, secret_name: str, secret_value: str) -> None:
        """Write a secret. Idempotent — overwrites existing.
        Operator-driven (CLI / settings panel); plugins themselves
        never write."""

    def delete(self, secret_name: str) -> None:
        """Remove a secret. Idempotent — deleting an absent name
        is a no-op."""

    def list_names(self) -> list[str]:
        """List all secret names this backend can serve. Used by
        the settings panel to surface "what does Forest have access
        to right now" without exposing values."""
```

The protocol intentionally does NOT include rotation, expiration,
or ACLs. Those are properties of the backend — VaultWarden has
its own rotation flow; macOS Keychain has its own ACL surface.
Forest treats the backend as a black-box keyed-string store.

### Decision 3 — Reference implementations

**`KeychainStore`** (macOS only, default on Darwin)
- Uses `security` CLI: `security add-generic-password -a forest-soul-forge -s <name> -w <value>` for put, `security find-generic-password -a forest-soul-forge -s <name> -w` for get
- Service prefix: `forest-soul-forge:` so an auditor inspecting
  Keychain sees Forest entries grouped together
- Errors when not on Darwin (raises `SecretStoreError`)
- No persistent state outside the system Keychain; portable across
  reboots

**`VaultWardenStore`** (cross-platform; runtime check that vaultwarden
is reachable)
- HTTPS calls to a configured VaultWarden URL with an operator-
  provided API token (read from `FSF_SECRET_STORE_CONFIG_PATH` →
  YAML with `url` + `token`)
- Each Forest secret maps to a VaultWarden item under a dedicated
  collection
- Rotation handled by VaultWarden's UI; Forest reads current values
  on each plugin launch
- The existing VaultWarden Forest agent kit (per genres.yaml
  security_high) can manage rotation autonomously per-policy

**`FileStore`** (cross-platform; default on Linux)
- Plaintext YAML at `~/.forest/secrets/secrets.yaml`
- chmod 600 enforced at write time; Forest refuses to read if
  permissions are looser
- Logs a warning at daemon startup that this backend is INSECURE
  if any other user on the system can compromise the running
  process; recommends KeychainStore (macOS) or VaultWardenStore
  (any platform) for production
- Useful for: CI environments, sandboxed daemons, operators
  bringing up a Forest install before configuring their preferred
  vault

**BYO module-path** (`FSF_SECRET_STORE=module:my_pkg.my_store.MyStore`)
- The resolver imports the dotted path, calls the no-arg
  constructor, asserts the result implements `SecretStoreProtocol`
- Lets operators integrate HashiCorp Vault, AWS Secrets Manager,
  1Password Connect, etc., without modifying Forest source

### Decision 4 — Resolution flow at plugin-launch time

```
plugin_loader detects required_secrets in plugin.yaml
   ↓
resolve_secret_store()  →  SecretStoreProtocol instance
   ↓
for each secret_name in required_secrets:
    value = store.get(secret_name)
    if value is None:
        raise PluginLaunchFailed(
            f"plugin {plugin.name!r} requires secret "
            f"{secret_name!r} but the {store.name} backend "
            f"doesn't have it. Operator must store it via "
            f"`fsf secret put {secret_name}` first."
        )
    env[f"FSF_SECRET_{secret_name.upper()}"] = value
   ↓
audit chain: secret_resolved event {
    plugin_name, secret_name, backend: store.name,
    NO value
}
   ↓
spawn subprocess with augmented env
```

The audit chain captures EVERY secret resolution: which plugin
asked for it, which secret name, which backend served it,
WITHOUT logging the value. That's the visibility surface an
auditor needs without inflating the chain into a credential
dump.

### Decision 5 — CLI surface (`fsf secret`)

A single new subcommand mirrors the protocol:

```
fsf secret put <name>            # prompts for value (no echo)
fsf secret get <name>            # masked print; --reveal for plain
fsf secret delete <name>         # confirmation prompt
fsf secret list                  # names only, no values
fsf secret backend               # shows active store + config
```

CLI uses the SAME protocol as the loader — no special operator-
escalation path. If the active backend fails, the CLI command
fails with the exact error the loader would see at plugin
launch.

### Decision 6 — Audit + observability

Every backend operation emits an audit event:

- `secret_put` — operator wrote a secret (CLI). event_data:
  secret_name, backend, set_by (operator_id). NO value.
- `secret_resolved` — plugin loader pulled a secret. event_data:
  plugin_name, secret_name, backend.
- `secret_delete` — operator removed a secret. event_data:
  secret_name, backend, deleted_by.
- `secret_store_unreachable` — backend failure (network, lock,
  permission). event_data: backend, error_class. Surfaces
  silently-broken vault connections that would otherwise just
  cause plugin launches to fail with cryptic messages.

No new event types beyond these four; all use the existing
event_data shape per ADR-0005.

## Implementation tranches

| # | Tranche | Description | Effort |
|---|---|---|---|
| T1 | Protocol + FileStore | Define `SecretStoreProtocol`, ship `FileStore` reference, wire `resolve_secret_store()` reading FSF_SECRET_STORE. Default to `file` everywhere for v0.1. | 1 burst |
| T2 | KeychainStore | macOS-only impl via `security` CLI. Defaults flip to `keychain` on Darwin. | 0.5 burst |
| T3 | VaultWardenStore | HTTPS client + config-file loader. Uses ADR-0049 KeyStore-style config pattern. | 1 burst |
| T4 | Loader integration | plugin_loader's secret-resolution path goes through the resolver. Audit events emitted. | 0.5 burst |
| T5 | `fsf secret` CLI | put/get/delete/list/backend subcommands. | 0.5 burst |
| T6 | Settings UI | Chat-tab settings panel exposes `secret list` for the bound assistant + a "rotate via vault" link when backend supports it. | 0.5 burst |

Total estimate: 4-5 bursts.

## Consequences

**Positive:**

- Closes ADR-0043 follow-up #4 — plugins can require auth without
  Forest dictating where it lives
- Matches the optional-substrate pattern operators already know
  (ADR-0042/0045/0049/0051)
- BYO module-path means Forest never needs vendor-specific code
  for HashiCorp Vault, AWS Secrets Manager, 1Password Connect —
  operators ship their own integrations
- Audit chain captures every secret operation by name + backend
  without logging values
- CLI mirrors the protocol exactly — no special-case operator
  escalation surface

**Negative:**

- Adds 3 new modules + a CLI subcommand (~600-800 LoC total
  across all tranches)
- Operator with no preference has to learn FSF_SECRET_STORE exists
  before they can use plugins requiring auth. Mitigation: the
  default is FileStore on Linux / KeychainStore on macOS, so the
  out-of-box experience works without configuration.
- Three-backends-plus-BYO surface means a bug in any one backend
  could affect operators differently. Mitigation: a shared
  conformance test suite (test_secret_store_conformance.py) every
  backend must pass.

**Neutral:**

- File-format / wire-format stays per-backend. KeychainStore uses
  Keychain's native format; VaultWardenStore uses VaultWarden's
  REST API; FileStore uses YAML. Forest never normalizes — it
  treats secrets as opaque strings.
- Rotation, expiration, key derivation all delegated to the
  backend. Forest's job is "give me the current value of <name>"
  — anything past that is the backend's problem.

## What this ADR does NOT do

- Does NOT specify any backend's INTERNAL secret format. Each
  backend chooses its own.
- Does NOT mandate that secrets be encrypted at rest. The
  FileStore is plaintext-by-design (with chmod-600 + warning
  banner); operators who need at-rest encryption use Keychain
  or a vault.
- Does NOT define a rotation API. Backends with rotation
  (VaultWarden, HashiCorp Vault) handle it themselves; Forest
  just reads the current value.
- Does NOT change ADR-0043's plugin manifest schema. The
  `required_secrets: [name, ...]` declaration stays as-is.

## References

- ADR-0019 — Tool dispatch + governance pipeline
- ADR-0042 — Optional substrate signing (T5 — opt-in pattern this
  ADR follows)
- ADR-0043 — MCP plugin protocol (this ADR closes follow-up #4)
- ADR-0044 — Kernel/userspace boundary (this ADR is userspace-only)
- ADR-0049 — Per-event signatures KeyStore Protocol (sibling
  pattern: per-environment configurable backend)
- ADR-0051 — Per-tool sandbox modes (sibling pattern: opt-in env
  var with reference implementations)

## Credit

The pluggable framing came from the operator (Alex) in the
2026-05-06 Cowork session: "it should be part in part user choice
so if they want the warden to hold it if they want the macOS
keychain to hold it or if there's another vault that they want to
install themselves." Three explicit defaults + BYO module-path
captures that framing in code.
