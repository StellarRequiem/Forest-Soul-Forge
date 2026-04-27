// Approvals tab — operator UI for the ADR-0019 T3 approval queue.
//
// Flow:
//   1. Operator picks an agent from #pending-agent-select (populated
//      from state.agents the moment agents.js publishes it).
//   2. We fetch GET /agents/{id}/pending_calls, render each ticket.
//   3. Operator types their id in #pending-operator-id, then clicks
//      Approve or Reject on a ticket.
//   4. POST /pending_calls/{ticket_id}/{approve|reject} sends the
//      operator id (+ reason for reject) under the daemon's write lock.
//   5. On success the ticket disappears from the pending list (it was
//      decided server-side); on the approve path the response carries
//      the dispatch result, which we surface as a toast.
//
// The badge on the tab itself shows the count of *some agent's*
// pending tickets — refreshed on a low-frequency interval (10s) so the
// operator notices new approvals without explicitly refreshing.

import { api, ApiError, writeCall } from "./api.js";
import * as state from "./state.js";
import { toast } from "./toast.js";

const OPERATOR_KEY = "fsf.operatorId"; // localStorage — survives reloads

let badgeAgentId = null;
let badgeTimer = null;

// ---------------------------------------------------------------------------
// Operator id persistence
// ---------------------------------------------------------------------------
function loadOperatorId() {
  return localStorage.getItem(OPERATOR_KEY) || "";
}

function saveOperatorId(value) {
  if (value) localStorage.setItem(OPERATOR_KEY, value);
  else localStorage.removeItem(OPERATOR_KEY);
}

function getOperatorId() {
  const input = document.getElementById("pending-operator-id");
  return input ? input.value.trim() : "";
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------
function fmtDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toISOString().slice(0, 19).replace("T", " ");
  } catch {
    return iso;
  }
}

function renderEmpty(message) {
  const root = document.getElementById("pending-list");
  root.innerHTML = "";
  const div = document.createElement("div");
  div.className = "empty";
  div.textContent = message;
  root.appendChild(div);
}

function renderTicket(ticket) {
  const card = document.createElement("div");
  card.className = "pending-card";
  if (ticket.status === "approved") card.classList.add("pending-card--approved");
  if (ticket.status === "rejected") card.classList.add("pending-card--rejected");

  // Header — tool key + status badge.
  const header = document.createElement("div");
  header.className = "pending-card__header";
  const tool = document.createElement("strong");
  tool.className = "pending-card__tool";
  tool.textContent = ticket.tool_key;
  header.appendChild(tool);
  const badge = document.createElement("span");
  badge.className = `pending-card__badge pending-card__badge--${ticket.status}`;
  badge.textContent = ticket.status;
  header.appendChild(badge);

  // Side-effects + ticket id pill.
  const pillRow = document.createElement("div");
  pillRow.className = "pending-card__pills";
  const sePill = document.createElement("span");
  sePill.className = `pill pill--se-${ticket.side_effects}`;
  sePill.textContent = `side_effects: ${ticket.side_effects}`;
  pillRow.appendChild(sePill);
  const idPill = document.createElement("span");
  idPill.className = "pill pill--ghost";
  idPill.textContent = ticket.ticket_id;
  pillRow.appendChild(idPill);
  const sessionPill = document.createElement("span");
  sessionPill.className = "pill pill--ghost";
  sessionPill.textContent = `session: ${ticket.session_id}`;
  pillRow.appendChild(sessionPill);

  // Args block — JSON, monospaced.
  const argsBlock = document.createElement("pre");
  argsBlock.className = "pending-card__args";
  argsBlock.textContent = JSON.stringify(ticket.args || {}, null, 2);

  // Footer — created_at + decision metadata if decided.
  const footer = document.createElement("div");
  footer.className = "pending-card__footer";
  const createdNote = document.createElement("span");
  createdNote.className = "tiny";
  createdNote.textContent = `queued: ${fmtDate(ticket.created_at)}`;
  footer.appendChild(createdNote);
  if (ticket.status !== "pending") {
    const decidedNote = document.createElement("span");
    decidedNote.className = "tiny";
    decidedNote.textContent =
      `${ticket.status} by ${ticket.decided_by || "?"} at ${fmtDate(ticket.decided_at)}` +
      (ticket.decision_reason ? ` — ${ticket.decision_reason}` : "");
    footer.appendChild(decidedNote);
  }

  card.appendChild(header);
  card.appendChild(pillRow);
  card.appendChild(argsBlock);
  card.appendChild(footer);

  // Action row — only shown for pending.
  if (ticket.status === "pending") {
    const actions = document.createElement("div");
    actions.className = "pending-card__actions";

    const approveBtn = document.createElement("button");
    approveBtn.type = "button";
    approveBtn.className = "btn btn--primary btn--sm";
    approveBtn.textContent = "approve";
    approveBtn.addEventListener("click", () => onApprove(ticket));

    const rejectBtn = document.createElement("button");
    rejectBtn.type = "button";
    rejectBtn.className = "btn btn--danger btn--sm";
    rejectBtn.textContent = "reject…";
    rejectBtn.addEventListener("click", () => onReject(ticket));

    actions.appendChild(approveBtn);
    actions.appendChild(rejectBtn);
    card.appendChild(actions);
  }

  return card;
}

function renderList(tickets) {
  const root = document.getElementById("pending-list");
  if (!tickets.length) {
    renderEmpty("No tickets in this view.");
    return;
  }
  root.innerHTML = "";
  for (const t of tickets) {
    root.appendChild(renderTicket(t));
  }
}

// ---------------------------------------------------------------------------
// Fetch + actions
// ---------------------------------------------------------------------------
async function fetchTickets() {
  const select = document.getElementById("pending-agent-select");
  const filter = document.getElementById("pending-status-filter");
  const id = select ? select.value : "";
  if (!id) {
    renderEmpty("Pick an agent above to see its queued tool calls.");
    return;
  }
  const status = filter ? filter.value : "pending";
  const qs = status === "pending" ? "" : `?status=${encodeURIComponent(status)}`;
  try {
    const data = await api.get(`/agents/${encodeURIComponent(id)}/pending_calls${qs}`);
    renderList(data.pending_calls || []);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      renderEmpty("Unknown agent.");
    } else {
      renderEmpty(`Failed to load: ${e.message}`);
    }
  }
}

async function onApprove(ticket) {
  const op = getOperatorId();
  if (!op) {
    toast({
      title: "Operator id required",
      msg: "Type your id in the operator field before approving.",
      kind: "warn",
      ttl: 5000,
    });
    return;
  }
  saveOperatorId(op);
  try {
    const resp = await writeCall(
      `/pending_calls/${encodeURIComponent(ticket.ticket_id)}/approve`,
      { operator_id: op },
    );
    const summary = resp.status === "succeeded"
      ? `succeeded · call_count_after=${resp.call_count_after}`
      : resp.status === "failed"
        ? `tool failed: ${resp.failure_exception_type || "unknown"}`
        : resp.status;
    toast({
      title: "Approved",
      msg: `${ticket.tool_key} → ${summary}`,
      kind: "success",
      ttl: 5000,
    });
  } catch (e) {
    const detail = e?.detail?.detail;
    const reason = detail && typeof detail === "object" ? detail.reason : null;
    toast({
      title: "Approval failed",
      msg: reason ? `${reason}: ${detail.detail || e.message}` : e.message,
      kind: "error",
      ttl: 8000,
    });
  }
  await fetchTickets();
  await refreshBadge();
}

async function onReject(ticket) {
  const op = getOperatorId();
  if (!op) {
    toast({
      title: "Operator id required",
      msg: "Type your id in the operator field before rejecting.",
      kind: "warn",
      ttl: 5000,
    });
    return;
  }
  // Native prompt is a deliberate choice — keeps the modal-less aesthetic of
  // the rest of the UI. ADR-0019 T3's reason field is required server-side,
  // so empty input cancels the action.
  const reason = window.prompt(
    `Reject ${ticket.tool_key} (${ticket.ticket_id})?\n\nReason will be persisted to the audit chain:`,
  );
  if (reason === null) return; // cancelled
  const trimmed = reason.trim();
  if (!trimmed) {
    toast({
      title: "Reason required",
      msg: "Reject needs a non-empty reason — re-click and try again.",
      kind: "warn",
      ttl: 5000,
    });
    return;
  }
  saveOperatorId(op);
  try {
    await writeCall(
      `/pending_calls/${encodeURIComponent(ticket.ticket_id)}/reject`,
      { operator_id: op, reason: trimmed },
    );
    toast({
      title: "Rejected",
      msg: `${ticket.tool_key} — “${trimmed}”`,
      kind: "success",
      ttl: 4000,
    });
  } catch (e) {
    toast({
      title: "Reject failed",
      msg: e.message,
      kind: "error",
      ttl: 6000,
    });
  }
  await fetchTickets();
  await refreshBadge();
}

// ---------------------------------------------------------------------------
// Tab badge — small unobtrusive count on the tab itself
// ---------------------------------------------------------------------------
async function refreshBadge() {
  const badge = document.getElementById("pending-badge");
  if (!badge || !badgeAgentId) {
    if (badge) badge.hidden = true;
    return;
  }
  try {
    const data = await api.get(
      `/agents/${encodeURIComponent(badgeAgentId)}/pending_calls`,
    );
    const n = data.count || 0;
    if (n > 0) {
      badge.hidden = false;
      badge.textContent = String(n);
    } else {
      badge.hidden = true;
    }
  } catch {
    badge.hidden = true;
  }
}

// ---------------------------------------------------------------------------
// Agent dropdown population — driven by state.agents
// ---------------------------------------------------------------------------
function repopulateAgentSelect(agents) {
  const select = document.getElementById("pending-agent-select");
  if (!select) return;
  const previous = select.value;
  select.innerHTML = "";
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "— pick an agent —";
  select.appendChild(placeholder);
  for (const a of (agents || [])) {
    const opt = document.createElement("option");
    opt.value = a.instance_id;
    opt.textContent = `${a.agent_name} · ${a.dna} · ${a.role}`;
    select.appendChild(opt);
  }
  // Preserve selection across refreshes.
  if (previous && [...select.options].some((o) => o.value === previous)) {
    select.value = previous;
  }
  // Track first-listed agent as the badge's source — single-user
  // deployment, the operator usually only watches one. If the user
  // picks a different one in the dropdown, the badge follows.
  badgeAgentId = select.value || (agents?.[0]?.instance_id ?? null);
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
export function start() {
  // Wire controls.
  const refreshBtn = document.getElementById("pending-refresh");
  const select = document.getElementById("pending-agent-select");
  const filter = document.getElementById("pending-status-filter");
  const operatorInput = document.getElementById("pending-operator-id");
  if (operatorInput) {
    operatorInput.value = loadOperatorId();
    operatorInput.addEventListener("change", () => {
      saveOperatorId(operatorInput.value.trim());
    });
  }
  if (refreshBtn) refreshBtn.addEventListener("click", fetchTickets);
  if (select) {
    select.addEventListener("change", () => {
      badgeAgentId = select.value || badgeAgentId;
      fetchTickets();
      refreshBadge();
    });
  }
  if (filter) filter.addEventListener("change", fetchTickets);

  // Subscribe to agent list updates from agents.js.
  state.subscribe("agents", (agents) => {
    repopulateAgentSelect(agents || []);
    refreshBadge();
  });

  // Refresh on tab activation so the operator sees a fresh view every
  // time they switch in.
  document.querySelectorAll(".tab").forEach((tab) => {
    if (tab.dataset.tab === "pending") {
      tab.addEventListener("click", fetchTickets);
    }
  });

  // Low-frequency badge refresh — 10s. Cheap GET against a single
  // endpoint; not a concern for the dev rig. T10 will replace this
  // with a server-push channel if needed.
  if (badgeTimer) clearInterval(badgeTimer);
  badgeTimer = setInterval(refreshBadge, 10_000);
}
