// API wrapper for the Forest Soul Forge daemon.
//
// Design notes:
//   - Base URL defaults to same-origin. A URL query param `?api=...` overrides
//     it (useful during dev when the daemon and frontend are on different
//     ports). Persisted in localStorage so subsequent loads remember.
//   - `X-FSF-Token` is pulled from localStorage under `fsf.token`. Missing is
//     fine — the daemon exempts /healthz from auth and reports auth_required
//     in its body, so health.js can prompt when needed.
//   - `X-Idempotency-Key` is generated per mutating call. Callers can pass
//     `{idempotencyKey: "..."}` to reuse one across retries. A fresh key means
//     "really do this again"; the same key means "return the cached response
//     if you've seen this before". 409 on same-key-different-body per ADR-0007.

const TOKEN_KEY = "fsf.token";
const API_BASE_KEY = "fsf.apiBase";

/** Resolve the API base URL, with precedence: ?api= param > localStorage > same-origin. */
function resolveApiBase() {
  const qs = new URLSearchParams(location.search);
  const fromQuery = qs.get("api");
  if (fromQuery) {
    localStorage.setItem(API_BASE_KEY, fromQuery);
    return fromQuery.replace(/\/$/, "");
  }
  const stored = localStorage.getItem(API_BASE_KEY);
  if (stored) return stored.replace(/\/$/, "");
  // Same-origin: the daemon must be proxied at / or the page is served from
  // the daemon itself. The common dev shape is `?api=http://127.0.0.1:7423`.
  return location.origin.replace(/\/$/, "");
}

export const API_BASE = resolveApiBase();

/** Read the stashed token or `null`. */
export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || null;
}

/** Persist a token (or clear it by passing null/empty). */
export function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

/** Generate an idempotency key — UUID v4 via crypto.randomUUID when available. */
export function makeIdempotencyKey() {
  if (crypto?.randomUUID) return crypto.randomUUID();
  // Fallback for very old browsers — not cryptographically strong, but the
  // key only needs to be unique across the client's own retries.
  return "fsf-" + Math.random().toString(36).slice(2) + Date.now().toString(36);
}

/**
 * Thrown by `request()` for any non-2xx response.
 * `status` is the HTTP code, `detail` is the parsed body (usually an object
 * with `.detail`), `body` is the raw string if parsing failed.
 */
export class ApiError extends Error {
  constructor(message, { status, detail, body, url }) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.body = body;
    this.url = url;
  }
}

/** Low-level request. Callers prefer the `api.get/post/put` helpers below. */
export async function request(path, { method = "GET", body, idempotencyKey, headers = {} } = {}) {
  const url = API_BASE + (path.startsWith("/") ? path : "/" + path);
  const token = getToken();

  const finalHeaders = { ...headers };
  if (body !== undefined) finalHeaders["Content-Type"] = "application/json";
  if (token) finalHeaders["X-FSF-Token"] = token;
  if (idempotencyKey) finalHeaders["X-Idempotency-Key"] = idempotencyKey;

  let resp;
  try {
    resp = await fetch(url, {
      method,
      headers: finalHeaders,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (netErr) {
    // Network-level failure (daemon not running, CORS, DNS). Surface it as
    // a synthetic 0 so the UI can distinguish "can't reach daemon" from
    // "daemon said no".
    throw new ApiError("network error: " + netErr.message, {
      status: 0,
      detail: { detail: netErr.message },
      body: null,
      url,
    });
  }

  const raw = await resp.text();
  let parsed = null;
  if (raw) {
    try {
      parsed = JSON.parse(raw);
    } catch {
      // Non-JSON body — odd from this daemon but tolerate it.
    }
  }

  if (!resp.ok) {
    const detailStr =
      (parsed && typeof parsed.detail === "string" && parsed.detail) ||
      (parsed && parsed.detail && JSON.stringify(parsed.detail)) ||
      resp.statusText ||
      "request failed";
    throw new ApiError(`${resp.status} ${detailStr}`, {
      status: resp.status,
      detail: parsed,
      body: raw,
      url,
    });
  }

  return parsed;
}

/** Thin method helpers. */
export const api = {
  get: (path, opts) => request(path, { ...opts, method: "GET" }),
  post: (path, body, opts) => request(path, { ...opts, method: "POST", body }),
  put: (path, body, opts) => request(path, { ...opts, method: "PUT", body }),
  // DELETE is idempotent by definition so we don't bake an idempotency
  // key in. Auth still flows via the X-FSF-Token header set on
  // request(); 401 retry is the caller's job (memory.js drives a
  // single retry via the same auth flow used by writeCall).
  del: (path, opts) => request(path, { ...opts, method: "DELETE" }),
};

/**
 * A concrete "write" helper that bakes in:
 *   - idempotency key generation
 *   - 401 -> prompt for token (once, via the callback)
 *   - returns the parsed JSON on success
 *
 * `onAuthRequired` is called when the server returns 401; the caller can
 * show a prompt, set a token via setToken(), and we'll retry exactly once.
 */
export async function writeCall(path, body, { onAuthRequired } = {}) {
  const idempotencyKey = makeIdempotencyKey();
  try {
    return await api.post(path, body, { idempotencyKey });
  } catch (e) {
    if (e instanceof ApiError && e.status === 401 && onAuthRequired) {
      const retried = await onAuthRequired();
      if (retried) {
        // Reuse the same idempotency key — if the first attempt somehow
        // reached the server before the 401 (it shouldn't for auth, but
        // defense in depth), we still get the cached response.
        return await api.post(path, body, { idempotencyKey });
      }
    }
    throw e;
  }
}
