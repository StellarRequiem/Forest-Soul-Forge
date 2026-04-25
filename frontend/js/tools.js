// Tools-overrides UI (ADR-0018 T4).
//
// Responsibilities:
//   1. Load /tools/catalog once on boot (cached for the session).
//   2. When selectedRole changes, hit /tools/kit/{role} to seed the
//      default-kit checklist. Newly-checked = in the kit; unchecked =
//      tools_remove.
//   3. Maintain `toolOverrides` in state.js: { tools_add: [{name,version}],
//      tools_remove: [string] }. preview.js and forms.js read this.
//   4. After /preview returns, decorate kit rows with the live constraint
//      pills (max calls cap, approval-required, side_effects).
//
// State shape:
//   state.toolCatalog: ToolCatalogOut from /tools/catalog
//   state.toolKit: ResolvedKitOut from /tools/kit/{role} (latest)
//   state.toolOverrides: { tools_add: [{name,version}], tools_remove: [str] }

import { api, ApiError } from "./api.js";
import * as state from "./state.js";

const SIDE_EFFECT_BADGE = {
  read_only: "safe",
  network: "network",
  filesystem: "filesystem",
  external: "external",
};

function $(id) {
  return document.getElementById(id);
}

function setStatus(text) {
  const el = $("tools-status");
  if (el) el.textContent = text;
}

function getOverrides() {
  return state.get("toolOverrides") || { tools_add: [], tools_remove: [] };
}

function setOverrides(next) {
  // Always replace (not mutate) — state.subscribe uses identity for change detection.
  state.set("toolOverrides", {
    tools_add: [...(next.tools_add || [])],
    tools_remove: [...(next.tools_remove || [])],
  });
}

function resetOverrides() {
  setOverrides({ tools_add: [], tools_remove: [] });
}

/** Look up the live constraints for a tool name from the latest /preview
 * response. Returns null when no preview has run yet (e.g., right after
 * role pick). */
function constraintsForToolName(name) {
  const preview = state.get("preview");
  const list = preview?.resolved_tools;
  if (!list) return null;
  return list.find((t) => t.name === name) || null;
}

function constraintBadges(resolved) {
  // Render the meaningful constraint deltas as pills. Skip the defaults
  // so the UI stays quiet on the common case.
  const badges = [];
  if (resolved.requires_human_approval !== undefined &&
      resolved.requires_human_approval === true) {
    badges.push({ kind: "warn", text: "approval required" });
  }
  if (resolved.max_calls_per_session !== undefined &&
      resolved.max_calls_per_session < 1000) {
    badges.push({ kind: "info", text: `≤ ${resolved.max_calls_per_session}/session` });
  }
  return badges;
}

function renderDefaultKit() {
  const wrap = $("tools-default-kit");
  if (!wrap) return;
  wrap.innerHTML = "";

  const kit = state.get("toolKit");
  if (!kit) {
    wrap.innerHTML = '<div class="muted">no role selected</div>';
    return;
  }
  if (!kit.tools || kit.tools.length === 0) {
    wrap.innerHTML =
      '<div class="muted">no default tools for this role — add one below</div>';
    return;
  }

  const overrides = getOverrides();
  const removed = new Set(overrides.tools_remove);

  for (const tool of kit.tools) {
    const row = document.createElement("div");
    row.className = "tool-row";

    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.id = `tool-default-${tool.name}`;
    cb.checked = !removed.has(tool.name);
    cb.addEventListener("change", () => {
      const ov = getOverrides();
      if (cb.checked) {
        ov.tools_remove = ov.tools_remove.filter((n) => n !== tool.name);
      } else if (!ov.tools_remove.includes(tool.name)) {
        ov.tools_remove.push(tool.name);
      }
      setOverrides(ov);
    });

    const label = document.createElement("label");
    label.htmlFor = cb.id;
    label.className = "tool-row__label";

    const name = document.createElement("span");
    name.className = "tool-row__name mono";
    name.textContent = `${tool.name}.v${tool.version}`;

    const sidefx = document.createElement("span");
    const sideClass = `tool-pill tool-pill--${tool.side_effects}`;
    sidefx.className = sideClass;
    sidefx.textContent = SIDE_EFFECT_BADGE[tool.side_effects] || tool.side_effects;

    const desc = document.createElement("div");
    desc.className = "tool-row__desc muted";
    desc.textContent = tool.description;

    label.appendChild(name);
    label.appendChild(sidefx);

    // Live policy badges from /preview.resolved_tools (only when checked
    // — an unchecked tool isn't in the resolved kit).
    if (cb.checked) {
      const live = constraintsForToolName(tool.name);
      if (live) {
        for (const b of constraintBadges(live.constraints)) {
          const pill = document.createElement("span");
          pill.className = `tool-pill tool-pill--${b.kind}`;
          pill.textContent = b.text;
          label.appendChild(pill);
        }
        if (live.applied_rules?.length) {
          const rules = document.createElement("span");
          rules.className = "tool-row__rules muted tiny";
          rules.title = "policy rules that fired";
          rules.textContent = "rules: " + live.applied_rules.join(", ");
          label.appendChild(rules);
        }
      }
    }

    row.appendChild(cb);
    row.appendChild(label);
    row.appendChild(desc);
    wrap.appendChild(row);
  }
}

function renderAdded() {
  const wrap = $("tools-added");
  if (!wrap) return;
  wrap.innerHTML = "";

  const overrides = getOverrides();
  if (!overrides.tools_add.length) return;

  const title = document.createElement("div");
  title.className = "tools-added__title muted";
  title.textContent = `added (${overrides.tools_add.length})`;
  wrap.appendChild(title);

  for (const ref of overrides.tools_add) {
    const row = document.createElement("div");
    row.className = "tool-row tool-row--added";

    const name = document.createElement("span");
    name.className = "tool-row__name mono";
    name.textContent = `${ref.name}.v${ref.version}`;

    // Side-effects badge from catalog if available.
    const td = (state.get("toolCatalog")?.tools || []).find(
      (t) => t.name === ref.name && t.version === ref.version,
    );
    if (td) {
      const sidefx = document.createElement("span");
      sidefx.className = `tool-pill tool-pill--${td.side_effects}`;
      sidefx.textContent = SIDE_EFFECT_BADGE[td.side_effects] || td.side_effects;
      name.appendChild(document.createTextNode(" "));
      name.appendChild(sidefx);
    }

    // Live policy badges (joined from /preview.resolved_tools).
    const live = constraintsForToolName(ref.name);
    if (live) {
      for (const b of constraintBadges(live.constraints)) {
        const pill = document.createElement("span");
        pill.className = `tool-pill tool-pill--${b.kind}`;
        pill.textContent = b.text;
        name.appendChild(document.createTextNode(" "));
        name.appendChild(pill);
      }
    }

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "btn btn--ghost btn--sm";
    remove.textContent = "×";
    remove.title = "remove from kit";
    remove.addEventListener("click", () => {
      const ov = getOverrides();
      ov.tools_add = ov.tools_add.filter(
        (r) => !(r.name === ref.name && r.version === ref.version),
      );
      setOverrides(ov);
    });

    if (td) {
      const desc = document.createElement("div");
      desc.className = "tool-row__desc muted";
      desc.textContent = td.description;
      row.appendChild(name);
      row.appendChild(remove);
      row.appendChild(desc);
    } else {
      row.appendChild(name);
      row.appendChild(remove);
    }

    wrap.appendChild(row);
  }
}

function refreshAddSelectOptions() {
  const sel = $("tools-add-select");
  const btn = $("tools-add-btn");
  if (!sel || !btn) return;

  const catalog = state.get("toolCatalog");
  if (!catalog) {
    sel.innerHTML = '<option value="">— catalog loading —</option>';
    btn.disabled = true;
    return;
  }

  const kit = state.get("toolKit");
  const overrides = getOverrides();
  // Names already in the effective kit (default minus removed plus added).
  const inKit = new Set();
  if (kit) {
    for (const t of kit.tools) {
      if (!overrides.tools_remove.includes(t.name)) inKit.add(t.name);
    }
  }
  for (const a of overrides.tools_add) inKit.add(a.name);

  // Build options: every catalog tool not already in the effective kit
  // (filtered by name — a tool with two versions both available shows
  // both as separate options).
  const options = [['', '— pick from catalog —']];
  for (const td of catalog.tools) {
    if (inKit.has(td.name)) continue;
    const key = `${td.name}.v${td.version}`;
    const label = `${td.name}.v${td.version}  ·  ${td.side_effects}`;
    options.push([key, label]);
  }
  sel.innerHTML = "";
  for (const [val, label] of options) {
    const opt = document.createElement("option");
    opt.value = val;
    opt.textContent = label;
    sel.appendChild(opt);
  }
  btn.disabled = options.length <= 1;
}

function handleAdd() {
  const sel = $("tools-add-select");
  if (!sel || !sel.value) return;
  // Format is name.vVERSION; parse the trailing version.
  const key = sel.value;
  const dotIdx = key.lastIndexOf(".");
  if (dotIdx === -1) return;
  const name = key.slice(0, dotIdx);
  const versionPart = key.slice(dotIdx + 1);
  const version = versionPart.startsWith("v") ? versionPart.slice(1) : versionPart;
  if (!name || !version) return;

  const ov = getOverrides();
  if (!ov.tools_add.find((r) => r.name === name && r.version === version)) {
    ov.tools_add.push({ name, version });
    setOverrides(ov);
  }
  sel.value = "";
}

async function loadCatalog() {
  setStatus("loading catalog…");
  try {
    const res = await api.get("/tools/catalog");
    state.set("toolCatalog", res);
    setStatus(`catalog v${res.version} · ${res.tools.length} tools`);
  } catch (e) {
    const msg =
      e instanceof ApiError ? `${e.status} ${e.detail?.detail || e.message}` : String(e);
    state.set("toolCatalog", { version: "0", tools: [], archetypes: [] });
    setStatus(`catalog error: ${msg}`);
  }
}

async function loadKitForRole(role) {
  if (!role) {
    state.set("toolKit", null);
    return;
  }
  try {
    const res = await api.get(`/tools/kit/${encodeURIComponent(role)}`);
    state.set("toolKit", res);
  } catch (e) {
    const msg =
      e instanceof ApiError ? `${e.status} ${e.detail?.detail || e.message}` : String(e);
    setStatus(`kit error: ${msg}`);
    state.set("toolKit", { role, catalog_version: "0", tools: [] });
  }
}

function rerender() {
  renderDefaultKit();
  renderAdded();
  refreshAddSelectOptions();
}

export async function start() {
  // Catalog once for the session.
  await loadCatalog();

  // Reset overrides + reload kit when role changes.
  state.subscribe("selectedRole", async (role) => {
    resetOverrides();
    await loadKitForRole(role);
  });

  // Re-render whenever overrides, kit, or preview change.
  state.subscribe("toolKit", rerender);
  state.subscribe("toolOverrides", rerender);
  state.subscribe("preview", rerender);
  state.subscribe("toolCatalog", rerender);

  // Wire add button + reset.
  $("tools-add-btn")?.addEventListener("click", handleAdd);
  $("tools-add-select")?.addEventListener("change", () => {
    const btn = $("tools-add-btn");
    const sel = $("tools-add-select");
    if (btn && sel) btn.disabled = !sel.value;
  });
  $("tools-reset")?.addEventListener("click", resetOverrides);

  // Initial render — overrides starts at the empty default.
  setOverrides({ tools_add: [], tools_remove: [] });
  rerender();
}
