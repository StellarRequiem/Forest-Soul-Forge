// Live audit-stream client — ADR-0096 v2 (the SSE upgrade the console header
// promised). The daemon already streams every new ledger entry over
// GET /audit/stream (event: chain_entry, id: <seq>, 30s ": heartbeat" comment).
// This wires the dashboard to it so the HUD + mission board update the instant
// an agent dispatches, instead of waiting for the 15s poll.
//
// Read-only: it only listens. One EventSource for the whole page (singleton);
// panels subscribe via onChainEntry / onChainEntryDebounced. The existing polls
// stay as a fallback, so a dropped stream degrades to the prior behaviour rather
// than going stale. Honest status: liveStatus() reflects the real connection.

import { API_BASE } from "./api.js";

const subscribers = new Set();          // cb(entry|null) on each chain_entry
const statusSubs = new Set();           // cb(status) on each transition
let source = null;                      // the singleton EventSource
let status = "connecting";              // "connecting" | "live" | "down"

function setStatus(s) {
  if (s === status) return;
  status = s;
  for (const cb of statusSubs) { try { cb(status); } catch { /* subscriber's problem */ } }
}

/** Current connection status: "connecting" | "live" | "down". */
export function liveStatus() { return status; }

/** Subscribe to status transitions. Fires immediately with the current value. */
export function onStatus(cb) {
  statusSubs.add(cb);
  try { cb(status); } catch { /* ignore */ }
  return () => statusSubs.delete(cb);
}

/** Subscribe to ledger entries. cb receives the parsed entry ({seq, ...}) or
 *  null if a line failed to parse. Returns an unsubscribe fn. */
export function onChainEntry(cb) {
  subscribers.add(cb);
  return () => subscribers.delete(cb);
}

/** Like onChainEntry, but coalesces bursts: cb fires once, `ms` after the last
 *  entry in a burst. Audit entries arrive in dispatch bursts then go quiet, so a
 *  trailing debounce keeps refreshes cheap without missing the settled state. */
export function onChainEntryDebounced(cb, ms = 1500) {
  let timer = null;
  let last = null;
  return onChainEntry((entry) => {
    last = entry;
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => { timer = null; try { cb(last); } catch { /* ignore */ } }, ms);
  });
}

/** Open the stream (idempotent — safe to call from every panel that needs it). */
export function startLive() {
  if (source) return;                              // singleton
  if (typeof EventSource === "undefined") { setStatus("down"); return; }
  try {
    source = new EventSource(`${API_BASE}/audit/stream`);
  } catch {
    setStatus("down");
    return;
  }
  source.addEventListener("open", () => setStatus("live"));
  source.addEventListener("chain_entry", (ev) => {
    setStatus("live");
    let entry = null;
    try { entry = JSON.parse(ev.data); } catch { /* malformed — verify() reports breaks */ }
    for (const cb of subscribers) { try { cb(entry); } catch { /* subscriber's problem */ } }
  });
  source.addEventListener("error", () => {
    // EventSource auto-reconnects; reflect the gap honestly until it's back.
    setStatus(source && source.readyState === EventSource.CLOSED ? "down" : "connecting");
  });
}
