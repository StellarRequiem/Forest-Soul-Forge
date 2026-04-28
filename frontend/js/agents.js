// Agents tab — list + detail pane + archive form. Also populates the
// #parent-select dropdown on the Forge tab so spawn can target a live agent.

import { api } from "./api.js";
import * as state from "./state.js";
import { archiveAgent } from "./forms.js";
import { toast } from "./toast.js";

function fmtDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toISOString().slice(0, 19).replace("T", " ");
  } catch {
    return iso;
  }
}

function renderList(agents, selectedId) {
  const root = document.getElementById("agents-list");
  root.innerHTML = "";
  if (!agents.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.style.color = "var(--fg-faint)";
    empty.style.textAlign = "center";
    empty.style.padding = "var(--sp-4)";
    empty.textContent = "No agents match the current filters.";
    root.appendChild(empty);
    return;
  }
  for (const a of agents) {
    const card = document.createElement("div");
    card.className = "agent-card";
    if (a.status === "archived") card.classList.add("agent-card--archived");
    if (a.instance_id === selectedId) card.classList.add("agent-card--selected");
    card.dataset.id = a.instance_id;

    const left = document.createElement("div");
    const name = document.createElement("div");
    name.className = "agent-card__name";
    name.textContent = a.agent_name;
    const meta = document.createElement("div");
    meta.className = "agent-card__meta";
    meta.textContent = `${a.dna} · ${fmtDate(a.created_at).slice(0, 10)}`;
    left.appendChild(name);
    left.appendChild(meta);

    const role = document.createElement("div");
    role.className = "agent-card__role";
    role.textContent = a.role;

    card.appendChild(left);
    card.appendChild(role);

    card.addEventListener("click", () => {
      state.set("selectedAgentId", a.instance_id);
      selectAgent(a.instance_id);
    });

    root.appendChild(card);
  }
}

async function selectAgent(id) {
  const detail = document.getElementById("agent-detail");
  detail.innerHTML = '<div class="empty">Loading…</div>';

  try {
    const agent = await api.get(`/agents/${encodeURIComponent(id)}`);
    state.set("agentDetail", agent);
    renderDetail(agent);
  } catch (e) {
    // Demo-friction audit P0 #3: a 404 here usually means the agent list
    // is stale (cache from a different daemon registry state). Auto-trigger
    // a list refresh and tell the user what's going on, instead of leaving
    // a bare "404 unknown agent: ..." that looks like a system error.
    detail.innerHTML = "";
    const wrap = document.createElement("div");
    wrap.className = "empty";
    const isNotFound = /\b404\b|unknown agent/i.test(e.message);
    if (isNotFound) {
      wrap.style.color = "var(--fg-muted)";
      wrap.textContent =
        "This agent isn't in the current registry — the list may be stale. " +
        "Refreshing now…";
      detail.appendChild(wrap);
      // Silently re-pull the list; if the agent really is gone, the card
      // disappears and the empty-detail prompt returns. If it's a transient
      // glitch, the user can click the card again.
      try {
        await refresh();
      } catch {
        /* refresh errors surface via the list panel itself */
      }
    } else {
      wrap.style.color = "var(--danger)";
      wrap.textContent = `Failed to load agent: ${e.message}`;
      detail.appendChild(wrap);
    }
  }
}

function renderDetail(a) {
  const detail = document.getElementById("agent-detail");
  detail.innerHTML = "";

  const h = document.createElement("h3");
  h.textContent = a.agent_name;
  detail.appendChild(h);

  const dl = document.createElement("dl");
  const rows = [
    ["instance", a.instance_id],
    ["dna (short)", a.dna],
    ["dna (full)", a.dna_full],
    ["role", a.role],
    ["parent", a.parent_instance || "—"],
    ["sibling #", String(a.sibling_index || 1)],
    ["owner", a.owner_id || "—"],
    ["model", a.model_name ? `${a.model_name} ${a.model_version || ""}`.trim() : "—"],
    ["soul", a.soul_path],
    ["constitution", a.constitution_path],
    ["constitution hash", a.constitution_hash],
    ["created", fmtDate(a.created_at)],
    ["status", a.status],
  ];
  for (const [k, v] of rows) {
    const dt = document.createElement("dt");
    dt.textContent = k;
    const dd = document.createElement("dd");
    dd.textContent = v;
    dl.appendChild(dt);
    dl.appendChild(dd);
  }
  detail.appendChild(dl);

  // Archive form (active agents only).
  if (a.status === "active") {
    const form = document.createElement("div");
    form.className = "archive-form";

    const reason = document.createElement("input");
    reason.type = "text";
    reason.className = "inp";
    reason.placeholder = "reason (required)";
    form.appendChild(reason);

    const btn = document.createElement("button");
    btn.className = "btn btn--danger";
    btn.type = "button";
    btn.textContent = "Archive";
    btn.addEventListener("click", async () => {
      const r = reason.value.trim();
      if (!r) {
        toast({ title: "Can't archive", msg: "reason is required", kind: "error" });
        return;
      }
      if (!confirm(`Archive ${a.agent_name}? This flips status and logs an agent_archived event.`)) return;
      btn.disabled = true;
      btn.textContent = "archiving…";
      try {
        await archiveAgent({
          instanceId: a.instance_id,
          reason: r,
          archivedBy: null,
        });
        await selectAgent(a.instance_id);
      } catch {
        btn.disabled = false;
        btn.textContent = "Archive";
      }
    });
    form.appendChild(btn);
    detail.appendChild(form);
  } else {
    const already = document.createElement("div");
    already.style.marginTop = "var(--sp-3)";
    already.style.color = "var(--warn)";
    already.style.fontSize = "12px";
    already.textContent = "This agent is archived. Re-archiving is a no-op.";
    detail.appendChild(already);
  }
}

function populateParentSelect(agents) {
  const sel = document.getElementById("parent-select");
  const current = sel.value;
  // Preserve the "— none —" option.
  sel.innerHTML = '<option value="">— none (birth a root agent) —</option>';
  // Only active agents make sense as parents.
  const active = agents.filter((a) => a.status === "active");
  for (const a of active) {
    const opt = document.createElement("option");
    opt.value = a.instance_id;
    opt.textContent = `${a.agent_name} · ${a.role} · ${a.dna}`;
    sel.appendChild(opt);
  }
  // Try to preserve the prior selection.
  if (current && active.some((a) => a.instance_id === current)) {
    sel.value = current;
  }
  // Nudge form.js's enable-state logic.
  sel.dispatchEvent(new Event("change"));
}

export async function refresh() {
  const role = document.getElementById("agents-role-filter").value;
  const status = document.getElementById("agents-status-filter").value;
  const qs = new URLSearchParams();
  if (role) qs.set("role", role);
  if (status) qs.set("status", status);
  const url = "/agents" + (qs.toString() ? "?" + qs.toString() : "");
  try {
    const res = await api.get(url);
    state.set("agents", res.agents);
    const selected = state.get("selectedAgentId");
    renderList(res.agents, selected);
    populateParentSelect(res.agents);
    // If the currently selected agent is no longer in the list, clear detail.
    if (selected && !res.agents.find((a) => a.instance_id === selected)) {
      state.set("selectedAgentId", null);
      document.getElementById("agent-detail").innerHTML =
        '<div class="empty">Select an agent to see its soul, lineage, and archive controls.</div>';
    }
  } catch (e) {
    toast({
      title: "Failed to load agents",
      msg: e.message,
      kind: "error",
    });
  }
}

export function start() {
  document.getElementById("agents-refresh").addEventListener("click", refresh);
  document.getElementById("agents-role-filter").addEventListener("change", refresh);
  document.getElementById("agents-status-filter").addEventListener("change", refresh);
  // Demo-friction audit P0 #3: refresh on tab activation so a returning
  // visitor doesn't see a stale list. The other panels do this; agents
  // got missed in the original wiring.
  document.querySelectorAll(".tab").forEach((tab) => {
    if (tab.dataset.tab === "agents") {
      tab.addEventListener("click", refresh);
    }
  });
  refresh();
}
