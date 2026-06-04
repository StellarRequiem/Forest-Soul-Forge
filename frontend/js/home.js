// HOME hub — the operator landing, styled as a sci-fi ops console. A live HUD
// (the forest's vitals) + tiles into each section. Reuses every existing panel:
// a tile click just drives the corresponding tab. Read-only; polls while visible.

import { api } from "./api.js";

function go(tab) {
  const t = document.querySelector(`.tab[data-tab="${tab}"]`);
  if (t) t.click();
}

function fmtAgo(iso) {
  if (!iso) return "—";
  try {
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 60) return `${Math.max(0, Math.round(s))}s ago`;
    if (s < 3600) return `${Math.round(s / 60)}m ago`;
    return `${Math.round(s / 3600)}h ago`;
  } catch { return "—"; }
}

function setCell(id, text, ok) {
  const e = document.getElementById(id);
  if (!e) return;
  const v = e.querySelector(".hud-cell__value");
  if (v) v.textContent = text;
  if (ok !== undefined) e.dataset.state = ok ? "ok" : "warn";
}

// ---------------------------------------------------------------------------
// HUD — the forest's vitals (daemon · model · units · missions · trust · chain)
// ---------------------------------------------------------------------------
async function refreshHud() {
  const [h, a, c, v, b, sec] = await Promise.allSettled([
    api.get("/healthz"),
    api.get("/agents"),
    api.get("/audit/tail?n=1"),
    api.get("/synapse/verify"),
    api.get("/synapse/bounties?top=50"),
    api.get("/security/status"),
  ]);
  const hd = h.status === "fulfilled" ? h.value : null;
  const providerOk = hd?.provider?.status === "ok" && !(hd?.provider?.details?.missing || []).length;
  setCell("hud-daemon", hd?.ok ? (hd.status || "ok") : "down", !!hd?.ok);
  setCell("hud-provider", hd?.provider?.models?.conversation || "—", providerOk);

  const agents = a.status === "fulfilled" ? (a.value?.count ?? a.value?.agents?.length ?? "—") : "—";
  setCell("hud-units", String(agents));

  const ev = c.status === "fulfilled" ? c.value?.events?.[0] : null;
  setCell("hud-chain", ev ? `#${ev.seq}` : "—");
  setCell("hud-activity", fmtAgo(ev?.timestamp));

  const vv = v.status === "fulfilled" ? v.value : null;
  setCell("hud-trust", vv ? (vv.ok ? `✓ ${vv.outcomes}` : "BROKEN") : "—", vv ? vv.ok : undefined);

  const missions = b.status === "fulfilled" ? (b.value?.count ?? 0) : "—";
  setCell("hud-missions", String(missions));

  // SHIELD — honest security signal from /security/status (no global posture
  // endpoint exists, so we surface this rather than invent one).
  const sv = sec.status === "fulfilled" ? sec.value : null;
  const secure = sv ? (sv.critical_last_24h === 0 && sv.quarantined_count === 0) : false;
  setCell("hud-shield", sv ? (secure ? "secure" : `${sv.critical_last_24h} crit`) : "—",
          sv ? secure : undefined);

  return { agents, chain: ev?.seq, missions };
}

// ---------------------------------------------------------------------------
// Tiles — the sections, each a route into an existing tab
// ---------------------------------------------------------------------------
const TILES = [
  { label: "COMMAND", tab: "console", desc: "mission board · trials · live readouts", stat: (s) => `${s.missions ?? "—"} open` },
  { label: "FLEET", tab: "fleet", desc: "your units + their trust-levels", stat: (s) => `${s.agents ?? "—"} units` },
  { label: "BUILD", tab: "forge", desc: "recruit units · smith tools & skills", stat: () => "forge ▸" },
  { label: "LOG", tab: "audit", desc: "the append-only run history", stat: (s) => (s.chain != null ? `#${s.chain}` : "—") },
  { label: "RULES", tab: "security", desc: "posture · gates · reality checks", stat: () => "gates ▸" },
  { label: "COMMS", tab: "chat", desc: "talk to your units", stat: () => "open ▸" },
];

function renderTiles(stats) {
  const grid = document.getElementById("hub-tiles");
  if (!grid) return;
  grid.innerHTML = "";
  for (const t of TILES) {
    const tile = document.createElement("button");
    tile.type = "button";
    tile.className = "hub-tile";
    tile.innerHTML =
      `<div class="hub-tile__label">${t.label}</div>` +
      `<div class="hub-tile__stat">${t.stat(stats)}</div>` +
      `<div class="hub-tile__desc">${t.desc}</div>` +
      `<div class="hub-tile__enter">ENTER ▸</div>`;
    tile.addEventListener("click", () => go(t.tab));
    grid.appendChild(tile);
  }
}

async function refreshAll() {
  let stats = {};
  try { stats = await refreshHud(); } catch { /* keep last */ }
  renderTiles(stats || {});
}

export function start() {
  if (!document.getElementById("home-hud")) return;  // tab not present
  refreshAll();
  const rb = document.getElementById("hub-refresh-btn");
  if (rb) rb.addEventListener("click", refreshAll);
  document.querySelectorAll('.tab[data-tab="home"]').forEach((t) =>
    t.addEventListener("click", refreshAll));        // re-pull on return to Home
  setInterval(() => {                                 // light poll while visible
    const panel = document.querySelector('.tab-panel[data-panel="home"]');
    if (panel && !panel.hidden) refreshAll();
  }, 15000);
}
