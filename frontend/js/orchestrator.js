// Orchestrator pane — ADR-0067 T7 (B299).
//
// Operator window into the cross-domain routing substrate. Three
// sections drive off three GET endpoints from B285 (ADR-0067 T8):
//
//   1. Status card  — /orchestrator/status hero numbers.
//   2. Domains      — /orchestrator/domains manifest table.
//   3. Recent routes — /orchestrator/recent-routes timeline.
//
// Plus a reload button that POSTs /orchestrator/reload (the
// hot-reload path; operator edits config/domains/*.yaml on disk
// and clicks reload — no daemon restart).
//
// Read-only by design. Per ADR-0067 D1, the domain registry is
// the source of truth; the operator owns it via YAML edits.
// Sibling shape to reality-anchor.js and security.js — same
// panel chrome, same toast posture, same fmtTime helper.

import { api, writeCall } from "./api.js";
import { toast } from "./toast.js";


function fmtTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toISOString().replace("T", " ").slice(0, 19);
  } catch {
    return iso;
  }
}


function statusChip(status) {
  const span = document.createElement("span");
  span.className = `chip chip--status-${(status || "unknown").toLowerCase()}`;
  span.textContent = status || "?";
  // Inline minimal coloring — full theme tokens land if the
  // panel gets visual polish in a later tranche.
  if (status === "dispatchable") {
    span.style.cssText = "background:#1f3a1f;color:#aef0ae;padding:1px 6px;border-radius:3px;font-size:11px;";
  } else if (status === "planned") {
    span.style.cssText = "background:#3a2f1f;color:#f0d8ae;padding:1px 6px;border-radius:3px;font-size:11px;";
  } else if (status === "disabled") {
    span.style.cssText = "background:#3a1f1f;color:#f0aeae;padding:1px 6px;border-radius:3px;font-size:11px;";
  } else {
    span.style.cssText = "background:#2c303a;color:#aaa;padding:1px 6px;border-radius:3px;font-size:11px;";
  }
  return span;
}


// ---------------------------------------------------------------------------
// Status card
// ---------------------------------------------------------------------------

async function refreshStatus() {
  const root = document.getElementById("orch-status");
  if (!root) return;
  root.textContent = "loading…";
  try {
    const s = await api.get("/orchestrator/status");
    root.innerHTML = "";
    const grid = document.createElement("div");
    grid.className = "ra-status-grid";
    const stat = (label, value, hint = "") => {
      const cell = document.createElement("div");
      cell.className = "ra-stat";
      const v = document.createElement("div");
      v.className = "ra-stat__value";
      v.textContent = value;
      const l = document.createElement("div");
      l.className = "ra-stat__label";
      l.textContent = label;
      cell.appendChild(v);
      cell.appendChild(l);
      if (hint) cell.title = hint;
      return cell;
    };
    grid.appendChild(stat(
      "domains",
      s.registry.total_domains,
      "All declared domains in config/domains/*.yaml.",
    ));
    grid.appendChild(stat(
      "dispatchable",
      s.registry.dispatchable_domains,
      "Domains with status='dispatchable' — eligible for routing.",
    ));
    grid.appendChild(stat(
      "planned",
      s.registry.planned_domains,
      "Domains declared but not yet dispatchable (substrate not ready).",
    ));
    grid.appendChild(stat(
      "skill mappings",
      s.handoffs.skill_mapping_count,
      "Hardcoded routes in config/handoffs.yaml (capability → skill).",
    ));
    grid.appendChild(stat(
      "cascade rules",
      s.handoffs.cascade_rule_count,
      "Cross-domain follow-on rules (A finishes → B starts).",
    ));
    grid.appendChild(stat(
      "routes (24h)",
      s.routing_activity_24h.total_routes,
      "domain_routed audit events in the last 24 hours.",
    ));
    root.appendChild(grid);

    // Top domains by route count.
    const topRoutes = Object.entries(s.routing_activity_24h.by_target_domain || {});
    if (topRoutes.length > 0) {
      const wrap = document.createElement("div");
      wrap.style.cssText = "margin-top:10px;font-size:12px;color:var(--muted,#888);";
      wrap.innerHTML = "<strong style='color:var(--text,#eee)'>top targets (24h):</strong> " +
        topRoutes
          .map(([dom, n]) => `${dom} (${n})`)
          .join(" · ");
      root.appendChild(wrap);
    }

    // Surface any registry / handoffs errors prominently.
    const errs = [
      ...(s.registry.errors || []),
      ...(s.handoffs.errors || []),
    ];
    if (errs.length > 0) {
      const errBox = document.createElement("div");
      errBox.className = "ra-errors";
      errBox.innerHTML =
        "<strong>config errors:</strong> " +
        errs.map((e) => `<div>• ${escapeHtml(e)}</div>`).join("");
      root.appendChild(errBox);
    }
  } catch (e) {
    root.textContent = "Failed to load orchestrator status: " + e.message;
    root.style.color = "var(--danger,#ff6b6b)";
  }
}


// ---------------------------------------------------------------------------
// Domain manifest table
// ---------------------------------------------------------------------------

async function refreshDomains() {
  const root = document.getElementById("orch-domains");
  if (!root) return;
  root.textContent = "loading…";
  try {
    const r = await api.get("/orchestrator/domains");
    root.innerHTML = "";

    const table = document.createElement("table");
    table.style.cssText = "width:100%;border-collapse:collapse;font-size:13px;";
    const thead = document.createElement("thead");
    thead.innerHTML =
      "<tr>" +
      "<th style='text-align:left;padding:6px 8px;border-bottom:1px solid #2c303a'>ID</th>" +
      "<th style='text-align:left;padding:6px 8px;border-bottom:1px solid #2c303a'>Name</th>" +
      "<th style='text-align:left;padding:6px 8px;border-bottom:1px solid #2c303a'>Status</th>" +
      "<th style='text-align:left;padding:6px 8px;border-bottom:1px solid #2c303a'>Capabilities</th>" +
      "<th style='text-align:left;padding:6px 8px;border-bottom:1px solid #2c303a'>Description</th>" +
      "</tr>";
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    for (const d of (r.domains || [])) {
      const tr = document.createElement("tr");
      tr.style.cssText = "border-bottom:1px solid #1c2028;";
      const idCell = document.createElement("td");
      idCell.style.cssText = "padding:6px 8px;font-family:var(--mono,monospace);color:var(--muted,#aaa);";
      idCell.textContent = d.domain_id;
      const nameCell = document.createElement("td");
      nameCell.style.cssText = "padding:6px 8px;";
      nameCell.textContent = d.name;
      const statusCell = document.createElement("td");
      statusCell.style.cssText = "padding:6px 8px;";
      statusCell.appendChild(statusChip(d.status));
      const capCell = document.createElement("td");
      capCell.style.cssText = "padding:6px 8px;color:var(--muted,#aaa);";
      capCell.textContent = (d.capabilities || []).join(", ") || "—";
      const descCell = document.createElement("td");
      descCell.style.cssText = "padding:6px 8px;color:var(--muted,#aaa);font-size:12px;";
      descCell.textContent = d.description || "—";
      tr.append(idCell, nameCell, statusCell, capCell, descCell);
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    root.appendChild(table);

    if ((r.domains || []).length === 0) {
      const empty = document.createElement("div");
      empty.style.cssText = "color:var(--muted,#888);padding:8px;";
      empty.textContent = "No domains loaded. Check config/domains/.";
      root.appendChild(empty);
    }
  } catch (e) {
    root.textContent = "Failed to load domains: " + e.message;
    root.style.color = "var(--danger,#ff6b6b)";
  }
}


// ---------------------------------------------------------------------------
// Recent routes timeline
// ---------------------------------------------------------------------------

async function refreshRoutes() {
  const root = document.getElementById("orch-routes");
  if (!root) return;
  root.textContent = "loading…";
  try {
    const r = await api.get("/orchestrator/recent-routes?limit=100");
    root.innerHTML = "";
    const events = r.events || [];
    if (events.length === 0) {
      root.textContent = "No domain_routed events recorded.";
      root.style.color = "var(--muted,#888)";
      return;
    }
    const list = document.createElement("div");
    list.style.cssText = "font-family:var(--mono,monospace);font-size:12px;";
    for (const ev of events) {
      const row = document.createElement("div");
      row.style.cssText = "padding:4px 0;border-bottom:1px solid #1c2028;display:flex;gap:12px;";
      const ts = document.createElement("span");
      ts.style.cssText = "color:var(--muted,#888);min-width:160px;";
      ts.textContent = fmtTime(ev.timestamp);
      const target = document.createElement("span");
      target.style.cssText = "color:var(--accent,#9fc5ff);min-width:160px;";
      target.textContent = (ev.event_data && ev.event_data.target_domain) || "—";
      const cap = document.createElement("span");
      cap.style.cssText = "color:var(--text,#ddd);";
      cap.textContent = (ev.event_data && ev.event_data.capability) || "";
      row.append(ts, target, cap);
      list.appendChild(row);
    }
    root.appendChild(list);
  } catch (e) {
    root.textContent = "Failed to load recent routes: " + e.message;
    root.style.color = "var(--danger,#ff6b6b)";
  }
}


// ---------------------------------------------------------------------------
// Reload button — hot-reload the domain registry + handoffs.
// ---------------------------------------------------------------------------

async function reloadConfig() {
  try {
    await writeCall("POST", "/orchestrator/reload");
    toast({
      title: "Orchestrator reloaded",
      msg: "Domain registry + handoffs reloaded from disk.",
      kind: "success",
      ttl: 4000,
    });
    await Promise.all([refreshStatus(), refreshDomains()]);
  } catch (e) {
    toast({
      title: "Reload failed",
      msg: e.message,
      kind: "error",
      ttl: 8000,
    });
  }
}


// Defensive HTML escaper — same shape as reality-anchor's local
// helper. Avoids dragging in a shared util just for this one site.
function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}


export function start() {
  const refreshBtn = document.getElementById("orch-refresh-btn");
  const reloadBtn = document.getElementById("orch-reload-btn");
  if (!refreshBtn) return;  // Tab not present (degraded HTML).

  const refreshAll = () => Promise.all([
    refreshStatus(),
    refreshDomains(),
    refreshRoutes(),
  ]);
  refreshBtn.addEventListener("click", refreshAll);
  if (reloadBtn) reloadBtn.addEventListener("click", reloadConfig);

  // Lazy-load on first tab activation — same pattern as
  // reality-anchor.js and security.js. The pane stays cheap until
  // the operator clicks the tab.
  let bootstrapped = false;
  document.querySelectorAll(".tab").forEach((t) => {
    if (t.dataset.tab !== "orchestrator") return;
    t.addEventListener("click", () => {
      if (bootstrapped) return;
      bootstrapped = true;
      refreshAll();
    });
  });
}
