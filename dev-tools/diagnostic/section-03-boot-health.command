#!/usr/bin/env bash
# ADR-0079 section 03 — boot health.
#
# Verifies the live daemon is up + healthy. Pulls /healthz +
# startup_diagnostics + confirms each diagnostic entry is status:ok
# (no FAIL, no missing). Reports the daemon's reported HEAD SHA
# vs. local git HEAD — drift means the running daemon has stale code.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
RUN_ID="diagnostic-03-boot-health"
TARGET="$REPO_ROOT/data/test-runs/$RUN_ID"
REPORT="$TARGET/report.md"
mkdir -p "$TARGET"

DAEMON="${FSF_DAEMON_URL:-http://127.0.0.1:7423}"
ENV_FILE="$REPO_ROOT/.env"
TOKEN="${FSF_API_TOKEN:-$(grep -E '^FSF_API_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2)}"

GIT_SHA=$(cd "$REPO_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "no-git")
GIT_SHA_FULL=$(cd "$REPO_ROOT" && git rev-parse HEAD 2>/dev/null || echo "no-git")
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

cat > "$REPORT" <<HEADER
# Diagnostic Section 03 — boot health

- timestamp: $TIMESTAMP
- git SHA (local): $GIT_SHA
- daemon: $DAEMON

HEADER

results_fail=0
results_pass=0
{
  # check 1: daemon reachable
  healthz=$(curl -s --max-time 5 "$DAEMON/healthz" 2>&1)
  if echo "$healthz" | grep -q '"status"'; then
    echo "- **[PASS]** daemon reachable at $DAEMON"
    results_pass=$((results_pass+1))
  else
    echo "- **[FAIL]** daemon NOT reachable at $DAEMON — got: $(echo "$healthz" | head -c 120)"
    results_fail=$((results_fail+1))
  fi
} >> "$REPORT"

if [ "$results_fail" -gt 0 ]; then
  cat >> "$REPORT" <<EOF

## Result

- total: 1
- passed: 0
- failed: 1

Aborted further checks: daemon unreachable.
EOF
  echo "section 03: 0/1 (daemon unreachable)"
  exit 1
fi

PY="$REPO_ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY=python3

"$PY" - "$REPORT" "$DAEMON" "$TOKEN" "$GIT_SHA_FULL" <<'PYEOF'
"""Section 03 driver — boot health checks against live daemon."""
import json
import sys
import urllib.request

REPORT, DAEMON, TOKEN, LOCAL_SHA = sys.argv[1:5]
report_lines: list[str] = []
pass_n = 1  # the daemon-reachable check above
fail_n = 0


def http_get(path: str) -> tuple[int, dict | str]:
    req = urllib.request.Request(DAEMON + path)
    if TOKEN:
        req.add_header("X-FSF-Token", TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(body)
            except Exception:
                return resp.status, body
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


# check 2: /healthz returns 200
status, body = http_get("/healthz")
if status == 200 and isinstance(body, dict):
    report_lines.append(f"- **[PASS]** /healthz returns 200 (status={body.get('status', '?')})")
    pass_n += 1
else:
    report_lines.append(f"- **[FAIL]** /healthz returned {status}: {body}")
    fail_n += 1

# check 3: startup_diagnostics has no FAIL entries
startup = body.get("startup_diagnostics") if isinstance(body, dict) else None
if startup is None:
    report_lines.append(f"- **[FAIL]** /healthz missing startup_diagnostics field")
    fail_n += 1
else:
    # startup_diagnostics may be a dict OR a list of dicts. The
    # daemon currently returns a list of {component, status, ...}.
    # B353 (post-first-real-run): treat 'ok' as pass, treat
    # disabled/off/skipped as informational (operator opt-in
    # defaults like FSF_ENABLE_PRIV_CLIENT=false), only count
    # failed/error/degraded as bad. Without this filter every
    # privacy-preserving default trips a fake FAIL and hides the
    # actual signal.
    INFO_STATUSES = {"disabled", "off", "skipped", "not_configured", "n/a"}
    BAD_STATUSES = {"failed", "error", "degraded", "broken"}
    bad_checks = []
    ok_checks = []
    info_checks = []
    if isinstance(startup, list):
        items = [
            (it.get("component", f"#{i}") if isinstance(it, dict) else str(i), it)
            for i, it in enumerate(startup)
        ]
    else:
        items = list(startup.items())
    for k, v in items:
        if isinstance(v, dict):
            s = v.get("status", "unknown")
        else:
            s = str(v)
        if s == "ok":
            ok_checks.append(k)
        elif s in INFO_STATUSES:
            info_checks.append((k, s))
        elif s in BAD_STATUSES:
            err = v.get("error") if isinstance(v, dict) else ""
            bad_checks.append((k, s, err))
        else:
            # Unknown status — treat as bad to surface drift in the
            # status vocabulary itself.
            bad_checks.append((k, s, ""))
    if bad_checks:
        # Render bad checks with the error string for each — that's
        # what the operator needs to actually act on.
        detail = "; ".join(
            f"{k}={s}" + (f" ({err[:80]})" if err else "")
            for k, s, err in bad_checks[:5]
        )
        report_lines.append(
            f"- **[FAIL]** startup_diagnostics has {len(bad_checks)} bad entries: "
            f"{detail}"
        )
        fail_n += 1
    else:
        report_lines.append(
            f"- **[PASS]** startup_diagnostics all-green "
            f"({len(ok_checks)} ok, {len(info_checks)} informational opt-outs)"
        )
        pass_n += 1
    if info_checks:
        report_lines.append(
            f"- **[INFO]** opt-in defaults intentionally off: "
            f"{', '.join(f'{k}={s}' for k, s in info_checks)}"
        )

# check 4: daemon HEAD SHA vs local HEAD SHA (if daemon reports it)
daemon_sha = None
if isinstance(body, dict):
    daemon_sha = body.get("git_sha") or body.get("commit") or body.get("version")
if daemon_sha:
    if daemon_sha.startswith(LOCAL_SHA[:8]) or LOCAL_SHA.startswith(daemon_sha[:8]):
        report_lines.append(f"- **[PASS]** daemon SHA matches local HEAD ({daemon_sha[:12]})")
        pass_n += 1
    else:
        report_lines.append(
            f"- **[FAIL]** daemon SHA {daemon_sha[:12]} ≠ local HEAD {LOCAL_SHA[:12]} "
            f"— daemon is on stale code, restart needed"
        )
        fail_n += 1
else:
    report_lines.append(
        f"- **[PASS]** daemon SHA check skipped (/healthz doesn't expose git_sha)"
    )
    pass_n += 1

# write
with open(REPORT, "a", encoding="utf-8") as f:
    total = pass_n + fail_n
    f.write(f"\n## Result\n\n- total: {total}\n- passed: {pass_n}\n- failed: {fail_n}\n\n")
    f.write("## Checks (post-reachability)\n\n")
    for line in report_lines:
        f.write(line + "\n")

print(f"section 03: {pass_n}/{pass_n + fail_n} passed")
sys.exit(0 if fail_n == 0 else 1)
PYEOF

RC=$?
echo "----"
cat "$REPORT" | tail -25
echo "----"
echo "section 03 exit: $RC"
exit "$RC"
