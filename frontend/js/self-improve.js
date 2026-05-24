// Self-improvement findings panel — sibling to the per-call Approvals
// queue and the Forged Proposals subsection on the same tab.
//
// Surfaces output from `scripts/self_improve.py` (audit→fix→validate
// pipeline). Each cycle drops a JSON report into docs/self-improvement/;
// this panel reads the latest via GET /api/self-improve/findings,
// renders each finding with its severity / category / affected files,
// and lets the operator decide what to do with it — Implement (queue
// for the next FIX cycle), Audit (flag for deeper review), or Reject.
//
// Decisions persist server-side in docs/self-improvement/decisions.json,
// keyed by a content-addressed finding_id, so re-running the audit
// pipeline doesn't reset the queue.

import { api, writeCall } from "./api.js";
import { toast } from "./toast.js";

const OPERATOR_KEY = "fsf.operatorId"; // shared with pending.js

const STATUS_LABEL = {
  pending: "pending",
  approved_for_fix: "approved for fix",
  under_audit: "under audit",
  implemented: "implemented",
  rejected: "rejected",
};

// Map decision-status → pill color reusing the existing palette.
const STATUS_PILL = {
  pending: "pill--ghost",
  approved_for_fix: "pill--info",
  under_audit: "pill--warn",
  implemented: "pill--success",
  rejected: "pill--danger",
};

let _state = {
  report: null,              // current report meta
  findings: [],              // current findings list
  filter: "all",             // category filter
  statusFilter: "pending",   // decision-status filter (matches HTML default)
  reportFilename: "",        // empty = let the API return the latest
};

function getOperatorId() {
  // Prefer this section's dedicated input, but fall back to the per-call
  // Approvals queue field above (same tab, shared localStorage key) so a
  // typed-once id flows across subsections. localStorage is the last
  // resort — pending.js + this module both persist there on change.
  const own = document.getElementById("self-improve-operator-id");
  const ownVal = own ? own.value.trim() : "";
  if (ownVal) return ownVal;
  const shared = document.getElementById("pending-operator-id");
  const sharedVal = shared ? shared.value.trim() : "";
  if (sharedVal) return sharedVal;
  return localStorage.getItem(OPERATOR_KEY) || "";
}

function escapeHTML(s) {
  return String(s || "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function pill(text, klass = "pill--ghost", title = "") {
  const el = document.createElement("span");
  el.className = `pill ${klass}`;
  el.style.cssText = "font-size:10px;text-transform:uppercase;letter-spacing:0.05em;";
  el.textContent = text;
  if (title) el.title = title;
  return el;
}

function renderFinding(f) {
  const row = document.createElement("div");
  row.className = "self-improve-row";
  row.dataset.findingId = f.finding_id;
  row.dataset.status = f.decision?.status || "pending";
  row.style.cssText =
    "display:flex;align-items:flex-start;justify-content:space-between;"
    + "gap:12px;padding:10px 12px;"
    + "border:1px solid var(--border,#2c303a);"
    + "border-radius:6px;margin-bottom:8px;";

  const left = document.createElement("div");
  left.style.cssText = "flex:1;min-width:0;";

  const headerRow = document.createElement("div");
  headerRow.style.cssText = "display:flex;align-items:center;gap:6px;margin-bottom:4px;flex-wrap:wrap;";
  // Severity (HIGH/MED/LOW)
  const sevKlass = f.severity === "HIGH"
    ? "pill--danger"
    : (f.severity === "MED" || f.severity === "MEDIUM"
        ? "pill--warn" : "pill--info");
  headerRow.appendChild(pill(f.severity || "—", sevKlass, "severity"));
  // Category bucket
  headerRow.appendChild(pill(f.category || "other", "pill--info", "category"));
  // Decision status
  const status = f.decision?.status || "pending";
  headerRow.appendChild(
    pill(STATUS_LABEL[status] || status, STATUS_PILL[status] || "pill--ghost", "decision"),
  );
  // Plan markers
  if (f.in_auto_fix_plan) headerRow.appendChild(pill("in auto-fix plan", "pill--success", "queued for auto-fix"));
  if (f.in_flagged_plan)  headerRow.appendChild(pill("flagged", "pill--warn", "flagged for manual review"));
  // Outcome from this report (if any)
  if (f.outcome && f.outcome.status) {
    headerRow.appendChild(pill(`outcome: ${f.outcome.status}`, "pill--ghost", f.outcome.error || ""));
  }
  // Stable id (first 6 chars) for cross-reference w/ sidecar
  const idTag = document.createElement("span");
  idTag.className = "muted";
  idTag.textContent = `#${f.finding_id.slice(0, 8)}`;
  idTag.style.cssText = "font-size:10px;font-family:var(--mono,monospace);";
  headerRow.appendChild(idTag);
  left.appendChild(headerRow);

  // Summary — the canonical one-liner from the report.
  const summary = document.createElement("div");
  summary.style.cssText = "font-size:13px;line-height:1.4;margin-bottom:4px;word-break:break-word;";
  summary.textContent = f.summary || "(no summary)";
  left.appendChild(summary);

  // Affected files (collapsed when long)
  if (f.affected_files && f.affected_files.length) {
    const files = document.createElement("div");
    files.className = "muted";
    files.style.cssText = "font-size:11px;font-family:var(--mono,monospace);word-break:break-all;";
    files.textContent = `files: ${f.affected_files.join(", ")}`;
    left.appendChild(files);
  }

  // Decision footer — who/when/note, only when decided.
  const dec = f.decision || {};
  if (dec.decided_at) {
    const footer = document.createElement("div");
    footer.className = "muted";
    footer.style.cssText = "font-size:11px;margin-top:4px;";
    footer.textContent =
      `${STATUS_LABEL[dec.status] || dec.status} by ${dec.decided_by || "?"} at ${dec.decided_at}`
      + (dec.note ? ` — ${dec.note}` : "");
    left.appendChild(footer);
  }

  // Action column — Implement / Audit / Reject / Reset.
  const actions = document.createElement("div");
  actions.style.cssText = "display:flex;flex-direction:column;gap:4px;min-width:120px;";

  const implementBtn = document.createElement("button");
  implementBtn.className = "btn btn--primary btn--sm";
  implementBtn.textContent = "Implement";
  implementBtn.title = "Queue this finding for the next fix cycle";
  implementBtn.addEventListener("click", () => setDecision(f, "approved_for_fix"));

  const auditBtn = document.createElement("button");
  auditBtn.className = "btn btn--sm";
  auditBtn.textContent = "Audit";
  auditBtn.title = "Flag for deeper human review before any fix";
  auditBtn.addEventListener("click", () => setDecision(f, "under_audit"));

  const rejectBtn = document.createElement("button");
  rejectBtn.className = "btn btn--danger btn--sm";
  rejectBtn.textContent = "Reject";
  rejectBtn.title = "Mark this finding as not actionable";
  rejectBtn.addEventListener("click", () => setDecision(f, "rejected", { promptForNote: true }));

  actions.appendChild(implementBtn);
  actions.appendChild(auditBtn);
  actions.appendChild(rejectBtn);

  // If already decided, offer a Reset that flips back to pending.
  if (dec.status && dec.status !== "pending") {
    const resetBtn = document.createElement("button");
    resetBtn.className = "btn btn--ghost btn--sm";
    resetBtn.textContent = "Reset";
    resetBtn.title = "Clear this decision (back to pending)";
    resetBtn.addEventListener("click", () => setDecision(f, "pending"));
    actions.appendChild(resetBtn);
  }

  row.appendChild(left);
  row.appendChild(actions);
  return row;
}

function rerender() {
  const root = document.getElementById("self-improve-list");
  if (!root) return;
  root.innerHTML = "";

  if (!_state.findings.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No findings to show. Run scripts/self_improve.py to generate a report.";
    root.appendChild(empty);
    return;
  }

  const filtered = _state.findings.filter((f) => {
    if (_state.filter !== "all" && f.category !== _state.filter) return false;
    if (_state.statusFilter !== "all"
        && (f.decision?.status || "pending") !== _state.statusFilter) return false;
    return true;
  });

  if (!filtered.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No findings match the current filters.";
    root.appendChild(empty);
    return;
  }

  // Sort: pending first, then alphabetical by summary (so a refresh
  // doesn't shuffle decided rows around the operator's cursor).
  filtered.sort((a, b) => {
    const sa = a.decision?.status || "pending";
    const sb = b.decision?.status || "pending";
    if (sa === "pending" && sb !== "pending") return -1;
    if (sa !== "pending" && sb === "pending") return 1;
    return String(a.summary || "").localeCompare(String(b.summary || ""));
  });

  for (const f of filtered) root.appendChild(renderFinding(f));
}

function updateSummary() {
  const root = document.getElementById("self-improve-summary");
  if (!root) return;
  const r = _state.report;
  if (!r) {
    root.textContent = "—";
    return;
  }
  const status = r.aborted ? "ABORTED" : "complete";
  root.innerHTML =
    `<strong>${escapeHTML(r.filename)}</strong> · `
    + `${escapeHTML(r.timestamp)} · `
    + `branch ${escapeHTML(r.branch)} · `
    + `findings ${r.finding_count} `
    + `(auto-fix ${r.auto_fix_count}, flagged ${r.flagged_count}) · `
    + `pytest ${r.pytest_passed}p/${r.pytest_failed}f/${r.pytest_errors}e · `
    + `<span class="${r.aborted ? 'danger' : 'muted'}">${status}</span>`;
}

function updateFilters(totals) {
  const catSel = document.getElementById("self-improve-category-filter");
  if (catSel) {
    const current = catSel.value || "all";
    catSel.innerHTML = "";
    const all = document.createElement("option");
    all.value = "all";
    all.textContent = "all categories";
    catSel.appendChild(all);
    const byCat = totals?.by_category || {};
    for (const [cat, n] of Object.entries(byCat).sort()) {
      const opt = document.createElement("option");
      opt.value = cat;
      opt.textContent = `${cat} (${n})`;
      catSel.appendChild(opt);
    }
    if ([...catSel.options].some((o) => o.value === current)) {
      catSel.value = current;
    }
  }
  const statusSel = document.getElementById("self-improve-status-filter");
  if (statusSel) {
    const current = statusSel.value || "all";
    statusSel.innerHTML = "";
    const all = document.createElement("option");
    all.value = "all";
    all.textContent = "all decisions";
    statusSel.appendChild(all);
    const byStatus = totals?.by_status || {};
    for (const s of ["pending", "approved_for_fix", "under_audit", "implemented", "rejected"]) {
      const n = byStatus[s] || 0;
      if (n === 0 && current !== s) continue;
      const opt = document.createElement("option");
      opt.value = s;
      opt.textContent = `${STATUS_LABEL[s] || s} (${n})`;
      statusSel.appendChild(opt);
    }
    if ([...statusSel.options].some((o) => o.value === current)) {
      statusSel.value = current;
    }
  }
}

async function populateReportSelect() {
  const sel = document.getElementById("self-improve-report-select");
  if (!sel) return;
  try {
    const data = await api.get("/api/self-improve/reports");
    const prev = sel.value;
    sel.innerHTML = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "— latest report —";
    sel.appendChild(placeholder);
    for (const r of (data.reports || [])) {
      const opt = document.createElement("option");
      opt.value = r.filename;
      const stamp = (r.timestamp || "").replace("T", " ").slice(0, 19);
      opt.textContent = `${stamp} · ${r.finding_count}↯`;
      sel.appendChild(opt);
    }
    if (prev && [...sel.options].some((o) => o.value === prev)) sel.value = prev;
  } catch {
    // Non-fatal — leave the placeholder.
  }
}

export async function fetchAndRender() {
  const root = document.getElementById("self-improve-list");
  if (!root) return;
  const status = document.getElementById("self-improve-summary");
  if (status && !_state.report) status.textContent = "loading…";
  const sel = document.getElementById("self-improve-report-select");
  const chosen = sel?.value || "";
  const qs = chosen ? `?report=${encodeURIComponent(chosen)}` : "";
  try {
    const data = await api.get(`/api/self-improve/findings${qs}`);
    _state.report = data.report;
    _state.findings = data.findings || [];
    updateSummary();
    updateFilters(data.totals);
    rerender();
  } catch (e) {
    root.innerHTML = "";
    const err = document.createElement("div");
    err.className = "empty";
    err.style.color = "var(--danger,#ff6b6b)";
    err.textContent = `Failed to load self-improvement report: ${e.message}`;
    root.appendChild(err);
    if (status) status.textContent = "error";
  }
}

async function setDecision(f, newStatus, opts = {}) {
  const op = getOperatorId();
  if (!op) {
    toast({
      title: "Operator id required",
      msg: "Set your id in the operator field on this tab before deciding.",
      kind: "warn",
      ttl: 5000,
    });
    return;
  }
  let note = "";
  if (opts.promptForNote) {
    const raw = window.prompt(
      `Reason for rejecting:\n\n${f.summary}\n\n(blank cancels)`,
    );
    if (raw === null) return;
    note = raw.trim();
    if (!note && newStatus === "rejected") {
      toast({title: "Reason required for reject", kind: "warn", ttl: 4000});
      return;
    }
  }
  localStorage.setItem(OPERATOR_KEY, op);
  try {
    const resp = await writeCall(
      `/api/self-improve/findings/${encodeURIComponent(f.finding_id)}/decision`,
      {
        status: newStatus,
        operator_id: op,
        note,
        report: _state.report?.filename || "",
      },
    );
    // Patch the in-memory finding so we don't have to refetch every row.
    f.decision = resp.decision;
    toast({
      title: `Set ${STATUS_LABEL[newStatus] || newStatus}`,
      msg: f.summary.slice(0, 80),
      kind: "success",
      ttl: 3000,
    });
    rerender();
    // Refresh the filter counts in the background — totals shift.
    fetchAndRender();
  } catch (e) {
    toast({
      title: "Couldn't save decision",
      msg: e.message,
      kind: "error",
      ttl: 6000,
    });
  }
}

export function start() {
  // Sync state.statusFilter from the DOM default so the initial render
  // matches the dropdown's visible selection (HTML defaults to "pending").
  const initialStatusSel = document.getElementById("self-improve-status-filter");
  if (initialStatusSel?.value) _state.statusFilter = initialStatusSel.value;

  const refresh = document.getElementById("self-improve-refresh");
  if (refresh) refresh.addEventListener("click", () => {
    populateReportSelect().then(fetchAndRender);
  });

  const reportSel = document.getElementById("self-improve-report-select");
  if (reportSel) reportSel.addEventListener("change", fetchAndRender);

  const operatorInput = document.getElementById("self-improve-operator-id");
  if (operatorInput) {
    operatorInput.value = localStorage.getItem(OPERATOR_KEY) || "";
    operatorInput.addEventListener("change", () => {
      const v = operatorInput.value.trim();
      if (v) localStorage.setItem(OPERATOR_KEY, v);
    });
  }

  const catSel = document.getElementById("self-improve-category-filter");
  if (catSel) catSel.addEventListener("change", () => {
    _state.filter = catSel.value || "all";
    rerender();
  });

  const statusSel = document.getElementById("self-improve-status-filter");
  if (statusSel) statusSel.addEventListener("change", () => {
    _state.statusFilter = statusSel.value || "all";
    rerender();
  });

  // Auto-refresh on tab activation (same pattern as forged-proposals.js).
  document.querySelectorAll(".tab").forEach((tab) => {
    if (tab.dataset.tab === "pending") {
      tab.addEventListener("click", () => {
        populateReportSelect().then(fetchAndRender);
      });
    }
  });

  populateReportSelect().then(fetchAndRender);
}
