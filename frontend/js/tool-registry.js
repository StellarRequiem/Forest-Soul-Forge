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


// ---------------------------------------------------------------------------
// New Tool modal — ADR-0058 / B202
// ---------------------------------------------------------------------------
function openNewToolModal() {
  const backdrop = document.createElement("div");
  backdrop.className = "modal-backdrop";
  backdrop.style.cssText =
    "position:fixed;inset:0;background:rgba(0,0,0,0.5);"
    + "display:flex;align-items:center;justify-content:center;"
    + "z-index:9999;backdrop-filter:blur(4px);";

  const modal = document.createElement("div");
  modal.className = "modal";
  modal.style.cssText =
    "background:var(--surface,#1c1f26);color:var(--text,#e7e9ee);"
    + "border:1px solid var(--border,#2c303a);border-radius:8px;"
    + "min-width:520px;max-width:720px;max-height:85vh;overflow:auto;"
    + "padding:24px;box-shadow:0 12px 40px rgba(0,0,0,0.5);";
  backdrop.appendChild(modal);

  const header = document.createElement("div");
  header.style.cssText =
    "display:flex;align-items:center;justify-content:space-between;"
    + "margin-bottom:12px;";
  const title = document.createElement("h3");
  title.textContent = "Forge a new prompt-template tool";
  title.style.cssText = "margin:0;font-size:16px;";
  const closeBtn = document.createElement("button");
  closeBtn.className = "btn btn--ghost btn--sm";
  closeBtn.textContent = "×";
  closeBtn.title = "Close (Esc)";
  closeBtn.addEventListener("click", () => backdrop.remove());
  header.appendChild(title);
  header.appendChild(closeBtn);
  modal.appendChild(header);

  const hint = document.createElement("p");
  hint.className = "muted";
  hint.style.cssText = "font-size:12px;line-height:1.4;margin:0 0 16px 0;";
  hint.innerHTML =
    "Describe what the tool should do. The daemon converts your description "
    + "to a <code>spec.yaml</code> with a prompt template baked in, then "
    + "registers it as a <code>read_only</code> tool wrapping "
    + "<code>llm_think.v1</code>. For tools that need real I/O (network, "
    + "filesystem), use the MCP plugin protocol per ADR-0043.";
  modal.appendChild(hint);

  const form = document.createElement("div");

  const descLabel = document.createElement("label");
  descLabel.className = "lbl";
  descLabel.textContent = "Description (10–4000 chars)";
  const descTextarea = document.createElement("textarea");
  descTextarea.className = "inp";
  descTextarea.placeholder =
    "e.g. \"A tool that takes the last N audit chain entries and writes "
    + "a one-paragraph summary highlighting any unusual events.\"";
  descTextarea.rows = 6;
  descTextarea.style.cssText =
    "width:100%;font-family:inherit;font-size:13px;line-height:1.5;"
    + "margin-bottom:12px;resize:vertical;";

  const nameRow = document.createElement("div");
  nameRow.style.cssText = "display:flex;gap:12px;margin-bottom:16px;";
  const nameField = document.createElement("div");
  nameField.style.flex = "1";
  const nameLabel = document.createElement("label");
  nameLabel.className = "lbl";
  nameLabel.textContent = "Name (optional, snake_case)";
  const nameInp = document.createElement("input");
  nameInp.type = "text";
  nameInp.className = "inp inp--sm";
  nameInp.placeholder = "auto-derived if blank";
  nameInp.style.width = "100%";
  nameField.appendChild(nameLabel);
  nameField.appendChild(nameInp);
  const verField = document.createElement("div");
  verField.style.width = "100px";
  const verLabel = document.createElement("label");
  verLabel.className = "lbl";
  verLabel.textContent = "Version";
  const verInp = document.createElement("input");
  verInp.type = "text";
  verInp.className = "inp inp--sm";
  verInp.value = "1";
  verInp.style.width = "100%";
  verField.appendChild(verLabel);
  verField.appendChild(verInp);
  nameRow.appendChild(nameField);
  nameRow.appendChild(verField);

  form.appendChild(descLabel);
  form.appendChild(descTextarea);
  form.appendChild(nameRow);

  const forgeBtn = document.createElement("button");
  forgeBtn.className = "btn btn--primary";
  forgeBtn.textContent = "Forge";
  form.appendChild(forgeBtn);
  modal.appendChild(form);

  const result = document.createElement("div");
  result.style.marginTop = "16px";
  modal.appendChild(result);

  forgeBtn.addEventListener("click", async () => {
    const description = descTextarea.value.trim();
    if (description.length < 10) {
      toast({title: "description too short", msg: "minimum 10 chars", kind: "error"});
      return;
    }
    forgeBtn.disabled = true;
    forgeBtn.textContent = "Forging…";
    result.innerHTML =
      "<div class=\"muted\" style=\"font-size:12px;\">"
      + "Calling LLM provider — this may take several seconds…"
      + "</div>";
    try {
      const body = {description};
      if (nameInp.value.trim()) body.name = nameInp.value.trim();
      if (verInp.value.trim()) body.version = verInp.value.trim();
      const resp = await writeCall("/tools/forge", body);
      form.style.display = "none";
      result.innerHTML = "";
      result.appendChild(_renderForgedToolPreview(resp, backdrop));
      toast({title: `Forged ${resp.name}.v${resp.version}`, kind: "success"});
    } catch (e) {
      result.innerHTML = "";
      const err = document.createElement("div");
      err.style.cssText =
        "color:var(--danger,#ff6b6b);font-size:12px;line-height:1.4;"
        + "background:rgba(255,107,107,0.08);border:1px solid var(--danger,#ff6b6b);"
        + "padding:8px;border-radius:4px;margin-top:8px;";
      err.textContent = `Forge failed: ${e.message}`;
      result.appendChild(err);
      forgeBtn.disabled = false;
      forgeBtn.textContent = "Forge";
    }
  });

  function onKey(e) {
    if (e.key === "Escape") {
      backdrop.remove();
      document.removeEventListener("keydown", onKey);
    }
  }
  document.addEventListener("keydown", onKey);

  document.body.appendChild(backdrop);
  descTextarea.focus();
}


function _renderForgedToolPreview(forged, backdrop) {
  const wrap = document.createElement("div");
  wrap.style.cssText =
    "background:rgba(255,255,255,0.03);border:1px solid var(--border,#2c303a);"
    + "border-radius:6px;padding:12px;";

  const titleRow = document.createElement("div");
  titleRow.style.cssText =
    "display:flex;justify-content:space-between;align-items:center;"
    + "margin-bottom:8px;";
  const title = document.createElement("strong");
  title.textContent = `${forged.name}.v${forged.version}`;
  const hash = document.createElement("span");
  hash.className = "pill pill--ghost";
  hash.textContent = forged.spec_hash.slice(0, 16);
  hash.style.fontSize = "11px";
  titleRow.appendChild(title);
  titleRow.appendChild(hash);
  wrap.appendChild(titleRow);

  const summary = document.createElement("div");
  summary.style.cssText = "font-size:12px;line-height:1.6;margin-bottom:12px;";
  summary.innerHTML =
    `<div><span class="muted">description:</span> ${(forged.description || "").slice(0, 200)}</div>`
    + `<div><span class="muted">inputs:</span> ${forged.input_schema_keys.join(", ") || "—"}</div>`
    + `<div><span class="muted">archetypes:</span> ${forged.archetype_tags.join(", ") || "—"}</div>`
    + `<div><span class="muted">staged at:</span> <code style="font-size:11px;">${forged.staged_path}</code></div>`
    + (forged.audit_seq != null
      ? `<div><span class="muted">audit seq:</span> #${forged.audit_seq}</div>`
      : "");
  wrap.appendChild(summary);

  const tplTitle = document.createElement("div");
  tplTitle.className = "muted";
  tplTitle.style.cssText = "font-size:11px;margin-top:8px;margin-bottom:4px;";
  tplTitle.textContent = "prompt template (preview)";
  wrap.appendChild(tplTitle);
  const tplPre = document.createElement("pre");
  tplPre.style.cssText =
    "font-size:11px;line-height:1.4;background:rgba(0,0,0,0.3);"
    + "padding:8px;border-radius:4px;max-height:140px;overflow:auto;"
    + "white-space:pre-wrap;word-break:break-word;margin:0 0 12px 0;";
  tplPre.textContent = forged.prompt_template_preview || "(empty template)";
  wrap.appendChild(tplPre);

  const actions = document.createElement("div");
  actions.style.cssText = "display:flex;gap:8px;justify-content:flex-end;";

  const discardBtn = document.createElement("button");
  discardBtn.className = "btn btn--ghost btn--sm";
  discardBtn.textContent = "Discard";
  discardBtn.addEventListener("click", async () => {
    discardBtn.disabled = true;
    try {
      await api.del(`/tools/staged/forged/${forged.name}/${forged.version}`);
      toast({title: `Discarded ${forged.name}.v${forged.version}`, kind: "info"});
      backdrop.remove();
    } catch (e) {
      toast({title: "Discard failed", msg: e.message, kind: "error"});
      discardBtn.disabled = false;
    }
  });

  const installBtn = document.createElement("button");
  installBtn.className = "btn btn--primary btn--sm";
  installBtn.textContent = "Install";
  installBtn.addEventListener("click", async () => {
    installBtn.disabled = true;
    installBtn.textContent = "Installing…";
    try {
      const resp = await writeCall("/tools/install", {
        staged_path: forged.staged_path,
      });
      toast({
        title: `Installed ${resp.name}.v${resp.version}`,
        msg: `audit #${resp.audit_seq}`,
        kind: "success",
      });
      backdrop.remove();
      // Refresh the Tools tab so the new tool shows in the registered list.
      fetchAndRender();
    } catch (e) {
      toast({title: "Install failed", msg: e.message, kind: "error"});
      installBtn.disabled = false;
      installBtn.textContent = "Install";
    }
  });

  actions.appendChild(discardBtn);
  actions.appendChild(installBtn);
  wrap.appendChild(actions);

  return wrap;
}


export function start() {
  const refreshBtn = document.getElementById("tool-registry-refresh");
  const reloadBtn = document.getElementById("tool-registry-reload");
  const newBtn = document.getElementById("tool-registry-new");
  if (refreshBtn) refreshBtn.addEventListener("click", fetchAndRender);
  if (reloadBtn) reloadBtn.addEventListener("click", onReload);
  // ADR-0058 B202: New Tool button opens the prompt-template forge modal.
  if (newBtn) newBtn.addEventListener("click", openNewToolModal);

  // Refresh on tab activation.
  document.querySelectorAll(".tab").forEach((tab) => {
    if (tab.dataset.tab === "tool-registry") {
      tab.addEventListener("click", fetchAndRender);
    }
  });

  // Initial load on app start.
  fetchAndRender();
}
