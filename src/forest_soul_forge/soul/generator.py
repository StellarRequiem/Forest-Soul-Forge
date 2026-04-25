"""Soul generator — converts a TraitProfile into a self-verifying soul.md.

Output anatomy:
    1. YAML frontmatter:   identity (dna, role, agent name), lineage chain,
                           schema version, full trait_values dict. Anyone can
                           re-hash the trait block and compare to `dna` to verify.
    2. Header & intro:     human-readable role + DNA short form.
    3. Voice (optional):   LLM-generated paragraphs in the agent's voice
                           (per ADR-0017). Emitted only when a ``voice``
                           argument is supplied; absent on legacy callers.
                           Templated fallback when the active provider was
                           unavailable at birth — provenance recorded in
                           the ``_template_voice`` block.
    4. Domain sections:    ordered by effective weight (dominant first).
    5. Core rules:         non-negotiable constraints shared by all v0.1 agents.
    6. Profile warnings:   flagged combinations, if any.
    7. Lineage footer:     ancestor DNA chain when the agent was spawned by
                           another agent. Absent for root agents.

Nothing here calls an LLM directly — when ``voice`` is supplied, the
caller (typically the daemon's writes router) has already invoked the
provider and produced a :class:`VoiceText`. Soul generation itself stays
sync and deterministic given its inputs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from forest_soul_forge.core.trait_engine import (
    Trait,
    TraitEngine,
    TraitProfile,
)
from forest_soul_forge.core.dna import Lineage, dna_full, dna_short

if TYPE_CHECKING:
    from forest_soul_forge.soul.voice_renderer import VoiceText

# Trait value bands. These label the qualitative intensity that shows up in prose.
BANDS: list[tuple[int, str]] = [
    (20, "very low"),
    (40, "low"),
    (60, "moderate"),
    (80, "fairly high"),
    (101, "very high"),  # 101 so value 100 is included in this band
]

# Tertiary traits below this threshold are skipped entirely in the prose — they
# don't contribute enough signal to earn a line. Primary/secondary always appear.
TERTIARY_MIN_VALUE = 40

FRONTMATTER_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SoulDocument:
    markdown: str
    agent_name: str
    role: str
    generated_at: str
    dna: str          # 12-char short form
    dna_full: str     # 64-char sha256 hex
    lineage: Lineage

    def write(self, path) -> None:
        from pathlib import Path as _P
        _P(path).write_text(self.markdown, encoding="utf-8")


def band_for(value: int) -> str:
    for upper, label in BANDS:
        if value < upper:
            return label
    return "very high"


def _scale_text(trait: Trait, band: str) -> str:
    """Pick the scale phrasing that matches the band. Falls back if mid is empty."""
    if band in ("very low", "low"):
        return trait.scale_low
    if band in ("fairly high", "very high"):
        return trait.scale_high
    # moderate band
    if trait.scale_mid:
        return trait.scale_mid
    # Graceful fallback for legacy YAML without scale.mid: blend low and high.
    return f"{_strip_end_period(trait.scale_low)}; shades toward: {_strip_end_period(trait.scale_high)}"


def _strip_end_period(text: str) -> str:
    """Strip a single trailing period if present, leaving other terminators alone."""
    text = text.rstrip()
    return text[:-1] if text.endswith(".") else text


def _ends_with_sentence_terminator(text: str) -> bool:
    """True when the text already ends a sentence — don't append another period."""
    text = text.rstrip()
    if not text:
        return True
    # Direct terminators: . ! ?
    if text[-1] in ".!?":
        return True
    # Terminator-before-closer cases: .'  ."  .)  .]
    if len(text) >= 2 and text[-2] == "." and text[-1] in "'\")]":
        return True
    return False


def _sentence(text: str) -> str:
    """Return `text` with exactly one trailing sentence terminator."""
    text = text.rstrip()
    return text if _ends_with_sentence_terminator(text) else text + "."


def _phrase_for_trait(trait: Trait, value: int) -> str:
    """Turn a trait+value into a single prose line.

    Format (v0.2):
        - **caution** — 85/100 (very high). Demands confirmation before any action.
          _Willingness to act on uncertain information._
    """
    band = band_for(value)
    scale = _sentence(_scale_text(trait, band))
    desc = _sentence(trait.desc)
    return (
        f"- **{trait.name}** — {value}/100 ({band}). {scale}\n"
        f"  _{desc}_"
    )


class SoulGenerator:
    """Produces a soul.md markdown document from a TraitProfile."""

    def __init__(self, engine: TraitEngine) -> None:
        self.engine = engine

    def generate(
        self,
        profile: TraitProfile,
        agent_name: str,
        agent_version: str = "v1",
        *,
        lineage: Lineage | None = None,
        constitution_hash: str | None = None,
        constitution_file: str | None = None,
        instance_id: str | None = None,
        parent_instance: str | None = None,
        sibling_index: int | None = None,
        voice: "VoiceText | None" = None,
    ) -> SoulDocument:
        """Generate a soul.md from a profile.

        Parameters
        ----------
        profile : TraitProfile
            The trait values + role for this agent.
        agent_name : str
            Display name for this agent (distinct from role).
        agent_version : str
            Version tag on this agent instance.
        lineage : Lineage | None
            If this agent was spawned by another agent, pass the child lineage
            produced via `Lineage.from_parent(...)`. None means root agent
            (spawned by a human).
        constitution_hash : str | None
            Full SHA-256 hex of the agent's derived constitution. When set,
            emitted in frontmatter so consumers can verify the paired
            constitution file hasn't been tampered with. Must be paired with
            ``constitution_file``.
        constitution_file : str | None
            Relative filename of the paired ``.constitution.yaml`` (sibling of
            the soul file). Informational — the hash is the tamper-evidence.
        """
        if (constitution_hash is None) != (constitution_file is None):
            raise ValueError(
                "constitution_hash and constitution_file must be provided together"
            )
        engine = self.engine
        lineage = lineage or Lineage.root()

        # Identity hash — computed once, used in frontmatter and header.
        dna_full_hex = dna_full(profile)
        dna = dna_full_hex[:12]

        # Order domains by descending effective weight; ties broken by declared order.
        domain_order = sorted(
            engine.domains.keys(),
            key=lambda d: (
                -engine.effective_domain_weight(profile, d),
                list(engine.domains.keys()).index(d),
            ),
        )

        role = engine.get_role(profile.role)
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")

        lines: list[str] = []

        # ---- frontmatter -------------------------------------------------
        lines.extend(self._emit_frontmatter(
            profile=profile,
            agent_name=agent_name,
            agent_version=agent_version,
            generated_at=generated_at,
            dna=dna,
            dna_full_hex=dna_full_hex,
            lineage=lineage,
            constitution_hash=constitution_hash,
            constitution_file=constitution_file,
            instance_id=instance_id,
            parent_instance=parent_instance,
            sibling_index=sibling_index,
            voice=voice,
        ))

        # ---- header ------------------------------------------------------
        lines.append(f"# Soul Definition — {agent_name} {agent_version}")
        lines.append("")
        lines.append(f"**Role:** `{role.name}` — {role.description}")
        lines.append(f"**DNA:** `{dna}` (schema v{FRONTMATTER_SCHEMA_VERSION})")
        if not lineage.is_root():
            parent_tag = lineage.spawned_by or "unknown"
            lines.append(f"**Spawned by:** {parent_tag} (`{lineage.parent_dna}`) — depth {lineage.depth}")
        lines.append(f"**Generated:** {generated_at} _(auto-generated; do not hand-edit)_")
        lines.append("")
        lines.append(f"You are the **{agent_name}** agent. Your behavior below is shaped by a")
        lines.append("structured trait profile. The profile values are not suggestions — they are")
        lines.append("your operating defaults. Deviation from them requires an explicit human override.")
        lines.append("")

        # ---- voice (optional) -------------------------------------------
        # Per ADR-0017: when a VoiceText is supplied, render the `## Voice`
        # section between the intro and the structured domain sections.
        # Caller decides whether to invoke a provider; SoulGenerator just
        # places what it's given. No-op when ``voice`` is None — preserves
        # the legacy templated path for callers that don't enrich.
        if voice is not None:
            lines.append("## Voice")
            lines.append("")
            lines.append(voice.markdown.rstrip())
            lines.append("")

        # ---- domain sections --------------------------------------------
        for domain_name in domain_order:
            domain = engine.domains[domain_name]
            dw = engine.effective_domain_weight(profile, domain_name)
            emphasis = _emphasis_label(dw)
            lines.append(f"## {_title(domain_name)} — {emphasis} (weight {dw:.1f})")
            if domain.description:
                lines.append(f"_{domain.description}_")
            lines.append("")
            for sd_name, sd in domain.subdomains.items():
                trait_lines: list[str] = []
                # Sort traits: primary first, then secondary, then tertiary; within tier by value desc
                sorted_traits = sorted(
                    sd.traits.values(),
                    key=lambda t: (
                        {"primary": 0, "secondary": 1, "tertiary": 2}[t.tier],
                        -profile.trait_values[t.name],
                    ),
                )
                for trait in sorted_traits:
                    v = profile.trait_values[trait.name]
                    if trait.tier == "tertiary" and v < TERTIARY_MIN_VALUE:
                        continue  # skip low-value flavor traits
                    trait_lines.append(_phrase_for_trait(trait, v))
                if not trait_lines:
                    continue
                lines.append(f"### {_title(sd_name)}")
                if sd.description:
                    lines.append(f"_{sd.description}_")
                lines.append("")
                lines.extend(trait_lines)
                lines.append("")

        # ---- core rules --------------------------------------------------
        lines.append("## Core rules (non-negotiable)")
        lines.append("")
        lines.append("- Every substantive finding or action is written to the tamper-evident audit chain before being acted on.")
        lines.append("- Every assertion is paired with the evidence that supports it, or flagged as inference.")
        lines.append("- Any action with external impact requires explicit human approval. No exceptions at this phase.")
        lines.append("- If you are uncertain, say so. Low confidence is never a reason to invent certainty.")
        lines.append("")

        # ---- warnings ----------------------------------------------------
        flags = engine.scan_flagged(profile)
        if flags:
            lines.append("## Profile warnings")
            lines.append("")
            lines.append("These trait combinations produced operator-visible warnings at agent creation time:")
            lines.append("")
            for fc in flags:
                lines.append(f"- **{fc.name}** — {fc.warning}")
            lines.append("")

        # ---- lineage footer ---------------------------------------------
        if not lineage.is_root():
            lines.append("## Lineage")
            lines.append("")
            lines.append("This agent was spawned by another agent. The ancestor chain below is")
            lines.append("root-first; each entry is a 12-char DNA short hash.")
            lines.append("")
            for i, ancestor in enumerate(lineage.ancestors):
                marker = "root" if i == 0 else f"gen{i}"
                lines.append(f"- `{ancestor}` ({marker})")
            lines.append(f"- `{dna}` (this agent, gen{lineage.depth})")
            lines.append("")

        return SoulDocument(
            markdown="\n".join(lines).rstrip() + "\n",
            agent_name=agent_name,
            role=profile.role,
            generated_at=generated_at,
            dna=dna,
            dna_full=dna_full_hex,
            lineage=lineage,
        )

    # -----------------------------------------------------------------
    def _emit_frontmatter(
        self,
        *,
        profile: TraitProfile,
        agent_name: str,
        agent_version: str,
        generated_at: str,
        dna: str,
        dna_full_hex: str,
        lineage: Lineage,
        constitution_hash: str | None = None,
        constitution_file: str | None = None,
        instance_id: str | None = None,
        parent_instance: str | None = None,
        sibling_index: int | None = None,
        voice: "VoiceText | None" = None,
    ) -> list[str]:
        """Hand-rolled YAML emitter — avoids the pyyaml dep at generation-time
        and guarantees a stable, sorted trait_values block (which keeps
        DNA-over-frontmatter verification straightforward).
        """
        out: list[str] = ["---"]
        out.append(f"schema_version: {FRONTMATTER_SCHEMA_VERSION}")
        out.append(f"dna: {dna}")
        out.append(f'dna_full: "{dna_full_hex}"')
        out.append(f"role: {profile.role}")
        out.append(f'agent_name: "{agent_name}"')
        out.append(f'agent_version: "{agent_version}"')
        out.append(f'generated_at: "{generated_at}"')

        # Identity — registry-issued instance_id + sibling disambiguator.
        # Written BEFORE trait_values so a human skimming the file sees the
        # "who" before the "what". Omitted when not supplied (root-sourced
        # generation from the CLI won't pass them; the daemon will).
        if instance_id is not None:
            out.append(f'instance_id: "{instance_id}"')
        if parent_instance is not None:
            out.append(f'parent_instance: "{parent_instance}"')
        if sibling_index is not None:
            out.append(f"sibling_index: {sibling_index}")

        # Constitution binding (optional — omitted when no constitution is attached).
        if constitution_hash is not None and constitution_file is not None:
            out.append(f'constitution_hash: "{constitution_hash}"')
            out.append(f'constitution_file: "{constitution_file}"')

        # Narrative provenance (ADR-0017) — purely informational, not in any
        # hash. Records which provider+model wrote the Voice section, or
        # "template" when the LLM call was bypassed / failed.
        if voice is not None:
            out.append(f'narrative_provider: "{voice.provider}"')
            out.append(f'narrative_model: "{voice.model}"')
            out.append(f'narrative_generated_at: "{voice.generated_at}"')

        # Lineage
        if lineage.is_root():
            out.append("parent_dna: null")
            out.append("spawned_by: null")
            out.append("lineage: []")
            out.append("lineage_depth: 0")
        else:
            out.append(f"parent_dna: {lineage.parent_dna}")
            sb = lineage.spawned_by or ""
            out.append(f'spawned_by: "{sb}"')
            out.append("lineage:")
            for ancestor in lineage.ancestors:
                out.append(f"  - {ancestor}")
            out.append(f"lineage_depth: {lineage.depth}")

        # Canonical, sorted trait values.
        out.append("trait_values:")
        for k in sorted(profile.trait_values):
            out.append(f"  {k}: {profile.trait_values[k]}")

        if profile.domain_weight_overrides:
            out.append("domain_weight_overrides:")
            for k in sorted(profile.domain_weight_overrides):
                out.append(f"  {k}: {profile.domain_weight_overrides[k]}")
        else:
            out.append("domain_weight_overrides: {}")

        out.append("---")
        out.append("")
        return out


def _title(slug: str) -> str:
    return slug.replace("_", " ").title()


def _emphasis_label(weight: float) -> str:
    if weight >= 1.8:
        return "dominant"
    if weight >= 1.3:
        return "strong"
    if weight >= 0.9:
        return "balanced"
    if weight >= 0.6:
        return "muted"
    return "deprioritized"
