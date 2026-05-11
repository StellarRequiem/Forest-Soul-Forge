// Catalog grants panel — ADR-0060 T6 (Burst 223).
//
// Per-agent runtime tool grants. The operator picks an agent, sees
// its active catalog grants, can revoke any of them, or grant a
// new tool by typing name + version + tier.
//
// Lives on the Agents tab as a sibling of the agents-split. Mirrors
// the "Forged proposals" sibling-panel pattern on the Approvals tab.
//
// Endpoints (ADR-0060 T3 / Burst 220):
//   GET    /agents/{instance_id}/tools/grants?history=false
//   POST   /agents/{instance_id}/tools/grant
//   DELETE /agents/{instance_id}/tools/grant/{name}/{version}

import { api, ApiError, writeCall } from "./api.js";
import { toast } from "./toast.js";


function _renderEmpty(root, msg) {
  root.innerHTML = "";
  const el = document.createElement("div");
  el.className = "empty";
  el.textContent = msg;
  root.appendChild(el);
}


function _renderGrantRow(instanceId, g, onChange) {
  const row = document.createElement("div");
  row.className = "catalog-grant-row";
  row.style.cssText =
    "display:flex;align-items:flex-start;justify-content:space-between;"
    + "gap:12px;padding:10px 12px;"
    + "border:1px solid var(--border,#2c303a);"
    + "border-radius:6px;margin-bottom:8px;";

  const left = document.createElement("div");
  left.style.cssText = "flex:1;min-width:0;";

  const titleRow = document.createElement("div");
  titleRow.style.cssText = "display:flex;align-items:center;gap:8px;margin-bottom:4px;";
  const tier = document.createElement("span");
  tier.className = "pill pill--" + (
    g.trust_tier === "green" ? "success"
      : g.trust_tier === "red" ? "danger"
      : "warning"
  );
  tier.textContent = g.trust_tier;
  tier.style.cssText = "font-size:10px;text-transform:uppercase;letter-spacing:0.05em;";
  const name = document.createElement("strong");
  name.textContent = g.tool_key;
  titleRow.appendChild(tier);
  titleRow.appendChild(name);
  left.appendChild(titleRow);

  const meta = document.createElement("div");
  meta.className = "muted";
  meta.style.cssText = "font-size:11px;font-family:var(--mono,monospace);word-break:break-all;";
  const reasonPart = g.reason ? ` · ${g.reason}` : "";
  meta.textContent =
    `granted: ${g.granted_at} (seq ${g.granted_at_seq})`
    + ` · by ${g.granted_by || "—"}` + reasonPart;
  left.appendChild(meta);

  const actions = document.createElement("div");
  actions.style.cssText = "display:flex;flex-direction:column;gap:4px;";
  const revokeBtn = document.createElement("button");
  revokeBtn.className = "btn btn--ghost btn--sm";
  revokeBtn.textContent = "Revoke";
  revokeBtn.addEventListener("click", async () => {
    revokeBtn.disabled = true;
    revokeBtn.textContent = "Revoking…";
    try {
      await api.del(
        `/agents/${instanceId}/tools/grant/${g.tool_name}/${g.tool_version}`,
      );
      toast({
        title: `Revoked ${g.tool_key}`,
        msg: `agent ${instanceId}`,
        kind: "info",
      });
      onChange();
    } catch (e) {
      toast({title: "Revoke failed", msg: e.message, kind: "error"});
      revokeBtn.disabled = false;
      revokeBtn.textContent = "Revoke";
    }
  });
  actions.appendChild(revokeBtn);

  row.appendChild(left);
  row.appendChild(actions);
  return row;
}


async function _fetchAgents() {
  // Populate the agent selector. Calls /agents once at start();
  // re-poll on demand via the refresh button.
  try {
    const resp = await api.get("/agents");
    return resp.agents || [];
  } catch (e) {
    return [];
  }
}


async function _fetchAndRender() {
  const select = document.getElementById("catalog-grants-agent-select");
  const root = document.getElementById("catalog-grants-list");
  const status = document.getElementById("catalog-grants-status");
  if (!root || !select) return;

  const instanceId = select.value;
  if (!instanceId) {
    _renderEmpty(root, "Pick an agent above to see its runtime tool grants.");
    if (status) status.textContent = "—";
    return;
  }

  try {
    const data = await api.get(`/agents/${instanceId}/tools/grants`);
    if (status) {
      status.textContent =
        data.count === 0
          ? "no active grants"
          : `${data.count} active grant${data.count === 1 ? "" : "s"}`;
    }
    root.innerHTML = "";
    if (data.count === 0) {
      _renderEmpty(
        root,
        "No active runtime grants for this agent. Use the form below to grant a catalog tool.",
      );
      return;
    }
    for (const g of data.grants) {
      root.appendChild(_renderGrantRow(instanceId, g, _fetchAndRender));
    }
  } catch (e) {
    let msg = e.message;
    if (e instanceof ApiError && e.status === 404) {
      msg = `Agent ${instanceId} not found in registry.`;
    }
    _renderEmpty(root, "Failed to load grants: " + msg);
  }
}


async function _onGrant() {
  const select = document.getElementById("catalog-grants-agent-select");
  const nameInput = document.getElementById("catalog-grants-tool-name");
  const versionInput = document.getElementById("catalog-grants-tool-version");
  const tierSelect = document.getElementById("catalog-grants-tier");
  const reasonInput = document.getElementById("catalog-grants-reason");
  const btn = document.getElementById("catalog-grants-grant-btn");
  if (!select || !nameInput || !versionInput || !tierSelect || !btn) return;

  const instanceId = select.value;
  if (!instanceId) {
    toast({title: "Pick an agent first", kind: "warning"});
    return;
  }
  const toolName = nameInput.value.trim();
  const toolVersion = versionInput.value.trim() || "1";
  if (!toolName) {
    toast({title: "Tool name required", kind: "warning"});
    return;
  }

  btn.disabled = true;
  btn.textContent = "Granting…";
  try {
    const resp = await writeCall(
      `/agents/${instanceId}/tools/grant`,
      {
        tool_name: toolName,
        tool_version: toolVersion,
        trust_tier: tierSelect.value,
        reason: (reasonInput && reasonInput.value.trim()) || null,
      },
    );
    toast({
      title: `Granted ${resp.grant.tool_key} to ${instanceId}`,
      msg: `tier=${resp.grant.trust_tier} seq=${resp.grant.granted_at_seq}`,
      kind: "success",
    });
    nameInput.value = "";
    versionInput.value = "";
    if (reasonInput) reasonInput.value = "";
    _fetchAndRender();
  } catch (e) {
    // ADR-0060 D5: 400 when the tool isn't in the live catalog
    // (hallucinated grant or stale frontend). Surface the daemon's
    // detail message which already explains the situation.
    let msg = e.message;
    if (e instanceof ApiError && e.body && typeof e.body.detail === "string") {
      msg = e.body.detail;
    }
    toast({title: "Grant failed", msg, kind: "error", ttl: 8000});
  } finally {
    btn.disabled = false;
    btn.textContent = "Grant";
  }
}


export async function start() {
  const select = document.getElementById("catalog-grants-agent-select");
  const refresh = document.getElementById("catalog-grants-refresh");
  const grantBtn = document.getElementById("catalog-grants-grant-btn");
  if (!select || !refresh || !grantBtn) return;

  // Initial agent population.
  const agents = await _fetchAgents();
  // Stable sort by role then instance_id for deterministic order.
  agents.sort((a, b) => {
    const r = (a.role || "").localeCompare(b.role || "");
    return r !== 0 ? r : (a.instance_id || "").localeCompare(b.instance_id || "");
  });
  for (const a of agents) {
    const opt = document.createElement("option");
    opt.value = a.instance_id;
    opt.textContent = `${a.role || "?"} · ${a.instance_id}`;
    select.appendChild(opt);
  }

  select.addEventListener("change", _fetchAndRender);
  refresh.addEventListener("click", _fetchAndRender);
  grantBtn.addEventListener("click", _onGrant);

  // Auto-refresh on tab activation so newly-installed tools / forged
  // tools show up after they land via the forge UI.
  document.querySelectorAll(".tab").forEach((tab) => {
    if (tab.dataset.tab === "agents") {
      tab.addEventListener("click", _fetchAndRender);
    }
  });

  _fetchAndRender();
}
