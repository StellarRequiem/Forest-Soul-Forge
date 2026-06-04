// FLEET — the roster screen. Units as cards: class (genre), trust-levels per
// problem_class (XP bars), status. Read-only; a card routes to the Agents tab for
// deep management. Sci-fi ops-console tone, sibling to home.js. Joins /agents with
// /synapse/trust client-side — units with a track record ("veterans") sort first;
// the rest read UNTESTED (which is exactly what the bounty board targets).

import { api } from "./api.js";

const CLASS_COLORS = {
  companion: "#f0a8d8", observer: "#8fd3ff", guardian: "#7cffb2",
  software_engineer: "#ffd47a", network_watcher: "#8fd3ff", reviewer: "#c9a8ff",
  log_analyst: "#8fd3ff", anomaly_investigator: "#ff9f9f", operator_companion: "#f0a8d8",
  operator_steward: "#c9a8ff", domain_orchestrator: "#2dd4bf",
};
const classColor = (g) => CLASS_COLORS[g] || "#9fb0c0";

const _state = { agents: [], trust: {}, q: "" };

function trustByNode(scores) {
  const m = {};
  for (const s of scores || []) (m[s.node] ||= []).push(s);
  for (const k in m) m[k].sort((a, b) => b.trust - a.trust);
  return m;
}

async function load() {
  const [a, t] = await Promise.allSettled([api.get("/agents"), api.get("/synapse/trust")]);
  _state.agents = a.status === "fulfilled" ? (a.value?.agents || a.value || []) : [];
  _state.trust = trustByNode(t.status === "fulfilled" ? t.value?.scores : []);
  render();
}

function skills(scores) {
  if (!scores || !scores.length)
    return `<div class="unit__untested">UNTESTED · awaiting first mission</div>`;
  return scores.slice(0, 3).map((s) => {
    const pct = Math.round(s.trust * 100);
    const col = s.trust >= 0.66 ? "#7cffb2" : (s.trust <= 0.4 ? "#f0aeae" : "#ffd47a");
    return `<div class="unit__skill">
        <div class="unit__skill-row"><span>${s.problem_class}</span>
          <span style="color:${col}">${Number(s.trust).toFixed(2)} · n${s.observations}</span></div>
        <div class="unit__bar"><div class="unit__bar-fill" style="width:${pct}%;background:${col}"></div></div>
      </div>`;
  }).join("");
}

function render() {
  const grid = document.getElementById("fleet-grid");
  if (!grid) return;
  let units = _state.agents.filter((a) => a.status !== "archived");
  if (_state.q) units = units.filter((a) =>
    `${a.agent_name || ""} ${a.role || ""} ${a.genre || ""}`.toLowerCase().includes(_state.q));
  units.sort((a, b) => {
    const ta = (_state.trust[a.instance_id] || []).length, tb = (_state.trust[b.instance_id] || []).length;
    if (ta !== tb) return tb - ta;                                  // veterans first
    return (a.agent_name || "").localeCompare(b.agent_name || "");
  });

  const cnt = document.getElementById("fleet-count");
  if (cnt) cnt.textContent = `${units.length} units`;

  grid.innerHTML = "";
  for (const a of units) {
    const scores = _state.trust[a.instance_id] || [];
    const col = classColor(a.genre);
    const card = document.createElement("button");
    card.type = "button";
    card.className = scores.length ? "unit unit--veteran" : "unit";
    card.innerHTML =
      `<div class="unit__head">
         <div class="unit__name">${a.agent_name || a.instance_id}</div>
         <div class="unit__class" style="color:${col};border-color:${col}55">${(a.genre || "—").toUpperCase()}</div>
       </div>
       <div class="unit__role">${a.role || ""} <span class="unit__dna">${a.dna || ""}</span></div>
       <div class="unit__skills">${skills(scores)}</div>
       <div class="unit__enter">DETAILS ▸</div>`;
    card.addEventListener("click", () => {
      const t = document.querySelector('.tab[data-tab="agents"]');
      if (t) t.click();
    });
    grid.appendChild(card);
  }
}

export function start() {
  if (!document.getElementById("fleet-grid")) return;
  let booted = false;
  const boot = () => { if (booted) return; booted = true; load(); };
  document.querySelectorAll('.tab[data-tab="fleet"]').forEach((t) => t.addEventListener("click", boot));
  const rb = document.getElementById("fleet-refresh-btn");
  if (rb) rb.addEventListener("click", load);
  const q = document.getElementById("fleet-search");
  if (q) q.addEventListener("input", (e) => { _state.q = e.target.value.toLowerCase(); render(); });
}
