// Marketplace browse pane — ADR-0055 M4 (Burst 228).
//
// Renders the configured marketplace's index entries with filters,
// supports one-click install. Sibling to ./forged-proposals.js and
// ./catalog-grants.js — same panel chrome, same toast posture.
//
// Endpoints:
//   GET  /marketplace/index    (M1, B184)
//   POST /marketplace/install  (M3, B227)
//
// Filters are client-side because the index is bounded (low
// hundreds of plugins) and the operator's "show me read_only
// plugins for my companion agent" intent is a simple predicate
// over the indexed fields. No round-trip needed.

import { api, ApiError, writeCall } from "./api.js";
import { toast } from "./toast.js";


const TIER_RANK = { read_only: 0, network: 1, filesystem: 2, external: 3 };
const TIER_LABEL = {
  read_only: "read_only",
  network: "network",
  filesystem: "filesystem",
  external: "external",
};


let _cachedEntries = [];
let _cachedMeta = null;


function _renderMeta() {
  const el = document.getElementById("marketplace-meta");
  if (!el || !_cachedMeta) {
    if (el) el.textContent = "—";
    return;
  }
  const m = _cachedMeta;
  const fetched = m.fetched_at ? new Date(m.fetched_at).toLocaleTimeString() : "—";
  const regs = (m.configured_registries || []).length;
  const failed = (m.failed_registries || []).length;
  const stale = m.stale ? " · STALE" : "";
  el.textContent =
    `${_cachedEntries.length} entries · ${regs} registr${regs === 1 ? "y" : "ies"} configured`
    + (failed ? ` · ${failed} failed` : "")
    + ` · fetched ${fetched}${stale}`;
}


function _setStatus(msg) {
  const el = document.getElementById("marketplace-status");
  if (el) el.textContent = msg;
}


function _renderEntry(entry) {
  const row = document.createElement("div");
  row.className = "marketplace-entry";
  row.style.cssText =
    "display:flex;align-items:flex-start;justify-content:space-between;"
    + "gap:12px;padding:12px;"
    + "border:1px solid var(--border,#2c303a);"
    + "border-radius:6px;margin-bottom:10px;";

  const left = document.createElement("div");
  left.style.cssText = "flex:1;min-width:0;";

  // Title row: name + version + tier pill + untrusted badge
  const titleRow = document.createElement("div");
  titleRow.style.cssText = "display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-wrap:wrap;";
  const name = document.createElement("strong");
  name.textContent = entry.name || entry.id;
  const version = document.createElement("span");
  version.className = "muted";
  version.style.cssText = "font-family:var(--mono,monospace);font-size:11px;";
  version.textContent = `v${entry.version}`;
  const tier = document.createElement("span");
  const t = entry.highest_side_effect_tier || "external";
  tier.className = "pill pill--" + (
    t === "read_only" ? "success"
      : t === "network" ? "info"
      : t === "filesystem" ? "warning"
      : "danger"
  );
  tier.textContent = TIER_LABEL[t] || t;
  tier.style.cssText = "font-size:10px;text-transform:uppercase;letter-spacing:0.05em;";
  titleRow.appendChild(name);
  titleRow.appendChild(version);
  titleRow.appendChild(tier);
  // Untrusted badge — M6 signing not yet enforced. Surface it
  // explicitly so operators know they're trusting the source URL
  // rather than a kernel-verified signature.
  const untrusted = document.createElement("span");
  untrusted.className = "pill pill--ghost";
  untrusted.textContent = "untrusted";
  untrusted.title = "Signature verification queued (M6). Trust depends on the source registry's reputation.";
  untrusted.style.cssText = "font-size:10px;text-transform:uppercase;letter-spacing:0.05em;";
  titleRow.appendChild(untrusted);
  left.appendChild(titleRow);

  // Description
  if (entry.description) {
    const desc = document.createElement("div");
    desc.style.cssText = "font-size:13px;line-height:1.4;margin-bottom:6px;";
    desc.textContent = entry.description;
    left.appendChild(desc);
  }

  // Permissions summary — load-bearing for operator informed consent
  if (entry.permissions_summary) {
    const perm = document.createElement("div");
    perm.style.cssText =
      "font-size:12px;line-height:1.35;padding:6px 8px;"
      + "background:var(--surface-soft,rgba(255,255,255,0.04));"
      + "border-left:3px solid var(--accent,#9aa);"
      + "border-radius:3px;margin-bottom:6px;";
    perm.textContent = entry.permissions_summary;
    left.appendChild(perm);
  }

  // Meta: author, source, capabilities count
  const meta = document.createElement("div");
  meta.className = "muted";
  meta.style.cssText = "font-size:11px;font-family:var(--mono,monospace);word-break:break-all;";
  const caps = (entry.contributes && entry.contributes.tools) || [];
  const skills = (entry.contributes && entry.contributes.skills) || [];
  const mcps = (entry.contributes && entry.contributes.mcp_servers) || [];
  const capsBits = [];
  if (caps.length) capsBits.push(`${caps.length} tool${caps.length === 1 ? "" : "s"}`);
  if (skills.length) capsBits.push(`${skills.length} skill${skills.length === 1 ? "" : "s"}`);
  if (mcps.length) capsBits.push(`${mcps.length} mcp${mcps.length === 1 ? "" : "s"}`);
  meta.textContent =
    `id: ${entry.id} · author: ${entry.author || "—"} · ${capsBits.join(", ") || "no listed capabilities"}`;
  if (entry.source_url) {
    const sep = document.createElement("span");
    sep.textContent = " · ";
    meta.appendChild(sep);
    const a = document.createElement("a");
    a.href = entry.source_url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = "source";
    meta.appendChild(a);
  }
  left.appendChild(meta);

  const actions = document.createElement("div");
  actions.style.cssText = "display:flex;flex-direction:column;gap:4px;min-width:90px;";
  const installBtn = document.createElement("button");
  installBtn.className = "btn btn--primary btn--sm";
  installBtn.textContent = "Install";
  installBtn.addEventListener("click", () => _onInstall(entry, installBtn));
  actions.appendChild(installBtn);

  row.appendChild(left);
  row.appendChild(actions);
  return row;
}


async function _onInstall(entry, btn) {
  btn.disabled = true;
  btn.textContent = "Installing…";
  try {
    const resp = await writeCall("/marketplace/install", {
      registry_id: entry.source_registry,
      entry_id: entry.id,
      version: entry.version,
    });
    toast({
      title: `Installed ${resp.plugin_name} v${resp.version}`,
      msg: resp.trusted
        ? "verified"
        : "untrusted (signature verification queued)",
      kind: "success",
      ttl: 6000,
    });
    btn.textContent = "Installed ✓";
    // Keep disabled — re-installing without overwrite would 409.
  } catch (e) {
    let msg = e.message;
    if (e instanceof ApiError && e.body && typeof e.body.detail === "string") {
      msg = e.body.detail;
    } else if (e instanceof ApiError && e.body && e.body.detail) {
      msg = JSON.stringify(e.body.detail);
    }
    toast({title: "Install failed", msg, kind: "error", ttl: 10000});
    btn.disabled = false;
    btn.textContent = "Install";
  }
}


function _applyFilters() {
  const root = document.getElementById("marketplace-list");
  if (!root) return;
  const searchEl = document.getElementById("marketplace-search");
  const tierEl = document.getElementById("marketplace-tier-filter");
  const q = (searchEl?.value || "").trim().toLowerCase();
  const tierLimit = tierEl?.value || "";
  const tierLimitRank = tierLimit ? TIER_RANK[tierLimit] : 99;

  const matches = _cachedEntries.filter((e) => {
    if (q) {
      const hay = `${e.name || ""} ${e.id} ${e.description || ""} ${(e.archetype_tags || []).join(" ")}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    const tier = e.highest_side_effect_tier || "external";
    if ((TIER_RANK[tier] ?? 99) > tierLimitRank) return false;
    return true;
  });

  // Sort: safer first (lower tier rank), then alphabetical by name.
  matches.sort((a, b) => {
    const ra = TIER_RANK[a.highest_side_effect_tier || "external"] ?? 99;
    const rb = TIER_RANK[b.highest_side_effect_tier || "external"] ?? 99;
    if (ra !== rb) return ra - rb;
    return (a.name || a.id).localeCompare(b.name || b.id);
  });

  root.innerHTML = "";
  if (matches.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty";
    if (_cachedEntries.length === 0) {
      empty.innerHTML =
        "No marketplace entries available. Configure registries via "
        + "<code>FSF_MARKETPLACE_REGISTRIES</code> in your <code>.env</code>, "
        + "then refresh.";
    } else {
      empty.textContent = "No entries match the current filters.";
    }
    root.appendChild(empty);
    return;
  }
  for (const e of matches) {
    root.appendChild(_renderEntry(e));
  }
}


async function _fetchAndRender() {
  const root = document.getElementById("marketplace-list");
  if (!root) return;
  _setStatus("loading…");
  try {
    const data = await api.get("/marketplace/index");
    _cachedEntries = data.entries || [];
    _cachedMeta = data;
    _setStatus(
      data.stale
        ? `${_cachedEntries.length} entries · STALE`
        : `${_cachedEntries.length} entries`,
    );
    _renderMeta();
    _applyFilters();
  } catch (e) {
    _cachedEntries = [];
    _cachedMeta = null;
    _setStatus("error");
    root.innerHTML = "";
    const err = document.createElement("div");
    err.className = "empty";
    err.style.color = "var(--danger,#ff6b6b)";
    err.textContent = "Failed to load marketplace index: " + e.message;
    root.appendChild(err);
  }
}


export function start() {
  const refresh = document.getElementById("marketplace-refresh");
  const search = document.getElementById("marketplace-search");
  const tier = document.getElementById("marketplace-tier-filter");
  if (!refresh) return;  // Tab not present (degraded HTML)

  refresh.addEventListener("click", _fetchAndRender);
  if (search) search.addEventListener("input", _applyFilters);
  if (tier) tier.addEventListener("change", _applyFilters);

  // Auto-refresh on Marketplace-tab activation so newly-pushed
  // entries show up without a manual refresh.
  document.querySelectorAll(".tab").forEach((t) => {
    if (t.dataset.tab === "marketplace") {
      t.addEventListener("click", _fetchAndRender);
    }
  });

  _fetchAndRender();
}
