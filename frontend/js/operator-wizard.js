// Operator wizard pane — ADR-0068 T7b (B318).
//
// Walks the operator through per-domain connector consent. Reads
// /orchestrator/domains to learn which connectors each domain
// expects, joins against /operator/profile/connectors for current
// state, lets the operator grant / deny each one. Decisions land
// via POST /operator/connectors/{domain_id}/{connector_name}.
//
// The pane is intentionally minimal — three buttons per row
// (Grant / Deny / Mark Pending) + a notes textarea. The operator
// builds up consent state one click at a time; nothing auto-fires.
//
// Sibling shape to reality-anchor.js and orchestrator.js — same
// panel chrome, lazy-load on first tab activation, toast posture.

import { api, writeCall } from "./api.js";
import { toast } from "./toast.js";


function statusChip(status) {
  const span = document.createElement("span");
  span.style.cssText = "padding:1px 6px;border-radius:3px;font-size:11px;";
  if (status === "granted") {
    span.style.background = "#1f3a1f";
    span.style.color = "#aef0ae";
  } else if (status === "denied") {
    span.style.background = "#3a1f1f";
    span.style.color = "#f0aeae";
  } else {
    span.style.background = "#3a2f1f";
    span.style.color = "#f0d8ae";
  }
  span.textContent = status;
  return span;
}


// ---------------------------------------------------------------------------
// Status hero
// ---------------------------------------------------------------------------

async function refreshStatus() {
  const root = document.getElementById("op-status");
  if (!root) return;
  root.textContent = "loading…";
  try {
    const c = await api.get("/operator/profile/connectors");
    const total = (c.connectors || []).length;
    const granted = (c.connectors || []).filter((x) => x.status === "granted").length;
    const denied = (c.connectors || []).filter((x) => x.status === "denied").length;
    const pending = (c.connectors || []).filter((x) => x.status === "pending").length;
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
      cell.appendChild(v); cell.appendChild(l);
      if (hint) cell.title = hint;
      return cell;
    };
    grid.appendChild(stat("operator", c.operator_id || "—"));
    grid.appendChild(stat("decisions", total));
    grid.appendChild(stat("granted", granted));
    grid.appendChild(stat("denied", denied));
    grid.appendChild(stat("pending", pending));
    root.appendChild(grid);
  } catch (e) {
    root.textContent = "Failed to load operator status: " + e.message;
    root.style.color = "var(--danger,#ff6b6b)";
  }
}


// ---------------------------------------------------------------------------
// Connectors table
// ---------------------------------------------------------------------------

async function refreshConnectors() {
  const root = document.getElementById("op-connectors");
  if (!root) return;
  root.textContent = "loading…";
  try {
    const [domainsResp, consentResp] = await Promise.all([
      api.get("/orchestrator/domains"),
      api.get("/operator/profile/connectors"),
    ]);
    const domains = domainsResp.domains || [];
    const consentMap = new Map();
    for (const c of (consentResp.connectors || [])) {
      consentMap.set(`${c.domain_id}:${c.connector_name}`, c);
    }
    root.innerHTML = "";

    // Build per-domain rows.
    if (domains.length === 0) {
      root.textContent = "No domains declared. Check config/domains/.";
      return;
    }

    for (const d of domains) {
      const connectors = d.depends_on_connectors || [];
      if (connectors.length === 0) continue;

      const block = document.createElement("div");
      block.style.cssText =
        "border:1px solid var(--border,#2c303a);border-radius:6px;"
        + "padding:10px;margin-bottom:12px;";

      const header = document.createElement("div");
      header.style.cssText =
        "display:flex;align-items:center;gap:8px;margin-bottom:8px;";
      const title = document.createElement("strong");
      title.textContent = d.name + " ";
      const sub = document.createElement("span");
      sub.style.cssText = "color:var(--muted,#aaa);font-size:12px;";
      sub.textContent = `(${d.domain_id})`;
      header.appendChild(title);
      header.appendChild(sub);
      block.appendChild(header);

      for (const connectorName of connectors) {
        const existing = consentMap.get(`${d.domain_id}:${connectorName}`);
        block.appendChild(
          _renderConnectorRow(d.domain_id, connectorName, existing),
        );
      }
      root.appendChild(block);
    }
  } catch (e) {
    root.textContent = "Failed to load connectors: " + e.message;
    root.style.color = "var(--danger,#ff6b6b)";
  }
}


function _renderConnectorRow(domainId, connectorName, existing) {
  const row = document.createElement("div");
  row.style.cssText =
    "display:flex;align-items:center;gap:8px;padding:6px 0;"
    + "border-top:1px solid #1c2028;font-size:13px;";

  const name = document.createElement("span");
  name.style.cssText = "min-width:200px;font-family:var(--mono,monospace);";
  name.textContent = connectorName;

  const chipWrap = document.createElement("span");
  chipWrap.style.cssText = "min-width:80px;";
  chipWrap.appendChild(statusChip((existing && existing.status) || "—"));

  const decided = document.createElement("span");
  decided.style.cssText = "color:var(--muted,#888);font-size:11px;min-width:160px;";
  decided.textContent = (existing && existing.decided_at) || "";

  const actions = document.createElement("span");
  actions.style.cssText = "display:flex;gap:4px;";
  const mkBtn = (label, status) => {
    const b = document.createElement("button");
    b.className = "btn btn--ghost btn--sm";
    b.type = "button";
    b.textContent = label;
    b.addEventListener("click", async () => {
      await _decide(domainId, connectorName, status, b);
    });
    return b;
  };
  actions.appendChild(mkBtn("Grant", "granted"));
  actions.appendChild(mkBtn("Deny", "denied"));
  actions.appendChild(mkBtn("Pending", "pending"));

  row.append(name, chipWrap, decided, actions);
  return row;
}


// ---------------------------------------------------------------------------
// Decide — POST one consent
// ---------------------------------------------------------------------------

async function _decide(domainId, connectorName, newStatus, button) {
  const reason = window.prompt(
    `Notes for setting ${connectorName} = ${newStatus}? (optional)`,
    "",
  );
  // Empty notes are fine; null cancels.
  if (reason === null) return;

  button.disabled = true;
  button.textContent = "…";
  try {
    const path = `/operator/connectors/${encodeURIComponent(domainId)}/${encodeURIComponent(connectorName)}`;
    const body = { status: newStatus };
    if (reason) body.notes = reason;
    const result = await writeCall("POST", path, body);
    toast({
      title: "Consent updated",
      msg: `${connectorName}: ${result.old_status || "—"} → ${result.new_status}`,
      kind: "success",
      ttl: 4000,
    });
    // Re-render both sections to reflect the change.
    await Promise.all([refreshStatus(), refreshConnectors()]);
  } catch (e) {
    toast({
      title: "Failed to update consent",
      msg: e.message,
      kind: "error",
      ttl: 8000,
    });
    button.disabled = false;
    button.textContent = newStatus.charAt(0).toUpperCase() + newStatus.slice(1);
  }
}


// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

export function start() {
  const refreshBtn = document.getElementById("op-refresh-btn");
  if (!refreshBtn) return;  // Tab not present (degraded HTML).

  const refreshAll = () => Promise.all([refreshStatus(), refreshConnectors()]);
  refreshBtn.addEventListener("click", refreshAll);

  // Lazy-load on first tab activation — same pattern as
  // reality-anchor.js, security.js, orchestrator.js.
  let bootstrapped = false;
  document.querySelectorAll(".tab").forEach((t) => {
    if (t.dataset.tab !== "operator-wizard") return;
    t.addEventListener("click", () => {
      if (bootstrapped) return;
      bootstrapped = true;
      refreshAll();
    });
  });
}
