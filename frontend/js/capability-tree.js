// ADR-0080 T2 (B381) — Agent Capabilities tab controller.
//
// Renders a per-agent dependency tree of effective capabilities
// composed by the B380 backend at GET /agents/{id}/capability-tree:
//   tools (constitution-bound, hard_wired binding)
//   skills (catalog-bound, operator_toggleable binding)
//   mcp_plugins (T1 placeholder)
//
// Three visual states per node:
//   live        ✓ green  — callable right now
//   broken      ✗ grey   — known but missing dep / unregistered
//   in_progress ⏳ amber — staged but not yet installed (skills)
//
// Two binding modes:
//   hard_wired         🔒 — constitution-bound; operator cannot
//                            toggle off (rebirth required)
//   operator_toggleable ☐ — togglable via posture (T3 toggle
//                            endpoint will land the on/off action;
//                            T1+T2 ship visibility only)
//
// Click any node to populate the detail pane with its full row.

import { api, ApiError } from "./api.js";
import * as state from "./state.js";

let _selectedAgent = "";
let _lastTree = null;
let _initialized = false;

function _escape(s) {
  const t = String(s == null ? "" : s);
  return t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// State -> visual class. Centralised so a future theme change
// touches one place.
const STATUS_CLASS = {
  live: "cap-node--live",
  broken: "cap-node--broken",
  in_progress: "cap-node--inprogress",
};

const STATUS_GLYPH = {
  live: "✓",
  broken: "✗",
  in_progress: "⏳",
};

const BINDING_GLYPH = {
  hard_wired: "🔒",
  operator_toggleable: "☐",
};

function _node(label, status, binding, payload) {
  // One leaf node element. Click handler attaches in render.
  const el = document.createElement("div");
  el.className = `cap-node ${STATUS_CLASS[status] || ""}`;
  el.setAttribute("role", "treeitem");
  el.dataset.status = status;
  el.dataset.binding = binding;
  el.style.cssText = (
    "padding:4px 8px; margin:2px 0; cursor:pointer; border-radius:3px; " +
    "display:flex; align-items:center; gap:8px; font-family:monospace;"
  );

  // Status colorization is inline (so a missing CSS file doesn't
  // strand the user) plus class-based (so a theme can override).
  const statusColors = {
    live: "var(--color-ok, #2e7d32)",
    broken: "var(--color-bad-muted, #6b6b6b)",
    in_progress: "var(--color-warn, #ffa726)",
  };
  el.style.color = statusColors[status] || "inherit";

  const glyph = document.createElement("span");
  glyph.textContent = STATUS_GLYPH[status] || "?";
  glyph.style.cssText = "width:16px; text-align:center; flex-shrink:0;";
  el.appendChild(glyph);

  const lockGlyph = document.createElement("span");
  lockGlyph.textContent = BINDING_GLYPH[binding] || "";
  lockGlyph.style.cssText = "width:16px; text-align:center; flex-shrink:0; opacity:0.7;";
  lockGlyph.title = binding === "hard_wired"
    ? "Hard-wired by constitution; rebirth required to remove"
    : "Operator-toggleable (T3 will add the toggle action)";
  el.appendChild(lockGlyph);

  const labelEl = document.createElement("span");
  labelEl.textContent = label;
  labelEl.style.flex = "1";
  el.appendChild(labelEl);

  el.addEventListener("click", () => _renderDetail(payload));
  return el;
}

function _section(title, count, summary) {
  // Group header for tools / skills / mcp_plugins.
  const wrap = document.createElement("div");
  wrap.style.cssText = "margin-bottom:18px;";
  const h = document.createElement("h3");
  h.style.cssText = "font-size:14px; margin:0 0 6px 0; color:var(--color-muted, #888);";
  h.textContent = `${title} (${count}${summary ? `, ${summary}` : ""})`;
  wrap.appendChild(h);
  return wrap;
}

function _renderDetail(payload) {
  const detail = document.getElementById("cap-detail");
  if (!detail) return;
  if (!payload) {
    detail.innerHTML = '<em class="muted">Click a node to see its details.</em>';
    return;
  }
  const lines = [];
  for (const [k, v] of Object.entries(payload)) {
    if (v === null || v === undefined) continue;
    const val = (typeof v === "object")
      ? `<pre style="margin:4px 0; font-size:11px;">${_escape(JSON.stringify(v, null, 2))}</pre>`
      : _escape(String(v));
    lines.push(`<div style="margin:4px 0;"><strong style="opacity:0.7;">${_escape(k)}:</strong> ${val}</div>`);
  }
  detail.innerHTML = lines.join("");
}

function _renderTree(tree) {
  const container = document.getElementById("cap-tree");
  if (!container) return;
  container.innerHTML = "";

  // Tools group.
  const toolsSec = _section(
    "Tools (constitution-bound)",
    tree.tools.length,
    tree.tools.length
      ? `${tree.tools.filter(t => t.status === "live").length} live`
      : "",
  );
  for (const t of tree.tools) {
    toolsSec.appendChild(_node(
      `${t.key}  (${t.side_effects || "?"})`,
      t.status,
      t.binding,
      t,
    ));
  }
  if (!tree.tools.length) {
    const empty = document.createElement("div");
    empty.className = "muted";
    empty.style.cssText = "padding:4px 8px; font-style:italic;";
    empty.textContent = "No constitution-bound tools (broken constitution or empty kit).";
    toolsSec.appendChild(empty);
  }
  container.appendChild(toolsSec);

  // Skills group.
  const skillsSec = _section(
    "Skills (operator-toggleable)",
    tree.skills.length,
    tree.skills.length
      ? `${tree.skills.filter(s => s.status === "live").length} live, ${tree.skills.filter(s => s.status === "broken").length} broken`
      : "",
  );
  for (const s of tree.skills) {
    skillsSec.appendChild(_node(
      `${s.name}.v${s.version}` + (s.missing_tools.length ? `  (missing: ${s.missing_tools.join(", ")})` : ""),
      s.status,
      s.binding,
      s,
    ));
  }
  if (!tree.skills.length) {
    const empty = document.createElement("div");
    empty.className = "muted";
    empty.style.cssText = "padding:4px 8px; font-style:italic;";
    empty.textContent = "No skills installed.";
    skillsSec.appendChild(empty);
  }
  container.appendChild(skillsSec);

  // MCP plugins group — T1 placeholder; populated when ADR-0043
  // per-agent grants surface lands.
  if (tree.mcp_plugins && tree.mcp_plugins.length) {
    const mcpSec = _section("MCP Plugins", tree.mcp_plugins.length, "");
    for (const m of tree.mcp_plugins) {
      mcpSec.appendChild(_node(m.name, m.status, m.binding, m));
    }
    container.appendChild(mcpSec);
  }
}

function _renderSummary(body) {
  const el = document.getElementById("cap-summary");
  if (!el) return;
  const a = body.agent;
  const s = body.summary;
  el.innerHTML = (
    `<strong>${_escape(a.agent_name || a.instance_id)}</strong> ` +
    `<span class="muted">(${_escape(a.role)}` +
    (a.genre ? ` / ${_escape(a.genre)}` : "") +
    (a.posture ? ` / ${_escape(a.posture)}` : "") +
    `)</span>` +
    ` &middot; tools: <strong>${s.tools_live}/${s.tools_total}</strong> live` +
    (s.tools_broken ? ` <span style="color:var(--color-bad, #ef5350);">(${s.tools_broken} broken)</span>` : "") +
    ` &middot; skills: <strong>${s.skills_live}/${s.skills_total}</strong> live` +
    (s.skills_broken ? ` <span style="color:var(--color-bad, #ef5350);">(${s.skills_broken} broken)</span>` : "")
  );
}

async function _loadTreeFor(instanceId) {
  if (!instanceId) {
    document.getElementById("cap-tree").innerHTML = "";
    document.getElementById("cap-summary").innerHTML =
      "Select an agent to view its capability tree.";
    return;
  }
  const tree = document.getElementById("cap-tree");
  tree.innerHTML = '<div class="muted" style="padding:8px;">Loading…</div>';
  try {
    const body = await api.get(
      `/agents/${encodeURIComponent(instanceId)}/capability-tree`
    );
    _lastTree = body;
    _renderSummary(body);
    _renderTree(body.tree);
  } catch (e) {
    const msg = (e instanceof ApiError)
      ? `HTTP ${e.status}: ${e.message}`
      : (e.message || String(e));
    tree.innerHTML =
      `<div class="muted" style="padding:8px; color:var(--color-bad, #ef5350);">` +
      `Failed to load: ${_escape(msg)}</div>`;
  }
}

function _populatePicker(agents) {
  const picker = document.getElementById("cap-agent-picker");
  if (!picker) return;
  const prev = picker.value;
  picker.innerHTML = '<option value="">— pick an agent —</option>';
  const active = (agents || []).filter(a => a.status === "active");
  // Sort by agent_name (fallback to role + instance) for stable ordering.
  active.sort((x, y) =>
    (x.agent_name || x.role || "").localeCompare(y.agent_name || y.role || "")
  );
  for (const a of active) {
    const opt = document.createElement("option");
    opt.value = a.instance_id;
    opt.textContent =
      `${a.agent_name || a.instance_id} · ${a.role}` +
      (a.posture ? ` (${a.posture})` : "");
    picker.appendChild(opt);
  }
  // Restore prior selection if the agent still exists.
  if (prev && active.find(a => a.instance_id === prev)) {
    picker.value = prev;
  }
}

export function start() {
  if (_initialized) return;
  _initialized = true;

  const picker = document.getElementById("cap-agent-picker");
  const refreshBtn = document.getElementById("cap-refresh");

  if (picker) {
    picker.addEventListener("change", () => {
      _selectedAgent = picker.value;
      _loadTreeFor(_selectedAgent);
    });
  }
  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => _loadTreeFor(_selectedAgent));
  }

  // Subscribe to the shared agent list (state.agents) so the
  // picker stays in sync as agents are born / archived.
  state.subscribe("agents", _populatePicker);

  // Initial paint with whatever state.agents has right now.
  const initial = state.get("agents") || [];
  _populatePicker(initial);
}
