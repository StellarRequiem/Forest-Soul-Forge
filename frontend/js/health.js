// Health polling + provider status dot + auth / writes-disabled banners.

import { api, ApiError, setToken, getToken } from "./api.js";
import * as state from "./state.js";

const DOT_EL = () => document.getElementById("provider-dot");
const LABEL_EL = () => document.getElementById("provider-label");
const BANNER_EL = () => document.getElementById("banner");
const AUTH_BTN_EL = () => document.getElementById("auth-button");

const POLL_INTERVAL_MS = 15_000;

let pollTimer = null;

/** Map ProviderStatus enum → CSS `data-status` value. */
function dotStatus(providerStatus) {
  // Pydantic ProviderStatus: OK | DEGRADED | UNREACHABLE | DISABLED
  if (!providerStatus) return "unknown";
  return String(providerStatus).toLowerCase();
}

function renderHealth(health) {
  const dot = DOT_EL();
  const label = LABEL_EL();
  if (!health) {
    dot.setAttribute("data-status", "unknown");
    label.textContent = "daemon unreachable";
    return;
  }
  const providerName = health.active_provider || "unknown";
  const providerStatus = health.provider?.status;
  dot.setAttribute("data-status", dotStatus(providerStatus));
  label.textContent = `${providerName} · ${providerStatus?.toLowerCase() || "…"}`;
  dot.title = health.provider?.error
    ? `${providerName}: ${health.provider.error}`
    : `${providerName}: ${providerStatus}`;
}

function renderBanner({ authRequired, writesEnabled, tokenPresent, unreachable }) {
  const el = BANNER_EL();
  const authBtn = AUTH_BTN_EL();

  let msg = null;
  let danger = false;

  if (unreachable) {
    msg = "Can't reach the daemon. Is it running? Try ?api=http://127.0.0.1:7423 in the URL.";
    danger = true;
  } else if (authRequired && !tokenPresent) {
    msg = "This daemon requires an X-FSF-Token. Click \"set token\" in the header to provide one.";
  } else if (!writesEnabled) {
    msg = "This daemon is read-only (allow_write_endpoints=false). Birth, spawn, and archive are disabled.";
  }

  if (msg) {
    el.hidden = false;
    el.textContent = msg;
    el.classList.toggle("banner--danger", danger);
  } else {
    el.hidden = true;
    el.textContent = "";
    el.classList.remove("banner--danger");
  }

  // Auth button visible whenever auth is required (so users can rotate the
  // token even when one is already set).
  authBtn.hidden = !authRequired;
  authBtn.textContent = tokenPresent ? "change token" : "set token";
}

async function pollOnce() {
  try {
    const health = await api.get("/healthz");
    state.set("health", health);
    state.set("authRequired", !!health.auth_required);
    state.set("writesEnabled", !!health.writes_enabled);
    renderHealth(health);
    renderBanner({
      authRequired: !!health.auth_required,
      writesEnabled: !!health.writes_enabled,
      tokenPresent: !!getToken(),
      unreachable: false,
    });
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      // /healthz shouldn't 401 per the daemon spec, but handle it defensively.
      state.set("authRequired", true);
      renderBanner({
        authRequired: true,
        writesEnabled: false,
        tokenPresent: !!getToken(),
        unreachable: false,
      });
      return;
    }
    state.set("health", null);
    renderHealth(null);
    renderBanner({
      authRequired: !!state.get("authRequired"),
      writesEnabled: false,
      tokenPresent: !!getToken(),
      unreachable: true,
    });
  }
}

/** Open a browser prompt to set/change the token. Returns true if a token was set. */
export function promptForToken() {
  const current = getToken() || "";
  const input = window.prompt(
    "Enter the daemon's X-FSF-Token (set FSF_API_TOKEN on the daemon to require it).\n" +
      "Leave blank and press OK to clear any stored token.",
    current
  );
  if (input === null) return false; // cancelled
  const trimmed = input.trim();
  setToken(trimmed || null);
  // Re-poll immediately so the banner updates and the dot re-colors.
  pollOnce();
  return true;
}

export function start() {
  // Immediate fetch, then interval.
  pollOnce();
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(pollOnce, POLL_INTERVAL_MS);

  AUTH_BTN_EL().addEventListener("click", promptForToken);
}

/** Force a refresh (used after manual provider flip, etc.). */
export function refresh() {
  return pollOnce();
}
