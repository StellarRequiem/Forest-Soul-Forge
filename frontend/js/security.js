// Security pane — ADR-0062 T6 (B258).
//
// Four sections:
//   1. Status card (rule count, recent refuse/allow counts,
//      quarantined dirs).
//   2. IoC catalog table — read-only viewer with severity chips.
//   3. Recent scans timeline — agent_security_scan_completed events
//      from the audit chain.
//   4. Quarantined proposals — staged dirs with REJECTED.md.
//
// Read-only by design. Per ADR-0062 D1 the operator owns the
// catalog by editing config/security_iocs.yaml directly; the
// pane has a Reload button to pick up edits without restart.
//
// Severity chip styles + table styles are reused from the
// Reality Anchor pane CSS (B256).

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

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ---------------------------------------------------------------------------
// Status card
// ---------------------------------------------------------------------------

async function refreshStatus() {
  const root = document.getElementById("sec-status");
  if (!root) return;
  root.textContent = "loading…";
  try {
    const s = await api.get("/security/status");
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
    grid.appendChild(stat("rules", s.ioc_rule_count, "IoC patterns in config/security_iocs.yaml"));
    grid.appendChild(stat("refused (24h)", s.refused_last_24h, "Installs / forges refused on CRITICAL findings."));
    grid.appendChild(stat("allowed (24h)", s.allowed_last_24h, "Scans that passed without CRITICAL findings."));
    grid.appendChild(stat("CRITICAL hits (24h)", s.critical_last_24h, "Scans where at least one CRITICAL rule fired."));
    grid.appendChild(stat("quarantined", s.quarantined_count, "Staged proposals with REJECTED.md marker."));
    grid.appendChild(stat("catalog v", s.ioc_catalog_version, "Operator-version-bumped IoC catalog."));
    root.appendChild(grid);

    // Per-surface counts.
    const surfaces = s.surface_counts || {};
    if (Object.keys(surfaces).length) {
      const surfRow = document.createElement("div");
      surfRow.className = "sec-surfaces";
      surfRow.innerHTML = "<strong>by surface:</strong> ";
      for (const [k, v] of Object.entries(surfaces).sort((a, b) => b[1] - a[1])) {
        const c = document.createElement("span");
        c.className = "sec-surface-chip";
        c.textContent = `${k} (${v})`;
        surfRow.appendChild(c);
      }
      root.appendChild(surfRow);
    }

    if (s.ioc_catalog_errors && s.ioc_catalog_errors.length) {
      const errs = document.createElement("div");
      errs.className = "ra-errors";
      errs.innerHTML =
        "<strong>catalog errors:</strong> " +
        s.ioc_catalog_errors.map((e) => `<div>• ${escapeHtml(e)}</div>`).join("");
      root.appendChild(errs);
    }
  } catch (e) {
    root.textContent = `failed to load status: ${e.message || e}`;
  }
}

// ---------------------------------------------------------------------------
// IoC catalog
// ---------------------------------------------------------------------------

async function refreshIocs() {
  const root = document.getElementById("sec-iocs");
  if (!root) return;
  root.textContent = "loading…";
  try {
    const r = await api.get("/security/iocs");
    const rules = r.rules || [];
    root.innerHTML = "";
    if (!rules.length) {
      root.textContent = "no IoC rules loaded";
      return;
    }
    const table = document.createElement("table");
    table.className = "ra-table";
    table.innerHTML =
      "<thead><tr><th>severity</th><th>id</th><th>pattern</th>" +
      "<th>applies to</th><th>rationale</th></tr></thead>";
    const tbody = document.createElement("tbody");
    for (const rule of rules) {
      const tr = document.createElement("tr");
      const sev = document.createElement("td");
      sev.appendChild(severityChip(rule.severity));
      tr.appendChild(sev);

      const id = document.createElement("td");
      id.className = "ra-fact-id";
      id.textContent = rule.id;
      tr.appendChild(id);

      const pat = document.createElement("td");
      pat.className = "ra-terms";
      pat.textContent = fmtShort(rule.pattern, 60);
      pat.title = rule.pattern;
      tr.appendChild(pat);

      const apl = document.createElement("td");
      apl.className = "ra-terms";
      apl.textContent = (rule.applies_to || []).join(", ") || "(any)";
      tr.appendChild(apl);

      const rat = document.createElement("td");
      rat.textContent = fmtShort(rule.rationale, 120);
      rat.title = rule.rationale;
      tr.appendChild(rat);

      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    root.appendChild(table);
  } catch (e) {
    root.textContent = `failed to load rules: ${e.message || e}`;
  }
}

// ---------------------------------------------------------------------------
// Recent scans timeline
// ---------------------------------------------------------------------------

async function refreshScans() {
  const root = document.getElementById("sec-scans");
  if (!root) return;
  root.textContent = "loading…";
  try {
    const r = await api.get("/security/recent-scans?limit=100");
    const events = r.events || [];
    root.innerHTML = "";
    if (!events.length) {
      root.textContent = "no scan events yet";
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

      const d = ev.event_data || {};
      const decision = d.decision || "?";
      const decChip = document.createElement("span");
      decChip.className = "ra-event__type";
      decChip.classList.add(decision === "refuse" ? "sec-dec-refuse" : "sec-dec-allow");
      decChip.textContent = decision;
      row.appendChild(decChip);

      const kind = document.createElement("span");
      kind.className = "ra-event__fact";
      kind.textContent = d.install_kind || "?";
      row.appendChild(kind);

      if (d.refused_on_tier) {
        row.appendChild(severityChip(d.refused_on_tier));
      }

      const counts = document.createElement("span");
      counts.className = "ra-event__claim";
      const parts = [];
      if (d.critical_count) parts.push(`crit=${d.critical_count}`);
      if (d.high_count) parts.push(`high=${d.high_count}`);
      if (d.medium_count) parts.push(`med=${d.medium_count}`);
      if (d.low_count) parts.push(`low=${d.low_count}`);
      counts.textContent = parts.join(" ") || "(no findings)";
      row.appendChild(counts);

      if (d.staging_dir) {
        const sd = document.createElement("span");
        sd.className = "ra-event__claim";
        sd.textContent = fmtShort(d.staging_dir, 60);
        sd.title = d.staging_dir;
        row.appendChild(sd);
      }

      list.appendChild(row);
    }
    root.appendChild(list);
  } catch (e) {
    root.textContent = `failed to load scans: ${e.message || e}`;
  }
}

// ---------------------------------------------------------------------------
// Quarantined proposals
// ---------------------------------------------------------------------------

async function refreshQuarantined() {
  const root = document.getElementById("sec-quarantined");
  if (!root) return;
  root.textContent = "loading…";
  try {
    const r = await api.get("/security/quarantined");
    const rows = r.quarantined || [];
    root.innerHTML = "";
    if (!rows.length) {
      root.textContent = "no quarantined proposals (clean state)";
      return;
    }
    const list = document.createElement("div");
    list.className = "ra-events-list";
    for (const q of rows) {
      const row = document.createElement("div");
      row.className = "sec-q-row";

      const header = document.createElement("div");
      header.className = "sec-q-header";

      const kindChip = document.createElement("span");
      kindChip.className = "ra-event__type sec-dec-refuse";
      kindChip.textContent = q.kind;
      header.appendChild(kindChip);

      const ts = document.createElement("span");
      ts.className = "ra-event__time";
      ts.textContent = fmtTime(q.rejected_at);
      header.appendChild(ts);

      const dir = document.createElement("span");
      dir.className = "sec-q-dir";
      dir.textContent = q.staged_dir;
      dir.title = q.staged_dir;
      header.appendChild(dir);

      row.appendChild(header);

      const excerpt = document.createElement("pre");
      excerpt.className = "sec-q-excerpt";
      excerpt.textContent = q.marker_excerpt;
      row.appendChild(excerpt);

      list.appendChild(row);
    }
    root.appendChild(list);
  } catch (e) {
    root.textContent = `failed to load quarantine list: ${e.message || e}`;
  }
}

// ---------------------------------------------------------------------------
// Buttons
// ---------------------------------------------------------------------------

async function handleReload() {
  try {
    const r = await api.post("/security/reload", {});
    toast(`IoC catalog reloaded — ${r.rule_count} rules`);
    await Promise.all([refreshStatus(), refreshIocs()]);
  } catch (e) {
    toast(`reload failed: ${e.message || e}`);
  }
}

async function handleRefreshAll() {
  await Promise.all([
    refreshStatus(),
    refreshIocs(),
    refreshScans(),
    refreshQuarantined(),
  ]);
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

export function start() {
  const reload = document.getElementById("sec-reload-btn");
  if (reload) reload.addEventListener("click", handleReload);
  const refresh = document.getElementById("sec-refresh-btn");
  if (refresh) refresh.addEventListener("click", handleRefreshAll);

  // Lazy-load on first tab click.
  const tabBtn = document.querySelector('[data-tab="security"]');
  if (tabBtn) {
    let loaded = false;
    tabBtn.addEventListener("click", () => {
      if (loaded) return;
      loaded = true;
      handleRefreshAll();
    });
  }
}
