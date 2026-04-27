// Memory tab — ADR-0022 v0.2 + ADR-0033 T17.
//
// Three things share one panel:
//   1. Entries visible to the selected agent, scoped by mode
//      (private | lineage | consented). Disclosed-copy rows are
//      called out so the operator can tell originals from references.
//   2. Consent grants the selected agent has ISSUED on its own
//      entries (read from GET /agents/{id}/memory/consents).
//   3. A small grant form: pick a consented-scope entry + recipient
//      agent, click "grant". Revoke is per-row in the grants list.
//
// The agent picker is shared with the rest of the app via state.agents.
// Mode + agent selection drives the recall query; the disclose tool
// itself isn't in this UI yet — granting consent is the operator-side
// action; the actual disclosure happens via the swarm's skill chains.

import { api, ApiError, writeCall } from "./api.js";
import * as state from "./state.js";
import { toast } from "./toast.js";


// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let _selectedAgent = "";
let _selectedMode = "private";
let _entriesCache = [];   // raw entries from last recall, used to populate
                           // the grant form's entry picker (only consented
                           // entries are eligible).


// ---------------------------------------------------------------------------
// Rendering — entries
// ---------------------------------------------------------------------------
function renderEntry(e) {
  const row = document.createElement("div");
  row.className = "memory-row";
  if (e.is_disclosed_copy) row.classList.add("memory-row--disclosed");

  // Top line — entry id + scope pill + (disclosed marker if applicable).
  const top = document.createElement("div");
  top.className = "memory-row__top";
  const idEl = document.createElement("code");
  idEl.className = "memory-row__id";
  idEl.textContent = e.entry_id.slice(0, 8) + "…";
  idEl.title = e.entry_id;
  top.appendChild(idEl);

  const scopePill = document.createElement("span");
  scopePill.className = `pill pill--scope-${e.scope}`;
  scopePill.textContent = e.scope;
  top.appendChild(scopePill);

  const layerPill = document.createElement("span");
  layerPill.className = "pill pill--ghost";
  layerPill.textContent = e.layer;
  top.appendChild(layerPill);

  if (e.instance_id !== _selectedAgent) {
    const ownerPill = document.createElement("span");
    ownerPill.className = "pill pill--source-plugin";  // reuse styling
    ownerPill.textContent = `from ${e.instance_id.slice(0, 8)}…`;
    ownerPill.title = `Cross-agent row owned by ${e.instance_id}`;
    top.appendChild(ownerPill);
  }

  if (e.is_disclosed_copy) {
    const discPill = document.createElement("span");
    discPill.className = "pill pill--warn";
    discPill.textContent = "disclosed copy";
    discPill.title =
      "This is a reference copy on this agent's store, not an " +
      "original observation. The full content stays on the source " +
      "agent's store; only the summary crossed.";
    top.appendChild(discPill);
  }

  row.appendChild(top);

  // Body — content (or summary for disclosed copies).
  const body = document.createElement("div");
  body.className = "memory-row__body";
  body.textContent = e.is_disclosed_copy
    ? `summary: ${e.disclosed_summary || e.content || ""}`
    : (e.content || "—");
  row.appendChild(body);

  // If disclosed, show the back-reference id.
  if (e.is_disclosed_copy && e.disclosed_from_entry) {
    const ref = document.createElement("div");
    ref.className = "memory-row__ref tiny muted";
    ref.textContent = `disclosed_from_entry = ${e.disclosed_from_entry.slice(0, 12)}…`;
    ref.title = e.disclosed_from_entry;
    row.appendChild(ref);
  }

  // Footer — created_at.
  const foot = document.createElement("div");
  foot.className = "memory-row__foot tiny muted";
  foot.textContent = e.created_at;
  row.appendChild(foot);

  return row;
}


function renderEntriesList(entries) {
  const root = document.getElementById("memory-entries");
  if (!root) return;
  root.innerHTML = "";
  if (!entries.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = `No entries visible in mode='${_selectedMode}'.`;
    root.appendChild(empty);
    return;
  }
  for (const e of entries) root.appendChild(renderEntry(e));
}


// ---------------------------------------------------------------------------
// Rendering — consent grants
// ---------------------------------------------------------------------------
function renderConsentRow(c) {
  const row = document.createElement("div");
  row.className = "memory-consent-row";

  // Entry id + recipient + status pills.
  const head = document.createElement("div");
  head.className = "memory-consent-row__head";
  const id = document.createElement("code");
  id.className = "memory-consent-row__id";
  id.textContent = c.entry_id.slice(0, 8) + "…";
  id.title = c.entry_id;
  head.appendChild(id);

  const arrow = document.createElement("span");
  arrow.className = "tiny muted";
  arrow.textContent = "→";
  head.appendChild(arrow);

  const recipient = document.createElement("code");
  recipient.className = "memory-consent-row__recipient";
  recipient.textContent = c.recipient_instance.slice(0, 8) + "…";
  recipient.title = c.recipient_instance;
  head.appendChild(recipient);

  const statusPill = document.createElement("span");
  if (c.revoked_at) {
    statusPill.className = "pill pill--ghost";
    statusPill.textContent = "revoked";
    statusPill.title = `revoked at ${c.revoked_at}`;
  } else {
    statusPill.className = "pill pill--scope-consented";
    statusPill.textContent = "active";
  }
  head.appendChild(statusPill);

  row.appendChild(head);

  // Granted at + by, on a quieter line.
  const meta = document.createElement("div");
  meta.className = "memory-consent-row__meta tiny muted";
  meta.textContent = `granted ${c.granted_at} by ${c.granted_by}`;
  row.appendChild(meta);

  // Revoke button — only for active grants.
  if (!c.revoked_at) {
    const actions = document.createElement("div");
    actions.className = "memory-consent-row__actions";
    const revokeBtn = document.createElement("button");
    revokeBtn.className = "btn btn--ghost btn--sm";
    revokeBtn.type = "button";
    revokeBtn.textContent = "revoke";
    revokeBtn.addEventListener("click", () =>
      onRevoke(c.entry_id, c.recipient_instance, revokeBtn),
    );
    actions.appendChild(revokeBtn);
    row.appendChild(actions);
  }

  return row;
}


function renderConsentsList(consents) {
  const root = document.getElementById("memory-consents");
  if (!root) return;
  root.innerHTML = "";
  if (!consents.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No consent grants issued.";
    root.appendChild(empty);
    return;
  }
  for (const c of consents) root.appendChild(renderConsentRow(c));
}


// ---------------------------------------------------------------------------
// Form population
// ---------------------------------------------------------------------------
function populateGrantPickers() {
  // Entry picker — only consented-scope entries owned by the selected
  // agent are eligible. (Per ADR-0027 §1, only consented entries are
  // disclosable across agents; granting consent on a private/lineage
  // entry is meaningless because the disclose tool will refuse them.)
  const entrySel = document.getElementById("memory-grant-entry");
  const recipientSel = document.getElementById("memory-grant-recipient");
  if (!entrySel || !recipientSel) return;

  const eligible = _entriesCache.filter(
    (e) => e.scope === "consented" &&
           e.instance_id === _selectedAgent &&
           !e.is_disclosed_copy,
  );

  entrySel.innerHTML = "";
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = eligible.length ? "—" : "(no consented entries to grant)";
  entrySel.appendChild(blank);
  for (const e of eligible) {
    const opt = document.createElement("option");
    opt.value = e.entry_id;
    opt.textContent =
      `${e.entry_id.slice(0, 8)}… — ${(e.content || "").slice(0, 50)}`;
    entrySel.appendChild(opt);
  }

  // Recipient picker — every other agent.
  const agents = state.get("agents") || [];
  recipientSel.innerHTML = "";
  const blankR = document.createElement("option");
  blankR.value = "";
  blankR.textContent = "—";
  recipientSel.appendChild(blankR);
  for (const a of agents) {
    if (a.instance_id === _selectedAgent) continue;
    const opt = document.createElement("option");
    opt.value = a.instance_id;
    opt.textContent = `${a.agent_name} (${a.instance_id.slice(0, 8)}…)`;
    recipientSel.appendChild(opt);
  }
}


function populateAgentSelect() {
  const sel = document.getElementById("memory-agent");
  if (!sel) return;
  const agents = state.get("agents") || [];
  const current = sel.value;
  sel.innerHTML = "";
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = "—";
  sel.appendChild(blank);
  for (const a of agents) {
    const opt = document.createElement("option");
    opt.value = a.instance_id;
    opt.textContent = `${a.agent_name} (${a.role})`;
    if (a.instance_id === current) opt.selected = true;
    sel.appendChild(opt);
  }
}


// ---------------------------------------------------------------------------
// Fetch helpers
// ---------------------------------------------------------------------------
async function fetchEntries() {
  if (!_selectedAgent) {
    _entriesCache = [];
    renderEntriesList([]);
    populateGrantPickers();
    return;
  }
  // Drive the dispatcher's memory_recall.v1 by POSTing a tool call.
  // The tool is read_only so it never gates — the response is
  // synchronous.
  try {
    const resp = await writeCall(
      `/agents/${_selectedAgent}/tools/call`,
      {
        tool_name: "memory_recall",
        tool_version: "1",
        args: { mode: _selectedMode, limit: 100 },
      },
    );
    const entries = (resp.result && resp.result.output && resp.result.output.entries) || [];
    _entriesCache = entries;
    renderEntriesList(entries);
    populateGrantPickers();
  } catch (e) {
    _entriesCache = [];
    renderEntriesList([]);
    if (e instanceof ApiError) {
      toast({
        title: "Couldn't load memory",
        msg: e.message,
        kind: "error", ttl: 6000,
      });
    } else {
      throw e;
    }
  }
}


async function fetchConsents() {
  if (!_selectedAgent) {
    renderConsentsList([]);
    return;
  }
  try {
    const data = await api.get(`/agents/${_selectedAgent}/memory/consents`);
    renderConsentsList(data.consents || []);
  } catch (e) {
    renderConsentsList([]);
    if (e instanceof ApiError) {
      toast({
        title: "Couldn't load consents",
        msg: e.message,
        kind: "warn", ttl: 6000,
      });
    }
  }
}


async function refreshAll() {
  await Promise.all([fetchEntries(), fetchConsents()]);
}


// ---------------------------------------------------------------------------
// Action handlers
// ---------------------------------------------------------------------------
async function onGrant() {
  const entrySel = document.getElementById("memory-grant-entry");
  const recipientSel = document.getElementById("memory-grant-recipient");
  const btn = document.getElementById("memory-grant-btn");
  if (!entrySel || !recipientSel || !btn) return;

  const entryId = entrySel.value;
  const recipient = recipientSel.value;
  if (!entryId || !recipient) {
    toast({
      title: "Pick an entry and a recipient",
      msg: "Both fields are required.",
      kind: "warn", ttl: 4000,
    });
    return;
  }
  btn.disabled = true;
  btn.textContent = "granting…";
  try {
    await writeCall(
      `/agents/${_selectedAgent}/memory/consents`,
      { entry_id: entryId, recipient_instance: recipient },
    );
    toast({
      title: "Consent granted",
      msg: `${entryId.slice(0, 8)}… → ${recipient.slice(0, 8)}…`,
      kind: "success", ttl: 4000,
    });
    await fetchConsents();
  } catch (e) {
    toast({
      title: "Grant failed",
      msg: e instanceof ApiError ? e.message : String(e),
      kind: "error", ttl: 6000,
    });
  } finally {
    btn.disabled = false;
    btn.textContent = "grant";
  }
}


async function onRevoke(entryId, recipient, btn) {
  btn.disabled = true;
  btn.textContent = "revoking…";
  try {
    // No body — the URL identifies the (entry, recipient) pair.
    // Idempotent by definition; api.del skips the idempotency-key
    // dance that writeCall does for POST.
    await api.del(
      `/agents/${_selectedAgent}/memory/consents/${entryId}/${recipient}`,
    );
    toast({
      title: "Consent revoked",
      msg: `${entryId.slice(0, 8)}… → ${recipient.slice(0, 8)}…`,
      kind: "success", ttl: 4000,
    });
    await fetchConsents();
  } catch (e) {
    toast({
      title: "Revoke failed",
      msg: e instanceof ApiError ? e.message : String(e),
      kind: "error", ttl: 6000,
    });
  } finally {
    btn.disabled = false;
    btn.textContent = "revoke";
  }
}


// ---------------------------------------------------------------------------
// Event wiring
// ---------------------------------------------------------------------------
function wireEvents() {
  const agentSel = document.getElementById("memory-agent");
  const modeSel = document.getElementById("memory-mode");
  const refreshBtn = document.getElementById("memory-refresh");
  const grantBtn = document.getElementById("memory-grant-btn");

  if (agentSel) {
    agentSel.addEventListener("change", () => {
      _selectedAgent = agentSel.value;
      refreshAll();
    });
  }
  if (modeSel) {
    modeSel.addEventListener("change", () => {
      _selectedMode = modeSel.value;
      fetchEntries();
    });
  }
  if (refreshBtn) {
    refreshBtn.addEventListener("click", refreshAll);
  }
  if (grantBtn) {
    grantBtn.addEventListener("click", onGrant);
  }
  // Refresh on tab activation.
  document.querySelectorAll(".tab").forEach((tab) => {
    if (tab.dataset.tab === "memory") {
      tab.addEventListener("click", refreshAll);
    }
  });
}


// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
export function start() {
  populateAgentSelect();
  wireEvents();
  // Re-populate the agent + recipient pickers when state.agents updates.
  state.subscribe("agents", () => {
    populateAgentSelect();
    populateGrantPickers();
  });
  // Default mode is private; surface the (likely empty) initial state.
  refreshAll();
}
