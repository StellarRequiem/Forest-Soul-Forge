// Debounced POST /preview on profile change. Renders DNA, grade, warnings,
// flagged combinations, and dispatches the per-domain scores to radar.js.

import { api, ApiError } from "./api.js";
import * as state from "./state.js";
import { drawRadar } from "./radar.js";

const DEBOUNCE_MS = 180;

let timer = null;
let inFlight = null;
// Epoch counter so we can drop stale responses (slider nudged again before
// the previous /preview came back).
let seq = 0;

function setStatus(text) {
  const el = document.getElementById("preview-status");
  if (el) el.textContent = text;
}

function renderPreview(res) {
  document.getElementById("pv-dna").textContent = res.dna;
  document.getElementById("pv-role").textContent = res.role;
  document.getElementById("pv-overall").textContent =
    `${res.grade.overall_score.toFixed(1)} / 100`;
  document.getElementById("pv-dominant").textContent = res.grade.dominant_domain;
  document.getElementById("pv-chash").textContent = res.constitution_hash_effective;

  // Warnings block.
  const warnEl = document.getElementById("pv-warnings");
  if (res.grade.warnings?.length) {
    warnEl.hidden = false;
    warnEl.innerHTML = "";
    const title = document.createElement("div");
    title.className = "warnings__title";
    title.textContent = `warnings (${res.grade.warnings.length})`;
    warnEl.appendChild(title);
    const ul = document.createElement("ul");
    for (const w of res.grade.warnings) {
      const li = document.createElement("li");
      li.textContent = w;
      ul.appendChild(li);
    }
    warnEl.appendChild(ul);
  } else {
    warnEl.hidden = true;
    warnEl.innerHTML = "";
  }

  // Flagged combinations block.
  const flagEl = document.getElementById("pv-flagged");
  if (res.flagged_combinations?.length) {
    flagEl.hidden = false;
    flagEl.innerHTML = "";
    const title = document.createElement("div");
    title.className = "flagged__title";
    title.textContent = `flagged combinations (${res.flagged_combinations.length})`;
    flagEl.appendChild(title);
    const ul = document.createElement("ul");
    for (const fc of res.flagged_combinations) {
      const li = document.createElement("li");
      const conds = Object.entries(fc.conditions)
        .map(([t, op]) => `${t} ${op}`)
        .join(", ");
      li.innerHTML = `<strong>${fc.name}</strong> — ${fc.warning} <span class="muted">(${conds})</span>`;
      ul.appendChild(li);
    }
    flagEl.appendChild(ul);
  } else {
    flagEl.hidden = true;
    flagEl.innerHTML = "";
  }

  // Radar from per-domain weighted scores.
  drawRadar(res.grade.per_domain);
}

function clearPreview() {
  document.getElementById("pv-dna").textContent = "—";
  document.getElementById("pv-role").textContent = "—";
  document.getElementById("pv-overall").textContent = "—";
  document.getElementById("pv-dominant").textContent = "—";
  document.getElementById("pv-chash").textContent = "—";
  document.getElementById("pv-warnings").hidden = true;
  document.getElementById("pv-flagged").hidden = true;
  drawRadar([]);
}

async function runPreview() {
  const profile = state.get("profile");
  const role = state.get("selectedRole");
  if (!profile || !role) return;

  const override =
    document.getElementById("constitution-override")?.value?.trim() || null;

  const payload = {
    profile: {
      role,
      trait_values: profile.trait_values || {},
      domain_weight_overrides: profile.domain_weight_overrides || {},
    },
    constitution_override: override || null,
  };

  const mySeq = ++seq;
  setStatus("previewing…");
  inFlight = api.post("/preview", payload);
  try {
    const res = await inFlight;
    if (mySeq !== seq) return; // stale — a newer request is pending
    state.set("preview", res);
    state.set("previewError", null);
    renderPreview(res);
    setStatus("idle");
  } catch (e) {
    if (mySeq !== seq) return;
    const msg =
      e instanceof ApiError
        ? `${e.status} ${e.detail?.detail || e.message}`
        : String(e);
    state.set("previewError", msg);
    setStatus(`error: ${msg}`);
  } finally {
    inFlight = null;
  }
}

function schedule() {
  if (timer) clearTimeout(timer);
  timer = setTimeout(runPreview, DEBOUNCE_MS);
}

export function start() {
  // Trigger on profile changes (slider drag) and role changes.
  state.subscribe("profile", schedule);
  state.subscribe("selectedRole", schedule);

  // Override textarea is not in state; wire it directly.
  const override = document.getElementById("constitution-override");
  if (override) override.addEventListener("input", schedule);

  // Initial render with blank values until first preview lands.
  clearPreview();
}

/** Kick a fresh preview right now (used after birth / spawn to refresh). */
export function refresh() {
  schedule();
}
