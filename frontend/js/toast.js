// Minimal toast system. Appends a DOM node to #toasts, auto-dismisses after
// a few seconds. `kind` is "info" (default), "warn", or "error".

const CONTAINER_ID = "toasts";
const DEFAULT_TTL_MS = 4500;

export function toast({ title, msg, kind = "info", ttl = DEFAULT_TTL_MS } = {}) {
  const container = document.getElementById(CONTAINER_ID);
  if (!container) return;

  const el = document.createElement("div");
  el.className = "toast";
  if (kind === "error") el.classList.add("toast--error");
  if (kind === "warn") el.classList.add("toast--warn");

  if (title) {
    const t = document.createElement("div");
    t.className = "toast__title";
    t.textContent = title;
    el.appendChild(t);
  }
  if (msg) {
    const m = document.createElement("div");
    m.className = "toast__msg";
    m.textContent = msg;
    el.appendChild(m);
  }

  container.appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transition = "opacity 150ms";
    setTimeout(() => el.remove(), 160);
  }, ttl);
}
