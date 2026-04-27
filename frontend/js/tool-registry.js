// Tools tab — live registry view + reload button. ADR-0019 T5 makes
// this useful: the registry can change at runtime when a plugin
// lands in data/plugins/, so the operator wants a "what's the
// dispatcher seeing right now?" view.
//
// /tools/registered returns the runtime contents (built-in vs plugin
// vs unknown). /tools/catalog (existing) returns the YAML view.
// They diverge when a plugin is loaded that hasn't been added to
// the catalog file — surface "in_catalog: false" as a warning pill
// so the operator notices.

import { api, ApiError, writeCall } from "./api.js";
import { toast } from "./toast.js";


function renderRow(t) {
  const row = document.createElement("div");
  row.className = "tool-row tool-row--registry";

  // Left: name + version, mono.
  const idCell = document.createElement("div");
  idCell.className = "tool-row__name";
  idCell.textContent = `${t.name}.v${t.version}`;
  row.appendChild(idCell);

  // Pills row.
  const pills = document.createElement("div");
  pills.className = "tool-row__pills";

  const sePill = document.createElement("span");
  sePill.className = `pill pill--se-${t.side_effects}`;
  sePill.textContent = t.side_effects;
  pills.appendChild(sePill);

  const sourcePill = document.createElement("span");
  sourcePill.className = `pill pill--source-${t.source}`;
  sourcePill.textContent = t.source;
  pills.appendChild(sourcePill);

  if (!t.in_catalog) {
    const warnPill = document.createElement("span");
    warnPill.className = "pill pill--warn";
    warnPill.textContent = "not in catalog YAML";
    warnPill.title =
      "This tool is registered but the catalog YAML doesn't list it. " +
      "Plugins augment the in-memory catalog; this is benign for plugins. " +
      "For a built-in, the catalog YAML probably needs an entry.";
    pills.appendChild(warnPill);
  }

  row.appendChild(pills);

  // Description / archetype tags.
  if (t.description || (t.archetype_tags && t.archetype_tags.length)) {
    const desc = document.createElement("div");
    desc.className = "tool-row__desc tiny";
    const parts = [];
    if (t.description) parts.push(t.description.split("\n")[0]);
    if (t.archetype_tags && t.archetype_tags.length) {
      parts.push(`archetypes: ${t.archetype_tags.join(", ")}`);
    }
    desc.textContent = parts.join(" · ");
    row.appendChild(desc);
  }

  return row;
}


function renderList(tools) {
  const root = document.getElementById("tool-registry-list");
  if (!root) return;
  root.innerHTML = "";
  if (!tools.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No tools registered.";
    root.appendChild(empty);
    return;
  }

  // Group by source so built-ins, plugins, and unknown each get
  // their own block.
  const groups = { builtin: [], plugin: [], unknown: [] };
  for (const t of tools) {
    (groups[t.source] || groups.unknown).push(t);
  }

  for (const [src, label] of [
    ["builtin", "Built-in tools"],
    ["plugin",  "Plugin tools (data/plugins/)"],
    ["unknown", "Unclassified"],
  ]) {
    if (!groups[src].length) continue;
    const heading = document.createElement("h3");
    heading.className = "tool-registry-group__title";
    heading.textContent = `${label} (${groups[src].length})`;
    root.appendChild(heading);
    for (const t of groups[src]) {
      root.appendChild(renderRow(t));
    }
  }
}


async function fetchAndRender() {
  const statusEl = document.getElementById("tool-registry-status");
  try {
    const data = await api.get("/tools/registered");
    renderList(data.tools || []);
    if (statusEl) statusEl.textContent = `${data.count || 0} registered`;
  } catch (e) {
    const root = document.getElementById("tool-registry-list");
    if (root) {
      root.innerHTML = "";
      const err = document.createElement("div");
      err.className = "empty";
      err.style.color = "var(--danger)";
      err.textContent = `Failed to load: ${e.message}`;
      root.appendChild(err);
    }
  }
}


async function onReload() {
  const btn = document.getElementById("tool-registry-reload");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "reloading…";
  }
  try {
    const resp = await writeCall("/tools/reload", {});
    const count = resp.registered_count ?? "?";
    const plugins = resp.plugins_loaded ?? "?";
    const errs = resp.plugin_errors || [];
    if (errs.length) {
      toast({
        title: "Reload completed with errors",
        msg: `${count} registered, ${plugins} plugin(s); ${errs.length} error(s).`,
        kind: "warn", ttl: 8000,
      });
    } else {
      toast({
        title: "Tool registry reloaded",
        msg: `${count} registered, ${plugins} plugin(s).`,
        kind: "success", ttl: 4000,
      });
    }
    await fetchAndRender();
  } catch (e) {
    toast({
      title: "Reload failed",
      msg: e.message, kind: "error", ttl: 6000,
    });
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "reload from disk";
    }
  }
}


export function start() {
  const refreshBtn = document.getElementById("tool-registry-refresh");
  const reloadBtn = document.getElementById("tool-registry-reload");
  if (refreshBtn) refreshBtn.addEventListener("click", fetchAndRender);
  if (reloadBtn) reloadBtn.addEventListener("click", onReload);

  // Refresh on tab activation.
  document.querySelectorAll(".tab").forEach((tab) => {
    if (tab.dataset.tab === "tool-registry") {
      tab.addEventListener("click", fetchAndRender);
    }
  });

  // Initial load on app start.
  fetchAndRender();
}
