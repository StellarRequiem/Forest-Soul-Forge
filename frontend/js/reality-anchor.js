// Reality Anchor pane — ADR-0063 T7 (B256).
//
// Four sections:
//   1. Status card (fact count, recent flag/refuse counts, top repeat).
//   2. Ground-truth facts table — read-only viewer with severity chips.
//   3. Recent events timeline — last N reality_anchor_* audit events.
//   4. Repeat offenders table — agents that keep tripping the same fact.
//
// Read-only by design. Per ADR-0063 D3, the operator owns ground truth
// by editing config/ground_truth.yaml directly; the pane offers a
// Reload button to pick up edits without a daemon restart, but no
// in-UI editing.

import { api } from "./api.js";
import { toast } from "./toast.js";

const SEVERITY_RANK = { INFO: 0, LOW: 1, MEDIUM: 2, HIGH: 3, CRITICAL: 4 };

function severityChip(sev) {
  const span = document.createElement("span");
  span.className = `chip chip--sev-${(sev || "info").toLowerCase()}`;
  span.textContent = sev || "?";
  return span;
}

function fmtTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toISOString().replace("T", " ").slice(0, 19);
  } catch {
    return iso;
  }
}

function fmtShort(s, max = 80) {
  if (!s) return "";
  return s.length > max ? s.slice(0, max - 1) + "…" : s;
}

// ---------------------------------------------------------------------------
// Status card
// ---------------------------------------------------------------------------

async function refreshStatus() {
  const root = document.getElementById("ra-status");
  if (!root) return;
  root.textContent = "loading…";
  try {
    const s = await api.get("/reality-anchor/status");
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
    grid.appendChild(stat("facts", s.fact_count, "Operator-asserted ground-truth facts loaded."));
    grid.appendChild(stat("refused (24h)", s.refused_last_24h, "Tool calls or turns refused on CRITICAL contradiction in the last 24h."));
    grid.appendChild(stat("flagged (24h)", s.flagged_last_24h, "HIGH / MEDIUM / LOW contradictions flagged but allowed."));
    grid.appendChild(stat("repeats (24h)", s.repeat_offender_24h, "Recurrent hallucinations caught again in the last 24h."));
    grid.appendChild(stat("total corrections", s.total_corrections, "Distinct hallucinated claims ever caught (lifetime)."));
    grid.appendChild(stat("top repeat", s.top_repeat_count, "Most-repeated single claim's count."));
    root.appendChild(grid);
    if (s.catalog_errors && s.catalog_errors.length) {
      const errs = document.createElement("div");
      errs.className = "ra-errors";
      errs.innerHTML =
        "<strong>catalog errors:</strong> " +
        s.catalog_errors.map((e) => `<div>• ${escapeHtml(e)}</div>`).join("");
      root.appendChild(errs);
    }
  } catch (e) {
    root.textContent = `failed to load status: ${e.message || e}`;
  }
}

// ---------------------------------------------------------------------------
// Ground-truth facts
// ---------------------------------------------------------------------------

async function refreshFacts() {
  const root = document.getElementById("ra-facts");
  if (!root) return;
  root.textContent = "loading…";
  try {
    const r = await api.get("/reality-anchor/ground-truth");
    const facts = (r.facts || []).slice().sort(
      (a, b) =>
        (SEVERITY_RANK[b.severity] ?? -1) - (SEVERITY_RANK[a.severity] ?? -1)
        || a.id.localeCompare(b.id),
    );
    root.innerHTML = "";
    if (!facts.length) {
      root.textContent = "no facts loaded";
      return;
    }
    const table = document.createElement("table");
    table.className = "ra-table";
    table.innerHTML =
      "<thead><tr><th>severity</th><th>id</th><th>statement</th>" +
      "<th>canonical</th><th>forbidden</th><th>last confirmed</th></tr></thead>";
    const tbody = document.createElement("tbody");
    for (const f of facts) {
      const tr = document.createElement("tr");
      const sevCell = document.createElement("td");
      sevCell.appendChild(severityChip(f.severity));
      tr.appendChild(sevCell);

      const idCell = document.createElement("td");
      idCell.className = "ra-fact-id";
      idCell.textContent = f.id;
      tr.appendChild(idCell);

      const stmt = document.createElement("td");
      stmt.textContent = fmtShort(f.statement, 120);
      stmt.title = f.statement;
      tr.appendChild(stmt);

      const canon = document.createElement("td");
      canon.className = "ra-terms";
      canon.textContent = (f.canonical_terms || []).join(", ");
      canon.title = `domain keywords: ${(f.domain_keywords || []).join(", ")}`;
      tr.appendChild(canon);

      const forb = document.createElement("td");
      forb.className = "ra-terms ra-terms--forbidden";
      forb.textContent = (f.forbidden_terms || []).join(", ") || "—";
      tr.appendChild(forb);

      const lc = document.createElement("td");
      lc.className = "ra-time";
      lc.textContent = f.last_confirmed_at || "—";
      tr.appendChild(lc);

      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    root.appendChild(table);
  } catch (e) {
    root.textContent = `failed to load facts: ${e.message || e}`;
  }
}

// ---------------------------------------------------------------------------
// Recent events timeline
// ---------------------------------------------------------------------------

async function refreshEvents() {
  const root = document.getElementById("ra-events");
  if (!root) return;
  root.textContent = "loading…";
  try {
    const r = await api.get("/reality-anchor/recent-events?limit=100");
    const events = r.events || [];
    root.innerHTML = "";
    if (!events.length) {
      root.textContent = "no reality_anchor_* events yet";
      return;
    }
    const list = document.createElement("div");
    list.className = "ra-events-list";
    for (const ev of events) {
      const row = document.createElement("div");
      row.className = "ra-event-row";

      const ts = document.createElement("span");
      ts.className = "ra-event__time";
      ts.textContent = fmtTime(ev.timestamp);
      row.appendChild(ts);

      const typeChip = document.createElement("span");
      typeChip.className = "ra-event__type ra-event__type--" + ev.event_type;
      typeChip.textContent = ev.event_type.replace("reality_anchor_", "");
      row.appendChild(typeChip);

      const data = ev.event_data || {};
      if (data.severity) {
        row.appendChild(severityChip(data.severity));
      }
      const factId = data.fact_id;
      if (factId) {
        const fid = document.createElement("span");
        fid.className = "ra-event__fact";
        fid.textContent = factId;
        row.appendChild(fid);
      }
      const claimText = data.claim || data.body_excerpt;
      if (claimText) {
        const c = document.createElement("span");
        c.className = "ra-event__claim";
        c.textContent = fmtShort(claimText, 100);
        c.title = claimText;
        row.appendChild(c);
      }
      if (data.repetition_count) {
        const rc = document.createElement("span");
        rc.className = "ra-event__repeat";
        rc.textContent = `×${data.repetition_count}`;
        rc.title = `Same claim seen ${data.repetition_count} times`;
        row.appendChild(rc);
      }
      list.appendChild(row);
    }
    root.appendChild(list);
  } catch (e) {
    root.textContent = `failed to load events: ${e.message || e}`;
  }
}

// ---------------------------------------------------------------------------
// Repeat offenders table
// ---------------------------------------------------------------------------

async function refreshCorrections() {
  const root = document.getElementById("ra-corrections");
  if (!root) return;
  root.textContent = "loading…";
  try {
    const r = await api.get("/reality-anchor/corrections?min_repetitions=2&limit=50");
    const rows = r.corrections || [];
    root.innerHTML = "";
    if (!rows.length) {
      root.textContent = "no repeat offenders yet (count ≥ 2 required)";
      return;
    }
    const table = document.createElement("table");
    table.className = "ra-table";
    table.innerHTML =
      "<thead><tr><th>count</th><th>severity</th><th>fact</th>" +
      "<th>claim</th><th>last surface</th><th>last seen</th>" +
      "<th>last decision</th></tr></thead>";
    const tbody = document.createElement("tbody");
    for (const c of rows) {
      const tr = document.createElement("tr");

      const ct = document.createElement("td");
      ct.className = "ra-count";
      ct.textContent = `×${c.repetition_count}`;
      tr.appendChild(ct);

      const sev = document.createElement("td");
      sev.appendChild(severityChip(c.worst_severity));
      tr.appendChild(sev);

      const fact = document.createElement("td");
      fact.className = "ra-fact-id";
      fact.textContent = c.contradicts_fact_id;
      tr.appendChild(fact);

      const claim = document.createElement("td");
      claim.textContent = fmtShort(c.canonical_claim, 80);
      claim.title = c.canonical_claim;
      tr.appendChild(claim);

      const surf = document.createElement("td");
      surf.textContent = c.last_surface;
      tr.appendChild(surf);

      const ls = document.createElement("td");
      ls.className = "ra-time";
      ls.textContent = fmtTime(c.last_seen_at);
      tr.appendChild(ls);

      const dec = document.createElement("td");
      dec.textContent = c.last_decision;
      dec.className = c.last_decision === "refused" ? "ra-decision-refused" : "";
      tr.appendChild(dec);

      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    root.appendChild(table);
  } catch (e) {
    root.textContent = `failed to load corrections: ${e.message || e}`;
  }
}

// ---------------------------------------------------------------------------
// Reload button
// ---------------------------------------------------------------------------

async function handleReload() {
  try {
    const r = await api.post("/reality-anchor/reload", {});
    toast(`ground truth reloaded — ${r.fact_count} facts`);
    await Promise.all([refreshStatus(), refreshFacts()]);
  } catch (e) {
    toast(`reload failed: ${e.message || e}`);
  }
}

async function handleRefreshAll() {
  await Promise.all([
    refreshStatus(),
    refreshFacts(),
    refreshEvents(),
    refreshCorrections(),
  ]);
}

// ---------------------------------------------------------------------------
// HTML helpers
// ---------------------------------------------------------------------------

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ---------------------------------------------------------------------------
// Module init
// ---------------------------------------------------------------------------

export function start() {
  const reloadBtn = document.getElementById("ra-reload-btn");
  if (reloadBtn) reloadBtn.addEventListener("click", handleReload);
  const refreshBtn = document.getElementById("ra-refresh-btn");
  if (refreshBtn) refreshBtn.addEventListener("click", handleRefreshAll);

  // Lazy-load: only fetch when the tab is selected. Cheaper than
  // hammering the daemon every page load.
  const tabBtn = document.querySelector('[data-tab="reality-anchor"]');
  if (tabBtn) {
    let loaded = false;
    tabBtn.addEventListener("click", () => {
      if (loaded) return;
      loaded = true;
      handleRefreshAll();
    });
  }
}
