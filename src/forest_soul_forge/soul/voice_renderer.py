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

import re

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
    "role.\n\n"
    "Write in second person addressed to the agent itself: 'you speak "
    "with...', 'when you are uncertain you...', 'you ask for...'. "
    "Produce 2 to 3 short paragraphs of plain declarative prose. No "
    "headers, no bullet points, no closing sign-off, no list of trait "
    "values.\n\n"
    "Voice rules — strict:\n"
    "1. Describe concrete behaviors. Each sentence should say what the "
    "agent does, not how impressive it is. 'You log every step before "
    "acting.' Yes. 'Your attention to detail is unparalleled.' No.\n"
    "2. No marketing adjectives or superlatives. Avoid 'meticulous', "
    "'laser-sharp', 'unparalleled', 'exceptional', 'remarkable', "
    "'borders on', 'precision', 'unwavering', 'steadfast'.\n"
    "3. No mystical or grandiose framing. The agent is a tool, not a "
    "hero.\n"
    "4. Translate the trait profile into decisions and outputs, not "
    "into adjectival praise. A high-caution agent doesn't have "
    "'remarkable caution' — it 'asks for confirmation before any "
    "action with external impact'.\n"
    "5. State, don't celebrate. Plain sentences beat dramatic ones."
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
    genre_name: str | None = None,
    genre_trait_emphasis: tuple[str, ...] = (),
) -> VoiceText:
    """Render the Voice section, falling back to a template on any error.

    Why ``settings`` is passed in rather than read from a global: it lets
    tests vary task_kind / max_tokens / temperature without monkey-
    patching, and matches how the rest of the daemon takes settings as
    an explicit dependency.

    ``genre_name`` and ``genre_trait_emphasis`` (ADR-0021 T7) let the
    user prompt anchor the LLM on the genre's signature traits regardless
    of whether those traits hit the very-high / very-low cutoffs. A
    Companion's voice should foreground empathy and warmth even at
    middling values; an Actuator should foreground caution and evidence
    demand. Both default to "no genre context" so callers without a
    genre engine (legacy births, unclaimed roles) keep the pre-T7
    behavior bit-for-bit.
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
        profile=profile, role=role, engine=engine, lineage=lineage,
        genre_name=genre_name,
        genre_trait_emphasis=genre_trait_emphasis,
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
    genre_name: str | None = None,
    genre_trait_emphasis: tuple[str, ...] = (),
) -> str:
    """Compose the user-message body for the LLM.

    Translates the profile into a paragraph the model can reason over:
    role description, dominant + strong domain weights in plain English,
    and the highest- / lowest-band traits with their qualitative band
    label (not the raw 0-100 number — the system prompt asks for voice,
    not a numeric readout).

    When a genre is provided (ADR-0021 T7), the prompt also includes the
    genre name and the traits the genre emphasizes regardless of whether
    those traits hit the very-high cutoff. This is the lever that makes
    a Companion voice sound different from an Actuator voice even at
    middling trait values — the genre tells the model which dimensions
    matter most for *this kind of agent* before the trait values nudge
    them.
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

    # Genre context (ADR-0021 T7). Include the genre name and a
    # trait-emphasis line listing the genre's signature traits with
    # their actual values from the profile — gives the LLM concrete
    # numbers for the dimensions the genre cares most about, even
    # when those dimensions don't appear in very_high / very_low.
    genre_block = ""
    if genre_name:
        emphasis_lines: list[str] = []
        for trait in genre_trait_emphasis:
            if trait in profile.trait_values:
                emphasis_lines.append(
                    f"{trait.replace('_', ' ')} = {profile.trait_values[trait]}"
                )
        emphasis_phrase = "; ".join(emphasis_lines) if emphasis_lines else "none"
        genre_block = (
            f"\n\nGenre: {genre_name}. The voice should foreground the "
            f"traits this genre cares most about: {emphasis_phrase}. "
            f"Lean on these dimensions even when their numeric values are "
            f"middling — they define what kind of agent this is."
        )

    return (
        f"Role: {role.name} — {role.description}\n\n"
        f"Effective emphasis (top domains): {domain_phrase}.\n\n"
        f"Pronounced strengths (very high traits): {high_phrase}.\n"
        f"Pronounced muted areas (very low traits): {low_phrase}."
        f"{genre_block}"
        f"{lineage_note}\n\n"
        "Write the Voice section per the rules in the system prompt."
    )


# ---------------------------------------------------------------------------
# Surgical update of an existing soul.md (used by /agents/{id}/regenerate-voice)
# ---------------------------------------------------------------------------
_FRONTMATTER_RE = re.compile(r"\A(---\s*\n)(.*?)(\n---\s*(?:\n|$))", re.DOTALL)
_NARRATIVE_LINE_RE = re.compile(
    r"^narrative_(provider|model|generated_at):.*$", re.MULTILINE
)


def update_soul_voice(soul_path, voice: VoiceText) -> None:
    """Rewrite soul.md with a new ``## Voice`` section + narrative_* fields.

    Used by the regenerate-voice endpoint. Preserves all other content
    byte-for-byte: identity frontmatter (dna, constitution_hash, lineage,
    trait_values), the templated trait readout, core rules, lineage
    footer. Only the three narrative_* lines and the body of the
    ``## Voice`` section change.

    If the soul was birthed without enrichment (no Voice section, no
    narrative_* fields), this function inserts both for the first time.
    """
    from pathlib import Path
    p = Path(soul_path)
    text = p.read_text(encoding="utf-8")

    text = _replace_or_insert_narrative_fields(text, voice)
    text = _replace_or_insert_voice_section(text, voice.markdown)

    p.write_text(text, encoding="utf-8")


def _replace_or_insert_narrative_fields(text: str, voice: VoiceText) -> str:
    """Update the three narrative_* lines in the YAML frontmatter.

    If the lines exist, replace them. Otherwise insert them right after
    ``constitution_file:`` (matches the position SoulGenerator emits).
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        # Soul has no frontmatter — leave untouched. Caller-side validation
        # should have caught this; the daemon never produces such files.
        return text
    open_, body, close_ = m.group(1), m.group(2), m.group(3)

    new_lines = [
        f'narrative_provider: "{voice.provider}"',
        f'narrative_model: "{voice.model}"',
        f'narrative_generated_at: "{voice.generated_at}"',
    ]
    new_block = "\n".join(new_lines)

    if _NARRATIVE_LINE_RE.search(body):
        # Replace each existing line. Order in the original file is
        # preserved by replacing them individually.
        body = re.sub(
            r"^narrative_provider:.*$",
            new_lines[0],
            body,
            count=1,
            flags=re.MULTILINE,
        )
        body = re.sub(
            r"^narrative_model:.*$",
            new_lines[1],
            body,
            count=1,
            flags=re.MULTILINE,
        )
        body = re.sub(
            r"^narrative_generated_at:.*$",
            new_lines[2],
            body,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        # Insert after the constitution_file line. If that's missing
        # (legacy), append before the closing fence — the parser only
        # cares about presence, not position.
        cf_re = re.compile(r"^(constitution_file:.*)$", re.MULTILINE)
        if cf_re.search(body):
            body = cf_re.sub(r"\1\n" + new_block, body, count=1)
        else:
            body = body.rstrip() + "\n" + new_block

    return open_ + body + close_ + text[m.end():]


def _replace_or_insert_voice_section(text: str, voice_markdown: str) -> str:
    """Replace or insert the body's ``## Voice`` section.

    Section boundary: from ``## Voice`` heading line through (but not
    including) the next ``## `` heading or end-of-file. If absent,
    inserted just before the first ``## `` heading after the closing
    frontmatter fence — that's the position SoulGenerator uses.
    """
    voice_block = f"## Voice\n\n{voice_markdown.rstrip()}\n\n"

    # Detect existing section.
    voice_re = re.compile(
        r"(^## Voice\s*\n)"  # the heading itself
        r".*?"                # the section body
        r"(?=^## |\Z)",       # up to next ## heading or EOF
        re.DOTALL | re.MULTILINE,
    )
    if voice_re.search(text):
        return voice_re.sub(voice_block, text, count=1)

    # Insert before the FIRST ## heading that appears AFTER the closing
    # frontmatter fence — that's where domain sections start. SoulGenerator
    # places Voice right above them.
    fm_close = text.find("\n---\n", 0)
    search_from = fm_close + 5 if fm_close != -1 else 0
    rest = text[search_from:]
    first_h2 = re.search(r"^## ", rest, re.MULTILINE)
    if first_h2 is None:
        # No domain sections — just append.
        return text.rstrip() + "\n\n" + voice_block
    insert_at = search_from + first_h2.start()
    return text[:insert_at] + voice_block + text[insert_at:]


def _template_voice(
    *,
    profile: "TraitProfile",
    role: "Role",
    engine: "TraitEngine",
    lineage: Lineage,
    note: str | None = None,
) -> VoiceText:
    """Templated fallback used when the provider is unavailable / errored.

    Renders 2–3 paragraphs derived from the actual profile so a fallback
    soul.md doesn't read sparser than an enriched one. Same shape as a
    valid LLM voice (plain declarative second-person prose), built
    deterministically from the trait values rather than via an LLM call.
    Marked with an italic provenance line at the end so a reader can
    always tell which is which.
    """
    domain_order = sorted(
        engine.domains.keys(),
        key=lambda d: (
            -engine.effective_domain_weight(profile, d),
            list(engine.domains.keys()).index(d),
        ),
    )
    dominant = domain_order[0].replace("_", " ") if domain_order else "balanced"
    second = (domain_order[1].replace("_", " ")
              if len(domain_order) > 1 else None)

    sorted_traits = sorted(
        profile.trait_values.items(), key=lambda kv: -kv[1]
    )
    very_high = [(t, v) for t, v in sorted_traits if v >= 80]
    fairly_high = [(t, v) for t, v in sorted_traits if 60 <= v < 80]
    very_low = [(t, v) for t, v in sorted_traits if v < 30]

    paragraphs: list[str] = []

    # Paragraph 1: orientation. Dominant domain + role context.
    role_desc = role.description.rstrip(".") if getattr(role, "description", None) else ""
    if role_desc:
        paragraphs.append(
            f"You operate as a **{role.name.replace('_', ' ')}**: {role_desc}. "
            f"Your dominant orientation is **{dominant}**"
            + (f", with **{second}** as a secondary emphasis." if second else ".")
        )
    else:
        paragraphs.append(
            f"Your dominant orientation is **{dominant}**"
            + (f", with **{second}** as a secondary emphasis." if second else ".")
        )

    # Paragraph 2: concrete behaviors derived from very-high traits.
    if very_high:
        # Cap to top 4 to keep the paragraph tight.
        named = [t for t, _ in very_high[:4]]
        behaviors = []
        # Map a handful of common high-value traits to concrete behaviors.
        # Generic fallback handles anything not in the map.
        BEHAVIOR_MAP = {
            "caution": "ask for confirmation before any action with external impact",
            "double_checking": "re-derive each conclusion from its inputs before stating it",
            "evidence_demand": "require independent corroboration before asserting a finding",
            "technical_accuracy": "verify every technical claim against its source",
            "thoroughness": "log reasoning, alternatives considered, and inputs examined",
            "research_thoroughness": "pull from multiple angles before concluding",
            "transparency": "name your gaps and assumptions out loud",
            "vigilance": "keep scanning even during low-signal periods",
            "suspicion": "treat every outlier as potentially significant until shown otherwise",
            "risk_aversion": "default to the lower-impact option when in doubt",
            "composure": "hold output quality steady under pressure",
            "patience": "welcome backtracking and repeated clarification",
            "directness": "make flat unhedged claims when the evidence is in",
            "empathy": "acknowledge what the user is feeling before offering an answer",
        }
        for t in named:
            phrase = BEHAVIOR_MAP.get(t)
            if phrase:
                behaviors.append(f"You {phrase}.")
            else:
                # Generic phrasing for traits we haven't mapped yet.
                behaviors.append(
                    f"You weight {t.replace('_', ' ')} above the median in your decisions."
                )
        # Combine into one paragraph rather than separate sentences-per-trait
        # so the prose reads as a continuous voice.
        paragraphs.append(" ".join(behaviors))

    # Paragraph 3: shaped by very-low and fairly-high. How you handle
    # uncertainty and where you avoid overcommitting.
    closer_bits: list[str] = []
    if very_low:
        low_named = [t.replace("_", " ") for t, _ in very_low[:2]]
        closer_bits.append(
            f"You do not lean on {', '.join(low_named)} — those are deprioritized in your output."
        )
    if fairly_high:
        # Pick one fairly-high trait that suggests how to handle uncertainty
        # if available, otherwise just acknowledge calibration.
        anchor = fairly_high[0][0].replace("_", " ")
        closer_bits.append(
            f"In uncertain cases you fall back on {anchor} rather than guessing."
        )
    if not closer_bits:
        closer_bits.append(
            "When uncertain you say so plainly rather than synthesizing confidence."
        )
    paragraphs.append(" ".join(closer_bits))

    body = "\n\n".join(paragraphs)
    suffix = note or "model provider was unavailable at birth"
    body += f"\n\n_(template fallback — {suffix})_"

    return VoiceText(
        markdown=body,
        provider="template",
        model="template",
        generated_at=_now_iso(),
    )
