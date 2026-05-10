// Forged proposals subsection — ADR-0057 / ADR-0058 / B205.
//
// Renders staged forge propose results (skills + prompt-template tools)
// in the Approvals tab so operators can review + Install + Discard
// without having to find the right modal. Skill modals live on the
// Skills tab; tool modals on the Tools tab. Without this subsection,
// staged proposals from a previous session (or from Smith / agent-
// driven cycles) had no UI surface — they sat on disk and the only
// way to get rid of them was to know the staged_path and rm by hand.
//
// Polls two endpoints:
//   GET /skills/staged                — list pending skill proposals
//   GET /tools/staged/forged          — list pending tool proposals
//
// Per-row actions:
//   Install  → POST /skills/install   (or /tools/install)
//   Discard  → DELETE /skills/staged/{name}/{version} (or /tools/staged/forged/...)
//
// Different governance shape from per-call tool-call approvals
// (per-artifact admission vs per-call dispatch) so kept in its own
// section per Alex's directive 2026-05-09.

import { api, ApiError, writeCall } from "./api.js";
import { toast } from "./toast.js";


function _renderRow(kind, item) {
  // kind: 'skill' | 'tool'
  // item: from /skills/staged or /tools/staged/forged
  const row = document.createElement("div");
  row.className = "forged-proposal-row";
  row.style.cssText =
    "display:flex;align-items:flex-start;justify-content:space-between;"
    + "gap:12px;padding:10px 12px;"
    + "border:1px solid var(--border,#2c303a);"
    + "border-radius:6px;margin-bottom:8px;";

  const left = document.createElement("div");
  left.style.cssText = "flex:1;min-width:0;";

  const titleRow = document.createElement("div");
  titleRow.style.cssText = "display:flex;align-items:center;gap:8px;margin-bottom:4px;";
  const kindBadge = document.createElement("span");
  kindBadge.className = "pill " + (kind === "skill" ? "pill--info" : "pill--success");
  kindBadge.textContent = kind;
  kindBadge.style.cssText = "font-size:10px;text-transform:uppercase;letter-spacing:0.05em;";
  const name = document.createElement("strong");
  name.textContent = `${item.name}.v${item.version}`;
  const hashKey = kind === "skill" ? "skill_hash" : "spec_hash";
  const hash = document.createElement("span");
  hash.className = "muted";
  hash.textContent = (item[hashKey] || "").slice(0, 14);
  hash.style.cssText = "font-size:10px;font-family:var(--mono,monospace);";
  titleRow.appendChild(kindBadge);
  titleRow.appendChild(name);
  titleRow.appendChild(hash);
  left.appendChild(titleRow);

  const desc = document.createElement("div");
  desc.className = "muted";
  desc.style.cssText = "font-size:12px;line-height:1.4;margin-bottom:4px;";
  desc.textContent = item.description_preview || "(no description)";
  left.appendChild(desc);

  const meta = document.createElement("div");
  meta.className = "muted";
  meta.style.cssText = "font-size:11px;font-family:var(--mono,monospace);word-break:break-all;";
  if (kind === "skill") {
    const reqs = (item.requires || []).join(", ") || "—";
    meta.textContent = `requires: ${reqs} · steps: ${item.step_count} · staged: ${item.forged_at || "?"}`;
  } else {
    meta.textContent = `staged: ${item.forged_at || "?"}`;
  }
  left.appendChild(meta);

  const actions = document.createElement("div");
  actions.style.cssText = "display:flex;flex-direction:column;gap:4px;";

  const installBtn = document.createElement("button");
  installBtn.className = "btn btn--primary btn--sm";
  installBtn.textContent = "Install";
  installBtn.addEventListener("click", () => _onInstall(kind, item, installBtn));

  const discardBtn = document.createElement("button");
  discardBtn.className = "btn btn--ghost btn--sm";
  discardBtn.textContent = "Discard";
  discardBtn.addEventListener("click", () => _onDiscard(kind, item, discardBtn));

  actions.appendChild(installBtn);
  actions.appendChild(discardBtn);

  row.appendChild(left);
  row.appendChild(actions);
  return row;
}


async function _onInstall(kind, item, btn) {
  btn.disabled = true;
  btn.textContent = "Installing…";
  const path = kind === "skill" ? "/skills/install" : "/tools/install";
  try {
    const resp = await writeCall(path, {staged_path: item.staged_path});
    toast({
      title: `Installed ${kind} ${resp.name}.v${resp.version}`,
      msg: `audit #${resp.audit_seq}`,
      kind: "success",
    });
    fetchAndRender();
  } catch (e) {
    // B204 catalog-aware install may return 422 with structured
    // unknown_tools_referenced detail. Surface meaningfully so the
    // operator knows why and can choose the force-install path
    // (currently only via direct API; future burst could add a
    // "Install anyway" button here).
    let msg = e.message;
    if (e instanceof ApiError && e.body && e.body.detail) {
      const d = e.body.detail;
      if (d.error === "unknown_tools_referenced") {
        msg = `unknown tools: ${(d.unknown_tools || []).join(", ")}. ${d.hint || ""}`;
      }
    }
    toast({title: "Install failed", msg, kind: "error", ttl: 8000});
    btn.disabled = false;
    btn.textContent = "Install";
  }
}


async function _onDiscard(kind, item, btn) {
  btn.disabled = true;
  btn.textContent = "Discarding…";
  const path = kind === "skill"
    ? `/skills/staged/${item.name}/${item.version}`
    : `/tools/staged/forged/${item.name}/${item.version}`;
  try {
    await api.del(path);
    toast({title: `Discarded ${kind} ${item.name}.v${item.version}`, kind: "info"});
    fetchAndRender();
  } catch (e) {
    toast({title: "Discard failed", msg: e.message, kind: "error"});
    btn.disabled = false;
    btn.textContent = "Discard";
  }
}


export async function fetchAndRender() {
  const root = document.getElementById("forged-proposals-list");
  const status = document.getElementById("forged-proposals-status");
  if (!root) return;

  let skills = {count: 0, staged: []};
  let tools = {count: 0, staged: []};
  let errors = [];
  try {
    skills = await api.get("/skills/staged");
  } catch (e) {
    errors.push(`skills: ${e.message}`);
  }
  try {
    tools = await api.get("/tools/staged/forged");
  } catch (e) {
    errors.push(`tools: ${e.message}`);
  }
  if (status) {
    const total = (skills.count || 0) + (tools.count || 0);
    status.textContent = `${total} pending (${skills.count || 0} skill, ${tools.count || 0} tool)`;
  }
  root.innerHTML = "";
  if (errors.length) {
    const err = document.createElement("div");
    err.className = "empty";
    err.style.color = "var(--danger,#ff6b6b)";
    err.textContent = "Failed to load: " + errors.join("; ");
    root.appendChild(err);
    return;
  }
  const total = (skills.count || 0) + (tools.count || 0);
  if (total === 0) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.innerHTML =
      "No staged proposals. Forge a skill via the <strong>Skills</strong> tab "
      + "or a tool via the <strong>Tools</strong> tab to see them appear here "
      + "for review before install.";
    root.appendChild(empty);
    return;
  }
  for (const s of (skills.staged || [])) {
    root.appendChild(_renderRow("skill", s));
  }
  for (const t of (tools.staged || [])) {
    root.appendChild(_renderRow("tool", t));
  }
}


export function start() {
  const btn = document.getElementById("forged-proposals-refresh");
  if (btn) btn.addEventListener("click", fetchAndRender);

  // Refresh on Approvals tab activation.
  document.querySelectorAll(".tab").forEach((tab) => {
    if (tab.dataset.tab === "pending") {
      tab.addEventListener("click", fetchAndRender);
    }
  });

  // Initial load.
  fetchAndRender();
}
