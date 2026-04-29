"""Chronicle — operator-facing HTML/Markdown export of the audit chain.

ADR-003X K5. Renders an agent's life (or a triune's, or the whole
forge's) as a self-contained HTML timeline. Designed to be
double-clickable from Finder, shareable as a single file, and safe
to email — payload content stays in the chain unless the operator
explicitly opts in via ``--include-payload``.
"""
from forest_soul_forge.chronicle.render import (
    filter_by_bond_name,
    filter_by_dna,
    is_milestone,
    render_html,
    render_markdown,
    sanitize_event,
)

__all__ = [
    "filter_by_bond_name",
    "filter_by_dna",
    "is_milestone",
    "render_html",
    "render_markdown",
    "sanitize_event",
]
