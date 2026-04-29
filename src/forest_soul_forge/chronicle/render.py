"""Chronicle renderer — HTML + Markdown export of audit chain entries.

ADR-003X K5. The chain is the source of truth for an agent's life;
this module turns that source into something an operator can read,
share, or attach to a postmortem. Two output formats:

* ``render_html`` — single-file HTML with inline CSS and a tiny
  client-side milestone toggle. No external assets, no fonts loaded
  from the network, no JS frameworks. Opens with double-click.
* ``render_markdown`` — flat Markdown. Useful for git-friendly
  diffs or feeding to a downstream tool.

Sanitization discipline (the IMPORTANT bit):

By default neither format includes the raw ``event_data`` payload.
Each event type has a hand-written sanitizer in :data:`SANITIZERS`
that produces a one-line summary using only fields known to be
safe (timestamps, type names, hashes, agent ids). The operator can
opt in to full payload via ``include_payload=True``, but the
default behavior matches the existing audit chain's
"metadata-not-content" posture — chronicles can be shared without
leaking memory contents, secret names, or tool-call digests beyond
what's already public-by-design.

Filter helpers:

* :func:`filter_by_dna` — events whose ``agent_dna`` matches.
  Note the chain stores 12-char short DNA, so the caller should
  pass the short form (``AgentRow.dna``).
* :func:`filter_by_bond_name` — events that reference a triune
  bond (ceremony events with ``bond_name``, ``agent_delegated``
  with ``triune_bond_name``, ``out_of_triune_attempt``).
"""
from __future__ import annotations

import html
import json
from datetime import datetime
from typing import Any, Iterable

from forest_soul_forge.core.audit_chain import ChainEntry


# ---------------------------------------------------------------------------
# Milestone classification — events that are operator-facing and meaningful
# even at a glance. Used by the HTML's "show milestones only" toggle.
# ---------------------------------------------------------------------------
MILESTONE_EVENT_TYPES: frozenset[str] = frozenset({
    "chain_created",
    "agent_created",
    "agent_archived",
    "agent_delegated",
    "ceremony",
    "memory_verified",
    "memory_verification_revoked",
    "out_of_triune_attempt",
    "secret_set",
    "secret_revoked",
    "governance_relaxed",
    "spawn_genre_override",
    "hardware_bound",
    "hardware_mismatch",
    "hardware_unbound",
})


def is_milestone(entry: ChainEntry) -> bool:
    return entry.event_type in MILESTONE_EVENT_TYPES


# ---------------------------------------------------------------------------
# Per-event sanitizers — return a short, safe-to-render summary.
# ---------------------------------------------------------------------------
def _short(s: str | None, n: int = 16) -> str:
    if not s:
        return "<unknown>"
    return s if len(s) <= n else s[:n] + "…"


def _san_chain_created(d: dict) -> str:
    return "Chain genesis"


def _san_agent_created(d: dict) -> str:
    name = d.get("agent_name") or "<unnamed>"
    role = d.get("role") or "<unknown role>"
    return f"Agent {name!r} born as {role}"


def _san_agent_archived(d: dict) -> str:
    inst = _short(d.get("instance_id"))
    reason = (d.get("reason") or "")[:80]
    return f"Agent {inst} archived" + (f" — {reason!r}" if reason else "")


def _san_agent_delegated(d: dict) -> str:
    caller = _short(d.get("caller_instance"))
    target = _short(d.get("target_instance"))
    skill = d.get("skill_name") or "<unknown>"
    if d.get("triune_internal"):
        bond = d.get("triune_bond_name") or "<unnamed-bond>"
        return f"{caller} → {target} ({skill}) [triune {bond!r}]"
    if d.get("allow_out_of_lineage"):
        return f"{caller} → {target} ({skill}) [out-of-lineage override]"
    return f"{caller} → {target} ({skill})"


def _san_ceremony(d: dict) -> str:
    name = d.get("ceremony_name") or "<unnamed>"
    summary = (d.get("summary") or "")[:120]
    return f"Ceremony {name!r}" + (f" — {summary}" if summary else "")


def _san_memory_verified(d: dict) -> str:
    inst = _short(d.get("instance_id"))
    entry_id = d.get("entry_id") or "<?>"
    verifier = d.get("verifier_id") or "<?>"
    return f"Memory entry #{entry_id} of {inst} verified by {verifier}"


def _san_memory_verification_revoked(d: dict) -> str:
    inst = _short(d.get("instance_id"))
    entry_id = d.get("entry_id") or "<?>"
    return f"Memory verification revoked for entry #{entry_id} of {inst}"


def _san_out_of_triune_attempt(d: dict) -> str:
    caller = _short(d.get("caller_instance"))
    target = _short(d.get("target_instance"))
    bond = d.get("bond_name") or "<unnamed>"
    return f"⚠ {caller} attempted out-of-bond delegate to {target} (triune {bond!r})"


def _san_governance_relaxed(d: dict) -> str:
    """T2.1 — dispatched per-relaxation, with relaxation_type indicating
    which kind. Operators filtering on this single event type get every
    constraint-bypass regardless of underlying event."""
    rt = d.get("relaxation_type") or "<unknown>"
    if rt == "out_of_lineage_delegate":
        caller = _short(d.get("caller_instance"))
        target = _short(d.get("target_instance"))
        skill = d.get("skill_name") or "<?>"
        return f"⚠ Governance relaxed: {caller} → {target} ({skill}) bypassed lineage gate"
    if rt == "spawn_genre_override":
        inst = _short(d.get("instance_id"))
        pg = d.get("parent_genre") or "?"
        cg = d.get("child_genre") or "?"
        return f"⚠ Governance relaxed: spawn {inst} crossed genre {pg}→{cg}"
    return f"⚠ Governance relaxed: {rt}"


def _san_spawn_genre_override(d: dict) -> str:
    inst = _short(d.get("instance_id"))
    pg = d.get("parent_genre") or "?"
    cg = d.get("child_genre") or "?"
    return f"Spawn override: {inst} crossed genre {pg}→{cg}"


def _san_hardware_bound(d: dict) -> str:
    inst = _short(d.get("instance_id"))
    fp = (d.get("fingerprint") or "")[:8]
    src = d.get("source") or "?"
    return f"Agent {inst} hardware-bound (fp={fp}… via {src})"


def _san_hardware_mismatch(d: dict) -> str:
    inst = _short(d.get("instance_id"))
    tool = d.get("tool_key") or "?"
    expected = (d.get("expected_machine_fingerprint") or "")[:8]
    binding = (d.get("constitution_binding") or "")[:8]
    return f"⚠ Hardware mismatch on {inst} ({tool}); machine={expected}… binding={binding}…"


def _san_hardware_unbound(d: dict) -> str:
    inst = _short(d.get("instance_id"))
    op = d.get("operator_id") or "?"
    return f"Agent {inst} unbound by {op}"


def _san_secret_set(d: dict) -> str:
    inst = _short(d.get("instance_id"))
    name = d.get("name") or "<?>"
    return f"Secret {name!r} set for {inst}"


def _san_secret_revoked(d: dict) -> str:
    inst = _short(d.get("instance_id"))
    name = d.get("name") or "<?>"
    return f"Secret {name!r} revoked from {inst}"


def _san_secret_revealed(d: dict) -> str:
    # Names are operator-pinned at provisioning, so revealing the NAME
    # is consistent with what's already in the chain. Value is never
    # in the chain regardless.
    inst = _short(d.get("instance_id"))
    name = d.get("name") or "<?>"
    return f"Secret {name!r} revealed to {inst}"


def _san_secret_blocked(d: dict) -> str:
    inst = _short(d.get("instance_id"))
    name = d.get("name") or "<?>"
    return f"Secret {name!r} blocked for {inst} (not in allowlist)"


def _san_tool_dispatched(d: dict) -> str:
    inst = _short(d.get("instance_id"))
    tool = d.get("tool_key") or "<?>"
    return f"{inst} dispatched {tool}"


def _san_tool_succeeded(d: dict) -> str:
    return f"Tool succeeded ({d.get('tool_key', '?')})"


def _san_tool_failed(d: dict) -> str:
    reason = (d.get("exception_message") or "")[:120]
    return f"Tool failed ({d.get('tool_key', '?')})" + (f" — {reason}" if reason else "")


def _san_tool_refused(d: dict) -> str:
    return f"Tool refused ({d.get('tool_key', '?')}) — {d.get('reason', '')}"


def _san_skill_invoked(d: dict) -> str:
    return f"Skill invoked ({d.get('skill_name', '?')})"


def _san_skill_completed(d: dict) -> str:
    return f"Skill completed ({d.get('skill_name', '?')})"


def _san_default(event_type: str):
    """Fallback sanitizer for events we don't have a hand-written rule for.
    Returns just the event type — never reaches into payload."""
    def _f(_d: dict) -> str:
        return f"({event_type})"
    return _f


# Hand-written sanitizers per event type. Anything not listed here gets
# the default (type-name only) sanitizer at lookup time.
SANITIZERS: dict[str, Any] = {
    "chain_created":               _san_chain_created,
    "agent_created":               _san_agent_created,
    "agent_archived":              _san_agent_archived,
    "agent_delegated":             _san_agent_delegated,
    "ceremony":                    _san_ceremony,
    "memory_verified":             _san_memory_verified,
    "memory_verification_revoked": _san_memory_verification_revoked,
    "out_of_triune_attempt":       _san_out_of_triune_attempt,
    "governance_relaxed":          _san_governance_relaxed,        # T2.1
    "spawn_genre_override":        _san_spawn_genre_override,
    "hardware_bound":              _san_hardware_bound,
    "hardware_mismatch":           _san_hardware_mismatch,
    "hardware_unbound":            _san_hardware_unbound,
    "secret_set":                  _san_secret_set,
    "secret_revoked":              _san_secret_revoked,
    "secret_revealed":             _san_secret_revealed,
    "secret_blocked":              _san_secret_blocked,
    "tool_call_dispatched":        _san_tool_dispatched,
    "tool_call_succeeded":         _san_tool_succeeded,
    "tool_call_failed":            _san_tool_failed,
    "tool_call_refused":           _san_tool_refused,
    "skill_invoked":               _san_skill_invoked,
    "skill_completed":             _san_skill_completed,
}


def sanitize_event(entry: ChainEntry) -> str:
    """Produce a safe one-line summary for an audit entry. Never
    surfaces raw payload fields outside the per-type sanitizers."""
    fn = SANITIZERS.get(entry.event_type, _san_default(entry.event_type))
    try:
        return fn(entry.event_data or {})
    except Exception:
        # A malformed payload should never break the renderer — fall back
        # to the type-only summary.
        return f"({entry.event_type})"


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
def filter_by_dna(
    entries: Iterable[ChainEntry], dna_short: str,
) -> list[ChainEntry]:
    """Return entries whose ``agent_dna`` matches ``dna_short``. The
    audit chain stores 12-char short DNA — pass ``AgentRow.dna``, not
    the 64-char ``dna_full``."""
    return [e for e in entries if e.agent_dna == dna_short]


def filter_by_bond_name(
    entries: Iterable[ChainEntry], bond_name: str,
) -> list[ChainEntry]:
    """Return entries that reference a triune bond by name. Pulls in:

    * ``ceremony`` events whose ``event_data.bond_name`` matches
    * ``agent_delegated`` events whose ``event_data.triune_bond_name``
      matches (triune-internal calls)
    * ``out_of_triune_attempt`` events whose ``event_data.bond_name``
      matches (rejected calls aimed at this bond)
    """
    out: list[ChainEntry] = []
    for e in entries:
        d = e.event_data or {}
        if e.event_type == "ceremony" and d.get("bond_name") == bond_name:
            out.append(e)
        elif e.event_type == "agent_delegated" and d.get("triune_bond_name") == bond_name:
            out.append(e)
        elif e.event_type == "out_of_triune_attempt" and d.get("bond_name") == bond_name:
            out.append(e)
    return out


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------
def render_markdown(
    entries: list[ChainEntry],
    *,
    title: str,
    include_payload: bool = False,
    sort_reverse: bool = False,
) -> str:
    """Plain markdown timeline — one row per event."""
    items = sorted(entries, key=lambda e: e.seq, reverse=sort_reverse)
    lines: list[str] = [
        f"# {title}",
        "",
        f"_Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
        f"Events: {len(items)}  ·  Milestones: {sum(1 for e in items if is_milestone(e))}",
        "",
        "---",
        "",
    ]
    for e in items:
        marker = "★" if is_milestone(e) else " "
        lines.append(
            f"- {marker} **seq {e.seq}** · `{e.timestamp}` · "
            f"`{e.event_type}` — {sanitize_event(e)}"
        )
        if include_payload and e.event_data:
            lines.append("  ```json")
            for ln in json.dumps(e.event_data, indent=2, default=str).splitlines():
                lines.append(f"  {ln}")
            lines.append("  ```")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# HTML renderer — single-file, inline CSS, tiny JS toggle
# ---------------------------------------------------------------------------
_HTML_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{
    --bg: #0f1419; --fg: #e8e6e3; --muted: #9ca3af;
    --card: #1a2026; --card-hover: #232a32; --line: #2a3038;
    --accent: #8aa9c5; --accent-strong: #c5deea;
    --milestone: #d4a574; --warn: #e07b7b; --ok: #88c082;
    --code: #11161b;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; background: var(--bg); color: var(--fg);
                font-family: -apple-system, system-ui, sans-serif; line-height: 1.45; }}
  .wrap {{ max-width: 880px; margin: 0 auto; padding: 32px 20px 64px; }}
  header h1 {{ font-size: 28px; margin: 0 0 4px; color: var(--accent-strong); font-weight: 600; }}
  header .sub {{ color: var(--muted); font-size: 13px; margin-bottom: 20px; }}
  .stats {{ display: flex; gap: 24px; padding: 12px 16px; background: var(--card);
            border: 1px solid var(--line); border-radius: 6px; margin-bottom: 20px;
            font-size: 13px; }}
  .stats span b {{ color: var(--accent-strong); margin-left: 4px; }}
  .controls {{ margin-bottom: 16px; padding: 10px 14px; background: var(--card);
               border: 1px solid var(--line); border-radius: 6px; display: flex;
               align-items: center; gap: 14px; }}
  .controls label {{ font-size: 13px; color: var(--muted); cursor: pointer;
                     user-select: none; display: flex; align-items: center; gap: 6px; }}
  .controls input[type=checkbox] {{ accent-color: var(--accent); }}
  .timeline {{ position: relative; padding-left: 28px; }}
  .timeline::before {{ content: ''; position: absolute; left: 8px; top: 0; bottom: 0;
                       width: 2px; background: var(--line); }}
  .event {{ position: relative; margin-bottom: 14px; padding: 12px 14px;
            background: var(--card); border: 1px solid var(--line); border-radius: 6px;
            transition: background 0.1s; }}
  .event:hover {{ background: var(--card-hover); }}
  .event::before {{ content: ''; position: absolute; left: -25px; top: 16px;
                    width: 12px; height: 12px; border-radius: 50%;
                    background: var(--card); border: 2px solid var(--accent); }}
  .event.milestone::before {{ background: var(--milestone); border-color: var(--milestone); }}
  .event.warn::before {{ background: var(--warn); border-color: var(--warn); }}
  .event .meta {{ display: flex; gap: 12px; align-items: center; font-size: 12px;
                  color: var(--muted); margin-bottom: 4px; }}
  .event .seq {{ font-family: ui-monospace, Menlo, monospace; }}
  .event .type {{ padding: 2px 8px; background: var(--code); border-radius: 3px;
                  font-family: ui-monospace, Menlo, monospace; font-size: 11px;
                  color: var(--accent); }}
  .event .summary {{ color: var(--fg); font-size: 14px; }}
  .event .summary .arrow {{ color: var(--muted); }}
  .event details {{ margin-top: 8px; }}
  .event details summary {{ cursor: pointer; color: var(--muted); font-size: 12px; }}
  .event details pre {{ margin: 8px 0 0; padding: 10px; background: var(--code);
                        border-radius: 4px; overflow-x: auto; font-size: 11px;
                        color: var(--accent-strong); }}
  .empty {{ color: var(--muted); font-style: italic; padding: 40px; text-align: center; }}
  .footer {{ margin-top: 40px; padding-top: 14px; border-top: 1px solid var(--line);
             color: var(--muted); font-size: 11px; text-align: center; }}
  .footer code {{ background: var(--code); padding: 1px 5px; border-radius: 3px; }}
</style>
</head>
<body>
<div class="wrap">
"""

_HTML_TAIL = """</div>
<script>
  // Milestone-only toggle. Pure DOM filter — no regen, no fetch.
  document.getElementById('milestonesOnly').addEventListener('change', function(e) {
    var on = e.target.checked;
    document.querySelectorAll('.event').forEach(function(el) {
      var isMs = el.classList.contains('milestone');
      el.style.display = (on && !isMs) ? 'none' : '';
    });
  });
</script>
</body>
</html>
"""


def _event_classes(entry: ChainEntry) -> str:
    cls = ["event"]
    if is_milestone(entry):
        cls.append("milestone")
    if entry.event_type in (
        "out_of_triune_attempt", "tool_call_failed",
        "tool_call_refused", "secret_blocked",
        "governance_relaxed",          # T2.1: operator constraint-relaxation
        "spawn_genre_override",        # the dedicated subset
        "hardware_mismatch",           # K6 quarantine fire
    ):
        cls.append("warn")
    return " ".join(cls)


def render_html(
    entries: list[ChainEntry],
    *,
    title: str,
    subtitle: str | None = None,
    include_payload: bool = False,
    sort_reverse: bool = False,
) -> str:
    """Render entries as a single self-contained HTML page.

    - inline CSS, no external assets
    - tiny JS for the milestone toggle (no framework)
    - safe to email or attach — payload is sanitized by default
    """
    items = sorted(entries, key=lambda e: e.seq, reverse=sort_reverse)
    milestone_count = sum(1 for e in items if is_milestone(e))
    spans: list[str] = []
    if items:
        spans = [items[0].timestamp, items[-1].timestamp]

    head = _HTML_HEAD.format(title=html.escape(title))
    parts: list[str] = [head]
    parts.append("<header>")
    parts.append(f'<h1>{html.escape(title)}</h1>')
    if subtitle:
        parts.append(f'<div class="sub">{html.escape(subtitle)}</div>')
    parts.append("</header>")

    parts.append('<div class="stats">')
    parts.append(f'<span>Events <b>{len(items)}</b></span>')
    parts.append(f'<span>Milestones <b>{milestone_count}</b></span>')
    if spans:
        parts.append(f'<span>Span <b>{html.escape(spans[0])}</b> → <b>{html.escape(spans[-1])}</b></span>')
    parts.append("</div>")

    parts.append('<div class="controls">')
    parts.append(
        '<label><input type="checkbox" id="milestonesOnly"> '
        'show milestones only</label>'
    )
    parts.append('</div>')

    if not items:
        parts.append('<div class="empty">No events to render.</div>')
    else:
        parts.append('<div class="timeline">')
        for e in items:
            classes = _event_classes(e)
            summary_text = sanitize_event(e)
            parts.append(f'<div class="{classes}">')
            parts.append('<div class="meta">')
            parts.append(f'<span class="seq">seq {e.seq}</span>')
            parts.append(f'<span>{html.escape(e.timestamp)}</span>')
            parts.append(f'<span class="type">{html.escape(e.event_type)}</span>')
            if e.agent_dna:
                parts.append(f'<span>dna {html.escape(e.agent_dna)}</span>')
            parts.append('</div>')
            parts.append(f'<div class="summary">{html.escape(summary_text)}</div>')
            if include_payload and e.event_data:
                pretty = json.dumps(e.event_data, indent=2, default=str, sort_keys=True)
                parts.append(
                    '<details><summary>raw payload</summary>'
                    f'<pre>{html.escape(pretty)}</pre></details>'
                )
            parts.append('</div>')
        parts.append('</div>')

    parts.append('<div class="footer">')
    parts.append(
        f'Forest Soul Forge chronicle · generated '
        f'{html.escape(datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"))} · '
        '<code>fsf chronicle</code>'
    )
    if not include_payload:
        parts.append(
            '<br>payload omitted by default — pass <code>--include-payload</code> '
            'to embed event_data fields'
        )
    parts.append('</div>')

    parts.append(_HTML_TAIL)
    return "".join(parts)
