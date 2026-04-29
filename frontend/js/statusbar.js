// Status bar — bottom-anchored at-a-glance health.
//
// Demo-friction audit P1 #14. Keeps four cells fresh:
//   daemon       — boot status (ok / degraded / down) — pulled from /healthz
//   agents       — total in the registry — pulled from /agents
//   chain        — most recent seq — pulled from /audit/tail?n=1
//   last activity — relative time of the most recent event — same source as chain
//
// Plus a "diagnostics" button that toggles a popover showing the
// daemon's startup_diagnostics array (component / status / error per row).
// This is the operator-debug surface — until F6 ships an SSE log tail, it's
// the cheapest way to surface "what's the daemon think about itself".
//
// Polls every 10s; doesn't block any other module. Errors silently
// degrade individual cells to "—" rather than blowing up the whole bar.

import { api } from "./api.js";

const POLL_MS = 10_000;

let pollTimer = null;
let lastSeq = null;
let lastTimestamp = null;

export function start() {
  // Initial fetch right away so the bar isn't empty for 10s after boot.
  refresh();
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(refresh, POLL_MS);

  // Also nudge the relative-time formatter every 30s so "5s ago" doesn't
  // get stuck. Lighter than re-polling because we don't hit the daemon.
  setInterval(redrawActivityCell, 30_000);

  const btn = document.getElementById("sb-diag-btn");
  const pop = document.getElementById("sb-diag-popover");
  if (btn && pop) {
    btn.addEventListener("click", () => togglePopover(btn, pop));
    document.addEventListener("click", (e) => {
      // Click-outside dismisses the popover.
      if (!pop.hidden && !pop.contains(e.target) && e.target !== btn) {
        pop.hidden = true;
      }
    });
  }
}

async function refresh() {
  // Fan out the three reads in parallel — they don't depend on each
  // other, and the daemon answers them from the same SQLite anyway.
  const [healthRes, agentsRes, chainRes] = await Promise.allSettled([
    api.get("/healthz"),
    api.get("/agents"),
    api.get("/audit/tail?n=1"),
  ]);

  // Daemon cell. The /healthz schema reports liveness as `ok: true` plus
  // a startup_diagnostics array of {component, status, error}. Derive a
  // single "ok / degraded / down" label from those: ok if everything is
  // ok-or-disabled, degraded if anything failed/degraded but the daemon
  // still answered, down only when the request itself failed.
  const daemonCell = document.getElementById("sb-daemon");
  const daemonDot = daemonCell?.querySelector(".statusbar__dot");
  const daemonValue = document.getElementById("sb-daemon-value");
  if (healthRes.status === "fulfilled") {
    const h = healthRes.value || {};
    const diags = Array.isArray(h.startup_diagnostics) ? h.startup_diagnostics : [];
    const anyBad = diags.some((d) => d.status === "failed" || d.status === "degraded");
    const ok = h.ok === true && !anyBad;
    const label = !h.ok ? "down" : anyBad ? "degraded" : "ok";
    daemonValue.textContent = label;
    daemonDot.classList.remove("statusbar__dot--ok", "statusbar__dot--warn", "statusbar__dot--down");
    daemonDot.classList.add(
      ok ? "statusbar__dot--ok"
      : !h.ok ? "statusbar__dot--down"
      : "statusbar__dot--warn",
    );
    // Stash diagnostics for the popover.
    daemonCell.dataset.diagnostics = JSON.stringify(diags);
  } else {
    daemonValue.textContent = "down";
    daemonDot.classList.remove("statusbar__dot--ok", "statusbar__dot--warn");
    daemonDot.classList.add("statusbar__dot--down");
    daemonCell.dataset.diagnostics = "[]";
  }

  // Agents cell.
  const agentsValue = document.getElementById("sb-agents-value");
  if (agentsRes.status === "fulfilled") {
    const count = agentsRes.value?.count ?? agentsRes.value?.agents?.length ?? 0;
    agentsValue.textContent = String(count);
  } else {
    agentsValue.textContent = "—";
  }

  // Chain cell + activity cell — both come from /audit/tail?n=1.
  const chainValue = document.getElementById("sb-chain-value");
  if (chainRes.status === "fulfilled" && chainRes.value?.events?.length) {
    const ev = chainRes.value.events[0];
    lastSeq = ev.seq;
    lastTimestamp = ev.timestamp;
    chainValue.textContent = `#${ev.seq}`;
  } else {
    lastSeq = null;
    lastTimestamp = null;
    chainValue.textContent = "—";
  }
  redrawActivityCell();
}

function redrawActivityCell() {
  const value = document.getElementById("sb-activity-value");
  if (!value) return;
  if (!lastTimestamp) {
    value.textContent = "—";
    return;
  }
  value.textContent = relativeTime(lastTimestamp);
}

/** "5s ago" / "3m ago" / "2h ago" / "yesterday" — quick eyeballable. */
function relativeTime(iso) {
  try {
    const then = new Date(iso).getTime();
    const now = Date.now();
    const diffSec = Math.max(0, Math.round((now - then) / 1000));
    if (diffSec < 60) return `${diffSec}s ago`;
    const diffMin = Math.round(diffSec / 60);
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.round(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    const diffDay = Math.round(diffHr / 24);
    if (diffDay === 1) return "yesterday";
    return `${diffDay}d ago`;
  } catch {
    return iso;
  }
}

function togglePopover(btn, pop) {
  if (!pop.hidden) {
    pop.hidden = true;
    return;
  }
  // Repopulate from the most recent diagnostics each time.
  const cell = document.getElementById("sb-daemon");
  const raw = cell?.dataset?.diagnostics || "[]";
  let diagnostics = [];
  try {
    diagnostics = JSON.parse(raw);
  } catch {
    diagnostics = [];
  }
  pop.innerHTML = "";
  const heading = document.createElement("div");
  heading.className = "statusbar__popover-heading";
  heading.textContent = `Startup diagnostics (${diagnostics.length})`;
  pop.appendChild(heading);

  if (diagnostics.length === 0) {
    const empty = document.createElement("div");
    empty.className = "statusbar__popover-empty";
    empty.textContent = "No diagnostics reported.";
    pop.appendChild(empty);
  } else {
    const list = document.createElement("ul");
    list.className = "statusbar__popover-list";
    for (const d of diagnostics) {
      const li = document.createElement("li");
      li.className = `statusbar__popover-item statusbar__popover-item--${d.status || "unknown"}`;
      const status = document.createElement("span");
      status.className = "statusbar__popover-status";
      status.textContent = `[${d.status || "?"}]`;
      const comp = document.createElement("span");
      comp.className = "statusbar__popover-comp";
      comp.textContent = d.component || "?";
      li.appendChild(status);
      li.appendChild(comp);
      if (d.error) {
        const err = document.createElement("div");
        err.className = "statusbar__popover-err";
        err.textContent = d.error;
        li.appendChild(err);
      }
      list.appendChild(li);
    }
    pop.appendChild(list);
  }
  pop.hidden = false;
}
