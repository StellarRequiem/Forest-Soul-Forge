// Skills tab — operator UI for ADR-0031 T5 catalog + T2b run endpoint.
//
// Each installed skill renders as a card showing:
//   - name + version + description
//   - requires (tool refs)
//   - inputs schema summary (which keys, which types)
//   - steps (id + tool ref or for_each marker)
//   - "Run on agent…" form: agent picker + inputs JSON textarea +
//     session id + run button
//   - Run result inline (succeeded → output JSON; failed →
//     failed step + reason)
//
// State.agents drives the agent picker per-card (subscribed once,
// applied to all cards on each refresh).

import { api, ApiError, writeCall } from "./api.js";
import * as state from "./state.js";
import { toast } from "./toast.js";


// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------
function renderSkillCard(skill) {
  const card = document.createElement("div");
  card.className = "skill-card";
  card.dataset.skill = `${skill.name}.v${skill.version}`;

  // Header.
  const header = document.createElement("div");
  header.className = "skill-card__header";
  const name = document.createElement("strong");
  name.className = "skill-card__name";
  name.textContent = `${skill.name}.v${skill.version}`;
  header.appendChild(name);
  const hashPill = document.createElement("span");
  hashPill.className = "pill pill--ghost";
  hashPill.textContent = skill.skill_hash.slice(0, 22);
  header.appendChild(hashPill);
  card.appendChild(header);

  // Description.
  const desc = document.createElement("p");
  desc.className = "skill-card__desc";
  desc.textContent = (skill.description || "").trim();
  card.appendChild(desc);

  // Requires + steps + outputs in a compact grid.
  const grid = document.createElement("div");
  grid.className = "skill-card__grid";
  grid.appendChild(_section("requires", skill.requires.join(", ") || "—"));
  grid.appendChild(_section(
    "steps",
    skill.steps.map((s) => {
      if (s.kind === "tool") return `${s.id} → ${s.tool}`;
      if (s.kind === "for_each") return `${s.id} → for_each(${s.inner_count})`;
      return s.id;
    }).join(", ") || "—",
  ));
  grid.appendChild(_section(
    "output keys",
    skill.output_keys.join(", ") || "—",
  ));
  if (skill.forged_by) {
    grid.appendChild(_section(
      "forged",
      `${skill.forged_by} via ${skill.forge_provider} at ${skill.forged_at}`,
    ));
  }
  card.appendChild(grid);

  // Run form.
  const runForm = document.createElement("div");
  runForm.className = "skill-card__run";

  const agentRow = document.createElement("div");
  agentRow.className = "skill-card__row";
  const agentLabel = document.createElement("label");
  agentLabel.className = "lbl";
  agentLabel.textContent = "agent";
  const agentSelect = document.createElement("select");
  agentSelect.className = "inp inp--sm skill-card__agent";
  _populateAgentSelect(agentSelect, state.get("agents") || []);
  agentRow.appendChild(agentLabel);
  agentRow.appendChild(agentSelect);

  const sessionLabel = document.createElement("label");
  sessionLabel.className = "lbl";
  sessionLabel.textContent = "session";
  const sessionInp = document.createElement("input");
  sessionInp.type = "text";
  sessionInp.className = "inp inp--sm";
  sessionInp.placeholder = "skill-run-1";
  sessionInp.value = `${skill.name}-${Date.now().toString(36)}`;
  agentRow.appendChild(sessionLabel);
  agentRow.appendChild(sessionInp);
  runForm.appendChild(agentRow);

  const inputsLabel = document.createElement("label");
  inputsLabel.className = "lbl";
  inputsLabel.textContent = "inputs (JSON)";
  runForm.appendChild(inputsLabel);
  const inputsTa = document.createElement("textarea");
  inputsTa.className = "inp inp--sm skill-card__inputs";
  inputsTa.rows = 4;
  inputsTa.spellcheck = false;
  inputsTa.value = _exampleInputs(skill.inputs_schema);
  runForm.appendChild(inputsTa);

  const actions = document.createElement("div");
  actions.className = "skill-card__actions";
  const runBtn = document.createElement("button");
  runBtn.type = "button";
  runBtn.className = "btn btn--primary btn--sm";
  runBtn.textContent = "run";
  runBtn.addEventListener("click", () => onRun(skill, card, {
    agentSelect, inputsTa, sessionInp,
  }));
  actions.appendChild(runBtn);
  runForm.appendChild(actions);
  card.appendChild(runForm);

  // Result placeholder.
  const result = document.createElement("div");
  result.className = "skill-card__result";
  card.appendChild(result);

  return card;
}


function _section(label, value) {
  const wrap = document.createElement("div");
  wrap.className = "skill-card__section";
  const lbl = document.createElement("span");
  lbl.className = "skill-card__label";
  lbl.textContent = label;
  const val = document.createElement("span");
  val.className = "skill-card__value";
  val.textContent = value;
  wrap.appendChild(lbl);
  wrap.appendChild(val);
  return wrap;
}


function _exampleInputs(schema) {
  // Best-effort placeholder JSON for the inputs schema. Looks at
  // ``properties`` and emits a ``{}`` with the right keys + tasteful
  // stub values. Operator edits before clicking run.
  if (!schema || schema.type !== "object" || !schema.properties) {
    return "{}";
  }
  const out = {};
  for (const [k, v] of Object.entries(schema.properties)) {
    if (v?.type === "string") out[k] = "";
    else if (v?.type === "integer") out[k] = 0;
    else if (v?.type === "number") out[k] = 0.0;
    else if (v?.type === "boolean") out[k] = false;
    else if (v?.type === "array") out[k] = [];
    else if (v?.type === "object") out[k] = {};
    else out[k] = null;
  }
  return JSON.stringify(out, null, 2);
}


function _populateAgentSelect(select, agents) {
  const previous = select.value;
  select.innerHTML = "";
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "— pick an agent —";
  select.appendChild(placeholder);
  for (const a of (agents || [])) {
    const opt = document.createElement("option");
    opt.value = a.instance_id;
    opt.textContent = `${a.agent_name} · ${a.role} · ${a.dna}`;
    select.appendChild(opt);
  }
  if (previous && [...select.options].some((o) => o.value === previous)) {
    select.value = previous;
  }
}


// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------
async function onRun(skill, card, { agentSelect, inputsTa, sessionInp }) {
  const instanceId = agentSelect.value;
  if (!instanceId) {
    toast({
      title: "Pick an agent",
      msg: "Skills run against a specific agent. Pick one in the dropdown.",
      kind: "warn", ttl: 4000,
    });
    return;
  }
  let inputs;
  try {
    inputs = JSON.parse(inputsTa.value || "{}");
  } catch (e) {
    toast({
      title: "Inputs not valid JSON",
      msg: e.message, kind: "warn", ttl: 5000,
    });
    return;
  }
  const sessionId = (sessionInp.value || "").trim()
    || `${skill.name}-${Date.now().toString(36)}`;

  const resultBox = card.querySelector(".skill-card__result");
  resultBox.innerHTML = "";
  const running = document.createElement("div");
  running.className = "skill-card__running";
  running.textContent = "running…";
  resultBox.appendChild(running);

  try {
    const resp = await writeCall(
      `/agents/${encodeURIComponent(instanceId)}/skills/run`,
      {
        skill_name: skill.name,
        skill_version: skill.version,
        session_id: sessionId,
        inputs,
      },
    );
    resultBox.innerHTML = "";
    resultBox.appendChild(_renderRunResponse(resp));
    toast({
      title: resp.status === "succeeded" ? "Skill succeeded" : "Skill failed",
      msg: `${skill.name}.v${skill.version} — ${resp.steps_executed || 0} steps`,
      kind: resp.status === "succeeded" ? "success" : "error",
      ttl: 5000,
    });
  } catch (e) {
    resultBox.innerHTML = "";
    const err = document.createElement("div");
    err.className = "skill-card__error";
    err.textContent = e.message;
    resultBox.appendChild(err);
    toast({
      title: "Skill request failed",
      msg: e.message, kind: "error", ttl: 6000,
    });
  }
}


function _renderRunResponse(resp) {
  const root = document.createElement("div");
  root.className = `skill-card__run-result skill-card__run-result--${resp.status}`;
  const statusLine = document.createElement("div");
  statusLine.className = "skill-card__run-status";
  statusLine.textContent =
    `${resp.status} · executed=${resp.steps_executed} skipped=${resp.steps_skipped} ` +
    `· invoked_seq=${resp.invoked_seq} completed_seq=${resp.completed_seq}`;
  root.appendChild(statusLine);
  if (resp.status === "succeeded") {
    const out = document.createElement("pre");
    out.className = "skill-card__run-output";
    out.textContent = JSON.stringify(resp.output || {}, null, 2);
    root.appendChild(out);
  } else if (resp.status === "failed") {
    const fail = document.createElement("div");
    fail.className = "skill-card__run-fail";
    fail.textContent =
      `failed step: ${resp.failed_step_id} — ${resp.failure_reason}: ` +
      `${resp.failure_detail || ""}`;
    root.appendChild(fail);
    if (resp.bindings_at_failure) {
      const partial = document.createElement("pre");
      partial.className = "skill-card__run-output";
      partial.textContent = JSON.stringify(resp.bindings_at_failure, null, 2);
      root.appendChild(partial);
    }
  }
  return root;
}


// ---------------------------------------------------------------------------
// Fetch + render
// ---------------------------------------------------------------------------
async function fetchAndRender() {
  const root = document.getElementById("skills-list");
  const status = document.getElementById("skills-status");
  if (!root) return;
  try {
    const data = await api.get("/skills");
    const skills = data.skills || [];
    root.innerHTML = "";
    if (status) status.textContent = `${data.count || 0} installed`;
    if (!skills.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.innerHTML =
        "No skills installed yet. Click <strong>+ New skill</strong> above to "
        + "describe a workflow and have the daemon forge a manifest "
        + "(ADR-0057), or use the CLI: <code>fsf forge skill \"...\"</code>.";
      root.appendChild(empty);
      return;
    }
    for (const s of skills) {
      root.appendChild(renderSkillCard(s));
    }
  } catch (e) {
    root.innerHTML = "";
    const err = document.createElement("div");
    err.className = "empty";
    err.style.color = "var(--danger)";
    err.textContent = `Failed to load skills: ${e.message}`;
    root.appendChild(err);
  }
}


// Re-populate every card's agent select when state.agents updates.
function refreshAgentSelectors(agents) {
  document.querySelectorAll(".skill-card__agent").forEach((sel) => {
    _populateAgentSelect(sel, agents || []);
  });
}


// ---------------------------------------------------------------------------
// New Skill modal — ADR-0057 / B201
// ---------------------------------------------------------------------------
//
// Two-stage flow:
//   1. PROPOSE — operator types a description, clicks Forge. We POST
//      /skills/forge, the daemon hits the LLM, parses the YAML, and
//      writes a staged manifest. Multi-second latency expected; the
//      modal shows a spinner.
//   2. PREVIEW — daemon returns a manifest summary. We show name +
//      requires + step count + the forge log excerpt. Operator
//      clicks Install (POST /skills/install) or Discard (DELETE
//      /skills/staged/{name}/{version}).
//
// All DOM is created inline rather than via a template, so the
// modal is self-contained and disposable. We append to document.body
// rather than into the Skills panel so it overlays other tabs.
// Closing the modal (X, Escape, or Discard/Install completion)
// removes the node entirely; no persistent state.
function openNewSkillModal() {
  // Backdrop + container.
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

  // Header.
  const header = document.createElement("div");
  header.style.cssText =
    "display:flex;align-items:center;justify-content:space-between;"
    + "margin-bottom:12px;";
  const title = document.createElement("h3");
  title.textContent = "Forge a new skill";
  title.style.cssText = "margin:0;font-size:16px;";
  const closeBtn = document.createElement("button");
  closeBtn.className = "btn btn--ghost btn--sm";
  closeBtn.textContent = "×";
  closeBtn.title = "Close (Esc)";
  closeBtn.addEventListener("click", () => backdrop.remove());
  header.appendChild(title);
  header.appendChild(closeBtn);
  modal.appendChild(header);

  // Hint paragraph.
  const hint = document.createElement("p");
  hint.className = "muted";
  hint.style.cssText = "font-size:12px;line-height:1.4;margin:0 0 16px 0;";
  hint.innerHTML =
    "Describe a workflow in plain English. The daemon converts it to a "
    + "<code>SkillDef</code> manifest by calling the configured LLM. "
    + "Be explicit about inputs, outputs, and which tools should chain — "
    + "vague descriptions yield manifests that reference tools by common "
    + "name rather than versioned id. The propose stage stages the "
    + "manifest under <code>data/forge/skills/staged/</code>; review the "
    + "preview before installing.";
  modal.appendChild(hint);

  // Stage 1 form.
  const form = document.createElement("div");
  form.className = "modal__form";

  const descLabel = document.createElement("label");
  descLabel.className = "lbl";
  descLabel.textContent = "Description (10–4000 chars)";
  const descTextarea = document.createElement("textarea");
  descTextarea.className = "inp";
  descTextarea.placeholder =
    "e.g. \"Summarize the last 10 audit chain entries by event type "
    + "and write a one-line headline per group to memory under tag "
    + "'daily_chain_summary'.\"";
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

  // Status / preview area (rendered after Forge).
  const result = document.createElement("div");
  result.style.marginTop = "16px";
  modal.appendChild(result);

  // Track the staged path so Install / Discard can reuse it.
  let staged = null;

  forgeBtn.addEventListener("click", async () => {
    const description = descTextarea.value.trim();
    if (description.length < 10) {
      toast("description too short — minimum 10 chars", "danger");
      return;
    }
    forgeBtn.disabled = true;
    forgeBtn.textContent = "Forging…";
    result.innerHTML =
      "<div class=\"muted\" style=\"font-size:12px;\">"
      + "Calling LLM provider — this may take several seconds…"
      + "</div>";
    try {
      const body = { description };
      if (nameInp.value.trim()) body.name = nameInp.value.trim();
      if (verInp.value.trim()) body.version = verInp.value.trim();
      const resp = await writeCall("/skills/forge", body);
      staged = resp;
      // Hide the form, show the preview.
      form.style.display = "none";
      result.innerHTML = "";
      result.appendChild(_renderForgedPreview(resp, backdrop));
      toast(`Forged ${resp.name}.v${resp.version}`, "success");
    } catch (e) {
      result.innerHTML = "";
      const err = document.createElement("div");
      err.style.cssText =
        "color:var(--danger,#ff6b6b);font-size:12px;line-height:1.4;"
        + "background:rgba(255,107,107,0.08);border:1px solid var(--danger,#ff6b6b);"
        + "padding:8px;border-radius:4px;margin-top:8px;";
      // B207: if the daemon surfaced a structured 422 with the
      // forge_log_excerpt + quarantine_dir, show those so the
      // operator can see what the LLM produced and where the raw
      // file is. Pre-B207 the modal only showed the bare error
      // string and the raw LLM output was thrown away.
      const detail = e?.body?.detail;
      if (detail && detail.error === "manifest_validation_failed") {
        const lines = [
          `<strong>Forge failed — manifest parse error</strong>`,
          `<div style="margin-top:6px;font-family:var(--mono,monospace);font-size:11px;">`
            + `<div>path: ${detail.path || "(root)"}</div>`
            + `<div style="margin-top:4px;white-space:pre-wrap;">${(detail.detail || "").replace(/&/g,"&amp;").replace(/</g,"&lt;")}</div>`
            + `</div>`,
        ];
        if (detail.quarantine_dir) {
          lines.push(
            `<div style="margin-top:8px;font-size:11px;">`
            + `Raw LLM output saved to:<br><code style="font-size:10px;">${detail.quarantine_dir}/manifest_raw.yaml</code>`
            + `<br>You can edit it by hand and re-install via the Approvals tab, or try Forge again with a tighter description.`
            + `</div>`,
          );
        }
        if (detail.forge_log_excerpt) {
          lines.push(
            `<details style="margin-top:8px;font-size:11px;">`
            + `<summary style="cursor:pointer;">forge.log (last 1200 chars — what the LLM produced)</summary>`
            + `<pre style="margin-top:4px;background:rgba(0,0,0,0.3);padding:6px;border-radius:3px;font-size:10px;white-space:pre-wrap;word-break:break-word;max-height:200px;overflow:auto;">`
            + (detail.forge_log_excerpt || "").replace(/&/g,"&amp;").replace(/</g,"&lt;")
            + `</pre></details>`,
          );
        }
        err.innerHTML = lines.join("");
      } else {
        err.textContent = `Forge failed: ${e.message}`;
      }
      result.appendChild(err);
      forgeBtn.disabled = false;
      forgeBtn.textContent = "Forge";
    }
  });

  // Esc to close.
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


function _renderForgedPreview(forged, backdrop) {
  // Preview card with manifest summary + Install / Discard buttons.
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
  hash.textContent = forged.skill_hash.slice(0, 16);
  hash.style.fontSize = "11px";
  titleRow.appendChild(title);
  titleRow.appendChild(hash);
  wrap.appendChild(titleRow);

  const summary = document.createElement("div");
  summary.style.cssText = "font-size:12px;line-height:1.6;margin-bottom:12px;";
  summary.innerHTML =
    `<div><span class="muted">requires:</span> ${forged.requires.join(", ") || "—"}</div>`
    + `<div><span class="muted">steps:</span> ${forged.step_count}</div>`
    + `<div><span class="muted">staged at:</span> <code style="font-size:11px;">${forged.staged_path}</code></div>`
    + (forged.audit_seq != null
      ? `<div><span class="muted">audit seq:</span> #${forged.audit_seq}</div>`
      : "");
  wrap.appendChild(summary);

  if (forged.forge_log_excerpt) {
    const logTitle = document.createElement("div");
    logTitle.className = "muted";
    logTitle.style.cssText = "font-size:11px;margin-top:8px;margin-bottom:4px;";
    logTitle.textContent = "forge.log (last 600 chars)";
    wrap.appendChild(logTitle);
    const logPre = document.createElement("pre");
    logPre.style.cssText =
      "font-size:11px;line-height:1.4;background:rgba(0,0,0,0.3);"
      + "padding:8px;border-radius:4px;max-height:140px;overflow:auto;"
      + "white-space:pre-wrap;word-break:break-word;margin:0 0 12px 0;";
    logPre.textContent = forged.forge_log_excerpt;
    wrap.appendChild(logPre);
  }

  const actions = document.createElement("div");
  actions.style.cssText = "display:flex;gap:8px;justify-content:flex-end;";

  const discardBtn = document.createElement("button");
  discardBtn.className = "btn btn--ghost btn--sm";
  discardBtn.textContent = "Discard";
  discardBtn.addEventListener("click", async () => {
    discardBtn.disabled = true;
    try {
      await api.del(`/skills/staged/${forged.name}/${forged.version}`);
      toast(`Discarded ${forged.name}.v${forged.version}`, "info");
      backdrop.remove();
    } catch (e) {
      toast(`Discard failed: ${e.message}`, "danger");
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
      const resp = await writeCall("/skills/install", {
        staged_path: forged.staged_path,
      });
      toast(
        `Installed ${resp.name}.v${resp.version} (audit #${resp.audit_seq})`,
        "success",
      );
      backdrop.remove();
      // Refresh the Skills tab so the new skill shows.
      fetchAndRender();
    } catch (e) {
      toast(`Install failed: ${e.message}`, "danger");
      installBtn.disabled = false;
      installBtn.textContent = "Install";
    }
  });

  actions.appendChild(discardBtn);
  actions.appendChild(installBtn);
  wrap.appendChild(actions);

  return wrap;
}


// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
export function start() {
  const btn = document.getElementById("skills-refresh");
  if (btn) btn.addEventListener("click", fetchAndRender);

  // ADR-0057 B201: New Skill button opens the forge modal.
  const newBtn = document.getElementById("skills-new");
  if (newBtn) newBtn.addEventListener("click", openNewSkillModal);

  // Refresh on tab activation.
  document.querySelectorAll(".tab").forEach((tab) => {
    if (tab.dataset.tab === "skills") {
      tab.addEventListener("click", fetchAndRender);
    }
  });

  // React to agents changes.
  state.subscribe("agents", refreshAgentSelectors);

  // Initial load.
  fetchAndRender();
}
