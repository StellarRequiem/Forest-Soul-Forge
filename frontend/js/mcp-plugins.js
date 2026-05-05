// MCP-plugins panel — ADR-0043 follow-up #3 (Burst 112).
//
// Sits in the Tools tab below the existing "Registered tools" panel.
// Surfaces every MCP server the dispatcher will route to via
// mcp_call.v1, distinguishing plugin-contributed servers from
// YAML-curated ones so an operator can see the agent's full
// reachable external surface in one place.
//
// Why a separate panel rather than folding into tool-registry.js:
//   - "Registered tools" lists individual tools (mcp_call.v1 is one
//     of them). The MCP servers that mcp_call.v1 dispatches to are
//     a different abstraction — second-tier targets, not first-tier
//     tools. Mixing them would muddle the mental model.
//   - The /plugins endpoint also exposes plugin lifecycle (active /
//     disabled, sha256, secrets, capabilities) that has no place in
//     a tool list.
//
// Data sources:
//   - GET /plugins — returns full manifest + state for every plugin
//     plus rt.mcp_servers_view() at the top level. We use the
//     mcp_servers_view section to also surface YAML-curated entries
//     when they show up alongside plugin-contributed ones (the
//     dispatcher merges them; this view shows the merge).
//
// Lifecycle:
//   - Initial load on app start.
//   - Refresh on tab activation (matches tool-registry.js posture).
//   - Manual "refresh" button + "reload from disk" button (which
//     hits POST /plugins/reload — same governance gate as tool
//     registry reload).

import { api, ApiError, writeCall } from "./api.js";
import { toast } from "./toast.js";


// ---- helpers ---------------------------------------------------------------

const SIDE_EFFECT_LABEL = {
  read_only: "read-only",
  network: "network",
  filesystem: "filesystem",
  external: "external",
};


function makePill(text, kind) {
  const el = document.createElement("span");
  el.className = `pill pill--${kind}`;
  el.textContent = text;
  return el;
}


function makeSourcePill(source) {
  // "plugin" — manifest under ~/.forest/plugins/
  // "yaml"   — entry in config/mcp_servers.yaml
  // The visual distinction is the load-bearing point of this panel.
  const el = makePill(
    source === "plugin" ? "plugin" : "yaml",
    `source-${source === "plugin" ? "plugin" : "yaml"}`,
  );
  el.title = (
    source === "plugin"
      ? "Operator-installed via fsf plugin install (~/.forest/plugins/)"
      : "Operator-curated entry in config/mcp_servers.yaml"
  );
  return el;
}


function makeSideEffectsPill(side_effects) {
  return makePill(
    SIDE_EFFECT_LABEL[side_effects] || side_effects,
    `se-${side_effects}`,
  );
}


function makeStatePill(state) {
  // "active" / "disabled" — only meaningful for plugin-source entries.
  return makePill(state, `state-${state}`);
}


function renderPluginRow(plugin) {
  // plugin: full /plugins serialize() shape — includes manifest +
  // state + directory.
  const m = plugin.manifest;
  const row = document.createElement("div");
  row.className = "mcp-plugin-row";

  // Header: name, version, source, state, side-effects.
  const header = document.createElement("div");
  header.className = "mcp-plugin-row__header";

  const nameEl = document.createElement("span");
  nameEl.className = "mcp-plugin-row__name mono";
  nameEl.textContent = m.display_name || m.name;
  header.appendChild(nameEl);

  const ver = document.createElement("span");
  ver.className = "mcp-plugin-row__version muted tiny";
  ver.textContent = `v${m.version}`;
  header.appendChild(ver);

  header.appendChild(makeSourcePill("plugin"));
  header.appendChild(makeStatePill(plugin.state));
  header.appendChild(makeSideEffectsPill(m.side_effects));

  // sha256 truncation as a one-liner badge — operator can hover for
  // the full pin to verify against an upstream release.
  if (m.entry_point && m.entry_point.sha256) {
    const sha = makePill(
      `sha256:${m.entry_point.sha256.slice(0, 12)}…`,
      "sha",
    );
    sha.title = m.entry_point.sha256;
    header.appendChild(sha);
  }

  row.appendChild(header);

  // Description (license + author when present).
  const meta = [];
  if (m.author) meta.push(`by ${m.author}`);
  if (m.license) meta.push(m.license);
  if (meta.length) {
    const metaEl = document.createElement("div");
    metaEl.className = "mcp-plugin-row__meta muted tiny";
    metaEl.textContent = meta.join(" · ");
    row.appendChild(metaEl);
  }

  // Capabilities — the per-tool view. requires_human_approval map
  // (from Burst 111) renders as per-tool gating badges so an operator
  // can see at a glance which tools will hit the approval queue.
  const caps = m.capabilities || [];
  const perToolApproval = m.requires_human_approval || {};
  if (caps.length) {
    const capsEl = document.createElement("div");
    capsEl.className = "mcp-plugin-row__caps";
    for (const cap of caps) {
      const capRow = document.createElement("span");
      capRow.className = "mcp-plugin-cap mono tiny";
      // Strip "mcp.<name>." prefix when present so the bare tool name
      // is what shows; matches the namespace convention from the
      // ADR-0043 examples README.
      const prefix = `mcp.${m.name}.`;
      const bare = cap.startsWith(prefix) ? cap.slice(prefix.length) : cap;
      capRow.textContent = bare;
      const gated = !!perToolApproval[bare];
      if (gated) {
        const gate = document.createElement("span");
        gate.className = "pill pill--warn pill--xs";
        gate.textContent = "approval";
        gate.title = "This tool gates at dispatch time per the manifest map";
        capRow.appendChild(gate);
      }
      capsEl.appendChild(capRow);
    }
    row.appendChild(capsEl);
  }

  // Required secrets — operator-facing reminder that `fsf plugin
  // secrets set <name>` (or env-var equivalent) needs to be wired.
  const secrets = m.required_secrets || [];
  if (secrets.length) {
    const secEl = document.createElement("div");
    secEl.className = "mcp-plugin-row__secrets muted tiny";
    secEl.textContent =
      "secrets: " + secrets.map((s) => s.env_var || s.name).join(", ");
    row.appendChild(secEl);
  }

  // Verification status — show a green tick when the registry has
  // signed the manifest, a quiet "(unverified)" when not.
  if (m.verified_at) {
    const v = makePill("verified", "ok");
    v.title = `Registry-signed at ${m.verified_at}`;
    row.appendChild(v);
  } else {
    const u = document.createElement("div");
    u.className = "mcp-plugin-row__unverified muted tiny";
    u.textContent = "(unverified — installed directly, not via signed registry)";
    row.appendChild(u);
  }

  return row;
}


function renderYamlServerRow(name, entry) {
  // entry shape: {url, sha256, side_effects, allowlisted_tools, ...}
  // — matches mcp_call.v1's YAML registry shape.
  const row = document.createElement("div");
  row.className = "mcp-plugin-row mcp-plugin-row--yaml";

  const header = document.createElement("div");
  header.className = "mcp-plugin-row__header";

  const nameEl = document.createElement("span");
  nameEl.className = "mcp-plugin-row__name mono";
  nameEl.textContent = name;
  header.appendChild(nameEl);

  header.appendChild(makeSourcePill("yaml"));
  if (entry.side_effects) {
    header.appendChild(makeSideEffectsPill(entry.side_effects));
  }
  if (entry.requires_human_approval === true) {
    header.appendChild(makePill("approval (server-wide)", "warn"));
  }

  if (entry.sha256) {
    const sha = makePill(`sha256:${entry.sha256.slice(0, 12)}…`, "sha");
    sha.title = entry.sha256;
    header.appendChild(sha);
  }

  row.appendChild(header);

  // Allowlisted tools list.
  const tools = entry.allowlisted_tools || [];
  if (tools.length) {
    const capsEl = document.createElement("div");
    capsEl.className = "mcp-plugin-row__caps";
    for (const tool of tools) {
      const cap = document.createElement("span");
      cap.className = "mcp-plugin-cap mono tiny";
      cap.textContent = tool;
      capsEl.appendChild(cap);
    }
    row.appendChild(capsEl);
  }

  return row;
}


function renderEmpty(root, msg) {
  root.innerHTML = "";
  const empty = document.createElement("div");
  empty.className = "empty";
  empty.textContent = msg;
  root.appendChild(empty);
}


function renderList(plugins, mcpServersView) {
  const root = document.getElementById("mcp-plugins-list");
  if (!root) return;
  root.innerHTML = "";

  // Plugin-source rows come from the /plugins listing (full manifest
  // detail). YAML-only rows come from mcp_servers_view entries whose
  // names AREN'T in the plugins list (the merge logic is plugins-win-
  // on-conflict, mirroring the dispatcher's merge in
  // _build_merged_mcp_registry).
  const pluginNames = new Set(plugins.map((p) => p.name));
  const yamlOnly = Object.entries(mcpServersView || {}).filter(
    ([name]) => !pluginNames.has(name),
  );

  if (!plugins.length && !yamlOnly.length) {
    renderEmpty(
      root,
      "No MCP plugins installed and no YAML-registered servers. " +
      "Install one with `fsf plugin install <dir>` or add to config/mcp_servers.yaml.",
    );
    return;
  }

  // Plugin-source section first — the new ADR-0043 surface is the
  // load-bearing thing for this panel.
  if (plugins.length) {
    const heading = document.createElement("h3");
    heading.className = "mcp-plugins-group__title";
    const active = plugins.filter((p) => p.state === "installed").length;
    const disabled = plugins.filter((p) => p.state === "disabled").length;
    heading.textContent =
      `Plugin-installed (${plugins.length}) — ${active} active, ${disabled} disabled`;
    root.appendChild(heading);
    for (const plugin of plugins) {
      root.appendChild(renderPluginRow(plugin));
    }
  }

  if (yamlOnly.length) {
    const heading = document.createElement("h3");
    heading.className = "mcp-plugins-group__title";
    heading.textContent = `YAML-registered (${yamlOnly.length})`;
    root.appendChild(heading);
    for (const [name, entry] of yamlOnly) {
      root.appendChild(renderYamlServerRow(name, entry));
    }
  }
}


async function fetchAndRender() {
  const statusEl = document.getElementById("mcp-plugins-status");
  try {
    const data = await api.get("/plugins");
    const plugins = data.plugins || [];
    const view = data.mcp_servers_view || {};
    renderList(plugins, view);
    if (statusEl) {
      const yamlCount = Object.keys(view).filter(
        (n) => !plugins.find((p) => p.name === n),
      ).length;
      const parts = [];
      if (data.active_count) parts.push(`${data.active_count} active`);
      if (data.disabled_count) parts.push(`${data.disabled_count} disabled`);
      if (yamlCount) parts.push(`${yamlCount} yaml`);
      statusEl.textContent = parts.length ? parts.join(" · ") : "—";
    }
  } catch (e) {
    const root = document.getElementById("mcp-plugins-list");
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
  const btn = document.getElementById("mcp-plugins-reload");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "reloading…";
  }
  try {
    const resp = await writeCall("/plugins/reload", {});
    // ReloadResult shape: {added: [], removed: [], updated: [], errors: []}
    const added = resp.added?.length || 0;
    const removed = resp.removed?.length || 0;
    const updated = resp.updated?.length || 0;
    const errs = resp.errors?.length || 0;
    if (errs) {
      toast({
        title: "Plugin reload completed with errors",
        msg: `+${added} -${removed} ~${updated}; ${errs} error(s).`,
        kind: "warn", ttl: 8000,
      });
    } else {
      toast({
        title: "Plugin runtime reloaded",
        msg: `+${added} -${removed} ~${updated}.`,
        kind: "success", ttl: 4000,
      });
    }
    await fetchAndRender();
  } catch (e) {
    toast({
      title: "Plugin reload failed",
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
  const refreshBtn = document.getElementById("mcp-plugins-refresh");
  const reloadBtn = document.getElementById("mcp-plugins-reload");
  if (refreshBtn) refreshBtn.addEventListener("click", fetchAndRender);
  if (reloadBtn) reloadBtn.addEventListener("click", onReload);

  // Refresh on tab activation — same posture as tool-registry.js.
  document.querySelectorAll(".tab").forEach((tab) => {
    if (tab.dataset.tab === "tool-registry") {
      tab.addEventListener("click", fetchAndRender);
    }
  });

  // Initial load on app start.
  fetchAndRender();
}
