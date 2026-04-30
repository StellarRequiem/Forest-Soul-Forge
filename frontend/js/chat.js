// ADR-003Y Y6 — Chat tab. Wires the conversations / participants /
// turns endpoints into a vanilla-JS room view. No framework, no build
// step; matches the existing tab modules' style (memory.js, agents.js).
//
// Persistence: the active conversation_id is stashed in localStorage
// so a refresh resumes the same room (per ADR-003Y "Daemon-restart
// stickiness" — frontend half).
//
// State flow:
//   loadRooms()    fetches /conversations, renders the left rail
//   selectRoom(id) loads participants + turns, shows the center panel
//   sendTurn()     posts to /conversations/{id}/turns with auto_respond
//                  and re-renders the chain
//
// All renders are full re-renders (no diff). Y6 ships a working
// surface; the wire-protocol-cost is fine at single-operator scale.
// A future Y6.1 pass can switch to /audit/stream for live updates.

import { api, ApiError, writeCall } from "./api.js";
import { state } from "./state.js";
import { toast } from "./toast.js";

const ACTIVE_KEY = "fsf.chat.activeConv";

let activeConversationId = null;
let activeConversation = null;   // ConversationOut row
let activeParticipants = [];      // ParticipantOut[]
let activeTurns = [];             // TurnOut[]
let agentLookupCache = new Map(); // instance_id -> agent row (name, role, etc.)

// ---------------------------------------------------------------------------
// Public entry point
// ---------------------------------------------------------------------------
export async function start() {
  wireRoomsRefresh();
  wireNewRoomDialog();
  wireComposer();
  wireRoomActions();
  await loadRooms();
  // Auto-resume an active conversation if one is stashed.
  const stashed = localStorage.getItem(ACTIVE_KEY);
  if (stashed) {
    try {
      await selectRoom(stashed);
    } catch (e) {
      // Stale id (room archived from another tab/session). Drop it
      // silently and let the user pick from the rail.
      localStorage.removeItem(ACTIVE_KEY);
    }
  }
}

// ---------------------------------------------------------------------------
// Rooms list (left rail)
// ---------------------------------------------------------------------------
async function loadRooms() {
  const list = document.getElementById("chat-rooms");
  list.innerHTML = `<p class="muted">Loading…</p>`;
  try {
    const resp = await api.get("/conversations?limit=200");
    renderRooms(resp.conversations || []);
  } catch (e) {
    list.innerHTML = `<p class="muted">Couldn't load rooms: ${escapeHTML(String(e.message || e))}</p>`;
  }
}

function renderRooms(conversations) {
  const list = document.getElementById("chat-rooms");
  if (!conversations.length) {
    list.innerHTML = `<p class="muted">No rooms yet. Click <strong>+ new</strong> to create one.</p>`;
    return;
  }
  // Group by domain (alphabetical), sort within domain by last_turn_at desc.
  const byDomain = {};
  for (const c of conversations) {
    if (!byDomain[c.domain]) byDomain[c.domain] = [];
    byDomain[c.domain].push(c);
  }
  const html = [];
  for (const domain of Object.keys(byDomain).sort()) {
    html.push(`<div class="chat-rooms__domain">
      <div class="chat-rooms__domain-label">${escapeHTML(domain)}</div>`);
    const rooms = byDomain[domain].sort((a, b) =>
      (b.last_turn_at || b.created_at).localeCompare(a.last_turn_at || a.created_at)
    );
    for (const r of rooms) {
      const isActive = r.conversation_id === activeConversationId;
      const ts = r.last_turn_at || r.created_at;
      const tsShort = ts ? ts.slice(11, 19) : "—";
      html.push(`<div class="chat-rooms__item ${isActive ? "chat-rooms__item--active" : ""} ${r.status === "archived" ? "chat-rooms__item--archived" : ""}"
                   data-cid="${r.conversation_id}">
        <div class="chat-rooms__item-id">${escapeHTML(r.conversation_id.slice(0, 8))}…</div>
        <div class="chat-rooms__item-meta">
          <span class="chat-rooms__item-status">${escapeHTML(r.status)}</span>
          <span class="chat-rooms__item-time">${tsShort}</span>
        </div>
      </div>`);
    }
    html.push(`</div>`);
  }
  list.innerHTML = html.join("");
  // Wire clicks.
  list.querySelectorAll(".chat-rooms__item").forEach((el) => {
    el.addEventListener("click", () => {
      const cid = el.dataset.cid;
      selectRoom(cid).catch((e) => {
        toast({ title: "Room load failed", msg: String(e.message || e), kind: "error", ttl: 8000 });
      });
    });
  });
}

function wireRoomsRefresh() {
  document.getElementById("chat-rooms-refresh")?.addEventListener("click", () => loadRooms());
}

// ---------------------------------------------------------------------------
// New-room dialog
// ---------------------------------------------------------------------------
function wireNewRoomDialog() {
  const dialog = document.getElementById("chat-new-room-dialog");
  const newBtn = document.getElementById("chat-new-room");
  const cancelBtn = document.getElementById("chat-new-cancel");
  const createBtn = document.getElementById("chat-new-create");
  const partSelect = document.getElementById("chat-new-participant");

  newBtn?.addEventListener("click", () => {
    // Default operator_id from localStorage if previously used.
    const lastOp = localStorage.getItem("fsf.chat.lastOperator") || "alex";
    document.getElementById("chat-new-operator").value = lastOp;
    document.getElementById("chat-new-domain").value = "";
    // Populate participants picker from agents state. Active agents only.
    const opts = ['<option value="">— none for now —</option>'];
    for (const a of (state.agents || [])) {
      if (a.status !== "active") continue;
      opts.push(`<option value="${a.instance_id}">${escapeHTML(a.agent_name)} (${escapeHTML(a.role)})</option>`);
    }
    partSelect.innerHTML = opts.join("");
    dialog.hidden = false;
    document.getElementById("chat-new-domain").focus();
  });

  cancelBtn?.addEventListener("click", () => { dialog.hidden = true; });

  createBtn?.addEventListener("click", async () => {
    const domain = document.getElementById("chat-new-domain").value.trim();
    const operator_id = document.getElementById("chat-new-operator").value.trim();
    const retention_policy = document.getElementById("chat-new-retention").value;
    const initialParticipant = partSelect.value;
    if (!domain || !operator_id) {
      toast({ title: "Missing fields", msg: "domain and operator_id required.", kind: "warn", ttl: 5000 });
      return;
    }
    localStorage.setItem("fsf.chat.lastOperator", operator_id);
    try {
      const conv = await writeCall("/conversations", { domain, operator_id, retention_policy });
      if (initialParticipant) {
        try {
          await writeCall(`/conversations/${conv.conversation_id}/participants`, {
            instance_id: initialParticipant,
          });
        } catch (e) {
          toast({ title: "Participant add failed", msg: String(e.message || e), kind: "warn", ttl: 6000 });
        }
      }
      dialog.hidden = true;
      toast({ title: "Room created", msg: `${domain} / ${conv.conversation_id.slice(0, 8)}…`, kind: "info", ttl: 4000 });
      await loadRooms();
      await selectRoom(conv.conversation_id);
    } catch (e) {
      toast({ title: "Couldn't create room", msg: String(e.message || e), kind: "error", ttl: 8000 });
    }
  });
}

// ---------------------------------------------------------------------------
// Room view
// ---------------------------------------------------------------------------
async function selectRoom(conversationId) {
  activeConversationId = conversationId;
  localStorage.setItem(ACTIVE_KEY, conversationId);
  // Load conversation, participants, turns in parallel.
  const [conv, parts, turnsResp] = await Promise.all([
    api.get(`/conversations/${conversationId}`),
    api.get(`/conversations/${conversationId}/participants`),
    api.get(`/conversations/${conversationId}/turns?limit=200`),
  ]);
  activeConversation = conv;
  activeParticipants = parts.participants || [];
  activeTurns = turnsResp.turns || [];

  // Refresh agent lookup cache for participants we don't already know.
  await refreshAgentLookup(activeParticipants.map((p) => p.instance_id));

  renderRoomHeader();
  renderParticipants();
  renderTurns();
  renderRooms(await fetchRoomList()); // refresh rail to show active highlight
  document.getElementById("chat-composer").hidden = (conv.status === "archived");
  document.getElementById("chat-room-actions").hidden = false;
  document.getElementById("chat-participants").hidden = false;
  document.getElementById("chat-turns").hidden = false;
}

async function fetchRoomList() {
  const resp = await api.get("/conversations?limit=200");
  return resp.conversations || [];
}

function renderRoomHeader() {
  const c = activeConversation;
  const title = `${c.domain} / ${c.conversation_id.slice(0, 8)}…`;
  document.getElementById("chat-room-title").textContent = title;
  const subtitle = `operator=${c.operator_id} · status=${c.status} · created ${c.created_at.slice(0, 19).replace("T", " ")}`;
  document.getElementById("chat-room-subtitle").textContent = subtitle;
  document.getElementById("chat-room-retention").textContent = `retention: ${c.retention_policy}`;
}

function renderParticipants() {
  const row = document.getElementById("chat-participants");
  const html = [];
  if (!activeParticipants.length) {
    html.push(`<span class="muted">No agents yet — <button class="btn btn--ghost btn--sm" id="chat-add-participant" type="button">+ add</button></span>`);
  } else {
    for (const p of activeParticipants) {
      const agent = agentLookupCache.get(p.instance_id);
      const name = agent ? agent.agent_name : p.instance_id.slice(0, 12);
      const bridged = p.bridged_from ? ` (bridged from ${escapeHTML(p.bridged_from)})` : "";
      html.push(`<span class="chat-chip" title="${escapeHTML(p.instance_id)}">@${escapeHTML(name)}${bridged}<button class="chat-chip__x" data-iid="${p.instance_id}" title="Remove from room">×</button></span>`);
    }
    html.push(`<button class="btn btn--ghost btn--sm" id="chat-add-participant" type="button" style="margin-left: auto;">+ add</button>`);
  }
  row.innerHTML = html.join("");
  // Wire chip × buttons.
  row.querySelectorAll(".chat-chip__x").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const iid = btn.dataset.iid;
      try {
        await api.del(`/conversations/${activeConversationId}/participants/${iid}`);
        toast({ title: "Removed", msg: `${iid.slice(0, 12)}…`, kind: "info", ttl: 3000 });
        await selectRoom(activeConversationId);
      } catch (e) {
        toast({ title: "Couldn't remove", msg: String(e.message || e), kind: "error", ttl: 6000 });
      }
    });
  });
  document.getElementById("chat-add-participant")?.addEventListener("click", () => promptAddParticipant());
}

async function promptAddParticipant() {
  // Lightweight in-place prompt — let the operator type or pick. Keeping
  // it simple for v1; a richer search/picker is a nice-to-have for Y6.1.
  const candidates = (state.agents || []).filter((a) => a.status === "active");
  if (!candidates.length) {
    toast({ title: "No agents", msg: "Birth one in the Forge tab first.", kind: "warn", ttl: 5000 });
    return;
  }
  const choices = candidates.map((a) => `${a.agent_name} (${a.role}) — ${a.instance_id.slice(0, 12)}`).join("\n");
  const pick = window.prompt(`Pick by typing the instance_id (paste from list):\n\n${choices}`);
  if (!pick) return;
  try {
    await writeCall(`/conversations/${activeConversationId}/participants`, {
      instance_id: pick.trim(),
    });
    toast({ title: "Added", msg: `${pick.slice(0, 12)}…`, kind: "info", ttl: 3000 });
    await selectRoom(activeConversationId);
  } catch (e) {
    toast({ title: "Couldn't add", msg: String(e.message || e), kind: "error", ttl: 6000 });
  }
}

function renderTurns() {
  const list = document.getElementById("chat-turns");
  if (!activeTurns.length) {
    list.innerHTML = `<p class="muted">No turns yet. Send the first message below.</p>`;
    return;
  }
  const html = [];
  for (const t of activeTurns) {
    const agent = agentLookupCache.get(t.speaker);
    const isAgent = !!agent;
    const speakerLabel = isAgent ? agent.agent_name : t.speaker;
    const ts = t.timestamp ? t.timestamp.slice(11, 19) : "—";
    const bodyOrSummary = t.body || (t.summary ? `[summarized] ${t.summary}` : `<em class="muted">[content purged]</em>`);
    const bodyEsc = t.body
      ? formatMentions(escapeHTML(t.body), activeParticipants, agentLookupCache)
      : (t.summary ? escapeHTML(`[summarized] ${t.summary}`) : `<em class="muted">[content purged]</em>`);
    const meta = isAgent
      ? `<span class="chat-turn__model">${escapeHTML(t.model_used || "?")}</span><span class="chat-turn__tokens">${t.token_count || 0} tok</span>`
      : "";
    html.push(`<div class="chat-turn ${isAgent ? "chat-turn--agent" : "chat-turn--operator"}">
      <div class="chat-turn__head">
        <span class="chat-turn__speaker">${escapeHTML(speakerLabel)}</span>
        <span class="chat-turn__time">${ts}</span>
        ${meta}
      </div>
      <div class="chat-turn__body">${bodyEsc}</div>
    </div>`);
  }
  list.innerHTML = html.join("");
  // Auto-scroll to bottom.
  list.scrollTop = list.scrollHeight;
}

// Highlight @AgentName in turn bodies (light visual cue).
function formatMentions(escapedBody, participants, lookup) {
  if (!participants.length) return escapedBody;
  const names = new Set();
  for (const p of participants) {
    const a = lookup.get(p.instance_id);
    if (a?.agent_name) names.add(a.agent_name);
  }
  if (!names.size) return escapedBody;
  // Build a regex matching @AnyKnownName (longer first to avoid prefix collision).
  const sorted = Array.from(names).sort((a, b) => b.length - a.length);
  const escaped = sorted.map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const re = new RegExp(`@(${escaped.join("|")})`, "g");
  return escapedBody.replace(re, '<span class="chat-mention">@$1</span>');
}

// ---------------------------------------------------------------------------
// Composer
// ---------------------------------------------------------------------------
function wireComposer() {
  const sendBtn = document.getElementById("chat-send");
  const input = document.getElementById("chat-composer-input");
  sendBtn?.addEventListener("click", () => sendTurn());
  input?.addEventListener("keydown", (e) => {
    // Cmd/Ctrl+Enter sends; plain Enter inserts a newline (the textarea
    // is typically multi-line).
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      sendTurn();
    }
  });
}

async function sendTurn() {
  if (!activeConversationId) return;
  const input = document.getElementById("chat-composer-input");
  const sendBtn = document.getElementById("chat-send");
  const body = input.value.trim();
  if (!body) return;
  const auto = document.getElementById("chat-auto-respond").checked;
  const depth = parseInt(document.getElementById("chat-max-depth").value, 10) || 4;
  const maxTokens = parseInt(document.getElementById("chat-max-tokens").value, 10) || 400;
  const operator_id = activeConversation?.operator_id || "alex";

  sendBtn.disabled = true;
  sendBtn.textContent = auto ? "thinking…" : "sending…";
  try {
    const resp = await writeCall(`/conversations/${activeConversationId}/turns`, {
      speaker: operator_id,
      body,
      auto_respond: auto,
      history_limit: 20,
      max_chain_depth: depth,
      max_response_tokens: maxTokens,
    });
    input.value = "";
    if (resp.agent_dispatch_failed) {
      toast({
        title: "Agent didn't respond",
        msg: "Operator turn landed; check audit/tail for the failure reason.",
        kind: "warn",
        ttl: 8000,
      });
    } else if (resp.chain_depth > 0) {
      toast({
        title: `Chain depth ${resp.chain_depth}`,
        msg: `${resp.chain_depth} agent reply${resp.chain_depth === 1 ? "" : "ies"} appended.`,
        kind: "info",
        ttl: 3000,
      });
    }
    await selectRoom(activeConversationId);
  } catch (e) {
    toast({ title: "Send failed", msg: String(e.message || e), kind: "error", ttl: 8000 });
  } finally {
    sendBtn.disabled = false;
    sendBtn.textContent = "send";
  }
}

// ---------------------------------------------------------------------------
// Room actions (archive)
// ---------------------------------------------------------------------------
function wireRoomActions() {
  document.getElementById("chat-room-archive")?.addEventListener("click", async () => {
    if (!activeConversationId) return;
    if (!confirm("Archive this room? Turns are preserved; the room becomes read-only.")) return;
    try {
      await writeCall(`/conversations/${activeConversationId}/status`, {
        status: "archived",
        reason: "operator archived from Chat tab",
      });
      toast({ title: "Archived", msg: activeConversationId.slice(0, 8) + "…", kind: "info", ttl: 3000 });
      await selectRoom(activeConversationId);
    } catch (e) {
      toast({ title: "Archive failed", msg: String(e.message || e), kind: "error", ttl: 6000 });
    }
  });
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
async function refreshAgentLookup(instanceIds) {
  // Use state.agents when populated; fall back to /agents/{id} for any miss.
  if (state.agents?.length) {
    for (const a of state.agents) agentLookupCache.set(a.instance_id, a);
  }
  for (const iid of instanceIds) {
    if (agentLookupCache.has(iid)) continue;
    try {
      const a = await api.get(`/agents/${iid}`);
      agentLookupCache.set(iid, a);
    } catch {
      // Silent — agent might be archived; we'll show the raw instance_id.
    }
  }
}

function escapeHTML(s) {
  return String(s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}
