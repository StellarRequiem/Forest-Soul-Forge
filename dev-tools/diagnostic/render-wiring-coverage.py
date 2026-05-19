#!/usr/bin/env python3
"""ADR-0081 T2 (B395) - render section-15's coverage.json into a
self-contained wiring-coverage.html page.

The HTML answers the operator's standing question: "show me one
page where I can see everything wired correctly OR not, and click
on something to see what's wrong." It mirrors section-15's four
checks (orphan tools, skill resolution, skill carrier, handoff
end-to-end) plus a per-tool carrier matrix drilldown.

Daemon-independent; reads the structured coverage.json that
section-15 emits. Outputs a single HTML file with no external
deps (inline CSS, no JS frameworks). Operator opens it in any
browser.

Usage:
    render-wiring-coverage.py <coverage.json> <output.html>

Both arguments are absolute or relative paths. The output HTML
expects to be opened in a browser - no server needed.
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path


def _row(cells: list[str], header: bool = False) -> str:
    tag = "th" if header else "td"
    return "<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>"


def _status_badge(label: str, count: int, kind: str) -> str:
    """Render a colored summary chip. kind: ok|warn|fail|info."""
    color = {
        "ok":   "#2e7d32",
        "warn": "#f57f17",
        "fail": "#c62828",
        "info": "#1565c0",
    }.get(kind, "#555")
    return (
        f'<span style="display:inline-block;padding:4px 10px;'
        f'border-radius:12px;background:{color};color:#fff;'
        f'font-size:13px;margin-right:8px;">'
        f'{html.escape(label)}: <b>{count}</b></span>'
    )


def render(coverage: dict) -> str:
    summary = coverage.get("summary", {})
    timestamp = coverage.get("timestamp", "?")

    orphan_tools = coverage.get("orphan_tools", [])
    kit_only_tools = coverage.get("kit_only_tools", [])
    skills_unresolvable = coverage.get("skills_unresolvable", [])
    skills_no_carrier = coverage.get("skills_no_carrier", [])
    handoffs_broken = coverage.get("handoffs_broken", [])
    tool_carriers = coverage.get("tool_carriers", {})

    tools_total = summary.get("tools_total", 0)
    tools_orphan = summary.get("tools_orphan", 0)
    tools_kit = summary.get("tools_kit_no_agent", 0)
    skills_total = summary.get("skills_total", 0)
    skills_unres = summary.get("skills_unresolvable", 0)
    skills_noc = summary.get("skills_no_carrier", 0)
    handoffs_total = summary.get("handoffs_total", 0)
    handoffs_brk = summary.get("handoffs_broken", 0)

    overall_ok = (tools_orphan == 0 and skills_unres == 0
                  and skills_noc == 0 and handoffs_brk == 0)
    overall_color = "#2e7d32" if overall_ok else "#c62828"
    overall_label = "ALL WIRED" if overall_ok else "GAPS DETECTED"

    # --- Section 1: top-level chips
    chips = (
        _status_badge("Tools (catalog)", tools_total, "info")
        + _status_badge("Orphan tools",   tools_orphan,
                        "fail" if tools_orphan else "ok")
        + _status_badge("Kit-only (no agent)", tools_kit,
                        "warn" if tools_kit else "ok")
        + _status_badge("Skills",        skills_total, "info")
        + _status_badge("Unresolvable",  skills_unres,
                        "fail" if skills_unres else "ok")
        + _status_badge("No carrier",    skills_noc,
                        "fail" if skills_noc else "ok")
        + _status_badge("Handoffs",      handoffs_total, "info")
        + _status_badge("Broken handoffs", handoffs_brk,
                        "fail" if handoffs_brk else "ok")
    )

    # --- Section 2: orphan tools table
    if orphan_tools:
        orphan_rows = "".join(
            _row([html.escape(t),
                  "<i>(none — cataloged but no archetype kit OR agent carries it)</i>"])
            for t in orphan_tools
        )
        orphan_block = (
            "<h2 id='orphans'>Orphan tools — cataloged but no carrier</h2>"
            "<p>These tools are registered in <code>config/tool_catalog.yaml</code> "
            "but no archetype kit, genre default, or alive agent constitution carries "
            "them. They are either retirement candidates or kit-assignment candidates. "
            "Operator decides per-tool.</p>"
            "<table><thead>"
            + _row(["Tool", "Carriers"], header=True)
            + "</thead><tbody>" + orphan_rows + "</tbody></table>"
        )
    else:
        orphan_block = "<h2 id='orphans'>Orphan tools</h2><p>None - every cataloged tool has at least one carrier.</p>"

    # --- Section 3: kit-only tools (INFO)
    if kit_only_tools:
        kit_rows = "".join(
            _row([html.escape(t),
                  ", ".join(html.escape(a) for a in
                            tool_carriers.get(t, {}).get("archetypes", []))])
            for t in kit_only_tools
        )
        kit_block = (
            "<h2 id='kit-only'>Tools in archetype kits but no alive agent yet</h2>"
            "<p>These tools are in at least one archetype's <code>standard_tools</code> "
            "but no alive agent currently carries them in its constitution. Expected "
            "during rollouts (rebirth required to refresh existing agents' kits).</p>"
            "<table><thead>"
            + _row(["Tool", "Archetype carriers"], header=True)
            + "</thead><tbody>" + kit_rows + "</tbody></table>"
        )
    else:
        kit_block = ""

    # --- Section 4: skill issues
    skill_block = "<h2 id='skills'>Skill wiring</h2>"
    if skills_unresolvable:
        skill_block += "<h3>Unresolvable requires</h3>"
        skill_rows = "".join(
            _row([html.escape(item["skill"]),
                  ", ".join(html.escape(m) for m in item["missing_from_catalog"])])
            for item in skills_unresolvable
        )
        skill_block += (
            "<table><thead>" + _row(["Skill", "Missing tools"], header=True)
            + "</thead><tbody>" + skill_rows + "</tbody></table>"
        )
    if skills_no_carrier:
        skill_block += "<h3>Skills with no carrier archetype</h3>"
        skill_rows = "".join(
            _row([html.escape(item["skill"]),
                  ", ".join(html.escape(r) for r in item["requires"])])
            for item in skills_no_carrier
        )
        skill_block += (
            "<table><thead>"
            + _row(["Skill", "Required tools (none archetype carries all)"],
                   header=True)
            + "</thead><tbody>" + skill_rows + "</tbody></table>"
        )
    if not skills_unresolvable and not skills_no_carrier:
        skill_block += (
            f"<p>All {skills_total} installed skills resolve to cataloged tools "
            "and have at least one archetype kit that carries all required tools.</p>"
        )

    # --- Section 5: handoffs
    handoff_block = "<h2 id='handoffs'>Handoff routes</h2>"
    if handoffs_broken:
        handoff_rows = "".join(
            _row([html.escape(item["domain"]),
                  html.escape(item["capability"]),
                  html.escape(item["reason"])])
            for item in handoffs_broken
        )
        handoff_block += (
            "<p>Each row is a <code>(domain, capability) &rarr; skill</code> route "
            "from <code>config/handoffs.yaml</code> that fails to resolve end-to-end. "
            "Each is either (a) build the missing skill, (b) re-route the handoff to "
            "an existing skill, or (c) remove the handoff entry.</p>"
            "<table><thead>"
            + _row(["Domain", "Capability", "Reason"], header=True)
            + "</thead><tbody>" + handoff_rows + "</tbody></table>"
        )
    else:
        handoff_block += (
            f"<p>All {handoffs_total} handoff routes resolve end-to-end "
            "(skill exists + at least one entry_agent role carries the required tools).</p>"
        )

    # --- Section 6: per-tool carrier matrix (drilldown).
    matrix_rows = ""
    for tool_key in sorted(tool_carriers.keys()):
        carriers = tool_carriers[tool_key]
        archs = carriers.get("archetypes", [])
        agents = carriers.get("agents", [])
        is_orphan = (not archs and not agents)
        is_kit_only = (archs and not agents)
        row_color = (
            "background:#fff3e0;"
            if is_kit_only else "background:#ffebee;"
            if is_orphan else ""
        )
        matrix_rows += (
            f'<tr style="{row_color}">'
            f'<td><code>{html.escape(tool_key)}</code></td>'
            f'<td>{len(archs)}</td>'
            f'<td>{len(agents)}</td>'
            f'<td>{", ".join(html.escape(a) for a in archs[:5])}'
            f'{"..." if len(archs) > 5 else ""}</td>'
            f'<td>{", ".join(html.escape(a) for a in agents[:5])}'
            f'{"..." if len(agents) > 5 else ""}</td>'
            f'</tr>'
        )
    matrix_block = (
        "<h2 id='matrix'>Per-tool carrier matrix</h2>"
        "<p>Every cataloged tool with its archetype/genre carriers and "
        "alive-agent carriers. Red row = orphan; orange row = kit-only "
        "(no alive agent yet, normal during rollouts); white = healthy.</p>"
        '<table><thead>'
        + _row(["Tool", "#Archetypes", "#Agents",
                "Sample archetypes", "Sample agents"], header=True)
        + f'</thead><tbody>{matrix_rows}</tbody></table>'
    )

    # --- assemble
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>FSF substrate wiring coverage</title>
  <style>
    body {{
      font-family: -apple-system, system-ui, sans-serif;
      max-width: 1200px; margin: 24px auto; padding: 0 16px; color: #222;
    }}
    h1 {{ font-size: 24px; margin: 0 0 4px; }}
    h2 {{ font-size: 18px; margin: 32px 0 8px; border-bottom: 1px solid #eee; padding-bottom: 4px; }}
    h3 {{ font-size: 15px; margin: 16px 0 6px; color: #c62828; }}
    .meta {{ color: #666; font-size: 13px; margin-bottom: 12px; }}
    .chips {{ margin: 12px 0 24px; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; margin: 8px 0 18px; }}
    th, td {{ padding: 6px 10px; border-bottom: 1px solid #eee; text-align: left; vertical-align: top; }}
    th {{ background: #f5f5f5; font-weight: 600; }}
    code {{ background: #f5f5f5; padding: 1px 4px; border-radius: 3px; font-size: 12px; }}
    a {{ color: #1565c0; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .nav {{ margin: 8px 0 20px; padding: 8px 12px; background: #f5f5f5; border-radius: 4px; font-size: 13px; }}
    .nav a {{ margin-right: 12px; }}
    .verdict {{
      display: inline-block; padding: 6px 14px; border-radius: 16px;
      background: {overall_color}; color: #fff; font-weight: 600;
      font-size: 14px; margin-left: 8px;
    }}
  </style>
</head>
<body>
  <h1>FSF substrate wiring coverage <span class="verdict">{overall_label}</span></h1>
  <div class="meta">
    Source: <code>section-15-wiring-cross-check</code> &nbsp;&middot;&nbsp;
    Generated: <code>{html.escape(timestamp)}</code> &nbsp;&middot;&nbsp;
    ADR-0081 T2
  </div>
  <div class="chips">{chips}</div>
  <div class="nav">
    Jump to:
    <a href="#orphans">Orphan tools ({tools_orphan})</a>
    <a href="#kit-only">Kit-only ({tools_kit})</a>
    <a href="#skills">Skills ({skills_unres + skills_noc} issues)</a>
    <a href="#handoffs">Handoffs ({handoffs_brk} broken)</a>
    <a href="#matrix">Per-tool matrix</a>
  </div>
  {orphan_block}
  {kit_block}
  {skill_block}
  {handoff_block}
  {matrix_block}
  <hr>
  <p style="color:#888;font-size:12px;">
    Generated by <code>dev-tools/diagnostic/render-wiring-coverage.py</code>
    (ADR-0081 T2). Reads <code>coverage.json</code> from
    <code>section-15-wiring-cross-check</code>. Re-run
    <code>diagnostic-all.command</code> to refresh.
  </p>
</body>
</html>
"""


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} <coverage.json> <output.html>", file=sys.stderr)
        return 2
    src = Path(argv[1])
    dst = Path(argv[2])
    if not src.exists():
        print(f"ERROR: coverage.json not found at {src}", file=sys.stderr)
        return 1
    try:
        coverage = json.loads(src.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"ERROR: failed to parse {src}: {e}", file=sys.stderr)
        return 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(render(coverage), encoding="utf-8")
    print(f"wrote wiring-coverage.html: {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
