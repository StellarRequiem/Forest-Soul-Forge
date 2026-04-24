// Audit tab — tails the last N chain entries. Event types get coloured
// chips so scanning is fast; event JSON is truncated to one line.

import { api } from "./api.js";
import * as state from "./state.js";
import { toast } from "./toast.js";

function fmtTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toISOString().replace("T", " ").slice(0, 19);
  } catch {
    return iso;
  }
}

function fmtEventData(json) {
  if (!json) return "";
  try {
    const obj = JSON.parse(json);
    const parts = [];
    // Most useful keys first, then fall back to raw JSON.
    for (const key of ["agent_name", "role", "reason", "parent_dna", "violation"]) {
      if (obj[key] !== undefined) parts.push(`${key}=${obj[key]}`);
    }
    if (parts.length === 0) {
      return JSON.stringify(obj);
    }
    return parts.join("  ");
  } catch {
    return json;
  }
}

function renderEvents(events) {
  const root = document.getElementById("audit-list");
  root.innerHTML = "";
  if (!events.length) {
    const empty = document.createElement("div");
    empty.style.color = "var(--fg-faint)";
    empty.style.textAlign = "center";
    empty.style.padding = "var(--sp-4)";
    empty.textContent = "No audit entries.";
    root.appendChild(empty);
    return;
  }
  // Newest first.
  const sorted = [...events].sort((a, b) => b.seq - a.seq);
  for (const ev of sorted) {
    const row = document.createElement("div");
    row.className = "audit-entry";

    const seq = document.createElement("div");
    seq.className = "audit-seq";
    seq.textContent = `#${ev.seq}`;

    const when = document.createElement("div");
    when.className = "audit-time";
    when.textContent = fmtTime(ev.timestamp);

    const event = document.createElement("div");
    const chip = document.createElement("span");
    chip.className = `audit-event audit-event--${ev.event_type}`;
    chip.textContent = ev.event_type;
    event.appendChild(chip);

    const data = document.createElement("div");
    data.className = "audit-data";
    data.textContent = fmtEventData(ev.event_json);
    data.title = ev.event_json || "";

    row.appendChild(seq);
    row.appendChild(when);
    row.appendChild(event);
    row.appendChild(data);

    root.appendChild(row);
  }
}

export async function refresh() {
  const n = Number(document.getElementById("audit-limit").value) || 50;
  try {
    const res = await api.get(`/audit/tail?n=${n}`);
    state.set("audit", res.events);
    renderEvents(res.events);
  } catch (e) {
    toast({
      title: "Failed to load audit chain",
      msg: e.message,
      kind: "error",
    });
  }
}

export function start() {
  document.getElementById("audit-refresh").addEventListener("click", refresh);
  document.getElementById("audit-limit").addEventListener("change", refresh);
  refresh();
}
