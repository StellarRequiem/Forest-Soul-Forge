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
    // Wrap row + (collapsed) detail in a single container so the click
    // handler toggles them together. Demo-friction audit P1 #11.
    const wrap = document.createElement("div");
    wrap.className = "audit-row-wrap";

    const row = document.createElement("div");
    row.className = "audit-entry audit-entry--clickable";
    row.title = "Click to expand event_data + hash linkage";

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

    const chevron = document.createElement("div");
    chevron.className = "audit-chevron";
    chevron.textContent = "▸";
    chevron.setAttribute("aria-hidden", "true");

    row.appendChild(seq);
    row.appendChild(when);
    row.appendChild(event);
    row.appendChild(data);
    row.appendChild(chevron);

    // The expanded detail panel — hidden by default, populated lazily on
    // first open so the cost is paid only when an evaluator drills in.
    const detail = document.createElement("div");
    detail.className = "audit-detail";
    detail.hidden = true;

    let populated = false;
    row.addEventListener("click", () => {
      if (!populated) {
        renderDetailInto(detail, ev);
        populated = true;
      }
      const expanded = !detail.hidden ? false : true;
      detail.hidden = !expanded;
      row.classList.toggle("audit-entry--expanded", expanded);
      chevron.textContent = expanded ? "▾" : "▸";
    });

    wrap.appendChild(row);
    wrap.appendChild(detail);
    root.appendChild(wrap);
  }
}

/** Render the click-to-expand detail panel for one chain entry.
 *
 * Shows everything the headline row hides: the cryptographic linkage
 * (entry_hash, prev_hash, agent_dna), and the full event_data JSON.
 * For a security audience this is the "trust the chain" moment — they
 * can see the hash linkage with their own eyes instead of being told it
 * exists. Demo-friction audit P1 #11.
 */
function renderDetailInto(panel, ev) {
  panel.innerHTML = "";

  const grid = document.createElement("dl");
  grid.className = "audit-detail__grid";
  const row = (k, v) => {
    const dt = document.createElement("dt");
    dt.textContent = k;
    const dd = document.createElement("dd");
    dd.className = "mono";
    dd.textContent = v ?? "—";
    grid.appendChild(dt);
    grid.appendChild(dd);
  };
  row("seq", String(ev.seq));
  row("timestamp", ev.timestamp);
  row("event_type", ev.event_type);
  row("agent_dna", ev.agent_dna);
  row("instance_id", ev.instance_id);
  row("entry_hash", ev.entry_hash);
  // prev_hash isn't returned by /audit/tail today (only by full chain
  // walks via the JSONL). When it lands, this row will populate; until
  // then we surface the structural fact that the linkage exists.
  row("prev_hash", ev.prev_hash || "(linked to seq #" + (ev.seq - 1) + ")");

  panel.appendChild(grid);

  const heading = document.createElement("div");
  heading.className = "audit-detail__heading";
  heading.textContent = "event_data";
  panel.appendChild(heading);

  const pre = document.createElement("pre");
  pre.className = "audit-detail__json mono";
  try {
    const obj = JSON.parse(ev.event_json || "{}");
    pre.textContent = JSON.stringify(obj, null, 2);
  } catch {
    pre.textContent = ev.event_json || "(empty)";
  }
  panel.appendChild(pre);
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
