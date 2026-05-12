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

// ADR-0055 D7: post-install grant trust_tier default derived from
// the plugin's highest_side_effect_tier. Operator can override in
// the grant picker before clicking Grant all.
const SIDE_EFFECT_TO_TIER = {
  read_only: "green",
  network: "green",
  filesystem: "yellow",
  external: "yellow",
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
    // ADR-0055 M5 (B229): swap the Install button for the
    // grant-to-agent picker so the operator can complete the
    // forge -> install -> grant -> dispatch loop in one panel.
    const row = btn.closest(".marketplace-entry");
    if (row && entry.contributes && Array.isArray(entry.contributes.tools)
        && entry.contributes.tools.length > 0) {
      _swapInGrantPicker(row, entry);
    }
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


// M5 helpers -------------------------------------------------------------

let _agentCache = null;

async function _fetchAgents() {
  if (_agentCache) return _agentCache;
  try {
    const resp = await api.get("/agents");
    _agentCache = (resp.agents || []).slice();
    _agentCache.sort((a, b) => {
      const r = (a.role || "").localeCompare(b.role || "");
      return r !== 0 ? r : (a.instance_id || "").localeCompare(b.instance_id || "");
    });
    return _agentCache;
  } catch (e) {
    return [];
  }
}


function _defaultTierForEntry(entry) {
  const tier = entry.highest_side_effect_tier || "external";
  return SIDE_EFFECT_TO_TIER[tier] || "yellow";
}


async function _swapInGrantPicker(row, entry) {
  // Replace the actions column with a grant picker. Hold a strong
  // ref to the actions container so we don't accidentally render
  // into the wrong row when the operator filters/searches mid-
  // operation.
  const actions = row.querySelector(".marketplace-entry > div:last-child")
    || row.lastElementChild;
  if (!actions) return;

  actions.innerHTML = "";
  actions.style.cssText =
    "display:flex;flex-direction:column;gap:6px;min-width:240px;"
    + "padding:8px;border:1px solid var(--accent,#9aa);"
    + "border-radius:4px;background:var(--surface-soft,rgba(255,255,255,0.03));";

  const title = document.createElement("div");
  title.className = "muted";
  title.style.cssText = "font-size:11px;text-transform:uppercase;letter-spacing:0.05em;";
  title.textContent = `Use with — ${entry.contributes.tools.length} tool${entry.contributes.tools.length === 1 ? "" : "s"}`;
  actions.appendChild(title);

  const agentSelect = document.createElement("select");
  agentSelect.className = "inp inp--sm";
  agentSelect.style.cssText = "width:100%;";
  const optPick = document.createElement("option");
  optPick.value = "";
  optPick.textContent = "— pick an agent —";
  agentSelect.appendChild(optPick);
  const agents = await _fetchAgents();
  for (const a of agents) {
    const opt = document.createElement("option");
    opt.value = a.instance_id;
    opt.textContent = `${a.role || "?"} · ${a.instance_id}`;
    agentSelect.appendChild(opt);
  }
  actions.appendChild(agentSelect);

  const tierSelect = document.createElement("select");
  tierSelect.className = "inp inp--sm";
  tierSelect.style.cssText = "width:100%;";
  const defaultTier = _defaultTierForEntry(entry);
  for (const t of ["green", "yellow", "red"]) {
    const opt = document.createElement("option");
    opt.value = t;
    opt.textContent = `tier: ${t}`;
    if (t === defaultTier) opt.selected = true;
    tierSelect.appendChild(opt);
  }
  actions.appendChild(tierSelect);

  const grantBtn = document.createElement("button");
  grantBtn.className = "btn btn--primary btn--sm";
  grantBtn.textContent = "Grant all";
  grantBtn.addEventListener("click", () => _grantAllTools(
    row, entry, agentSelect.value, tierSelect.value, grantBtn,
  ));
  actions.appendChild(grantBtn);

  const skipBtn = document.createElement("button");
  skipBtn.className = "btn btn--ghost btn--sm";
  skipBtn.textContent = "Skip";
  skipBtn.addEventListener("click", () => {
    actions.innerHTML = "";
    actions.style.cssText = "display:flex;flex-direction:column;gap:4px;min-width:90px;";
    const done = document.createElement("button");
    done.className = "btn btn--ghost btn--sm";
    done.textContent = "Installed ✓";
    done.disabled = true;
    actions.appendChild(done);
  });
  actions.appendChild(skipBtn);
}


async function _grantAllTools(row, entry, instanceId, tier, btn) {
  if (!instanceId) {
    toast({title: "Pick an agent first", kind: "warning"});
    return;
  }
  const tools = entry.contributes.tools || [];
  if (tools.length === 0) return;

  btn.disabled = true;
  btn.textContent = "Granting…";

  let ok = 0;
  const failures = [];
  for (const t of tools) {
    try {
      await writeCall(`/agents/${instanceId}/tools/grant`, {
        tool_name: t.name,
        tool_version: String(t.version || "1"),
        trust_tier: tier,
        reason: `marketplace install of ${entry.id} v${entry.version}`,
      });
      ok += 1;
    } catch (e) {
      // 400 (unknown tool — plugin reload still pending) and 409
      // (conflict — already granted) are both "non-fatal continue"
      // cases. Capture them for the summary toast.
      let msg = e.message;
      if (e instanceof ApiError && e.body && typeof e.body.detail === "string") {
        msg = e.body.detail;
      }
      failures.push({tool: `${t.name}.v${t.version || "1"}`, reason: msg});
    }
  }

  if (failures.length === 0) {
    toast({
      title: `Granted ${ok} tool${ok === 1 ? "" : "s"} to ${instanceId}`,
      msg: `tier=${tier}`,
      kind: "success",
      ttl: 6000,
    });
  } else if (ok > 0) {
    toast({
      title: `Granted ${ok} / ${tools.length}; ${failures.length} failed`,
      msg: failures.map((f) => `${f.tool}: ${f.reason.slice(0, 80)}`).join(" · "),
      kind: "warning",
      ttl: 10000,
    });
  } else {
    toast({
      title: "All grants failed",
      msg: failures.map((f) => `${f.tool}: ${f.reason.slice(0, 100)}`).join(" · "),
      kind: "error",
      ttl: 12000,
    });
  }

  // Collapse to a finished state regardless of partial success —
  // operator can re-attempt failures via the Agents tab grant pane.
  const actions = row.querySelector(".marketplace-entry > div:last-child")
    || row.lastElementChild;
  if (actions) {
    actions.innerHTML = "";
    actions.style.cssText = "display:flex;flex-direction:column;gap:4px;min-width:90px;";
    const done = document.createElement("button");
    done.className = "btn btn--ghost btn--sm";
    done.textContent = failures.length === 0 ? "Granted ✓" : `Granted ${ok}/${tools.length}`;
    done.disabled = true;
    actions.appendChild(done);
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
