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
// ADR-0047 T2 (B154): operator's bound assistant agent instance_id.
// Stored after first-use birth so subsequent loads jump straight to
// the assistant chat (T3) instead of re-prompting birth. Operator
// can clear via the "reset assistant binding" button — that removes
// the binding but does NOT archive the agent (archival is operator-
// driven from the Agents tab, per Forest's audit-chain principle).
const ASSISTANT_INSTANCE_KEY = "fsf.chat.assistantInstanceId";
// ADR-0047 T3 (B155): the persistent conversation_id this operator's
// assistant uses. Cached locally so we don't re-list /conversations on
// every Assistant-mode entry. Authoritative source of truth is still
// the daemon — when missing or stale, T3 re-discovers via /conversations
// ?domain=assistant&operator_id=<op> and creates one if absent.
const ASSISTANT_CONV_KEY = "fsf.chat.assistantConvId";
// ADR-0047 T3: conventional operator_id for the assistant conversation.
// Single-operator scale today; one row per operator by convention. The
// frontend uses the same default everywhere ("alex", consistent with
// chat-rooms operator default).
const ASSISTANT_OPERATOR_ID = "alex";

// T3 in-memory state: the current assistant conversation + its turns.
// Kept separate from the multi-agent rooms state (activeConversation /
// activeTurns) so switching modes doesn't trample either side.
let assistantConvId    = null;
let assistantTurns     = [];
let assistantAgentName = null;  // populated from agentLookupCache after participant resolve
let assistantLoading   = false; // guard against re-entrant load calls

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
  wireAssistantBirthFlow();   // ADR-0047 T2 (B154)
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
  // ADR-0056 E4 (B190) — three modes now: rooms, assistant, cycles.
  const VALID_MODES = ["rooms", "assistant", "cycles"];
  const initialMode = VALID_MODES.includes(stored) ? stored : "rooms";
  showChatMode(initialMode);

  roomsBtn.addEventListener("click", () => {
    showChatMode("rooms");
    localStorage.setItem(CHAT_MODE_KEY, "rooms");
  });
  assistantBtn.addEventListener("click", () => {
    showChatMode("assistant");
    localStorage.setItem(CHAT_MODE_KEY, "assistant");
  });
  const cyclesBtn = document.getElementById("chat-mode-cycles");
  if (cyclesBtn) {
    cyclesBtn.addEventListener("click", () => {
      showChatMode("cycles");
      localStorage.setItem(CHAT_MODE_KEY, "cycles");
    });
  }
}

function showChatMode(mode) {
  const roomsPane = document.getElementById("chat-pane-rooms");
  const assistantPane = document.getElementById("chat-pane-assistant");
  const cyclesPane = document.getElementById("chat-pane-cycles");
  const roomsBtn = document.getElementById("chat-mode-rooms");
  const assistantBtn = document.getElementById("chat-mode-assistant");
  const cyclesBtn = document.getElementById("chat-mode-cycles");
  const isAssistant = mode === "assistant";
  const isCycles = mode === "cycles";
  const isRooms = !isAssistant && !isCycles;  // default
  if (roomsPane) roomsPane.hidden = !isRooms;
  if (assistantPane) assistantPane.hidden = !isAssistant;
  if (cyclesPane) cyclesPane.hidden = !isCycles;
  if (roomsBtn) roomsBtn.classList.toggle("chat-mode-btn--active", isRooms);
  if (assistantBtn) assistantBtn.classList.toggle("chat-mode-btn--active", isAssistant);
  if (cyclesBtn) cyclesBtn.classList.toggle("chat-mode-btn--active", isCycles);
  // T2 (B154): when entering assistant mode, refresh which pane (birth /
  // ready) to show based on whether an assistant instance is bound.
  if (isAssistant) refreshAssistantPane();
  // ADR-0056 E4: when entering cycles mode, fetch fresh cycle list.
  if (isCycles) refreshCyclesPane();
}

// ---------------------------------------------------------------------------
// ADR-0047 T2 (B154) — Assistant birth flow + bound-instance state
// ADR-0047 T3 (B155) — Auto-conversation init + chat surface
// ---------------------------------------------------------------------------
// Two-state machine inside the assistant pane:
//
//   [no assistant bound]  →  birth prompt (name input, genre locked, button)
//                              ↓ on click → POST /birth → store instance_id
//   [assistant bound]     →  chat surface (auto-resolved conversation +
//                              turn history + composer, T3)
//                              ↑ on reset → drop binding, return to birth prompt
//
// T3 conversation resolution (cheap path on every entry):
//   1. Check ASSISTANT_CONV_KEY in localStorage. If present → GET it; if
//      404 (operator wiped it from the daemon side), fall through to step 2.
//   2. List /conversations?domain=assistant&operator_id=<op>; pick the
//      first non-archived row whose participants include the bound
//      instance_id. Cache its id in localStorage.
//   3. Otherwise create a new one: POST /conversations (domain=assistant,
//      retention_policy=full_indefinite), then POST .../participants with
//      the bound instance_id. Cache.
//
// All steps tolerate transient errors and surface the failure inline in
// the surface header — they don't silently fall back to "no assistant"
// (that would be confusing, since the binding is real).

function refreshAssistantPane() {
  const status = document.getElementById("chat-assistant-status");
  const birthSection = document.getElementById("chat-assistant-birth");
  const readySection = document.getElementById("chat-assistant-ready");
  if (!birthSection || !readySection) return;

  const instanceId = localStorage.getItem(ASSISTANT_INSTANCE_KEY) || "";
  if (instanceId) {
    birthSection.hidden = true;
    readySection.hidden = false;
    if (status) status.textContent = `bound: ${instanceId.slice(0, 8)}…`;
    const idEl = document.getElementById("chat-assistant-instance-id");
    if (idEl) idEl.textContent = instanceId;
    // T3: kick off conversation resolution + render. The function is
    // idempotent + guarded against re-entrancy, so toggling Assistant
    // ↔ Rooms repeatedly is fine.
    loadAssistantConversation(instanceId).catch((e) => {
      const turns = document.getElementById("chat-assistant-turns");
      if (turns) {
        turns.innerHTML = `<p class="muted">Couldn't load assistant conversation: ${escapeHTML(String(e.message || e))}</p>`;
      }
    });
  } else {
    birthSection.hidden = false;
    readySection.hidden = true;
    if (status) status.textContent = "no assistant bound";
  }
}

function wireAssistantBirthFlow() {
  // Birth button.
  const birthBtn = document.getElementById("chat-assistant-birth-btn");
  if (birthBtn) {
    birthBtn.addEventListener("click", async () => {
      const nameEl = document.getElementById("chat-assistant-name");
      const feedback = document.getElementById("chat-assistant-birth-feedback");
      const name = (nameEl?.value || "").trim();
      if (!name) {
        if (feedback) feedback.textContent = "Name required.";
        return;
      }
      birthBtn.disabled = true;
      if (feedback) feedback.textContent = "Birthing…";
      try {
        // ADR-0047 Decision 2 (B156): dedicated `assistant` role.
        // Defined in config/trait_tree.yaml + constitution_templates.yaml
        // + tool_catalog.yaml + claimed by companion-genre in genres.yaml.
        // Inherits Companion-genre risk floor (read_only + local providers
        // + private memory ceiling); computer-control capabilities (ADR-0048)
        // layer on top via per-(agent, plugin) grants.
        const conv = await writeCall("/birth", {
          profile: {
            role: "assistant",
            trait_values: {},
            domain_weight_overrides: {},
          },
          agent_name: name,
          agent_version: "v1",
          enrich_narrative: false,
        });
        if (!conv?.instance_id) {
          throw new Error("birth response missing instance_id");
        }
        localStorage.setItem(ASSISTANT_INSTANCE_KEY, conv.instance_id);
        toast({
          title: "Assistant born",
          msg: `${name} (${conv.instance_id.slice(0, 8)}…)`,
          kind: "info", ttl: 4000,
        });
        // Clear the name field so the prompt is clean if operator resets.
        if (nameEl) nameEl.value = "";
        refreshAssistantPane();
      } catch (e) {
        if (feedback) feedback.textContent = `birth failed: ${String(e.message || e).slice(0, 200)}`;
        toast({
          title: "Birth failed",
          msg: String(e.message || e),
          kind: "error", ttl: 8000,
        });
      } finally {
        birthBtn.disabled = false;
      }
    });
  }

  // Reset button.
  const resetBtn = document.getElementById("chat-assistant-reset-btn");
  if (resetBtn) {
    resetBtn.addEventListener("click", () => {
      if (!confirm(
        "Forget this assistant binding?\n\nThe agent itself stays in your registry " +
        "(archive it from the Agents tab if you want it gone). This only clears the " +
        "frontend's localStorage pointer, returning the assistant pane to the birth prompt."
      )) return;
      localStorage.removeItem(ASSISTANT_INSTANCE_KEY);
      // T3: also clear the cached conversation pointer. The conversation
      // row + turns persist in the daemon — the audit chain has every turn
      // — but the frontend forgets WHICH conversation belongs to this
      // (now-defunct) binding. Re-binding triggers fresh discovery.
      localStorage.removeItem(ASSISTANT_CONV_KEY);
      assistantConvId = null;
      assistantTurns  = [];
      toast({
        title: "Binding cleared", msg: "agent itself preserved",
        kind: "info", ttl: 3000,
      });
      refreshAssistantPane();
    });
  }

  // T3 (B155): composer. Cmd/Ctrl+Enter sends; click sends; Enter alone
  // inserts newline (keeps the multi-line affordance, matching the
  // multi-agent rooms composer convention).
  const sendBtn = document.getElementById("chat-assistant-send");
  if (sendBtn) sendBtn.addEventListener("click", () => sendAssistantTurn());
  const input = document.getElementById("chat-assistant-input");
  if (input) {
    input.addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        sendAssistantTurn();
      }
    });
  }
}

// ---------------------------------------------------------------------------
// ADR-0047 T3 (B155) — Auto-conversation init + chat surface
// ---------------------------------------------------------------------------

async function loadAssistantConversation(instanceId) {
  if (assistantLoading) return;       // re-entrancy guard
  assistantLoading = true;
  try {
    const turnsEl = document.getElementById("chat-assistant-turns");
    const convIdEl = document.getElementById("chat-assistant-conv-id");
    if (turnsEl && !assistantConvId) {
      // Only paint the loading placeholder on first entry — subsequent
      // refreshes preserve the existing render so the operator doesn't
      // see flicker.
      turnsEl.innerHTML = `<p class="muted">Resolving conversation…</p>`;
    }

    // ----- Step 1: try cached conv id -------------------------------------
    let convId = localStorage.getItem(ASSISTANT_CONV_KEY) || "";
    if (convId) {
      try {
        await api.get(`/conversations/${convId}`);
      } catch (_) {
        // Cached id is stale (404 / archived from another path). Drop
        // the cache and fall through to discovery.
        convId = "";
        localStorage.removeItem(ASSISTANT_CONV_KEY);
      }
    }

    // ----- Step 2: discover an existing assistant conversation ------------
    if (!convId) {
      const listResp = await api.get(
        `/conversations?domain=assistant&operator_id=${encodeURIComponent(ASSISTANT_OPERATOR_ID)}&limit=50`
      );
      const candidates = (listResp.conversations || [])
        .filter((c) => c.status !== "archived");
      // Pick the first whose participants include the bound instance_id.
      // Most operators will have at most one — convention, not enforcement,
      // per ADR-0047 Decision 2.
      for (const c of candidates) {
        try {
          const parts = await api.get(`/conversations/${c.conversation_id}/participants`);
          const ids = (parts.participants || []).map((p) => p.instance_id);
          if (ids.includes(instanceId)) {
            convId = c.conversation_id;
            break;
          }
        } catch (_) { /* skip; continue search */ }
      }
    }

    // ----- Step 3: create one if nothing matched --------------------------
    if (!convId) {
      const created = await writeCall("/conversations", {
        domain: "assistant",
        operator_id: ASSISTANT_OPERATOR_ID,
        retention_policy: "full_indefinite",
      });
      convId = created.conversation_id;
      // Add the bound assistant as participant. Idempotent on
      // (conv, instance_id) — the daemon enforces single-row.
      await writeCall(`/conversations/${convId}/participants`, {
        instance_id: instanceId,
      });
      toast({
        title: "Assistant conversation created",
        msg: `domain=assistant, retention=full_indefinite`,
        kind: "info", ttl: 4000,
      });
    }

    // Cache for next entry.
    localStorage.setItem(ASSISTANT_CONV_KEY, convId);
    assistantConvId = convId;
    if (convIdEl) convIdEl.textContent = `conv: ${convId.slice(0, 8)}…`;

    // Resolve participant agent name for nicer turn labels.
    try {
      const parts = await api.get(`/conversations/${convId}/participants`);
      const ids = (parts.participants || []).map((p) => p.instance_id);
      await refreshAgentLookup(ids);
      const agent = agentLookupCache.get(instanceId);
      assistantAgentName = agent?.agent_name || null;
    } catch (_) { /* non-fatal — turns still render with instance_id */ }

    // Load + render turns + settings panel content. Settings load
    // is best-effort (non-fatal on per-card error) so the chat
    // surface is never blocked on a settings-card failure.
    await loadAssistantTurns();
    loadAssistantSettings(instanceId).catch(() => {});
  } finally {
    assistantLoading = false;
  }
}

// ---------------------------------------------------------------------------
// ADR-0047 T4 (B158, partial) — Settings panel.
// ---------------------------------------------------------------------------
// Three sub-cards ship against existing kernel substrate; the fourth
// (allowances) stubs until ADR-0048 implementation tranches land.
//
//   1. Identity card  — GET /agents/{id}
//   2. Posture dial   — GET/POST /agents/{id}/posture (ADR-0045)
//   3. Memory consents — GET /agents/{id}/memory/consents (ADR-0027)
//   4. Allowances     — placeholder copy (ADR-0048 pending)
//
// All loads are best-effort: a per-card fetch failure renders an
// inline error in that card without blocking the others. The panel
// is collapsed by default (operator opens via the <summary>); state
// persists in localStorage so each entry into Assistant mode shows
// the same posture/identity at a glance.

const ASSISTANT_SETTINGS_OPEN_KEY = "fsf.chat.assistantSettingsOpen";

async function loadAssistantSettings(instanceId) {
  if (!instanceId) return;

  // Restore the operator's last-seen open/closed preference for the
  // settings <details> block.
  const det = document.getElementById("chat-assistant-settings");
  if (det) {
    det.open = localStorage.getItem(ASSISTANT_SETTINGS_OPEN_KEY) === "true";
    if (!det.dataset.wired) {
      det.addEventListener("toggle", () => {
        localStorage.setItem(ASSISTANT_SETTINGS_OPEN_KEY, String(det.open));
      });
      det.dataset.wired = "1";
    }
  }

  // Five independent fetches; render each on completion.
  await Promise.allSettled([
    renderAssistantIdentity(instanceId),
    renderAssistantPosture(instanceId),
    renderAssistantConsents(instanceId),
    renderAssistantAllowances(instanceId),    // ADR-0048 T4 (B165)
    renderAssistantSecrets(),                 // ADR-0052 T6 (B173)
  ]);

  // Wire posture buttons + preset buttons (idempotent — only
  // attaches once per pane via dataset.wired).
  wireAssistantPostureButtons(instanceId);
  wireAssistantAllowanceButtons(instanceId);
}

async function renderAssistantIdentity(instanceId) {
  try {
    const a = await api.get(`/agents/${instanceId}`);
    const set = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = val ?? "—";
    };
    set("chat-asst-name", a.agent_name);
    set("chat-asst-role", a.role);
    // Genre is implicit from the role's claim in genres.yaml; we
    // keep the static "companion" copy in HTML — no per-row API.
    set("chat-asst-dna", a.dna ? `${a.dna.slice(0, 16)}…` : "—");
    set("chat-asst-cons-hash",
        a.constitution_hash ? `${a.constitution_hash.slice(0, 16)}…` : "—");
    set("chat-asst-created", a.created_at ? a.created_at.slice(0, 19).replace("T", " ") : "—");
  } catch (e) {
    const el = document.getElementById("chat-asst-name");
    if (el) el.textContent = `error: ${String(e.message || e).slice(0, 80)}`;
  }
}

async function renderAssistantPosture(instanceId) {
  const cur = document.getElementById("chat-assistant-posture-current");
  try {
    const r = await api.get(`/agents/${instanceId}/posture`);
    const posture = r.posture || "—";
    if (cur) cur.textContent = `current: ${posture}`;
    // Highlight the active posture button.
    document.querySelectorAll("#chat-assistant-posture [data-posture]").forEach((b) => {
      b.classList.toggle("chat-assistant-posture-btn--active", b.dataset.posture === posture);
    });
  } catch (e) {
    if (cur) cur.textContent = `error: ${String(e.message || e).slice(0, 80)}`;
  }
}

function wireAssistantPostureButtons(instanceId) {
  const row = document.getElementById("chat-assistant-posture");
  if (!row || row.dataset.wired === instanceId) return;
  row.dataset.wired = instanceId;
  row.querySelectorAll("[data-posture]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const next = btn.dataset.posture;
      if (!next) return;
      const reason = window.prompt(
        `Switching posture → ${next}. Reason for the audit chain (optional, max 200 chars):`,
        "",
      );
      // window.prompt returns null on cancel — abort then.
      if (reason === null) return;
      btn.disabled = true;
      try {
        await writeCall(`/agents/${instanceId}/posture`, {
          posture: next,
          reason: (reason || "").slice(0, 200) || null,
        });
        toast({
          title: `Posture: ${next}`,
          msg: "Audit event emitted; takes effect immediately.",
          kind: "info", ttl: 4000,
        });
        await renderAssistantPosture(instanceId);
      } catch (e) {
        toast({ title: "Posture change failed", msg: String(e.message || e), kind: "error", ttl: 8000 });
      } finally {
        btn.disabled = false;
      }
    });
  });
}

async function renderAssistantConsents(instanceId) {
  const list = document.getElementById("chat-assistant-consents");
  if (!list) return;
  try {
    const r = await api.get(`/agents/${instanceId}/memory/consents`);
    const all = r.consents || [];
    const active = all.filter((c) => !c.revoked_at);
    if (!active.length) {
      list.innerHTML = `<span class="muted">No active consent grants. The assistant's memory stays private to itself.</span>`;
      return;
    }
    const html = [];
    for (const c of active) {
      const recip = c.recipient_instance ? c.recipient_instance.slice(0, 8) + "…" : "—";
      html.push(`<div class="chat-assistant-consent-row">
        <code>${escapeHTML(c.entry_id.slice(0, 12))}…</code>
        → <code>${escapeHTML(recip)}</code>
        <span class="muted" style="margin-left: 8px;">${escapeHTML(c.granted_at.slice(0, 19).replace("T", " "))}</span>
      </div>`);
    }
    list.innerHTML = html.join("");
  } catch (e) {
    list.innerHTML = `<span class="muted">consents fetch failed: ${escapeHTML(String(e.message || e))}</span>`;
  }
}

// ---------------------------------------------------------------------------
// ADR-0048 T4 (B165) + ADR-0053 T5 (B240) — Allowance UI.
// ---------------------------------------------------------------------------
// Three presets map to plugin-grant operations against the
// /agents/{id}/plugin-grants endpoint (and the per-tool DELETE
// route added by ADR-0053 T3 / B238):
//
//   Restricted → DELETE the plugin-level grant + revoke ALL
//                per-tool grants. Clean state.
//   Specific   → revoke plugin-level + issue per-tool grants
//                for the two read_only tools (screenshot +
//                clipboard read) at yellow tier. Operator can
//                tune via Advanced checkboxes from there.
//   Full       → revoke ALL per-tool grants + POST a plugin-
//                level grant at green tier. The plugin-level
//                grant covers every tool the manifest declares.
//
// Per-tool granularity (ADR-0053 D5): each Advanced checkbox
// toggles a per-tool grant via POST or DELETE on the new
// `/tools/{tool_name}` path. Per-tool grants OVERRIDE the
// plugin-level grant for the named tool via ADR-0053 D3
// specificity-wins resolution: a per-tool yellow grant on top
// of a plugin-level green grant gates THAT one tool while
// leaving the others ungated.

const ALLOW_PLUGIN_NAME = "soulux-computer-control";

// The six tools the plugin declares. Keep in sync with
// examples/plugins/soulux-computer-control/plugin.yaml and the
// runtime tool catalog. read_only tools fire without approval;
// external/network tools require per-call approval at standard/
// yellow tier and ungate at green tier.
const ALLOW_TOOLS = [
  { name: "computer_screenshot.v1",     side_effects: "read_only", approval: "none"    },
  { name: "computer_read_clipboard.v1", side_effects: "read_only", approval: "none"    },
  { name: "computer_click.v1",          side_effects: "external",  approval: "per-call" },
  { name: "computer_type.v1",           side_effects: "external",  approval: "per-call" },
  { name: "computer_run_app.v1",        side_effects: "external",  approval: "per-call" },
  { name: "computer_launch_url.v1",     side_effects: "network",   approval: "per-call" },
];

// The "Specific" preset's seeded set — the two read-only tools.
// Operator can extend via Advanced toggles after applying.
const SPECIFIC_PRESET_TOOLS = [
  "computer_screenshot.v1",
  "computer_read_clipboard.v1",
];

function _allowStatusEl() {
  return document.getElementById("chat-assistant-allow-status");
}
function _allowFeedbackEl() {
  return document.getElementById("chat-assistant-allow-feedback");
}

async function renderAssistantAllowances(instanceId) {
  const status = _allowStatusEl();
  if (!status) return;
  try {
    const r = await api.get(`/agents/${instanceId}/plugin-grants`);
    const grants = r.grants || [];
    // ADR-0053 T2/T3: rows now distinguish plugin-level (tool_name
    // null) from per-tool (tool_name non-null).
    const sccPluginLevel = grants.find(
      (g) => g.plugin_name === ALLOW_PLUGIN_NAME && g.tool_name == null
        && g.is_active,
    );
    const sccPerTool = grants.filter(
      (g) => g.plugin_name === ALLOW_PLUGIN_NAME && g.tool_name != null
        && g.is_active,
    );

    // Preset resolution per ADR-0053 D5:
    //   Full       — plugin-level grant exists (covers all manifest tools).
    //   Specific   — no plugin-level grant, at least one per-tool grant active.
    //   Restricted — no grants at all.
    //   Mixed      — plugin-level + per-tool grants both exist. Not a
    //                normal preset but possible if the operator left a
    //                per-tool override on top of plugin-level. We show
    //                "Full" as the active preset (plugin-level is the
    //                dominant signal) and the per-tool rows show their
    //                override state in the Advanced table.
    let preset;
    if (sccPluginLevel) {
      preset = "full";
    } else if (sccPerTool.length > 0) {
      preset = "specific";
    } else {
      preset = "restricted";
    }

    // Status line summary.
    if (sccPluginLevel && sccPerTool.length > 0) {
      status.innerHTML = (
        `Plugin <code>${escapeHTML(ALLOW_PLUGIN_NAME)}</code> ` +
        `granted at tier <code>${escapeHTML(sccPluginLevel.trust_tier)}</code> ` +
        `with <strong>${sccPerTool.length}</strong> per-tool override` +
        `${sccPerTool.length === 1 ? "" : "s"}. Per-tool grants win for ` +
        `the tools they cover (specificity-wins resolution).`
      );
    } else if (sccPluginLevel) {
      status.innerHTML = (
        `Plugin <code>${escapeHTML(ALLOW_PLUGIN_NAME)}</code> ` +
        `granted (plugin-level, tier: <code>${escapeHTML(sccPluginLevel.trust_tier)}</code>). ` +
        `All ${ALLOW_TOOLS.length} tools available. Posture clamps still apply.`
      );
    } else if (sccPerTool.length > 0) {
      status.innerHTML = (
        `<strong>${sccPerTool.length}</strong> per-tool grant` +
        `${sccPerTool.length === 1 ? "" : "s"} active. No plugin-level grant. ` +
        `Only the granted tools fire; the rest stay refused.`
      );
    } else {
      status.innerHTML = (
        `Plugin <code>${escapeHTML(ALLOW_PLUGIN_NAME)}</code> ` +
        `<strong>not granted</strong>. The assistant cannot fire any ` +
        `computer-control tool — the kit stays at its constitutional baseline.`
      );
    }

    // Highlight the active preset button.
    document.querySelectorAll("[data-preset]").forEach((b) => {
      b.classList.toggle(
        "chat-assistant-posture-btn--active",
        b.dataset.preset === preset,
      );
    });

    // Render the per-tool toggle grid.
    renderPerToolGrid(instanceId, sccPluginLevel, sccPerTool);
  } catch (e) {
    status.textContent = `grant state fetch failed: ${String(e.message || e).slice(0, 100)}`;
  }
}

// ADR-0053 T5 (B240): per-tool toggle grid. Each row is a checkbox
// wired to POST a per-tool grant (checked) or DELETE one (unchecked).
// Checkbox state reflects the EFFECTIVE coverage:
//   - per-tool grant exists for this tool → checked (override active)
//   - no per-tool grant, plugin-level exists → checked (covered by
//     plugin-level)
//   - neither → unchecked (no grant covers this tool)
// The dispatcher's specificity-wins resolver (T4, B239) applies the
// per-tool tier when one exists, else falls back to plugin-level —
// the table mirrors that semantic.
function renderPerToolGrid(instanceId, pluginLevelGrant, perToolGrants) {
  const tbody = document.getElementById("chat-assistant-allow-tools");
  if (!tbody) return;
  const perToolByName = new Map(perToolGrants.map((g) => [g.tool_name, g]));

  const rows = ALLOW_TOOLS.map((t) => {
    const perTool = perToolByName.get(t.name);
    const covered = perTool != null || pluginLevelGrant != null;
    const coverageNote = perTool
      ? `<span class="muted" style="font-size: 0.85em;">(per-tool ${escapeHTML(perTool.trust_tier)})</span>`
      : (pluginLevelGrant
          ? `<span class="muted" style="font-size: 0.85em;">(via plugin-level)</span>`
          : "");
    const cb = (
      `<input type="checkbox" ` +
      `data-tool="${escapeHTML(t.name)}" ` +
      `data-has-per-tool="${perTool != null ? "1" : "0"}" ` +
      (covered ? "checked " : "") +
      `aria-label="grant ${escapeHTML(t.name)}">`
    );
    return (
      `<tr>` +
        `<td>${cb} ${coverageNote}</td>` +
        `<td><code>${escapeHTML(t.name)}</code></td>` +
        `<td>${escapeHTML(t.side_effects)}</td>` +
        `<td>${escapeHTML(t.approval)}</td>` +
      `</tr>`
    );
  }).join("");
  tbody.innerHTML = rows;
  wirePerToolCheckboxes(instanceId);
}

// Per-tool checkbox handler. Re-wires every render (idempotent —
// we replace tbody.innerHTML wholesale above so the listeners
// don't survive between renders anyway).
function wirePerToolCheckboxes(instanceId) {
  const tbody = document.getElementById("chat-assistant-allow-tools");
  if (!tbody) return;
  tbody.querySelectorAll('input[type="checkbox"][data-tool]').forEach((cb) => {
    cb.addEventListener("change", async () => {
      const tool = cb.dataset.tool;
      const hadPerTool = cb.dataset.hasPerTool === "1";
      const wantsGranted = cb.checked;
      const fb = _allowFeedbackEl();
      cb.disabled = true;
      try {
        if (wantsGranted && !hadPerTool) {
          // Operator wants this tool ON, no per-tool grant exists
          // yet. Issue one at yellow tier (cautious default; matches
          // the existing Specific preset semantic).
          await writeCall(`/agents/${instanceId}/plugin-grants`, {
            plugin_name: ALLOW_PLUGIN_NAME,
            tool_name:   tool,
            trust_tier:  "yellow",
            reason:      "operator toggled on in Advanced disclosure",
          });
          if (fb) fb.textContent = `per-tool grant issued for ${tool}`;
        } else if (!wantsGranted && hadPerTool) {
          // Operator unchecking a tool that had its own per-tool
          // grant. Revoke just that one.
          await api.del(
            `/agents/${instanceId}/plugin-grants/${ALLOW_PLUGIN_NAME}/tools/${encodeURIComponent(tool)}`,
          );
          if (fb) fb.textContent = `per-tool grant revoked for ${tool}`;
        } else if (!wantsGranted && !hadPerTool) {
          // Operator unchecking a tool that was covered by the
          // plugin-level grant (no per-tool row to delete). Issue
          // a per-tool grant at RED so the plugin-level grant gets
          // overridden TO refused for this one tool via specificity-
          // wins. This is "let everything else through but block
          // this specific tool" — the use case ADR-0053 D2 calls out.
          await writeCall(`/agents/${instanceId}/plugin-grants`, {
            plugin_name: ALLOW_PLUGIN_NAME,
            tool_name:   tool,
            trust_tier:  "red",
            reason:      "operator carved out per-tool denial against plugin-level grant",
          });
          if (fb) fb.textContent = `per-tool denial recorded for ${tool} (red tier)`;
        }
        // The remaining case (wantsGranted && hadPerTool) is a no-op:
        // the box was already checked because of its per-tool row;
        // re-clicking-to-check would be unusual. Just refresh.
        await renderAssistantAllowances(instanceId);
      } catch (e) {
        if (fb) fb.textContent = `per-tool toggle failed: ${String(e.message || e).slice(0, 100)}`;
        cb.checked = !cb.checked; // revert
      } finally {
        cb.disabled = false;
      }
    });
  });
}

function wireAssistantAllowanceButtons(instanceId) {
  const row = document.querySelector(".chat-assistant-preset-row");
  if (!row || row.dataset.wired === instanceId) return;
  row.dataset.wired = instanceId;
  row.querySelectorAll("[data-preset]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const preset = btn.dataset.preset;
      if (!preset) return;
      const fb = _allowFeedbackEl();
      if (fb) fb.textContent = "applying…";
      btn.disabled = true;
      try {
        await applyAssistantAllowancePreset(instanceId, preset);
        if (fb) fb.textContent = `preset applied: ${preset}`;
        toast({
          title: `Allowance: ${preset}`,
          msg: (preset === "restricted")
            ? "Plugin grant revoked. Assistant cannot use computer-control tools."
            : "Plugin granted. Read tools fire freely; action tools require per-call approval.",
          kind: "info", ttl: 4000,
        });
        await renderAssistantAllowances(instanceId);
      } catch (e) {
        if (fb) fb.textContent = `apply failed: ${String(e.message || e).slice(0, 100)}`;
        toast({
          title: "Allowance change failed",
          msg: String(e.message || e),
          kind: "error", ttl: 8000,
        });
      } finally {
        btn.disabled = false;
      }
    });
  });
}

// ---------------------------------------------------------------------------
// ADR-0052 T6 (B173) — Plugin-secrets card.
// ---------------------------------------------------------------------------
// Read-only view of the active backend + secret name list. Values
// NEVER traverse the HTTP surface; the daemon's GET /secrets/names
// returns names only. Mutation stays CLI-only per the ADR-0052
// design (`fsf secret put|delete`). The chat tab shouldn't be a
// destructive surface for credentials — operators removing a
// secret should do it through a deliberate terminal action, not a
// browser click.

async function renderAssistantSecrets() {
  const backendEl = document.getElementById("chat-assistant-secrets-backend");
  const namesEl = document.getElementById("chat-assistant-secrets-names");
  if (!backendEl || !namesEl) return;
  // Two independent fetches — let one fail without taking the
  // other down.
  try {
    const r = await api.get("/secrets/backend");
    backendEl.innerHTML = (
      `Active backend: <code>${escapeHTML(r.name)}</code> ` +
      `<span class="muted" style="font-size: 0.85em;">` +
      `(${escapeHTML(r.selection_source)} — ${escapeHTML(r.selection_via)})</span>`
    );
  } catch (e) {
    backendEl.textContent = `backend fetch failed: ${String(e.message || e).slice(0, 100)}`;
  }
  try {
    const r = await api.get("/secrets/names");
    const names = r.names || [];
    if (!names.length) {
      namesEl.innerHTML = (
        `<span class="muted">No secrets stored. Plugins requiring ` +
        `auth tokens will fail launch with an actionable error ` +
        `pointing at <code>fsf secret put &lt;name&gt;</code>.</span>`
      );
      return;
    }
    const html = [`<div class="muted" style="margin-bottom: 4px; font-size: 0.85em;">${names.length} stored:</div>`];
    html.push(`<ul class="chat-assistant-secrets-list">`);
    for (const n of names) {
      html.push(`<li><code>${escapeHTML(n)}</code></li>`);
    }
    html.push(`</ul>`);
    namesEl.innerHTML = html.join("");
  } catch (e) {
    namesEl.innerHTML = `<span class="muted">names fetch failed: ${escapeHTML(String(e.message || e))}</span>`;
  }
}

async function applyAssistantAllowancePreset(instanceId, preset) {
  // ADR-0053 D5: presets compose plugin-level + per-tool grants.
  // Always start by clearing existing per-tool rows for this plugin
  // so a preset switch is a clean state transition rather than an
  // additive layering. The plugin-level grant gets its own
  // dispositions per branch below.
  const current = await api.get(`/agents/${instanceId}/plugin-grants`);
  const existingPerTool = (current.grants || []).filter(
    (g) => g.plugin_name === ALLOW_PLUGIN_NAME && g.tool_name != null
      && g.is_active,
  );

  async function clearAllPerTool() {
    // Revoke every active per-tool grant on this plugin.
    // 404s shouldn't happen because we just listed them, but treat
    // as idempotent in case of a race.
    for (const g of existingPerTool) {
      try {
        await api.del(
          `/agents/${instanceId}/plugin-grants/${ALLOW_PLUGIN_NAME}/tools/${encodeURIComponent(g.tool_name)}`,
        );
      } catch (e) {
        if (e?.status !== 404) throw e;
      }
    }
  }

  if (preset === "restricted") {
    // Clean state: no plugin-level + no per-tool.
    await clearAllPerTool();
    try {
      await api.del(`/agents/${instanceId}/plugin-grants/${ALLOW_PLUGIN_NAME}`);
    } catch (e) {
      if (e?.status !== 404) throw e;
    }
    return;
  }

  if (preset === "full") {
    // Plugin-level grant at green covers everything. Strip any
    // conflicting per-tool overrides so the operator's intent
    // ("full access") isn't accidentally narrowed.
    await clearAllPerTool();
    await writeCall(`/agents/${instanceId}/plugin-grants`, {
      plugin_name: ALLOW_PLUGIN_NAME,
      trust_tier:  "green",
      reason:      "operator selected full preset",
    });
    return;
  }

  // Specific: no plugin-level grant; seed per-tool grants for the
  // SPECIFIC_PRESET_TOOLS list (the two read_only tools). Operator
  // can extend via Advanced checkboxes from there.
  try {
    await api.del(`/agents/${instanceId}/plugin-grants/${ALLOW_PLUGIN_NAME}`);
  } catch (e) {
    if (e?.status !== 404) throw e;
  }
  // Clear stale per-tool rows from a previous preset, then issue
  // the seeded set fresh.
  await clearAllPerTool();
  for (const toolName of SPECIFIC_PRESET_TOOLS) {
    await writeCall(`/agents/${instanceId}/plugin-grants`, {
      plugin_name: ALLOW_PLUGIN_NAME,
      tool_name:   toolName,
      trust_tier:  "yellow",
      reason:      "operator selected specific preset (seeded read-only tools)",
    });
  }
}

async function loadAssistantTurns() {
  if (!assistantConvId) return;
  const resp = await api.get(`/conversations/${assistantConvId}/turns?limit=200`);
  assistantTurns = resp.turns || [];
  renderAssistantTurns();
  // ADR-0054 T5b (B195) — after turns render, check whether the
  // most recent agent turn was a shortcut substitution. If so,
  // surface the reinforcement widget.
  refreshAssistantShortcutWidget();
}

// ADR-0054 T5b (B195) — last-shortcut reinforcement widget on
// the Assistant pane.
//
// Detection: Sage's most recent turn has `model_used === 'shortcut'`
// when the dispatcher substituted via ProceduralShortcutStep
// (the synthetic ToolResult sets model: 'shortcut' per
// dispatcher._shortcut_substitute). We additionally fetch
// /conversations/{id}/last-shortcut to get the shortcut_id +
// similarity for the reinforcement dispatch.
//
// Click thumbs → dispatch memory_tag_outcome.v1 against the
// agent that owned the shortcut (instance_id from the audit
// event). good = strengthen (success+1), bad = weaken
// (failure+1), neutral = no counter change but audit-visible.
async function refreshAssistantShortcutWidget() {
  const widget = document.getElementById("chat-assistant-shortcut");
  if (!widget) return;

  // Hide by default; show only on a fresh shortcut hit.
  widget.hidden = true;
  widget.innerHTML = "";

  if (!assistantConvId || !assistantTurns.length) return;

  // Find the most recent agent turn (model_used could be
  // 'shortcut' or anything else; we only care if it's
  // 'shortcut').
  const instanceId = localStorage.getItem(ASSISTANT_INSTANCE_KEY) || "";
  let lastAgentTurn = null;
  for (let i = assistantTurns.length - 1; i >= 0; i--) {
    if (assistantTurns[i].speaker === instanceId) {
      lastAgentTurn = assistantTurns[i];
      break;
    }
  }
  if (!lastAgentTurn || lastAgentTurn.model_used !== "shortcut") return;

  // Fetch the shortcut metadata.
  let shortcut;
  try {
    shortcut = await api.get(`/conversations/${assistantConvId}/last-shortcut`);
  } catch (e) {
    // 404 is normal pre-T6 daemons or before any shortcut has
    // ever fired; just don't render the widget.
    return;
  }

  const sid = shortcut.shortcut_id || "?";
  const sim = shortcut.shortcut_similarity != null
    ? Number(shortcut.shortcut_similarity).toFixed(3)
    : "?";
  const ownerInstance = shortcut.instance_id || instanceId;

  widget.innerHTML = `
    <div class="chat-shortcut-widget__head muted">
      Last response was a recorded pattern
      <code>${escapeHTML(sid)}</code> (cosine ${escapeHTML(sim)}).
      Reinforce so future matches grow stronger / weaker.
    </div>
    <div class="chat-shortcut-widget__buttons">
      <button class="btn btn--sm chat-shortcut-btn"
              data-outcome="good"
              data-shortcut-id="${escapeHTML(sid)}"
              data-instance-id="${escapeHTML(ownerInstance)}"
              type="button">good (success +1)</button>
      <button class="btn btn--sm btn--ghost chat-shortcut-btn"
              data-outcome="neutral"
              data-shortcut-id="${escapeHTML(sid)}"
              data-instance-id="${escapeHTML(ownerInstance)}"
              type="button">neutral (audit only)</button>
      <button class="btn btn--sm btn--ghost chat-shortcut-btn"
              data-outcome="bad"
              data-shortcut-id="${escapeHTML(sid)}"
              data-instance-id="${escapeHTML(ownerInstance)}"
              type="button">bad (failure +1)</button>
    </div>
    <div class="chat-shortcut-widget__status muted" id="chat-shortcut-status"></div>
  `;
  widget.hidden = false;

  widget.querySelectorAll(".chat-shortcut-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => _onShortcutTag(e.currentTarget));
  });
}

async function _onShortcutTag(btn) {
  const outcome = btn.dataset.outcome;
  const shortcutId = btn.dataset.shortcutId;
  const instanceId = btn.dataset.instanceId;
  const status = document.getElementById("chat-shortcut-status");
  if (!outcome || !shortcutId || !instanceId) return;

  // Disable buttons while the dispatch is in flight.
  document.querySelectorAll(".chat-shortcut-btn").forEach((b) => b.disabled = true);
  if (status) status.textContent = `tagging ${outcome}…`;

  try {
    const resp = await writeCall(
      `/agents/${encodeURIComponent(instanceId)}/tools/call`,
      {
        tool_name: "memory_tag_outcome",
        tool_version: "1",
        session_id: `chat-tab-thumbs-${Date.now()}`,
        args: { shortcut_id: shortcutId, outcome },
      },
    );
    if (resp.status === "succeeded") {
      const out = (resp.result && resp.result.output) || {};
      if (status) {
        status.innerHTML = (
          `<strong style="color: rgba(72,187,120,1);">tagged ${escapeHTML(outcome)}</strong> — ` +
          `success=${out.new_success_count ?? "?"} failure=${out.new_failure_count ?? "?"} ` +
          `score=${out.new_reinforcement_score ?? "?"}` +
          (out.soft_deleted ? ` <strong>· soft-deleted</strong>` : "")
        );
      }
    } else if (resp.status === "pending_approval") {
      if (status) {
        status.innerHTML = `queued for approval (ticket ${escapeHTML(resp.ticket_id || "?")})`;
      }
    } else {
      if (status) status.textContent = `dispatch ${resp.status}`;
    }
  } catch (e) {
    if (status) {
      status.innerHTML = `<strong style="color: rgba(220,80,80,1);">error</strong> — ${escapeHTML(String(e.message || e))}`;
    }
    document.querySelectorAll(".chat-shortcut-btn").forEach((b) => b.disabled = false);
  }
}

function renderAssistantTurns() {
  const list = document.getElementById("chat-assistant-turns");
  if (!list) return;
  if (!assistantTurns.length) {
    list.innerHTML = `<p class="muted">No turns yet. Send the first message below to start the conversation.</p>`;
    return;
  }
  const instanceId = localStorage.getItem(ASSISTANT_INSTANCE_KEY) || "";
  const html = [];
  for (const t of assistantTurns) {
    const isAgent = t.speaker === instanceId;
    const speakerLabel = isAgent
      ? (assistantAgentName || `${instanceId.slice(0, 8)}…`)
      : (t.speaker || "operator");
    const ts = t.timestamp ? t.timestamp.slice(11, 19) : "—";
    const bodyEsc = t.body
      ? escapeHTML(t.body)
      : (t.summary
          ? escapeHTML(`[summarized] ${t.summary}`)
          : `<em class="muted">[content purged]</em>`);
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
  list.scrollTop = list.scrollHeight;
}

async function sendAssistantTurn() {
  if (!assistantConvId) {
    toast({ title: "No conversation yet", msg: "Wait for the assistant pane to finish loading.", kind: "warn", ttl: 4000 });
    return;
  }
  const input = document.getElementById("chat-assistant-input");
  const sendBtn = document.getElementById("chat-assistant-send");
  const feedback = document.getElementById("chat-assistant-feedback");
  if (!input || !sendBtn) return;
  const body = input.value.trim();
  if (!body) return;

  sendBtn.disabled = true;
  sendBtn.textContent = "thinking…";
  if (feedback) feedback.textContent = "";
  const startedAt = performance.now();
  try {
    const resp = await writeCall(`/conversations/${assistantConvId}/turns`, {
      speaker: ASSISTANT_OPERATOR_ID,
      body,
      auto_respond: true,
      history_limit: 30,
      max_chain_depth: 1,        // 1:1 conversation — no chains
      max_response_tokens: 600,
    });
    input.value = "";
    if (resp.agent_dispatch_failed) {
      toast({
        title: "Assistant didn't respond",
        msg: "Operator turn landed; check audit/tail or the daemon log for the failure reason.",
        kind: "warn", ttl: 8000,
      });
    }
    const elapsedSec = Math.round((performance.now() - startedAt) / 100) / 10;
    if (feedback && elapsedSec > 0) {
      feedback.textContent = `round-trip ${elapsedSec}s`;
    }
    await loadAssistantTurns();
  } catch (e) {
    toast({ title: "Send failed", msg: String(e.message || e), kind: "error", ttl: 8000 });
  } finally {
    sendBtn.disabled = false;
    sendBtn.textContent = "send";
  }
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


// ===========================================================================
// ADR-0056 E4 (B190) — Cycles pane: Smith's branch-isolated work review.
// ===========================================================================
// Reads cycles via GET /agents/{instance_id}/cycles. The instance_id of
// Smith is auto-resolved by querying /agents and finding the
// experimenter-role row. We could persist this in localStorage like the
// assistant pattern, but cycles is a less-frequent surface — re-resolving
// on each pane entry is cheap and avoids stale-key bugs when the
// experimenter is re-birthed.

const SMITH_ROLE = "experimenter";

async function _resolveSmithInstanceId() {
  try {
    const resp = await api.get("/agents?limit=200");
    const agents = resp.agents || [];
    const smith = agents.find((a) => a.role === SMITH_ROLE);
    return smith ? smith.instance_id : null;
  } catch (e) {
    return null;
  }
}

async function refreshCyclesPane() {
  const status = document.getElementById("chat-cycles-status");
  const empty = document.getElementById("chat-cycles-empty");
  const noWs = document.getElementById("chat-cycles-no-workspace");
  const list = document.getElementById("chat-cycles-list");
  const detail = document.getElementById("chat-cycles-detail");
  const wsPath = document.getElementById("chat-cycles-workspace-path");

  if (!status || !list) return;
  status.textContent = "loading…";
  empty.hidden = true;
  noWs.hidden = true;
  list.innerHTML = "";
  detail.hidden = true;
  detail.innerHTML = "";

  const instanceId = await _resolveSmithInstanceId();
  if (!instanceId) {
    status.textContent = "no experimenter agent found";
    noWs.hidden = false;
    return;
  }

  let resp;
  try {
    resp = await api.get(`/agents/${encodeURIComponent(instanceId)}/cycles`);
  } catch (e) {
    status.textContent = `error: ${e.message || e}`;
    return;
  }

  if (!resp.workspace_available) {
    status.textContent = "workspace not provisioned";
    noWs.hidden = false;
    return;
  }
  if (wsPath && resp.workspace_path) wsPath.textContent = resp.workspace_path;

  const cycles = resp.cycles || [];
  status.textContent = `${cycles.length} cycle${cycles.length === 1 ? "" : "s"} in workspace`;

  if (cycles.length === 0) {
    empty.hidden = false;
    return;
  }

  // Render the list — newest first (descending cycle number) for review
  // recency. The backend returns ascending; we reverse here.
  for (const c of [...cycles].reverse()) {
    const li = document.createElement("li");
    li.className = "chat-cycles-row";
    li.dataset.cycleId = c.cycle_id;
    li.innerHTML = `
      <div class="chat-cycles-row__head">
        <span class="chat-cycles-row__id">${escapeHTML(c.cycle_id)}</span>
        <span class="chat-cycles-row__status chat-cycles-row__status--${escapeHTML(c.status)}">${escapeHTML(c.status)}</span>
        <span class="chat-cycles-row__sha muted">${escapeHTML(c.head_sha)}</span>
        <span class="chat-cycles-row__time muted">${escapeHTML((c.head_timestamp || "").slice(0, 19))}</span>
      </div>
      <div class="chat-cycles-row__msg">${escapeHTML(c.head_message)}</div>
      <div class="chat-cycles-row__stats muted">
        ${c.files_changed} files · +${c.insertions} / -${c.deletions}
        ${c.has_cycle_report ? " · <strong>report</strong>" : ""}
      </div>
    `;
    li.addEventListener("click", () => _expandCycle(instanceId, c.cycle_id, li));
    list.appendChild(li);
  }
}

async function _expandCycle(instanceId, cycleId, rowEl) {
  const detail = document.getElementById("chat-cycles-detail");
  if (!detail) return;
  detail.hidden = false;
  detail.innerHTML = `<div class="muted">Loading cycle ${escapeHTML(cycleId)}…</div>`;

  // Highlight the active row.
  document.querySelectorAll(".chat-cycles-row--active")
    .forEach((el) => el.classList.remove("chat-cycles-row--active"));
  if (rowEl) rowEl.classList.add("chat-cycles-row--active");

  let d;
  try {
    d = await api.get(`/agents/${encodeURIComponent(instanceId)}/cycles/${encodeURIComponent(cycleId)}`);
  } catch (e) {
    detail.innerHTML = `<div class="muted">Error loading detail: ${escapeHTML(String(e.message || e))}</div>`;
    return;
  }

  const reqTools = (d.requested_tools || []).map((t) =>
    `<li><code>${escapeHTML(t.name || "?")}.v${escapeHTML(t.version || "?")}</code>
      — side_effects=${escapeHTML(t.side_effects || "?")}
      ${t.reason ? `<div class="muted">${escapeHTML(t.reason)}</div>` : ""}
    </li>`).join("");

  const truncatedNote = d.diff_truncated
    ? `<div class="muted" style="margin: 4px 0;">⚠ Diff truncated for size — see workspace at branch <code>${escapeHTML(d.branch)}</code> for the full version.</div>`
    : "";

  detail.innerHTML = `
    <div class="chat-cycles-detail__head">
      <h3>${escapeHTML(d.cycle_id)} — ${escapeHTML(d.status)}</h3>
      <div class="muted">
        <code>${escapeHTML(d.branch)}</code> @ <code>${escapeHTML(d.head_sha)}</code>
        · ${d.files_changed} files · +${d.insertions}/-${d.deletions}
      </div>
    </div>

    <details class="chat-cycles-detail__commit" open>
      <summary>Commit message</summary>
      <pre class="chat-cycles-pre">${escapeHTML(d.full_commit_message || d.head_message)}</pre>
    </details>

    ${d.cycle_report_content ? `
      <details class="chat-cycles-detail__report" open>
        <summary>Cycle report — <code>${escapeHTML(d.cycle_report_path || "")}</code></summary>
        <pre class="chat-cycles-pre">${escapeHTML(d.cycle_report_content)}</pre>
      </details>
    ` : `
      <div class="muted" style="margin: 8px 0;">No CYCLE_REPORT.md on this branch yet.</div>
    `}

    ${reqTools ? `
      <details class="chat-cycles-detail__tools" open>
        <summary>Requested tools (operator-approval gates land in E5)</summary>
        <ul class="chat-cycles-reqtools">${reqTools}</ul>
      </details>
    ` : ""}

    <details class="chat-cycles-detail__diff">
      <summary>Diff vs <code>main</code></summary>
      ${truncatedNote}
      <pre class="chat-cycles-pre chat-cycles-pre--diff">${escapeHTML(d.diff || "(no diff)")}</pre>
    </details>

    <div class="chat-cycles-detail__actions" style="margin-top: 12px; padding-top: 12px; border-top: 1px solid rgba(255,255,255,0.08);">
      <div style="display: flex; gap: 8px; flex-wrap: wrap; align-items: center;">
        <button class="btn btn--sm chat-cycles-action chat-cycles-action--approve"
                type="button"
                data-cycle-id="${escapeHTML(d.cycle_id)}"
                data-instance-id="${escapeHTML(instanceId)}"
                data-action="approve">approve + merge</button>
        <button class="btn btn--sm btn--ghost chat-cycles-action chat-cycles-action--deny"
                type="button"
                data-cycle-id="${escapeHTML(d.cycle_id)}"
                data-instance-id="${escapeHTML(instanceId)}"
                data-action="deny">deny</button>
        <button class="btn btn--sm btn--ghost chat-cycles-action chat-cycles-action--counter"
                type="button"
                data-cycle-id="${escapeHTML(d.cycle_id)}"
                data-instance-id="${escapeHTML(instanceId)}"
                data-action="counter">counter-propose</button>
        <label class="muted" style="margin-left: 12px; font-size: 0.85em;">
          <input type="checkbox" id="chat-cycles-deny-delete-branch" />
          delete branch on deny
        </label>
      </div>
      <div class="chat-cycles-action-note" style="margin-top: 8px;">
        <textarea id="chat-cycles-decision-note"
                  class="inp"
                  rows="2"
                  maxlength="2000"
                  placeholder="optional note (required for counter-propose) — lands in audit + Smith's next explore tick"></textarea>
      </div>
      <div id="chat-cycles-action-status" class="muted" style="margin-top: 6px; font-size: 0.85em;"></div>
    </div>
  `;

  // Wire the action buttons.
  document.querySelectorAll(".chat-cycles-action").forEach((btn) => {
    btn.addEventListener("click", (e) => _onCycleDecision(e.currentTarget));
  });
}

async function _onCycleDecision(btn) {
  const action = btn.dataset.action;
  const cycleId = btn.dataset.cycleId;
  const instanceId = btn.dataset.instanceId;
  const noteEl = document.getElementById("chat-cycles-decision-note");
  const statusEl = document.getElementById("chat-cycles-action-status");
  const deleteBranchEl = document.getElementById("chat-cycles-deny-delete-branch");
  const note = (noteEl?.value || "").trim();
  const deleteBranch = !!(deleteBranchEl?.checked);

  if (action === "counter" && !note) {
    statusEl.textContent = "counter-propose requires a note.";
    return;
  }
  // Confirm destructive actions inline.
  if (action === "approve" && !confirm(
    `Approve + merge ${cycleId} into the workspace's main? ` +
    `This runs git merge --no-ff in ~/.fsf/experimenter-workspace/. ` +
    `You'll still need to push to origin manually after.`
  )) return;
  if (action === "deny" && deleteBranch && !confirm(
    `Deny ${cycleId} AND delete its branch? ` +
    `Branch deletion is permanent.`
  )) return;

  // Disable all action buttons during the request.
  document.querySelectorAll(".chat-cycles-action").forEach((b) => b.disabled = true);
  statusEl.textContent = `${action}…`;

  const body = { action, note: note || null, delete_branch: deleteBranch };
  try {
    const resp = await api.post(
      `/agents/${encodeURIComponent(instanceId)}/cycles/${encodeURIComponent(cycleId)}/decision`,
      body,
    );
    statusEl.innerHTML = `<strong style="color: rgba(72,187,120,1);">ok</strong> — ${escapeHTML(resp.detail || action)}`;
    // After 2s, refresh the list so the row's status updates.
    setTimeout(() => refreshCyclesPane(), 2000);
  } catch (e) {
    statusEl.innerHTML = `<strong style="color: rgba(220,80,80,1);">error</strong> — ${escapeHTML(String(e.message || e))}`;
    document.querySelectorAll(".chat-cycles-action").forEach((b) => b.disabled = false);
  }
}

function wireCyclesRefresh() {
  const btn = document.getElementById("chat-cycles-refresh");
  if (btn) btn.addEventListener("click", () => refreshCyclesPane());
  // ADR-0056 E6 (B192) — posture toggle wiring.
  document.querySelectorAll(".chat-cycles-posture-btn").forEach((b) => {
    b.addEventListener("click", () => _onPostureChange(b.dataset.posture));
  });
  // First-load posture display kick.
  refreshSmithPosture();
}

// ADR-0056 E6 — Smith posture toggle.
//
// Reads/writes via existing /agents/{id}/posture endpoints
// (ADR-0045). Highlights the active posture button + surfaces
// a one-line current-state label next to the buttons.
async function refreshSmithPosture() {
  const label = document.getElementById("chat-cycles-posture-current");
  if (!label) return;
  const id = await _resolveSmithInstanceId();
  if (!id) {
    label.textContent = "(no Smith)";
    return;
  }
  try {
    const resp = await api.get(`/agents/${encodeURIComponent(id)}/posture`);
    const posture = resp.posture || "?";
    label.textContent = `current: ${posture}`;
    // Highlight the active button.
    document.querySelectorAll(".chat-cycles-posture-btn").forEach((b) => {
      b.classList.toggle(
        "chat-cycles-posture-btn--active",
        b.dataset.posture === posture,
      );
    });
  } catch (e) {
    label.textContent = `(error reading posture: ${e.message || e})`;
  }
}

async function _onPostureChange(newPosture) {
  if (!["green", "yellow", "red"].includes(newPosture)) return;
  const label = document.getElementById("chat-cycles-posture-current");
  const id = await _resolveSmithInstanceId();
  if (!id) {
    if (label) label.textContent = "(no Smith — can't set posture)";
    return;
  }
  // Confirm the destructive flip.
  if (newPosture === "red" && !confirm(
    "Flip Smith to RED?\n\nRED refuses every non-read-only " +
    "dispatch. Useful before stepping away or after a bad " +
    "cycle. Reversible at any time."
  )) return;
  if (newPosture === "green" && !confirm(
    "Flip Smith to GREEN?\n\nGREEN auto-fires every dispatch " +
    "within Smith's genre cap (no per-call operator approval). " +
    "Recommended only after several clean cycles establish trust. " +
    "Reversible at any time."
  )) return;

  if (label) label.textContent = `setting ${newPosture}…`;
  try {
    await api.post(
      `/agents/${encodeURIComponent(id)}/posture`,
      {
        posture: newPosture,
        reason: "operator-driven via Cycles pane (ADR-0056 E6)",
      },
    );
  } catch (e) {
    if (label) label.textContent = `error: ${e.message || e}`;
    return;
  }
  // Refresh the display + the cycles list (some cycles may
  // surface new info after a posture flip).
  refreshSmithPosture();
}

// Wire the refresh button at module load time. The pane itself
// auto-refreshes on every showChatMode("cycles") call.
if (typeof document !== "undefined" && document.readyState !== "loading") {
  wireCyclesRefresh();
} else if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", wireCyclesRefresh);
}
