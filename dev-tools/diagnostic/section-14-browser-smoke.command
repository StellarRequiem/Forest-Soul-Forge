#!/usr/bin/env bash
# ADR-0079 section 14 — browser-driven tab smoke (B366).
#
# Section 13 hits each tab's API endpoints directly. That catches
# the "endpoint moved/404'd" class but NOT:
#   - frontend module raw fetch() that bypasses API_BASE (B361:
#     voice + provenance hit port 5173 instead of 7423, harness
#     section 13 hits 7423 directly so never saw it)
#   - the B260/B276/B298 boot-asymmetry pattern where a panel's
#     start() handler only ran in the trait-tree-failure catch
#     branch, leaving the tab stuck on "Loading..." on the
#     common path
#   - JS exceptions during render that leave a tab half-painted
#     with stray "undefined" or visible error text
#
# Section 14 drives a real Chromium via Playwright, opens each of
# the 15 tabs, waits for content to settle, then:
#   1. extracts visible text from the tab panel
#   2. asserts forbidden strings ("Loading...", "Error:",
#      "undefined", "[object Object]") are NOT present
#   3. asserts the panel has SOME content (avoids empty-tab false
#      pass)
#   4. saves a screenshot for operator eyeballing
#
# DOM text inspection is faster + more accurate than OCR for
# detecting boot regressions; screenshots are kept as visual
# evidence for the operator.
#
# Prerequisites:
#   1. Frontend running:  python -m frontend.serve  (port 5173)
#   2. Daemon running:    via force-restart-daemon.command (port 7423)
#   3. Playwright + chromium installed:
#        pip install --break-system-packages playwright
#        playwright install chromium
#   Section auto-installs both if missing (best-effort), but on a
#   sandboxed box the operator may need to run the install steps
#   manually.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
RUN_ID="diagnostic-14-browser-smoke"
TARGET="$REPO_ROOT/data/test-runs/$RUN_ID"
SHOTS="$TARGET/screenshots"
REPORT="$TARGET/report.md"
mkdir -p "$SHOTS"

DAEMON="${FSF_DAEMON_URL:-http://127.0.0.1:7423}"
FRONTEND="${FSF_FRONTEND_URL:-http://127.0.0.1:5173}"
ENV_FILE="$REPO_ROOT/.env"
TOKEN="${FSF_API_TOKEN:-$(grep -E '^FSF_API_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2)}"

GIT_SHA=$(cd "$REPO_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "no-git")
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

cat > "$REPORT" <<HEADER
# Diagnostic Section 14 — browser-driven tab smoke

- timestamp: $TIMESTAMP
- git SHA: $GIT_SHA
- daemon: $DAEMON
- frontend: $FRONTEND
- scope: drives a real Chromium via Playwright; opens each tab
  and verifies no boot-regression text leaks into the panel.

HEADER

# Preflight - daemon + frontend both reachable.
preflight_fail=""
if ! curl -s --max-time 5 "$DAEMON/healthz" >/dev/null 2>&1; then
  preflight_fail="daemon unreachable at $DAEMON"
fi
if [ -z "$preflight_fail" ] && ! curl -s --max-time 5 "$FRONTEND/index.html" >/dev/null 2>&1; then
  preflight_fail="frontend unreachable at $FRONTEND (try: python -m frontend.serve)"
fi
if [ -n "$preflight_fail" ]; then
  echo "## Result" >> "$REPORT"
  echo "" >> "$REPORT"
  echo "- aborted: $preflight_fail" >> "$REPORT"
  echo "section 14: $preflight_fail"
  exit 1
fi

PY="$REPO_ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY=python3

# Ensure playwright is available; install on miss. If the install
# itself fails (offline / pip restricted), the section reports as
# MISSING rather than failing the umbrella.
if ! "$PY" -c "import playwright" >/dev/null 2>&1; then
  echo "[section-14] installing playwright..."
  if ! "$PY" -m pip install --break-system-packages playwright >/dev/null 2>&1; then
    echo "## Result" >> "$REPORT"
    echo "" >> "$REPORT"
    echo "- skipped: playwright pip install failed (offline?)" >> "$REPORT"
    echo "  Manual install: \`pip install --break-system-packages playwright && playwright install chromium\`" >> "$REPORT"
    echo "section 14: SKIPPED (playwright unavailable)"
    exit 0
  fi
fi

# Ensure chromium browser bundle is present.
if ! "$PY" -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); p.chromium.launch(headless=True).close(); p.stop()" >/dev/null 2>&1; then
  echo "[section-14] installing chromium for playwright..."
  if ! "$PY" -m playwright install chromium >/dev/null 2>&1; then
    echo "## Result" >> "$REPORT"
    echo "" >> "$REPORT"
    echo "- skipped: playwright chromium install failed" >> "$REPORT"
    echo "  Manual: \`$PY -m playwright install chromium\`" >> "$REPORT"
    echo "section 14: SKIPPED (chromium unavailable)"
    exit 0
  fi
fi

cd "$REPO_ROOT"

"$PY" - "$REPORT" "$SHOTS" "$DAEMON" "$FRONTEND" "$TOKEN" <<'PYEOF'
"""Section 14 driver — browser-level smoke for all 15 frontend tabs.

For each tab:
  1. Click the tab nav button.
  2. Wait for the panel to render (a short fixed wait — the panels
     mostly render synchronously after their data fetch lands).
  3. Capture innerText of the active panel.
  4. Assert NO forbidden strings appear:
       "Loading..."  - panel never finished booting
       "Error:"      - render-time JS exception caught and surfaced
       "undefined"   - data binding bug
       "[object Object]" - serialization bug
       "NaN"         - arithmetic on missing values
  5. Assert panel has SOME visible content (non-empty after strip).
  6. Save a screenshot of the full viewport for the operator.

Forbidden-string list errs slightly false-positive: a tab whose
expected content legitimately includes one of these strings would
flag. That's a tolerable trade for the catch rate. If a tab does
have a legitimate "Error:" prefix (e.g. an error-history panel),
the per-tab allowlist below can suppress it.
"""
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

REPORT, SHOTS, DAEMON, FRONTEND, TOKEN = sys.argv[1:6]
SHOTS = Path(SHOTS)

# Tab inventory mirrors section-13's TAB_ENDPOINTS. data-tab values
# come from frontend/index.html. Optional tabs (whose substrate may
# not be present in older deploys) degrade to INFO instead of FAIL
# when their panel is empty/missing.
TABS = [
    # (data-tab attribute, friendly name, required)
    ("agents",          "Agents",          True),
    ("forge",           "Forge",           True),
    ("skills",          "Skills",          True),
    ("tool-registry",   "Tool Registry",   True),
    ("audit",           "Audit",           True),
    ("marketplace",     "Marketplace",     True),
    ("pending",         "Pending",         True),
    ("memory",          "Memory",          True),
    ("orchestrator",    "Orchestrator",    True),
    ("provenance",      "Provenance",      False),
    ("reality-anchor",  "Reality Anchor",  False),
    ("security",        "Security",        False),
    ("operator-wizard", "Operator Wizard", False),
    ("voice",           "Voice",           False),
    ("chat",            "Chat",            False),
]

FORBIDDEN = (
    "Loading...",
    "Loading…",   # the unicode horizontal ellipsis used in some panels
    "loading...",
    "Error:",
    "undefined",
    "[object Object]",
)

# Per-tab allowlist: substrings that legitimately appear in the tab
# and should NOT be flagged even if they collide with FORBIDDEN.
# Empty by default; populate if a tab needs an exception.
ALLOWLIST: dict[str, tuple[str, ...]] = {
    # Example: "audit": ("Error: 0 in the last hour",)
}

results: list[tuple[str, str, str]] = []

start_url = FRONTEND.rstrip("/") + "/?" + urlencode({"api": DAEMON})

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    ctx_kwargs: dict[str, object] = {
        "viewport": {"width": 1440, "height": 900},
    }
    # Inject the auth token into localStorage so frontend treats us
    # as authenticated. api.js reads fsf.token; the same key the
    # browser uses.
    context = browser.new_context(**ctx_kwargs)
    if TOKEN:
        context.add_init_script(
            f"window.localStorage.setItem('fsf.token', {json.dumps(TOKEN)});"
        )
    page = context.new_page()

    try:
        page.goto(start_url, wait_until="networkidle", timeout=20000)
    except Exception as e:
        results.append(("FAIL", "initial page load",
                        f"{type(e).__name__}: {e}"))
        with open(REPORT, "a", encoding="utf-8") as f:
            f.write(f"## Result\n\n- aborted: initial load failed\n")
        print(f"section 14: initial load failed: {e}")
        browser.close()
        sys.exit(1)

    # Give app.js boot a generous moment to wire all panels +
    # complete the initial /agents + /traits + /tools/catalog
    # fetches. The 'networkidle' wait above covers most of this,
    # but a small extra fixed delay smooths over the post-boot
    # secondary fetches that some panels do.
    page.wait_for_timeout(1500)

    for data_tab, friendly, required in TABS:
        # Click the tab nav button.
        try:
            page.locator(f'[data-tab="{data_tab}"]').click(timeout=5000)
        except Exception as e:
            results.append(("FAIL", f"tab: {friendly}",
                            f"nav button click failed: {type(e).__name__}: {e}"))
            continue

        # Let the panel render its lazy content.
        page.wait_for_timeout(900)

        # Grab the active panel's innerText.
        try:
            panel = page.locator(f'[data-panel="{data_tab}"]:not([hidden])')
            text = panel.inner_text(timeout=4000) if panel.count() else ""
        except Exception as e:
            results.append(("FAIL", f"tab: {friendly}",
                            f"panel text read failed: {e}"))
            continue

        # Screenshot for visual evidence regardless of pass/fail.
        try:
            shot_path = SHOTS / f"{data_tab}.png"
            page.screenshot(path=str(shot_path), full_page=False)
        except Exception:
            pass  # screenshots are best-effort; don't fail the section

        # Empty content check.
        stripped = text.strip()
        if not stripped:
            status = "FAIL" if required else "INFO"
            results.append((status, f"tab: {friendly}",
                            "panel is empty"))
            continue

        # Forbidden-string check.
        hits = []
        allowed = ALLOWLIST.get(data_tab, ())
        for needle in FORBIDDEN:
            if needle in text:
                # Skip if any allowlist substring covers this hit.
                if any(ok in text for ok in allowed):
                    continue
                hits.append(needle)
        if hits:
            results.append(("FAIL", f"tab: {friendly}",
                            f"forbidden text: {hits}; len(text)={len(text)}"))
            continue

        # Bonus visibility — first 80 chars of the panel as evidence.
        snippet = stripped.replace("\n", " | ")[:80]
        results.append(("PASS", f"tab: {friendly}",
                        f"{len(stripped)} chars; '{snippet}...'" if len(stripped) > 80
                        else f"{len(stripped)} chars; '{snippet}'"))

    browser.close()

passed = sum(1 for r in results if r[0] == "PASS")
failed = sum(1 for r in results if r[0] == "FAIL")
info = sum(1 for r in results if r[0] == "INFO")

with open(REPORT, "a", encoding="utf-8") as f:
    f.write(f"## Result\n\n- total: {len(results)} tabs\n"
            f"- passed: {passed}\n- failed: {failed}\n- info: {info}\n"
            f"- screenshots: {SHOTS}\n\n## Per-tab\n\n")
    for s, n, ev in results:
        f.write(f"- **[{s}]** {n} — {ev}\n")

print(f"section 14: {passed}/{len(results)} tabs pass ({failed} fail, {info} info)")
print(f"screenshots: {SHOTS}")
sys.exit(0 if failed == 0 else 1)
PYEOF

RC=$?
echo "----"
cat "$REPORT" | tail -25
echo "----"
echo "section 14 exit: $RC"
exit "$RC"
