// Operator Console — ADR-0096.
//
// The clickable surface over the task exchange + the synaptic layer:
//   1. Run training ladder — POST /training/run -> the report (per-tier scores).
//   2. Task ladder         — GET /training/tasks (the tiered catalog).
//   3. Fleet trust         — GET /synapse/trust (live synaptic layer readout).
//   4. Routing             — GET /synapse/route?problem_class=... (trust-ranked).
//
// Safe by design: the ladder is deterministic + read-only; routing INFORMS
// (capability stays human-gated, ADR-0095). Polling-based (v1) — an SSE live
// stream is the v2 upgrade. Sibling shape to orchestrator.js.

import { api } from "./api.js";
import { toast } from "./toast.js";

const TIER_NAMES = {
  0: "Baseline", 1: "L1 determinism", 2: "L2 audit",
  3: "L3 composition", 4: "L4 doc+integrity",
};

function el(tag, css, text) {
  const e = document.createElement(tag);
  if (css) e.style.cssText = css;
  if (text != null) e.textContent = text;
  return e;
}

// ---------------------------------------------------------------------------
// Run the ladder
// ---------------------------------------------------------------------------
async function runLadder() {
  const root = document.getElementById("console-run");
  if (!root) return;
  root.textContent = "running the ladder…";
  root.style.color = "";
  try {
    const d = await api.post("/training/run");
    root.innerHTML = "";
    const pass = d.passed === d.total;
    const head = el("div", `font-size:14px;margin-bottom:8px;color:${pass ? "#aef0ae" : "#f0aeae"}`);
    head.innerHTML =
      `<strong>${d.passed}/${d.total} tasks passed</strong> · ` +
      `audit ${d.audit_chain_ok ? "OK" : "FAILED"} · ` +
      `trust ${d.trust_graph_ok ? "OK" : "FAILED"}`;
    root.appendChild(head);

    const grid = el("div", "display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px;");
    for (const [t, c] of Object.entries(d.by_tier || {})) {
      const ok = c.passed === c.total;
      grid.appendChild(el(
        "span",
        `padding:3px 8px;border-radius:4px;font-size:12px;background:${ok ? "#1f3a1f" : "#3a1f1f"};color:${ok ? "#aef0ae" : "#f0aeae"}`,
        `T${t} ${TIER_NAMES[t] || ""} ${c.passed}/${c.total}`));
    }
    root.appendChild(grid);

    for (const task of d.tasks || []) {
      const row = el("div", "padding:4px 0;border-bottom:1px solid #1c2028;font-family:var(--mono,monospace);font-size:12px;");
      row.innerHTML =
        `${task.passed ? "✅" : "❌"} <span style="color:#9fc5ff">${task.id}</span> ` +
        `<span style="color:#888">(tier ${task.tier} · ${task.problem_class})</span>`;
      root.appendChild(row);
    }
    toast({ title: "Training ladder complete", msg: `${d.passed}/${d.total} passed`,
            kind: pass ? "success" : "error", ttl: 4000 });
  } catch (e) {
    root.textContent = "run failed: " + e.message;
    root.style.color = "var(--danger,#ff6b6b)";
  }
}

// ---------------------------------------------------------------------------
// Task catalog (the tiered ladder)
// ---------------------------------------------------------------------------
async function refreshTasks() {
  const root = document.getElementById("console-tasks");
  if (!root) return;
  root.textContent = "loading…";
  try {
    const r = await api.get("/training/tasks");
    root.innerHTML = "";
    const byTier = {};
    for (const t of r.tasks || []) (byTier[t.tier] ||= []).push(t);
    for (const tier of Object.keys(byTier).sort()) {
      root.appendChild(el("div", "margin:6px 0 2px;color:var(--text,#ddd);font-weight:600;",
                          `Tier ${tier} · ${TIER_NAMES[tier] || ""}`));
      for (const t of byTier[tier]) {
        const row = el("div", "padding:3px 0 3px 12px;font-size:12px;color:var(--muted,#aaa);");
        const tools = (t.steps || []).map((s) => s.tool).join(" → ");
        row.innerHTML =
          `<span style="font-family:var(--mono,monospace);color:#9fc5ff">${t.id}</span> — ${tools} ` +
          `<span style="color:#666">[${t.problem_class}]</span>`;
        root.appendChild(row);
      }
    }
  } catch (e) {
    root.textContent = "failed to load tasks: " + e.message;
    root.style.color = "var(--danger,#ff6b6b)";
  }
}

// ---------------------------------------------------------------------------
// Fleet trust (live synaptic layer)
// ---------------------------------------------------------------------------
async function refreshTrust() {
  const root = document.getElementById("console-trust");
  if (!root) return;
  root.textContent = "loading…";
  try {
    const r = await api.get("/synapse/trust");
    root.innerHTML = "";
    if (!(r.scores || []).length) {
      root.textContent = "No trust recorded yet. Agents accrue trust as they dispatch.";
      root.style.color = "var(--muted,#888)";
      return;
    }
    const table = el("table", "width:100%;border-collapse:collapse;font-size:12px;");
    table.innerHTML = "<thead><tr>" +
      ["agent", "problem_class", "trust", "n"]
        .map((h) => `<th style='text-align:left;padding:4px 8px;border-bottom:1px solid #2c303a'>${h}</th>`)
        .join("") + "</tr></thead>";
    const tb = el("tbody");
    for (const s of r.scores) {
      const col = s.trust >= 0.66 ? "#aef0ae" : (s.trust <= 0.4 ? "#f0aeae" : "#f0d8ae");
      const tr = el("tr", "border-bottom:1px solid #1c2028;");
      tr.innerHTML =
        `<td style='padding:4px 8px;font-family:var(--mono,monospace);color:#aaa'>${s.node}</td>` +
        `<td style='padding:4px 8px'>${s.problem_class}</td>` +
        `<td style='padding:4px 8px;color:${col}'>${Number(s.trust).toFixed(2)}</td>` +
        `<td style='padding:4px 8px;color:#888'>${s.observations}</td>`;
      tb.appendChild(tr);
    }
    table.appendChild(tb);
    root.appendChild(table);
  } catch (e) {
    root.textContent = "failed to load trust: " + e.message;
    root.style.color = "var(--danger,#ff6b6b)";
  }
}

// ---------------------------------------------------------------------------
// Routing recommendation (trust informs; you decide)
// ---------------------------------------------------------------------------
async function recommend() {
  const root = document.getElementById("console-route");
  const input = document.getElementById("console-route-pc");
  const pc = (input && input.value || "").trim();
  if (!root) return;
  if (!pc) { root.textContent = "Enter a problem_class first."; return; }
  root.textContent = "ranking…";
  root.style.color = "";
  try {
    const r = await api.get(`/synapse/route?problem_class=${encodeURIComponent(pc)}`);
    root.innerHTML = "";
    if (!r.recommended) {
      root.textContent = `No candidates with a track record for "${pc}".`;
      root.style.color = "var(--muted,#888)";
      return;
    }
    const head = el("div", "margin-bottom:6px;");
    head.innerHTML = `Recommended: <strong style="color:#aef0ae">${r.recommended}</strong> ` +
                     `<span style="color:#666">— ${r.note}</span>`;
    root.appendChild(head);
    for (const x of r.ranking || []) {
      const row = el("div", "padding:2px 0;font-family:var(--mono,monospace);font-size:12px;");
      row.innerHTML = `<span style="color:#9fc5ff">${x.node}</span> — ` +
        `trust ${Number(x.trust).toFixed(2)} · sample ${Number(x.sample).toFixed(2)} · n ${x.observations}`;
      root.appendChild(row);
    }
  } catch (e) {
    root.textContent = "routing failed: " + e.message;
    root.style.color = "var(--danger,#ff6b6b)";
  }
}

export function start() {
  const runBtn = document.getElementById("console-run-btn");
  if (!runBtn) return;  // tab not present (degraded HTML)
  const refreshAll = () => Promise.all([refreshTasks(), refreshTrust()]);
  runBtn.addEventListener("click", runLadder);
  const refreshBtn = document.getElementById("console-refresh-btn");
  if (refreshBtn) refreshBtn.addEventListener("click", refreshAll);
  const routeBtn = document.getElementById("console-route-btn");
  if (routeBtn) routeBtn.addEventListener("click", recommend);
  const routeInput = document.getElementById("console-route-pc");
  if (routeInput) routeInput.addEventListener("keydown", (e) => { if (e.key === "Enter") recommend(); });

  // Lazy-load on first tab activation — same pattern as orchestrator.js.
  let bootstrapped = false;
  document.querySelectorAll(".tab").forEach((t) => {
    if (t.dataset.tab !== "console") return;
    t.addEventListener("click", () => {
      if (bootstrapped) return;
      bootstrapped = true;
      refreshAll();
    });
  });
}
