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
      empty.textContent =
        "No skills installed. Forge one with `fsf forge skill \"...\"` "
        + "and copy the manifest to data/forge/skills/installed/.";
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
// Boot
// ---------------------------------------------------------------------------
export function start() {
  const btn = document.getElementById("skills-refresh");
  if (btn) btn.addEventListener("click", fetchAndRender);

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
