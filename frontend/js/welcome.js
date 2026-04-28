// Welcome banner — first-load context for new users.
//
// Shown by default; dismissible with the close button. Dismissal is
// remembered in localStorage so returning users don't see it again.
// Demo-friction audit P1 #7 (docs/audits/2026-04-28-demo-friction-audit.md).
//
// Reset behavior: clear localStorage `fsf:welcomeDismissed` to see it again,
// or open in a private window. (We don't expose a "show again" button in
// the UI yet — that lands with F5 self-serve tour controls.)

const STORAGE_KEY = "fsf:welcomeDismissed";

export function start() {
  const banner = document.getElementById("welcome");
  const closeBtn = document.getElementById("welcome-close");
  if (!banner || !closeBtn) return;

  const dismissed = readDismissed();
  if (!dismissed) {
    banner.hidden = false;
  }

  closeBtn.addEventListener("click", () => {
    banner.hidden = true;
    writeDismissed();
  });
}

function readDismissed() {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    // localStorage can throw in private mode / disabled storage. Default
    // to "show banner" rather than "hide forever" — first impression is
    // worth more than the minor annoyance of dismissing each session.
    return false;
  }
}

function writeDismissed() {
  try {
    window.localStorage.setItem(STORAGE_KEY, "1");
  } catch {
    /* storage unavailable — banner will reappear next load */
  }
}
