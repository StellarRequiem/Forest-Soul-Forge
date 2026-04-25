// Birth / spawn / archive submit handlers. Wired to #btn-birth / #btn-spawn
// and to the per-agent archive form rendered by agents.js.

import { writeCall, ApiError } from "./api.js";
import * as state from "./state.js";
import { toast } from "./toast.js";
import { promptForToken } from "./health.js";
import { refresh as refreshAgents } from "./agents.js";
import { refresh as refreshAudit } from "./audit.js";
import { refresh as refreshPreview } from "./preview.js";

function currentProfilePayload() {
  const profile = state.get("profile") || { trait_values: {}, domain_weight_overrides: {} };
  const role = state.get("selectedRole");
  return {
    role,
    trait_values: profile.trait_values || {},
    domain_weight_overrides: profile.domain_weight_overrides || {},
  };
}

function identityPayload() {
  // Per ADR-0017: enrich_narrative true → daemon invokes the active
  // provider to write the soul.md ## Voice section; false → templated
  // body only. Frontend checkbox defaults to checked so the typical
  // birth gets a real narrative voice; uncheck for deterministic /
  // reproducible artifact runs (tests, byte-stable regression checks).
  const enrichEl = document.getElementById("enrich-narrative");
  return {
    agent_name: document.getElementById("agent-name").value.trim(),
    agent_version: document.getElementById("agent-version").value.trim() || "v1",
    owner_id: document.getElementById("owner-id").value.trim() || null,
    constitution_override:
      document.getElementById("constitution-override").value.trim() || null,
    enrich_narrative: enrichEl ? enrichEl.checked : null,
  };
}

function validateName(name) {
  if (!name) return "agent name is required";
  if (name.length > 80) return "agent name must be ≤ 80 chars";
  return null;
}

async function handleBirth() {
  const ident = identityPayload();
  const nameErr = validateName(ident.agent_name);
  if (nameErr) {
    toast({ title: "Can't birth", msg: nameErr, kind: "error" });
    return;
  }
  const body = { profile: currentProfilePayload(), ...ident };

  setButtonsDisabled(true, "birthing…");
  try {
    const agent = await writeCall("/birth", body, {
      onAuthRequired: async () => promptForToken(),
    });
    toast({
      title: "Birthed",
      msg: `${agent.agent_name} · ${agent.dna}${agent.sibling_index > 1 ? ` (sibling ${agent.sibling_index})` : ""}`,
    });
    refreshAgents();
    refreshAudit();
    refreshPreview();
  } catch (e) {
    showError(e, "birth");
  } finally {
    setButtonsDisabled(false);
  }
}

async function handleSpawn() {
  const ident = identityPayload();
  const nameErr = validateName(ident.agent_name);
  if (nameErr) {
    toast({ title: "Can't spawn", msg: nameErr, kind: "error" });
    return;
  }
  const parentId = document.getElementById("parent-select").value;
  if (!parentId) {
    toast({ title: "Can't spawn", msg: "select a parent agent first", kind: "error" });
    return;
  }
  const body = {
    profile: currentProfilePayload(),
    ...ident,
    parent_instance_id: parentId,
  };

  setButtonsDisabled(true, "spawning…");
  try {
    const agent = await writeCall("/spawn", body, {
      onAuthRequired: async () => promptForToken(),
    });
    toast({
      title: "Spawned",
      msg: `${agent.agent_name} · ${agent.dna} (parent ${parentId.slice(0, 8)}…)`,
    });
    refreshAgents();
    refreshAudit();
    refreshPreview();
  } catch (e) {
    showError(e, "spawn");
  } finally {
    setButtonsDisabled(false);
  }
}

/** Exported so agents.js can call it from the per-agent archive form. */
export async function archiveAgent({ instanceId, reason, archivedBy }) {
  const body = {
    instance_id: instanceId,
    reason,
    archived_by: archivedBy || null,
  };
  try {
    const agent = await writeCall("/archive", body, {
      onAuthRequired: async () => promptForToken(),
    });
    toast({ title: "Archived", msg: `${agent.agent_name} · ${agent.dna}` });
    refreshAgents();
    refreshAudit();
    return agent;
  } catch (e) {
    showError(e, "archive");
    throw e;
  }
}

function showError(e, what) {
  if (!(e instanceof ApiError)) {
    toast({ title: `${what} failed`, msg: String(e), kind: "error" });
    return;
  }
  // Special-case 409 from idempotency: same key, different body.
  if (e.status === 409) {
    toast({
      title: `${what} conflict (409)`,
      msg: "idempotency key already used with a different request body",
      kind: "warn",
    });
    return;
  }
  const detail = typeof e.detail?.detail === "string" ? e.detail.detail : e.message;
  toast({
    title: `${what} failed (${e.status})`,
    msg: detail,
    kind: "error",
  });
}

function setButtonsDisabled(disabled, birthLabel) {
  const birth = document.getElementById("btn-birth");
  const spawn = document.getElementById("btn-spawn");
  birth.disabled = disabled;
  spawn.disabled = disabled;
  if (disabled && birthLabel) {
    birth.dataset.prev = birth.textContent;
    spawn.dataset.prev = spawn.textContent;
    birth.textContent = birthLabel;
    spawn.textContent = birthLabel.replace("birthing", "spawning");
  } else {
    if (birth.dataset.prev) {
      birth.textContent = birth.dataset.prev;
      delete birth.dataset.prev;
    } else {
      birth.textContent = "Birth";
    }
    if (spawn.dataset.prev) {
      spawn.textContent = spawn.dataset.prev;
      delete spawn.dataset.prev;
    } else {
      spawn.textContent = "Spawn";
    }
  }
}

function refreshEnableState() {
  const writesEnabled = state.get("writesEnabled");
  const hasTree = !!state.get("traitTree");
  const enabled = writesEnabled !== false && hasTree;
  document.getElementById("btn-birth").disabled = !enabled;
  // Spawn also requires a selected parent; the button is enabled once a
  // parent is chosen. We leave the parent-select-change handler below to
  // manage that.
  document.getElementById("btn-spawn").disabled =
    !enabled || !document.getElementById("parent-select").value;
}

export function start() {
  document.getElementById("btn-birth").addEventListener("click", handleBirth);
  document.getElementById("btn-spawn").addEventListener("click", handleSpawn);

  // Enable spawn button only when a parent is selected.
  document.getElementById("parent-select").addEventListener("change", refreshEnableState);

  state.subscribe("writesEnabled", refreshEnableState);
  state.subscribe("traitTree", refreshEnableState);
  refreshEnableState();
}
