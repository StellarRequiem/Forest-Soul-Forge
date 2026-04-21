# Forest Soul Forge — Handoff v0.1

**Subtitle:** Local-First Blue-Team Personal Agent Factory
**Date:** April 2026
**Author:** Alex
**Status:** Phase 1 — Cells & Organs (ready for implementation)

> **Editor's note.** This document is preserved verbatim from the original handoff that seeded this repository. It includes claims that should be independently verified before being treated as load-bearing — see the "Open Questions" section appended at the end. The technical skeleton referenced in Section 5 contained syntax and JSON errors that are not replicated here; corrected versions will live in `src/` once Phase 2 begins.

---

## 1. Vision & Positioning

ForestForge turns the existing Forest repo into a plug-and-play Personal Agent Factory. Users define agents via 0–100 sliders for personality and cognitive traits, then build individual agents or full hierarchical swarms — all running 100% locally with tamper-proof audit chains and human approval gates.

**Positioning:** Blue-team cybersecurity and personal digital defense only. We build the tools. The operator is legally responsible for compliance. We will follow any future government certification requirements for high-risk uses.

## 2. Core Architecture — The Pyramid

A hierarchical multi-agent pyramid.

**Layers:**

- **Level 0 (Foundation):** Pure tools — dictionaries, small local LLMs, retrieval. No traits.
- **Level 1–5 Agents:** Specialized roles with full trait sliders.
- **Omega / Brain (Top):** Strategic orchestrator that only sees summaries. Maintains swarm emotional valence.
- **Human Gate:** Final approval on high-impact actions.

## 3. Trait System (The Core Innovation)

Every agent is defined by quantifiable sliders. Machine-readable, and directly influences grading, constitution, and behavior.

Master schema: `config/agent_traits.json`.

**Cited precedents** (to verify before committing to the framing):

- Big Five personality modeling in AI agents.
- Persona vectors / trait steering used at Anthropic and elsewhere.
- Slider-based customization discussed in agent design pattern literature.

## 4. SOUL.md Integration

The system builds on the emerging pattern of `.soul.md` files (free-form natural language for the LLM; persistent identity, tone, rules).

**Difference:**

- `.soul.md` — free-form natural language for the LLM.
- `agent_traits.json` — structured numbers the code can act on.

**Solution:** The factory auto-generates a clean `soul.md` from the trait sliders so the LLM gets rich prose while the Python code stays fully controllable.

## 5. Working Skeleton (Phase 1)

Target folder structure:

```
forest/
├── config/agent_traits.json
├── core/trait_engine.py
├── core/grading_engine.py
├── agents/factory.py
├── ui/agent_factory_streamlit.py
└── README.md
```

> **Note:** In this repo, that layout is adapted to a `src/forest_soul_forge/` package — see `docs/architecture/layout.md` for the adaptation.

### Trait schema (draft)

```json
{
  "version": "0.1",
  "core_traits": {
    "sarcasm":    { "min": 0, "max": 100, "default": 20 },
    "curiosity":  { "min": 0, "max": 100, "default": 70 },
    "caution":    { "min": 0, "max": 100, "default": 85 },
    "empathy":    { "min": 0, "max": 100, "default": 60 },
    "confidence": { "min": 0, "max": 100, "default": 65 },
    "directness": { "min": 0, "max": 100, "default": 70 }
  },
  "cognitive_skills": {
    "research_thoroughness": { "min": 0, "max": 100, "default": 85 },
    "double_checking":       { "min": 0, "max": 100, "default": 90 },
    "technical_accuracy":    { "min": 0, "max": 100, "default": 90 }
  }
}
```

### Soul.md auto-generation (intent)

User moves sliders → system writes `traits.json` → generator reads `traits.json` and emits a `soul.md` that translates each numeric band into a natural-language line for the LLM.

**Example output** (sarcasm 85 / caution 92 / curiosity 65 / directness 80):

```markdown
# Soul Definition - Network Watcher v1

You are a highly professional Network Watcher agent.

Personality profile:
- Sharp, dry sense of humor; blunt when something looks wrong (sarcasm: 85).
- Security-first; extremely cautious and double-checks findings before raising alerts (caution: 92).
- Curious about anomalies but not reckless (curiosity: 65).
- Clear and direct without fluff (directness: 80).

Core rules:
- Every finding is graded and logged in the tamper-proof audit chain.
- Risk level is presented before any suggested action.
- Human approval is required for disruptive actions.
```

## 6. Immediate Roadmap (Next 30 Days, from original handoff)

- **Week 1–2:** Trait system and dashboard.
- **Week 3:** Three pre-built blue-team agents (Network Watcher, Log Analyst, Anomaly Investigator).
- **Week 4:** Package as macOS `.app`; launch GitHub Sponsors + Gumroad.

## 7. Compliance & Legal Notes

- Form LLC early.
- Target SOC 2 Type 1 within 6–9 months.
- TOS must prohibit criminal use.

---

## Open Questions (appended 2026-04-21)

These are items I want to confirm or resolve before building on the handoff as-is:

1. **Precedents that need citations.** ".soul.md files used in OpenClaw, Hermes Agent" — needs source. "Big Five persona vectors as a 2026 standard" — needs source. Neither claim should be load-bearing in the architecture until they're verified or reframed as "our design, inspired by..."
2. **Hierarchy is not yet designed.** The handoff calls for a "full hierarchical talent tree" but the trait schema shown is flat (two categories, flat sliders). The tree structure needs to be designed first — ADR and `config/trait_tree.yaml` coming in Phase 1.
3. **LangGraph + Ollama are referenced as the "existing stack."** This is a fresh repo; the stack choice should be an explicit ADR, not inherited by assumption.
4. **"Tamper-proof audit chain" needs a threat model.** What tampering are we protecting against — local attacker with root? Remote? Operator error? The answer dictates whether we need hash chains, signatures, external anchoring, or just ordered append-only logs.
5. **Human approval gate mechanics.** How is "approval" surfaced — CLI prompt, Streamlit dialog, macOS notification, cryptographic signature? Same operational question across all of them.

These are tracked as pending ADR topics.
