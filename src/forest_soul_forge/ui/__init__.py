"""Aspirational placeholder — future Python-side UI bridge.

Status: empty by design at v0.1.

The actual frontend lives at top-level ``frontend/`` (vanilla JS, no
build step, served by nginx). This Python package was scaffolded
early as a potential home for a server-side rendering bridge or
WebSocket session manager — neither of which is in v0.1 scope.

Phase E audit (2026-04-30) verified zero imports anywhere; kept as
an explicit placeholder for v0.3+ work that may want a Python-side
UI primitive (e.g., the operator-only setup wizard, or a Streamlit
fallback for environments without nginx).

Removal goes through the §0 Hippocratic gate.
"""
