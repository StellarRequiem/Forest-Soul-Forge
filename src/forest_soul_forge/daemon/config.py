"""Daemon configuration — pydantic-settings loaded from env.

Every setting has a ``FSF_``-prefixed env var. Defaults are chosen so a
fresh clone of the repo with no env vars set will come up on
``localhost:7423``, bind the registry to ``./registry.sqlite``, and
default to the local provider pointed at Ollama's default port.

Change any of these without editing code by exporting the env var.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from forest_soul_forge.daemon.providers import TaskKind


# Default local model — a solid consumer-hardware workhorse. Every
# task_kind defaults to the same tag for simplicity; set
# ``FSF_LOCAL_MODEL_CLASSIFY`` etc. to override per-task.
_DEFAULT_LOCAL_MODEL = "llama3.1:8b"


class DaemonSettings(BaseSettings):
    """Runtime knobs for the FSF daemon.

    Prefer env vars over hard-coding in tests — pass a ``DaemonSettings``
    instance directly into ``build_app`` to override.
    """

    model_config = SettingsConfigDict(
        env_prefix="FSF_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ----- server ----------------------------------------------------------
    host: str = Field(default="127.0.0.1", description="Bind host. Do NOT set 0.0.0.0 casually — local-first.")
    port: int = Field(default=7423, ge=1, le=65535, description="Bind port.")

    # ----- registry / artifacts -------------------------------------------
    registry_db_path: Path = Field(
        default=Path("data/registry.sqlite"),
        description=(
            "Where the SQLite registry lives. Rebuildable from artifacts. "
            "Default lives under data/ (gitignored) so a casual daemon "
            "launch doesn't leave a registry.sqlite stray at repo root. "
            "Phase E audit (2026-04-30) moved this from 'registry.sqlite' "
            "→ 'data/registry.sqlite'."
        ),
    )
    artifacts_dir: Path = Field(
        default=Path("examples"),
        description="Canonical artifacts root (soul.md / constitution.yaml files).",
    )
    audit_chain_path: Path = Field(
        default=Path("examples/audit_chain.jsonl"),
        description="Audit chain JSONL file.",
    )
    scheduled_tasks_path: Path = Field(
        default=Path("config/scheduled_tasks.yaml"),
        description=(
            "ADR-0041 set-and-forget orchestrator config. Optional — if "
            "absent the scheduler runs with zero tasks (no-op heartbeat). "
            "Override via FSF_SCHEDULED_TASKS_PATH."
        ),
    )
    # When True, /runtime endpoints can trigger a rebuild-from-artifacts.
    # Off by default to protect registries in production-ish use.
    allow_rebuild_endpoint: bool = Field(default=False)

    # ----- ADR-0054 T6 — procedural-shortcut substrate (opt-in) ---------
    # When enabled, the dispatcher's ProceduralShortcutStep matches
    # llm_think dispatches against stored situation→action shortcuts
    # in the memory_procedural_shortcuts table. On a high-confidence
    # match, the dispatcher substitutes the recorded response without
    # firing the LLM — sub-100ms response instead of multi-second
    # round-trip. Default OFF; operators opt in via
    # FSF_PROCEDURAL_SHORTCUT_ENABLED=1 + restart.
    #
    # Per ADR-0054 D1 + ADR-0001 D2: shortcuts are per-instance state
    # (not identity). constitution_hash + DNA stay constant across
    # shortcut growth. Operators can rebuild the table freely without
    # touching agent identity.
    procedural_shortcut_enabled: bool = Field(
        default=False,
        description=(
            "Master switch for ADR-0054 procedural-shortcut "
            "substrate. Default OFF. Operator opts in via "
            "FSF_PROCEDURAL_SHORTCUT_ENABLED=1 + daemon restart."
        ),
    )
    procedural_cosine_floor: float = Field(
        default=0.92,
        ge=0.0, le=1.0,
        description=(
            "Cosine-similarity threshold for shortcut match. "
            "Lower = more matches, lower confidence. Default 0.92 "
            "per ADR-0054 D2. Tune down to ~0.85 for noisier "
            "operator phrasing; tune up to 0.95+ for high-stakes "
            "assistants where false-shortcut hits would cause "
            "harm."
        ),
    )
    procedural_reinforcement_floor: int = Field(
        default=2,
        ge=0,
        description=(
            "Minimum (success_count - failure_count) before a "
            "shortcut row participates in the search. Default 2: "
            "operator must thumbs-up at least twice (or once "
            "without thumbs-down) before the shortcut auto-fires. "
            "Set to 0 to allow brand-new shortcuts to fire "
            "immediately (operator-tagged via memory_tag_outcome.v1)."
        ),
    )
    procedural_embed_model: str = Field(
        default="nomic-embed-text:latest",
        description=(
            "Embedding model name for situation vectorization. "
            "Default nomic-embed-text:latest matches the standing "
            "Forest baseline. 768-dim float32. Override per-deploy "
            "if Ollama hosts a different embedding model."
        ),
    )

    # ----- ADR-0056 experimenter workspace -------------------------------
    # Smith's branch-isolated work tree. birth-smith.command provisions
    # this clone at ~/.fsf/experimenter-workspace/Forest-Soul-Forge/ and
    # creates the experimenter/cycle-1 branch. The cycles router (E4)
    # reads from here to surface cycle reports + diffs in the
    # display-mode chat pane.
    #
    # Set to None when the experimenter substrate isn't wired (test
    # contexts, headless deployments without Smith). The cycles
    # router treats None as "no cycles available" and returns an
    # empty list rather than crashing.
    experimenter_workspace_path: Path | None = Field(
        default=Path.home() / ".fsf/experimenter-workspace/Forest-Soul-Forge",
        description=(
            "Path to Smith's branch-isolated workspace clone. "
            "birth-smith.command provisions this. "
            "Override via FSF_EXPERIMENTER_WORKSPACE_PATH."
        ),
    )

    # ----- write path (birth / spawn / archive) ---------------------------
    # Trait tree and constitution template roots. When /birth or /spawn is
    # called, the daemon loads these lazily on lifespan startup and holds
    # them on app.state so each request reuses one engine instance.
    trait_tree_path: Path = Field(
        default=Path("config/trait_tree.yaml"),
        description="TraitEngine YAML source (roles, domains, traits, flags).",
    )
    constitution_templates_path: Path = Field(
        default=Path("config/constitution_templates.yaml"),
        description="Constitution template YAML (single file, role-keyed).",
    )
    tool_catalog_path: Path = Field(
        default=Path("config/tool_catalog.yaml"),
        description="Tool catalog YAML (ADR-0018) — declarative tool descriptors + per-archetype standard kits.",
    )
    genres_path: Path = Field(
        default=Path("config/genres.yaml"),
        description=(
            "Role genres YAML (ADR-0021) — claims each role for a genre and "
            "carries the genre's risk floor, trait emphasis, memory pattern, "
            "and spawn-compatibility table. Loaded best-effort at lifespan; "
            "missing or malformed file degrades to empty engine and the "
            "daemon stays up (births get genre=None)."
        ),
    )
    soul_output_dir: Path = Field(
        default=Path("soul_generated"),
        description=(
            "Where the daemon writes newly generated soul.md and "
            "constitution.yaml pairs. Distinct from artifacts_dir so "
            "production output doesn't overwrite committed examples."
        ),
    )
    skill_install_dir: Path = Field(
        default=Path("data/forge/skills/installed"),
        description=(
            "Where the skills/run endpoint looks for installed skill "
            "manifests (ADR-0031 T2b ad-hoc loader; T5 introduces a "
            "registry-backed catalog). One YAML per skill named "
            "<name>.v<version>.yaml. Operators move staged manifests "
            "from data/forge/skills/staged/ into here once reviewed."
        ),
    )
    plugins_dir: Path = Field(
        default=Path("data/plugins"),
        description=(
            "Where the daemon looks for installed tool plugins at "
            "lifespan (ADR-0019 T5). One subdirectory per plugin "
            "named <name>.v<version>/ containing spec.yaml + tool.py. "
            "Loaded after built-in tools so a plugin can NOT shadow "
            "a built-in (registry registration would raise duplicate). "
            "POST /tools/reload re-walks this dir without a restart."
        ),
    )
    # Allow the write endpoints. Off by default so a misconfigured instance
    # can't accept birth requests. Ops flips this on once the artifact dir
    # is writable and the trait tree is present.
    allow_write_endpoints: bool = Field(default=True)

    # ----- auth ------------------------------------------------------------
    # Shared-secret bearer token. When None (default), auth is bypassed —
    # preserves the frictionless local-dev experience. When set, every
    # mutating endpoint requires the ``X-FSF-Token`` header to match.
    # Reads stay open; rotating the secret only affects writes + provider
    # switch. Threat model in ADR-0007: token protects an unattended
    # Docker deployment from casual access on a shared LAN — it is NOT a
    # substitute for TLS, and the daemon refuses to bind 0.0.0.0 by
    # default for a reason.
    api_token: str | None = Field(
        default=None,
        description=(
            "Shared-secret token required on write endpoints via the "
            "X-FSF-Token header. As of B148 (T25 security hardening), "
            "if this is unset the daemon AUTO-GENERATES a token on "
            "first boot, writes it to .env, and uses it. Operators who "
            "want to opt out of auth (e.g., dev-only loopback) must "
            "explicitly set FSF_INSECURE_NO_TOKEN=true."
        ),
    )
    insecure_no_token: bool = Field(
        default=False,
        description=(
            "B148 (T25 security hardening): explicit opt-out of API "
            "token auth. Default false → if api_token is also unset, "
            "the daemon auto-generates one on first boot and writes "
            "to .env. Set to true to keep writes open (e.g., for "
            "frictionless dev loopback). Same shape as "
            "FSF_ENABLE_PRIV_CLIENT (default off, explicit opt-in/out)."
        ),
    )

    # ----- active provider -------------------------------------------------
    # Default is "local" by mission (ADR-0008). Changing this default is a
    # policy decision, not a convenience tweak.
    default_provider: str = Field(default="local")

    # ----- local provider --------------------------------------------------
    local_base_url: str = Field(
        default="http://127.0.0.1:11434",
        description="Ollama-compatible HTTP endpoint. Works with LM Studio / llama.cpp server too.",
    )
    local_model: str = Field(
        default=_DEFAULT_LOCAL_MODEL,
        description="Default model tag for all task_kinds unless overridden below.",
    )
    local_model_classify: str | None = None
    local_model_generate: str | None = None
    local_model_safety_check: str | None = None
    local_model_conversation: str | None = None
    local_model_tool_use: str | None = None
    local_timeout_s: float = Field(default=60.0, gt=0)

    # ----- narrative voice (ADR-0017) -------------------------------------
    # Per-birth global default for whether to invoke the active provider to
    # write the `## Voice` section in soul.md. BirthRequest.enrich_narrative
    # overrides per-request; this is the fallback when the field is None.
    enrich_narrative_default: bool = Field(default=True)
    # task_kind passed to provider.complete() when rendering the Voice
    # section. Operators routing narrative voice through their conversation-
    # tuned model set this to "conversation" without code changes — the
    # model behind each task_kind is independently configurable via
    # FSF_LOCAL_MODEL_<KIND> / FSF_FRONTIER_MODEL_<KIND> already.
    narrative_task_kind: str = Field(
        default="generate",
        description=(
            "Task kind label for narrative generation. Must parse to a "
            "TaskKind value (classify|generate|safety_check|conversation|tool_use)."
        ),
    )
    narrative_max_tokens: int = Field(default=400, ge=1, le=8192)
    # When set, passed through as temperature=... to provider.complete().
    # Unset → provider default (Ollama or upstream-side decides).
    narrative_temperature: float | None = Field(default=None, ge=0.0, le=2.0)

    # ----- frontier provider (opt-in) -------------------------------------
    frontier_enabled: bool = Field(default=False)
    frontier_base_url: str = Field(
        default="https://api.openai.com",
        description=(
            "OpenAI-compatible base URL. Works with OpenAI itself, "
            "Anthropic via their /v1/chat/completions compatibility "
            "endpoint, xAI, gateways (LiteLLM), etc. For Anthropic "
            "set this to 'https://api.anthropic.com/v1'."
        ),
    )
    frontier_api_key: str | None = None
    # B185 (ADR-0052 follow-up): when frontier_api_key is unset,
    # _build_provider_registry falls back to reading this secret name
    # from the resolved SecretStoreProtocol (keychain by default on
    # macOS). Lets operators paste the key once via `fsf secret put`
    # rather than smuggling it through the launchd plist as plaintext.
    # Override per-deployment if you call your key something different
    # in the store (e.g. 'openai_api_key' for OpenAI deployments).
    frontier_api_key_secret_name: str = Field(
        default="anthropic_api_key",
        description=(
            "Name to look up in the secrets store when "
            "FSF_FRONTIER_API_KEY is unset. Default 'anthropic_api_key'."
        ),
    )
    frontier_model: str = Field(default="gpt-4o-mini")
    frontier_model_classify: str | None = None
    frontier_model_generate: str | None = None
    frontier_model_safety_check: str | None = None
    frontier_model_conversation: str | None = None
    frontier_model_tool_use: str | None = None
    frontier_timeout_s: float = Field(default=60.0, gt=0)

    # ----- per-agent encrypted secrets (ADR-003X Phase C1) ---------------
    # Master key for the agent_secrets table. 32-byte base64-encoded
    # value (urlsafe alphabet OK; padding optional). When unset, the
    # secrets subsystem is DISABLED — get_secret/set_secret raise
    # SecretsUnavailableError. The daemon stays up; defensive plane
    # unaffected. When set, the daemon loads it once at lifespan and
    # holds it in process memory; never written to disk.
    #
    # Generate one with:
    #   .venv/bin/python -c "from forest_soul_forge.core.secrets \
    #     import generate_master_key_b64; print(generate_master_key_b64())"
    secrets_master_key: str | None = Field(
        default=None,
        description=(
            "32-byte base64-encoded AES-256 key for the agent_secrets "
            "table. Unset = secrets subsystem disabled (open-web tools "
            "that need a secret refuse cleanly)."
        ),
    )

    # ----- privileged operations (ADR-0033 A6 + B3) ----------------------
    # Off by default so a fresh clone boots cleanly without the sudo
    # helper installed. Operators flip this on AFTER running
    # docs/runbooks/sudo-helper-install.md. When False, isolate_process.v1,
    # dynamic_policy.v1, and tamper_detect.v1's SIP path all refuse
    # cleanly with "no PrivClient wired" — the daemon stays up, those
    # specific tools degrade. When True, the lifespan calls
    # PrivClient.assert_available() and raises a startup_diagnostic on
    # failure but DOES NOT abort boot — read-only tools keep working.
    enable_priv_client: bool = Field(default=False)
    priv_helper_path: str = Field(
        default="/usr/local/sbin/fsf-priv",
        description=(
            "Absolute path to the fsf-priv sudo helper. Override for "
            "test contexts that point at a mock helper."
        ),
    )

    # ----- cors ------------------------------------------------------------
    # Default allowlist serves the SoulUX reference frontend (port 5173) +
    # local file:// loads. Per ADR-0044 the kernel runs without a frontend
    # — headless installs that don't need browser access can override to
    # ``[]`` via ``FSF_CORS_ALLOW_ORIGINS=""``. Tighten for any non-local
    # deployment regardless of distribution.
    cors_allow_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173", "null"],
    )

    # ----- marketplace (ADR-0055) ------------------------------------------
    # The kernel exposes GET /marketplace/index that aggregates configured
    # registries, returns a merged plugin index, caches per TTL.
    #
    # Default is empty — operators opt in by pinning at least one
    # registry URL. The first official registry will be the
    # forest-marketplace sibling repo's raw GitHub URL once the
    # operator publishes it. Until then, operators can pin a local
    # file:// URL pointing at their local clone of forest-marketplace's
    # registry/marketplace.yaml.
    #
    # Each entry in the list is a registry index URL. Supported schemes:
    #   file:///absolute/path/to/marketplace.yaml
    #   https://example.com/marketplace.yaml
    #
    # Override via FSF_MARKETPLACE_REGISTRIES (comma- or
    # newline-separated; pydantic-settings parses list[str] from the
    # env var).
    marketplace_registries: list[str] = Field(
        default_factory=list,
        description=(
            "Pinned marketplace registry URLs (file:// or https://). "
            "Empty = marketplace browse pane shows 'no registries "
            "configured'; operator opts in by pointing at their "
            "forest-marketplace clone or the official registry URL."
        ),
    )

    # M6 forward-compat (signing). Per-registry trusted ed25519 keys
    # for verifying manifest_signature on each entry. Empty list at
    # M1-M5 means signing isn't enforced — entries surface with the
    # 'untrusted' badge but install is still permitted with operator
    # confirmation. M6 will refuse install on bad-signature when at
    # least one trusted key is configured.
    marketplace_trusted_keys: list[str] = Field(
        default_factory=list,
        description=(
            "Base64-encoded ed25519 public keys trusted to sign "
            "marketplace registry entries. Empty = signing not "
            "enforced (M6 lifts this)."
        ),
    )

    # Cache TTL for the merged registry index. Default 1 hour balances
    # freshness for active marketplace browsing against unnecessary
    # network chatter for an idle daemon. The /marketplace/index
    # endpoint serves the cached value within the TTL window; on
    # expiry the next call re-fetches synchronously (the response
    # surfaces an updated fetched_at timestamp). Operators pinning
    # local file:// registries can crank this up arbitrarily — the
    # filesystem read is cheap, but caching avoids redundant YAML
    # parses on rapid refreshes.
    marketplace_cache_ttl_s: int = Field(
        default=3600,
        ge=0,
        description=(
            "TTL for the cached merged registry index in seconds. "
            "0 = no caching (always fetch). Operators with local "
            "file:// registries can set this high; remote https:// "
            "registries should respect maintainer rate limits."
        ),
    )

    # ----- derived maps ----------------------------------------------------
    def local_model_map(self) -> dict[TaskKind, str]:
        """Return the task_kind → model tag map for the local provider."""
        return {
            TaskKind.CLASSIFY: self.local_model_classify or self.local_model,
            TaskKind.GENERATE: self.local_model_generate or self.local_model,
            TaskKind.SAFETY_CHECK: self.local_model_safety_check or self.local_model,
            TaskKind.CONVERSATION: self.local_model_conversation or self.local_model,
            TaskKind.TOOL_USE: self.local_model_tool_use or self.local_model,
        }

    def frontier_model_map(self) -> dict[TaskKind, str]:
        return {
            TaskKind.CLASSIFY: self.frontier_model_classify or self.frontier_model,
            TaskKind.GENERATE: self.frontier_model_generate or self.frontier_model,
            TaskKind.SAFETY_CHECK: self.frontier_model_safety_check or self.frontier_model,
            TaskKind.CONVERSATION: self.frontier_model_conversation or self.frontier_model,
            TaskKind.TOOL_USE: self.frontier_model_tool_use or self.frontier_model,
        }


def build_settings(**overrides: Any) -> DaemonSettings:
    """Small helper so tests can pass kwargs without env-var gymnastics."""
    return DaemonSettings(**overrides)
