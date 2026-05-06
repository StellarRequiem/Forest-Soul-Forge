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
import * as state from "./state.js";
import { toast } from "./toast.js";

const ACTIVE_KEY = "fsf.chat.activeConv";
// T19 (B145): persist the operator's "show archived" preference across
// page reloads. Default false — the rail stays clean by default; toggle
// to surface archived rooms (e.g., to restore one or audit history).
const SHOW_ARCHIVED_KEY = "fsf.chat.showArchived";
// ADR-0047 T1 (B147): persist Chat-tab mode (rooms vs assistant).
// Default "rooms" until T2-T6 build out the assistant flow; T1 is
// scaffold only. Operator can toggle now to confirm the mode wires.
const CHAT_MODE_KEY = "fsf.chat.mode";

let activeConversationId = null;
let activeConversation = null;   // ConversationOut row
let activeParticipants = [];      // ParticipantOut[]
let activeTurns = [];             // TurnOut[]
let agentLookupCache = new Map(); // instance_id -> agent row (name, role, etc.)
let showArchived = false;         // T19 (B145): rail filter state

// ---------------------------------------------------------------------------
// Public entry point
// ---------------------------------------------------------------------------
export async function start() {
  wireChatModeToggle();       // ADR-0047 T1 (B147)
  wireRoomsRefresh();
  wireShowArchivedToggle();   // T19 (B145)
  wireNewRoomDialog();
  wireComposer();
  wireRoomActions();
  wireBridgeDialog();
  wireAmbientDialog();
  wireSweepDialog();
  wireAddParticipantDialog();
  // Restore archived-toggle preference before first render so the
  // rail's initial paint matches what the operator last saw.
  showArchived = localStorage.getItem(SHOW_ARCHIVED_KEY) === "true";
  const cb = document.getElementById("chat-rooms-show-archived");
  if (cb) cb.checked = showArchived;
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
  // T19 (B145): filter archived rooms from the rail unless the
  // operator opted in via the "archived" toggle. Honors the
  // distinction between "really gone" (Forest doesn't do that —
  // audit chain is append-only) and "out of sight" (rail filter).
  const visible = showArchived
    ? conversations
    : conversations.filter((c) => c.status !== "archived");
  const archivedHidden = conversations.length - visible.length;

  if (!visible.length) {
    if (archivedHidden > 0) {
      list.innerHTML = `<p class="muted">No active rooms. <strong>${archivedHidden}</strong> archived hidden — toggle <em>archived</em> to see them, or click <strong>+ new</strong>.</p>`;
    } else {
      list.innerHTML = `<p class="muted">No rooms yet. Click <strong>+ new</strong> to create one.</p>`;
    }
    return;
  }
  // Group by domain (alphabetical), sort within domain by last_turn_at desc.
  const byDomain = {};
  for (const c of visible) {
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
      // T19 (B145): per-row archive button (×) — only render for
      // active rooms (no point archiving an already-archived one).
      // Title says "archive" not "delete" because Forest's audit chain
      // is append-only; nothing is truly removed, just hidden from
      // default view.
      const archiveBtn = (r.status !== "archived")
        ? `<button class="chat-rooms__item-archive-btn" data-archive-cid="${r.conversation_id}" title="Archive (hides from rail; audit chain preserved)" type="button">×</button>`
        : "";
      html.push(`<div class="chat-rooms__item ${isActive ? "chat-rooms__item--active" : ""} ${r.status === "archived" ? "chat-rooms__item--archived" : ""}"
                   data-cid="${r.conversation_id}">
        ${archiveBtn}
        <div class="chat-rooms__item-id">${escapeHTML(r.conversation_id.slice(0, 8))}…</div>
        <div class="chat-rooms__item-meta">
          <span class="chat-rooms__item-status">${escapeHTML(r.status)}</span>
          <span class="chat-rooms__item-time">${tsShort}</span>
        </div>
      </div>`);
    }
    html.push(`</div>`);
  }
  // T19 (B145): summary footer — when archived rooms are hidden,
  // show the operator how many were filtered + a one-click toggle.
  if (!showArchived && archivedHidden > 0) {
    html.push(`<div class="chat-rooms__archived-note muted" style="padding:8px 10px;font-size:0.8em;">
      ${archivedHidden} archived hidden
    </div>`);
  }
  list.innerHTML = html.join("");
  // Wire room-select clicks (the whole item).
  list.querySelectorAll(".chat-rooms__item").forEach((el) => {
    el.addEventListener("click", () => {
      const cid = el.dataset.cid;
      selectRoom(cid).catch((e) => {
        toast({ title: "Room load failed", msg: String(e.message || e), kind: "error", ttl: 8000 });
      });
    });
  });
  // T19 (B145): wire per-row archive (×) buttons. stopPropagation so
  // clicking × archives without first selecting the room.
  list.querySelectorAll(".chat-rooms__item-archive-btn").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const cid = btn.dataset.archiveCid;
      if (!cid) return;
      if (!confirm(`Archive room ${cid.slice(0, 8)}…?\n\nThe room is hidden from the rail (toggle "archived" to restore). Turns + audit history are preserved.`)) {
        return;
      }
      try {
        await writeCall(`/conversations/${cid}/status`, {
          status: "archived",
          reason: "operator archived from rail",
        });
        toast({ title: "Archived", msg: cid.slice(0, 8) + "…", kind: "info", ttl: 3000 });
        // If we just archived the active room, drop selection.
        if (cid === activeConversationId) {
          activeConversationId = null;
          localStorage.removeItem(ACTIVE_KEY);
        }
        await loadRooms();
      } catch (err) {
        toast({ title: "Archive failed", msg: String(err.message || err), kind: "error", ttl: 6000 });
      }
    });
  });
}

function wireRoomsRefresh() {
  document.getElementById("chat-rooms-refresh")?.addEventListener("click", () => loadRooms());
}

// ADR-0047 T1 (B147): wire the Chat-tab mode toggle (Assistant / Rooms).
// T1 is the scaffold — the assistant pane shows a placeholder. T2-T6
// add: birth flow, conversation auto-init, settings panel, memory
// integration, role definition. Mode preference persists in localStorage.
function wireChatModeToggle() {
  const roomsBtn = document.getElementById("chat-mode-rooms");
  const assistantBtn = document.getElementById("chat-mode-assistant");
  if (!roomsBtn || !assistantBtn) return;

  const stored = localStorage.getItem(CHAT_MODE_KEY);
  const initialMode = (stored === "assistant") ? "assistant" : "rooms";
  showChatMode(initialMode);

  roomsBtn.addEventListener("click", () => {
    showChatMode("rooms");
    localStorage.setItem(CHAT_MODE_KEY, "rooms");
  });
  assistantBtn.addEventListener("click", () => {
    showChatMode("assistant");
    localStorage.setItem(CHAT_MODE_KEY, "assistant");
  });
}

function showChatMode(mode) {
  const roomsPane = document.getElementById("chat-pane-rooms");
  const assistantPane = document.getElementById("chat-pane-assistant");
  const roomsBtn = document.getElementById("chat-mode-rooms");
  const assistantBtn = document.getElementById("chat-mode-assistant");
  const isAssistant = mode === "assistant";
  if (roomsPane) roomsPane.hidden = isAssistant;
  if (assistantPane) assistantPane.hidden = !isAssistant;
  if (roomsBtn) roomsBtn.classList.toggle("chat-mode-btn--active", !isAssistant);
  if (assistantBtn) assistantBtn.classList.toggle("chat-mode-btn--active", isAssistant);
}

// T19 (B145): the "archived" toggle in the rail header. Defaults off
// (rail stays clean); on persists across reloads. On change, re-renders
// the rail using the cached conversation list — no re-fetch needed
// because we only filter what to show, not what to fetch.
function wireShowArchivedToggle() {
  const cb = document.getElementById("chat-rooms-show-archived");
  if (!cb) return;
  cb.addEventListener("change", () => {
    showArchived = cb.checked;
    localStorage.setItem(SHOW_ARCHIVED_KEY, String(showArchived));
    // Re-render via loadRooms — simplest path that picks up server-
    // side changes too (e.g., another tab archived something).
    loadRooms();
  });
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

  newBtn?.addEventListener("click", async () => {
    // Default operator_id from localStorage if previously used.
    const lastOp = localStorage.getItem("fsf.chat.lastOperator") || "alex";
    document.getElementById("chat-new-operator").value = lastOp;
    document.getElementById("chat-new-domain").value = "";
    // Populate participants picker. Try state cache first; if empty,
    // fetch /agents directly so the user doesn't have to visit the
    // Agents tab to populate the cache before creating a room.
    let agents = state.get("agents") || [];
    if (!agents.length) {
      try {
        const res = await api.get("/agents");
        agents = res.agents || [];
        state.set("agents", agents);
      } catch (e) {
        // Non-fatal — dialog still opens with the placeholder option.
      }
    }
    const opts = ['<option value="">— none for now —</option>'];
    for (const a of agents) {
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
      html.push(`<span class="chat-chip" title="${escapeHTML(p.instance_id)}">@${escapeHTML(name)}${bridged}<button class="chat-chip__nudge" data-iid="${p.instance_id}" title="Y5 — ambient nudge">⚡</button><button class="chat-chip__x" data-iid="${p.instance_id}" title="Remove from room">×</button></span>`);
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
  // Y5 — ambient nudge buttons (⚡ on each chip).
  row.querySelectorAll(".chat-chip__nudge").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      openAmbientDialog(btn.dataset.iid);
    });
  });
}

async function promptAddParticipant() {
  // Opens the add-participant dialog and populates the agent <select>
  // with active agents not already in the room. Replaced the original
  // window.prompt() flow because (a) the message text in window.prompt
  // can't be selected/copied on macOS browsers, and (b) the truncated
  // instance_ids previously shown didn't match what the API expected.
  // Now: clickable dropdown, server-side resolved IDs, no copy/paste
  // required.
  const dialog = document.getElementById("chat-add-participant-dialog");
  const sel = document.getElementById("chat-add-participant-instance");
  if (!dialog || !sel) {
    // Fallback for older index.html without the dialog markup.
    toast({ title: "UI out of date", msg: "Refresh the page to pick up the new participant dialog.", kind: "warn", ttl: 6000 });
    return;
  }
  // Fetch agents directly so the cache state is irrelevant.
  let agents = state.get("agents") || [];
  if (!agents.length) {
    try {
      const res = await api.get("/agents");
      agents = res.agents || [];
      state.set("agents", agents);
    } catch (e) { /* non-fatal */ }
  }
  const inRoom = new Set(activeParticipants.map((p) => p.instance_id));
  const opts = [];
  for (const a of agents) {
    if (a.status !== "active") continue;
    if (inRoom.has(a.instance_id)) continue;
    opts.push(`<option value="${a.instance_id}">${escapeHTML(a.agent_name)} (${escapeHTML(a.role)})</option>`);
  }
  if (!opts.length) {
    toast({ title: "No eligible agents", msg: "All active agents are already in this room, or none are born yet (Forge tab).", kind: "warn", ttl: 6000 });
    return;
  }
  sel.innerHTML = opts.join("");
  dialog.hidden = false;
}

function wireAddParticipantDialog() {
  const dialog = document.getElementById("chat-add-participant-dialog");
  if (!dialog) return;  // older index.html
  document.getElementById("chat-add-participant-cancel")?.addEventListener("click", () => {
    dialog.hidden = true;
  });
  document.getElementById("chat-add-participant-confirm")?.addEventListener("click", async () => {
    const sel = document.getElementById("chat-add-participant-instance");
    const instance_id = sel?.value;
    if (!instance_id) {
      toast({ title: "Pick an agent", msg: "Use the dropdown to select one.", kind: "warn", ttl: 4000 });
      return;
    }
    if (!activeConversationId) {
      toast({ title: "No active room", msg: "Pick a room first.", kind: "warn", ttl: 4000 });
      return;
    }
    try {
      await writeCall(`/conversations/${activeConversationId}/participants`, { instance_id });
      const opt = sel.options[sel.selectedIndex];
      toast({ title: "Added", msg: opt?.textContent || instance_id, kind: "info", ttl: 3000 });
      dialog.hidden = true;
      await selectRoom(activeConversationId);
    } catch (e) {
      toast({ title: "Couldn't add", msg: String(e.message || e), kind: "error", ttl: 6000 });
    }
  });
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
  // DON'T disable the input field — user can keep typing the next message
  // while the agent is generating. The send button gates duplicate sends.
  // (Earlier UX felt 'sticky' because the entire page seemed frozen during
  // the LLM round-trip, which can be 5-30s on a 7B local model.)
  const startedAt = performance.now();
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
    const elapsedSec = Math.round((performance.now() - startedAt) / 100) / 10;
    if (auto && elapsedSec > 5) {
      toast({ title: `Round-trip ${elapsedSec}s`, msg: "LLM generation latency. Lower max_tokens or switch to a smaller model to speed this up.", kind: "info", ttl: 5000 });
    }
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
// Y4 — Bridge dialog
// ---------------------------------------------------------------------------
function wireBridgeDialog() {
  const dialog = document.getElementById("chat-bridge-dialog");
  document.getElementById("chat-room-bridge")?.addEventListener("click", async () => {
    if (!activeConversationId) return;
    // Populate agent picker from active agents NOT already in the room.
    // Try state cache first; if empty, fetch /agents directly so the
    // user doesn't have to visit the Agents tab to seed the cache.
    const sel = document.getElementById("chat-bridge-instance");
    let agents = state.get("agents") || [];
    if (!agents.length) {
      try {
        const res = await api.get("/agents");
        agents = res.agents || [];
        state.set("agents", agents);
      } catch (e) { /* non-fatal */ }
    }
    const inRoom = new Set(activeParticipants.map((p) => p.instance_id));
    const opts = [];
    for (const a of agents) {
      if (a.status !== "active") continue;
      if (inRoom.has(a.instance_id)) continue;
      opts.push(`<option value="${a.instance_id}">${escapeHTML(a.agent_name)} (${escapeHTML(a.role)})</option>`);
    }
    sel.innerHTML = opts.length ? opts.join("") : `<option value="">— no eligible agents —</option>`;
    document.getElementById("chat-bridge-from-domain").value = "";
    document.getElementById("chat-bridge-reason").value = "";
    dialog.hidden = false;
  });
  document.getElementById("chat-bridge-cancel")?.addEventListener("click", () => { dialog.hidden = true; });
  document.getElementById("chat-bridge-confirm")?.addEventListener("click", async () => {
    const instance_id = document.getElementById("chat-bridge-instance").value;
    const from_domain = document.getElementById("chat-bridge-from-domain").value.trim();
    const reason = document.getElementById("chat-bridge-reason").value.trim();
    const operator_id = activeConversation?.operator_id || "alex";
    if (!instance_id || !from_domain || !reason) {
      toast({ title: "Missing fields", msg: "agent + from_domain + reason all required.", kind: "warn", ttl: 4000 });
      return;
    }
    try {
      await writeCall(`/conversations/${activeConversationId}/bridge`, {
        instance_id, from_domain, operator_id, reason,
      });
      dialog.hidden = true;
      toast({ title: "Bridged in", msg: `${instance_id.slice(0, 12)}… from ${from_domain}`, kind: "info", ttl: 4000 });
      await selectRoom(activeConversationId);
    } catch (e) {
      toast({ title: "Bridge failed", msg: String(e.message || e), kind: "error", ttl: 6000 });
    }
  });
}


// ---------------------------------------------------------------------------
// Y5 — Ambient nudge dialog
// ---------------------------------------------------------------------------
function openAmbientDialog(instance_id) {
  const dialog = document.getElementById("chat-ambient-dialog");
  const agent = agentLookupCache.get(instance_id);
  const display = agent ? `${agent.agent_name}  (${instance_id})` : instance_id;
  document.getElementById("chat-ambient-instance").value = display;
  dialog.dataset.iid = instance_id;
  document.getElementById("chat-ambient-kind").value = "proactive";
  dialog.hidden = false;
}

function wireAmbientDialog() {
  const dialog = document.getElementById("chat-ambient-dialog");
  document.getElementById("chat-ambient-cancel")?.addEventListener("click", () => { dialog.hidden = true; });
  document.getElementById("chat-ambient-confirm")?.addEventListener("click", async () => {
    const instance_id = dialog.dataset.iid;
    if (!instance_id || !activeConversationId) return;
    const operator_id = activeConversation?.operator_id || "alex";
    const nudge_kind = document.getElementById("chat-ambient-kind").value;
    try {
      const resp = await writeCall(`/conversations/${activeConversationId}/ambient/nudge`, {
        instance_id, operator_id, nudge_kind,
        max_response_tokens: 200,
        history_limit: 20,
      });
      dialog.hidden = true;
      toast({
        title: "Nudge sent",
        msg: `quota ${resp.quota_used}/${resp.quota_max} (rate=${resp.rate})`,
        kind: "info",
        ttl: 4000,
      });
      await selectRoom(activeConversationId);
    } catch (e) {
      const status = e?.status;
      if (status === 403) {
        toast({ title: "Not opted in", msg: "Set interaction_modes.ambient_opt_in=true in this agent's constitution.", kind: "warn", ttl: 8000 });
      } else if (status === 429) {
        toast({ title: "Quota exhausted", msg: e.detail?.detail || "Wait until tomorrow or raise FSF_AMBIENT_RATE.", kind: "warn", ttl: 8000 });
      } else {
        toast({ title: "Nudge failed", msg: String(e.message || e), kind: "error", ttl: 6000 });
      }
    }
  });
}


// ---------------------------------------------------------------------------
// Y7 — Retention sweep dialog
// ---------------------------------------------------------------------------
function wireSweepDialog() {
  const dialog = document.getElementById("chat-sweep-dialog");
  const statusEl = document.getElementById("chat-sweep-status");
  const resultsEl = document.getElementById("chat-sweep-results");
  const runBtn = document.getElementById("chat-sweep-run");
  let lastDryRunHadCandidates = false;

  document.getElementById("chat-room-sweep")?.addEventListener("click", () => {
    statusEl.textContent = "Click Dry-run to preview candidates.";
    resultsEl.innerHTML = "";
    runBtn.disabled = true;
    lastDryRunHadCandidates = false;
    dialog.hidden = false;
  });
  document.getElementById("chat-sweep-close")?.addEventListener("click", () => { dialog.hidden = true; });

  async function runSweep(dry_run) {
    statusEl.textContent = dry_run ? "Running dry-run…" : "Running sweep + summarizing…";
    resultsEl.innerHTML = "";
    runBtn.disabled = true;
    try {
      const resp = await writeCall(`/admin/conversations/sweep_retention`, {
        limit: 20,
        dry_run,
        summary_max_tokens: 200,
      });
      statusEl.innerHTML = `<strong>candidates:</strong> ${resp.candidates}  ·  <strong>summarized:</strong> ${resp.summarized}  ·  <strong>skipped:</strong> ${resp.skipped}  ·  <strong>failed:</strong> ${resp.failed}  ${resp.dry_run ? "<em>(dry-run)</em>" : ""}`;
      if (!resp.entries.length) {
        resultsEl.innerHTML = `<em>No candidates. Sweep is up to date.</em>`;
      } else {
        const lines = [];
        for (const e of resp.entries) {
          const cidShort = e.conversation_id.slice(0, 8);
          const tidShort = e.turn_id.slice(0, 8);
          const tag = `<span style="display:inline-block; padding:1px 6px; border-radius:3px; background: var(--bg-soft, #161c25);">${escapeHTML(e.status)}</span>`;
          let line = `${tag}  conv=${cidShort}…  turn=${tidShort}…  age=${e.age_days}d`;
          if (e.summary) line += `<br><em style="color:var(--fg-faint);">${escapeHTML(e.summary.slice(0, 120))}${e.summary.length > 120 ? "…" : ""}</em>`;
          if (e.error) line += `<br><span style="color:#d44;">${escapeHTML(e.error)}</span>`;
          lines.push(`<div style="padding: 4px 0; border-bottom: 1px dashed var(--border, #2a3340);">${line}</div>`);
        }
        resultsEl.innerHTML = lines.join("");
      }
      lastDryRunHadCandidates = (dry_run && resp.candidates > 0);
      runBtn.disabled = !lastDryRunHadCandidates;
      if (!dry_run) {
        // After a real sweep, reload the active conversation so summarized
        // turns surface in the room view.
        if (activeConversationId) {
          await selectRoom(activeConversationId);
        }
      }
    } catch (e) {
      statusEl.innerHTML = `<span style="color:#d44;">Sweep failed: ${escapeHTML(String(e.message || e))}</span>`;
      runBtn.disabled = true;
    }
  }

  document.getElementById("chat-sweep-dryrun")?.addEventListener("click", () => runSweep(true));
  runBtn?.addEventListener("click", () => runSweep(false));
}


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
async function refreshAgentLookup(instanceIds) {
  // Use state.get("agents") when populated; fall back to /agents/{id} for any miss.
  const cachedAgents = state.get("agents");
  if (cachedAgents?.length) {
    for (const a of cachedAgents) agentLookupCache.set(a.instance_id, a);
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
