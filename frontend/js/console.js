// Operator Console — ADR-0096.
//
// The clickable surface over the task exchange + the synaptic layer:
//   1. Mission board       — GET /synapse/bounties (uncertainty-ranked); click a
//                            mission → it assigns the class + opens its receipts.
//   2. Assign              — GET /synapse/route?problem_class=... (trust-ranked).
//   3. Receipts            — GET /synapse/why (every audited outcome behind a
//                            trust value) — tamper-evident evidence, not assertion.
//   4. Run training ladder — POST /training/run -> the report (per-tier scores).
//   5. Task ladder         — GET /training/tasks (the tiered catalog).
//   6. Fleet trust         — GET /synapse/trust; click a row → its receipts.
//
// Safe by design: every endpoint here is read-only; routing INFORMS (capability
// stays human-gated, ADR-0095). Polling-based (v1) — an SSE live stream is the
// v2 upgrade. Sibling shape to orchestrator.js.

import { api } from "./api.js";
import { toast } from "./toast.js";
import { startLive, onChainEntryDebounced } from "./live.js";

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
      const tr = el("tr", "border-bottom:1px solid #1c2028;cursor:pointer;");
      tr.title = `receipts for ${s.node} @ ${s.problem_class}`;
      tr.innerHTML =
        `<td style='padding:4px 8px;font-family:var(--mono,monospace);color:#aaa'>${s.node}</td>` +
        `<td style='padding:4px 8px'>${s.problem_class}</td>` +
        `<td style='padding:4px 8px;color:${col}'>${Number(s.trust).toFixed(2)}</td>` +
        `<td style='padding:4px 8px;color:#888'>${s.observations}</td>`;
      tr.addEventListener("click", () => showWhy(s.node, s.problem_class));
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
// Bounty board — what to test next (uncertainty-ranked)
// ---------------------------------------------------------------------------
async function refreshBounties() {
  const root = document.getElementById("console-bounties");
  if (!root) return;
  root.textContent = "loading…";
  try {
    const r = await api.get("/synapse/bounties?top=10");
    root.innerHTML = "";
    if (!(r.bounties || []).length) {
      root.textContent = "No bounties — every tracked (agent, class) pair is well-tested.";
      root.style.color = "var(--muted,#888)";
      return;
    }
    root.appendChild(el("div", "color:var(--muted,#888);font-size:11px;margin-bottom:6px;", r.note || ""));
    for (const b of r.bounties) {
      const row = el("div", "padding:5px 0;border-bottom:1px solid #1c2028;font-size:12px;cursor:pointer;");
      row.title = `assign ${b.problem_class} + show ${b.node}'s receipts`;
      row.innerHTML =
        `<span style="font-family:var(--mono,monospace);color:#9fc5ff">${b.node}</span> @ ${b.problem_class} — ` +
        `uncertainty <strong style="color:#f0d8ae">${Number(b.uncertainty).toFixed(2)}</strong> ` +
        `<span style="color:#888">(trust ${Number(b.trust).toFixed(2)}, n ${b.observations})</span> ` +
        `<span style="color:#4a6b5a">▸ assign · why</span>`;
      row.addEventListener("click", () => { assignTo(b.problem_class); showWhy(b.node, b.problem_class); });
      root.appendChild(row);
    }
  } catch (e) {
    root.textContent = "failed to load bounties: " + e.message;
    root.style.color = "var(--danger,#ff6b6b)";
  }
}

// ---------------------------------------------------------------------------
// Receipts — the audited outcomes behind a trust value (GET /synapse/why).
// The trust thesis made tangible: every number traces to tamper-evident
// evidence the operator can read. Invoked by clicking a mission or trust row.
// ---------------------------------------------------------------------------
function assignTo(problem_class) {
  const input = document.getElementById("console-route-pc");
  if (input) input.value = problem_class;
  recommend();
}

async function showWhy(node, problem_class) {
  const root = document.getElementById("console-why");
  if (!root) return;
  root.style.color = "";
  root.textContent = `loading receipts for ${node} @ ${problem_class}…`;
  try {
    const r = await api.get(
      `/synapse/why?node=${encodeURIComponent(node)}&problem_class=${encodeURIComponent(problem_class)}`);
    root.innerHTML = "";
    const head = el("div", "margin-bottom:6px;font-size:12px;");
    head.innerHTML =
      `<span style="font-family:var(--mono,monospace);color:#9fc5ff">${node}</span> @ ${problem_class} — ` +
      `<strong>${r.n}</strong> audited outcome${r.n === 1 ? "" : "s"} on the hash-chained ledger`;
    root.appendChild(head);
    if (!(r.outcomes || []).length) {
      root.appendChild(el("div", "color:var(--muted,#888);font-size:12px;",
        "No outcomes yet — this pair is untested, which is exactly why it surfaces as a mission."));
      return;
    }
    for (const o of r.outcomes) {
      const row = el("div", "padding:3px 0;border-bottom:1px solid #1c2028;font-family:var(--mono,monospace);font-size:12px;");
      const col = o.success ? "#aef0ae" : "#f0aeae";
      row.innerHTML =
        `<span style="color:#666">#${Number(o.seq)}</span> ${o.success ? "✅" : "❌"} ` +
        `<span style="color:${col}">${o.success ? "succeeded" : "failed"}</span> ` +
        `<span style="color:#888">· weight ${Number(o.weight).toFixed(2)}</span>`;
      if (o.evidence != null && o.evidence !== "") {
        // evidence is free-form → textContent (el's 3rd arg) so it can't inject markup.
        row.appendChild(el("span", "color:#9aa",
          " — " + (typeof o.evidence === "string" ? o.evidence : JSON.stringify(o.evidence))));
      }
      root.appendChild(row);
    }
  } catch (e) {
    root.textContent = "failed to load receipts: " + e.message;
    root.style.color = "var(--danger,#ff6b6b)";
  }
}

// Prove the ledger these receipts live on hasn't been tampered with: GET
// /synapse/verify folds the whole hash chain and reports the first break, if any.
// The receipts are the evidence; this is the proof that evidence wasn't forged.
async function verifyLedger() {
  const out = document.getElementById("console-verify-result");
  if (!out) return;
  out.textContent = "verifying…";
  out.style.color = "#caa86a";
  try {
    const r = await api.get("/synapse/verify");
    if (r.ok) {
      out.textContent = `✓ untampered · ${r.outcomes} outcomes`;
      out.style.color = "#5cd6a8";
    } else {
      out.textContent = `✗ BROKEN: ${r.reason || "chain integrity failure"}`;
      out.style.color = "#d68a8a";
    }
  } catch (e) {
    out.textContent = "verify failed: " + e.message;
    out.style.color = "var(--danger,#ff6b6b)";
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
  const refreshAll = () => Promise.all([refreshTasks(), refreshTrust(), refreshBounties()]);
  runBtn.addEventListener("click", runLadder);
  const refreshBtn = document.getElementById("console-refresh-btn");
  if (refreshBtn) refreshBtn.addEventListener("click", refreshAll);
  const routeBtn = document.getElementById("console-route-btn");
  if (routeBtn) routeBtn.addEventListener("click", recommend);
  const verifyBtn = document.getElementById("console-verify-btn");
  if (verifyBtn) verifyBtn.addEventListener("click", verifyLedger);
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

  // Live audit stream — when COMMAND is open, re-rank the mission board and
  // refresh fleet trust the instant a dispatch lands (the loop, live). The task
  // ladder is a static catalog, so it's left out. No-op until bootstrapped.
  startLive();
  onChainEntryDebounced(() => {
    const panel = document.querySelector('.tab-panel[data-panel="console"]');
    if (bootstrapped && panel && !panel.hidden) { refreshBounties(); refreshTrust(); }
  });
}
