"""Renders the soul.md `## Voice` section via the active model provider.

Per ADR-0017. The renderer is a single async function that takes a
provider, a built profile, and a few helpers, and returns a
:class:`VoiceText` carrying the rendered markdown plus enough metadata
to land in soul.md frontmatter (provider name, model tag, timestamp).

Every provider failure mode — unreachable, disabled, or a non-2xx
upstream — is caught here and converted into a templated fallback. Soul
generation never fails because Ollama is down: callers always receive
a usable :class:`VoiceText`. The frontmatter records
``narrative_provider: "template"`` when the LLM call didn't produce the
content, so the audit trail is honest about what wrote the paragraph.

The system prompt and the templated fallback are both *product*
decisions, not provider plumbing — that's why they live in ``soul/``
rather than as a method on ``LocalProvider`` / ``FrontierProvider``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from forest_soul_forge.core.dna import Lineage
from forest_soul_forge.daemon.providers import (
    ProviderDisabled,
    ProviderError,
    ProviderUnavailable,
    TaskKind,
)

if TYPE_CHECKING:
    from forest_soul_forge.core.trait_engine import (
        Role,
        TraitEngine,
        TraitProfile,
    )
    from forest_soul_forge.daemon.config import DaemonSettings
    from forest_soul_forge.daemon.providers import ModelProvider


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VoiceText:
    """Rendered Voice section + traceability metadata.

    ``markdown`` is the section body (without the leading ``## Voice``
    heading; SoulGenerator emits the heading). ``provider`` is the active
    provider name on the call OR ``"template"`` when the LLM was bypassed
    or failed. ``model`` is the resolved model tag, or the literal
    string ``"template"`` for the fallback path. ``generated_at`` is an
    ISO-8601 UTC timestamp.
    """

    markdown: str
    provider: str
    model: str
    generated_at: str


# ---------------------------------------------------------------------------
# Constants — system prompt + templated fallback
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "You are writing a short '## Voice' section for an agent's soul.md "
    "document. The section captures how this specific agent speaks, "
    "decides, and handles uncertainty — given its trait profile and "
    "role. Write in second person addressed to the agent itself ('you "
    "speak with...', 'when you are uncertain...'). Produce 2 to 4 short "
    "paragraphs. No headers, no bullet points, no closing sign-off. Do "
    "not restate the trait values numerically — translate them into "
    "voice and decision-making cadence. Keep it grounded; avoid mystical "
    "or grandiose language."
)


# ---------------------------------------------------------------------------
# Public renderer
# ---------------------------------------------------------------------------
async def render_voice(
    provider: "ModelProvider",
    *,
    profile: "TraitProfile",
    role: "Role",
    engine: "TraitEngine",
    lineage: Lineage,
    settings: "DaemonSettings",
) -> VoiceText:
    """Render the Voice section, falling back to a template on any error.

    Why ``settings`` is passed in rather than read from a global: it lets
    tests vary task_kind / max_tokens / temperature without monkey-
    patching, and matches how the rest of the daemon takes settings as
    an explicit dependency.
    """
    # Validate task_kind early — bad config should fail loudly during
    # startup, not silently fall back to template on every birth.
    try:
        task_kind = TaskKind(settings.narrative_task_kind)
    except ValueError:
        # Bad config — render with the template and record the misconfig
        # in the model field so an operator inspecting a soul file can see
        # exactly what went wrong.
        return _template_voice(
            profile=profile,
            role=role,
            engine=engine,
            lineage=lineage,
            note=f"invalid FSF_NARRATIVE_TASK_KIND={settings.narrative_task_kind!r}",
        )

    user_prompt = _build_user_prompt(
        profile=profile, role=role, engine=engine, lineage=lineage
    )
    extra_kwargs: dict = {}
    if settings.narrative_temperature is not None:
        extra_kwargs["temperature"] = settings.narrative_temperature

    try:
        text = await provider.complete(
            user_prompt,
            task_kind=task_kind,
            system=SYSTEM_PROMPT,
            max_tokens=settings.narrative_max_tokens,
            **extra_kwargs,
        )
    except (ProviderUnavailable, ProviderDisabled, ProviderError):
        # Any provider-layer error → template fallback. We deliberately
        # do not raise — the soul.md must always be writable.
        return _template_voice(
            profile=profile, role=role, engine=engine, lineage=lineage
        )
    except Exception:  # pragma: no cover — defensive
        # Truly unexpected (e.g. a coding bug in provider.complete). Still
        # fall back rather than failing /birth — the user's task is to
        # birth an agent, not to debug the model server.
        return _template_voice(
            profile=profile,
            role=role,
            engine=engine,
            lineage=lineage,
            note="unexpected provider exception",
        )

    # Resolve the model tag for the response. Both LocalProvider and
    # FrontierProvider expose `.models`; the protocol doesn't mandate it,
    # so we degrade gracefully if a future provider opts out.
    model_tag = _resolve_model_tag(provider, task_kind)
    return VoiceText(
        markdown=text.strip(),
        provider=getattr(provider, "name", "unknown"),
        model=model_tag,
        generated_at=_now_iso(),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _resolve_model_tag(provider: "ModelProvider", task_kind: TaskKind) -> str:
    models = getattr(provider, "models", None)
    if not isinstance(models, dict):
        return "unknown"
    return (
        models.get(task_kind)
        or models.get(task_kind.value)
        or models.get(TaskKind.CONVERSATION)
        or models.get(TaskKind.CONVERSATION.value)
        or "unknown"
    )


def _build_user_prompt(
    *,
    profile: "TraitProfile",
    role: "Role",
    engine: "TraitEngine",
    lineage: Lineage,
) -> str:
    """Compose the user-message body for the LLM.

    Translates the profile into a paragraph the model can reason over:
    role description, dominant + strong domain weights in plain English,
    and the highest- / lowest-band traits with their qualitative band
    label (not the raw 0-100 number — the system prompt asks for voice,
    not a numeric readout).
    """
    # Domain ordering by effective weight (dominant first). This is the
    # same ordering SoulGenerator uses for its templated body.
    domain_order = sorted(
        engine.domains.keys(),
        key=lambda d: (
            -engine.effective_domain_weight(profile, d),
            list(engine.domains.keys()).index(d),
        ),
    )

    # Top-level domain summary.
    top_domains: list[str] = []
    for d in domain_order[:3]:
        weight = engine.effective_domain_weight(profile, d)
        if weight >= 0.9:
            label = "dominant" if weight >= 1.8 else ("strong" if weight >= 1.3 else "balanced")
            top_domains.append(f"{d.replace('_', ' ')} ({label})")

    # Pick the strongest "very high" trait and the most pronounced "very
    # low" trait across the profile to give the LLM concrete anchors.
    sorted_traits = sorted(
        profile.trait_values.items(), key=lambda kv: kv[1]
    )
    very_low = [(t, v) for t, v in sorted_traits if v < 20]
    very_high = [(t, v) for t, v in reversed(sorted_traits) if v >= 80]

    # Cap to 3 each so the prompt stays bounded.
    very_low = very_low[:3]
    very_high = very_high[:3]

    high_phrase = ", ".join(t.replace("_", " ") for t, _ in very_high) or "none"
    low_phrase = ", ".join(t.replace("_", " ") for t, _ in very_low) or "none"
    domain_phrase = ", ".join(top_domains) or "balanced across all domains"

    lineage_note = ""
    if not lineage.is_root():
        lineage_note = (
            f"\n\nThis agent was spawned by another agent (depth "
            f"{lineage.depth}). Acknowledge that lineage briefly without "
            f"making it the focus."
        )

    return (
        f"Role: {role.name} — {role.description}\n\n"
        f"Effective emphasis (top domains): {domain_phrase}.\n\n"
        f"Pronounced strengths (very high traits): {high_phrase}.\n"
        f"Pronounced muted areas (very low traits): {low_phrase}."
        f"{lineage_note}\n\n"
        "Write the Voice section per the rules in the system prompt."
    )


def _template_voice(
    *,
    profile: "TraitProfile",
    role: "Role",
    engine: "TraitEngine",
    lineage: Lineage,
    note: str | None = None,
) -> VoiceText:
    """Templated fallback used when the provider is unavailable / errored.

    Produces a single short paragraph derived from the dominant domain
    and any very-high trait. Marked with an italic provenance line so a
    reader doesn't mistake it for LLM-authored voice.
    """
    domain_order = sorted(
        engine.domains.keys(),
        key=lambda d: (
            -engine.effective_domain_weight(profile, d),
            list(engine.domains.keys()).index(d),
        ),
    )
    dominant = domain_order[0] if domain_order else "balanced"
    dominant_h = dominant.replace("_", " ")

    very_high = sorted(
        profile.trait_values.items(), key=lambda kv: -kv[1]
    )
    very_high_named = [t for t, v in very_high if v >= 80][:2]

    if very_high_named:
        strengths = ", ".join(t.replace("_", " ") for t in very_high_named)
        body = (
            f"You operate from a base of **{dominant_h}**, with pronounced "
            f"strengths in {strengths}. Your decisions weight that emphasis; "
            f"deviations require explicit override."
        )
    else:
        body = (
            f"You operate from a base of **{dominant_h}**. Your decisions "
            f"reflect that emphasis without overcommitting to any single "
            f"trait."
        )

    suffix = note or "model provider was unavailable at birth"
    body += f"\n\n_(template fallback — {suffix})_"

    return VoiceText(
        markdown=body,
        provider="template",
        model="template",
        generated_at=_now_iso(),
    )
