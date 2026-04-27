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
        default=Path("registry.sqlite"),
        description="Where the SQLite registry lives. Rebuildable from artifacts.",
    )
    artifacts_dir: Path = Field(
        default=Path("examples"),
        description="Canonical artifacts root (soul.md / constitution.yaml files).",
    )
    audit_chain_path: Path = Field(
        default=Path("examples/audit_chain.jsonl"),
        description="Audit chain JSONL file.",
    )
    # When True, /runtime endpoints can trigger a rebuild-from-artifacts.
    # Off by default to protect registries in production-ish use.
    allow_rebuild_endpoint: bool = Field(default=False)

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
            "Optional shared-secret token required on write endpoints "
            "via the X-FSF-Token header. Unset = writes open (dev)."
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
        description="OpenAI-compatible base URL. Works with gateways (LiteLLM, etc.).",
    )
    frontier_api_key: str | None = None
    frontier_model: str = Field(default="gpt-4o-mini")
    frontier_model_classify: str | None = None
    frontier_model_generate: str | None = None
    frontier_model_safety_check: str | None = None
    frontier_model_conversation: str | None = None
    frontier_model_tool_use: str | None = None
    frontier_timeout_s: float = Field(default=60.0, gt=0)

    # ----- cors ------------------------------------------------------------
    # Allow the local frontend (file:// and localhost) to call the daemon
    # during dev. Tighten for any non-local deployment.
    cors_allow_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173", "null"],
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
