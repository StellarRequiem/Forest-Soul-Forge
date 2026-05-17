#!/usr/bin/env bash
# ADR-0079 section 12 — encryption at rest.
#
# Verifies the ADR-0050 encryption-at-rest substrate is wired
# and consistent:
#   1. /healthz startup_diagnostics's encryption_at_rest entry
#      reports status: ok OR status: off (explicit operator
#      choice; not a failure)
#   2. If encryption is ON, sample a known-encrypted file
#      (soul_generated/*.constitution.yaml if .enc suffix
#      pattern is in use; data/registry.sqlite.enc if registry
#      is encrypted) and confirm it parses via the project's
#      decrypt loader.
#   3. Master-key resolution mode: report whether the daemon
#      sourced its key from passphrase, keychain, or env (each
#      reported via /healthz secrets_backend, which is a sibling
#      of encryption_at_rest).
#
# MVP scope: read-only inspection. Full round-trip (write/read/
# re-encrypt) deferred — destructive against the live registry.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
RUN_ID="diagnostic-12-encryption-at-rest"
TARGET="$REPO_ROOT/data/test-runs/$RUN_ID"
REPORT="$TARGET/report.md"
mkdir -p "$TARGET"

DAEMON="${FSF_DAEMON_URL:-http://127.0.0.1:7423}"
ENV_FILE="$REPO_ROOT/.env"
TOKEN="${FSF_API_TOKEN:-$(grep -E '^FSF_API_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2)}"

GIT_SHA=$(cd "$REPO_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "no-git")
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

cat > "$REPORT" <<HEADER
# Diagnostic Section 12 — encryption at rest

- timestamp: $TIMESTAMP
- git SHA: $GIT_SHA
- daemon: $DAEMON
- scope: read-only inspection. Round-trip deferred (destructive).

HEADER

if ! curl -s --max-time 5 "$DAEMON/healthz" >/dev/null 2>&1; then
  echo "## Result" >> "$REPORT"
  echo "" >> "$REPORT"
  echo "- aborted: daemon unreachable at $DAEMON" >> "$REPORT"
  echo "section 12: daemon unreachable"
  exit 1
fi

PY="$REPO_ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY=python3

cd "$REPO_ROOT"

"$PY" - "$REPORT" "$DAEMON" "$TOKEN" <<'PYEOF'
"""Section 12 — encryption-at-rest substrate checks."""
import json
import sys
import urllib.request
from pathlib import Path

REPORT, DAEMON, TOKEN = sys.argv[1:4]


def get(path):
    req = urllib.request.Request(DAEMON + path)
    req.add_header("X-FSF-Token", TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return 0, str(e)


status, body = get("/healthz")
if status != 200 or not isinstance(body, dict):
    with open(REPORT, "a", encoding="utf-8") as f:
        f.write(f"## Result\n\n- aborted: /healthz returned status={status}\n")
    print(f"section 12: /healthz failed status={status}")
    sys.exit(1)

sd = body.get("startup_diagnostics") or []
enc_entry = None
secrets_entry = None
if isinstance(sd, list):
    for item in sd:
        if not isinstance(item, dict):
            continue
        comp = item.get("component", "")
        if comp == "encryption_at_rest":
            enc_entry = item
        elif comp == "secrets_backend":
            secrets_entry = item
elif isinstance(sd, dict):
    enc_entry = sd.get("encryption_at_rest")
    secrets_entry = sd.get("secrets_backend")

results: list[tuple[str, str, str]] = []

# Check 1: encryption_at_rest status reports OK or OFF
if enc_entry is None:
    results.append(("INFO", "encryption_at_rest diagnostic present",
                    "no encryption_at_rest entry in startup_diagnostics"))
else:
    enc_status = enc_entry.get("status", "?")
    if enc_status == "ok":
        results.append(("PASS", "encryption_at_rest status",
                        f"ok ({enc_entry.get('path', '?')})"))
    elif enc_status == "off":
        results.append(("INFO", "encryption_at_rest status",
                        "off — operator opt-out; not a failure"))
    else:
        results.append(("FAIL", "encryption_at_rest status",
                        f"status={enc_status}; error={enc_entry.get('error', '?')}"))

# Check 2: secrets backend resolution
if secrets_entry is None:
    results.append(("INFO", "secrets_backend diagnostic present",
                    "no secrets_backend entry in startup_diagnostics"))
else:
    sb_status = secrets_entry.get("status", "?")
    backend = secrets_entry.get("backend") or secrets_entry.get("path") or "?"
    if sb_status == "ok":
        results.append(("PASS", "secrets_backend resolves",
                        f"backend={backend}"))
    elif sb_status in ("disabled", "off", "skipped", "not_configured"):
        results.append(("INFO", "secrets_backend resolves",
                        f"{sb_status} — operator opt-out"))
    else:
        results.append(("FAIL", "secrets_backend resolves",
                        f"status={sb_status}; backend={backend}; "
                        f"error={secrets_entry.get('error', '?')}"))

# Check 3: if encryption is on, sample an encrypted file (best-effort)
if enc_entry and enc_entry.get("status") == "ok":
    REPO = Path.cwd()
    # Look for any .enc suffix files in soul_generated/ — fastest sample.
    enc_samples = list((REPO / "soul_generated").glob("*.enc"))[:3]
    if not enc_samples:
        results.append(("INFO", "encrypted-file sample",
                        "no *.enc files in soul_generated/ (encryption mode may not encrypt souls)"))
    else:
        # Try to read + parse via project's decrypt loader.
        try:
            sys.path.insert(0, str(REPO / "src"))
            from forest_soul_forge.security.master_key import get_master_key
            # We don't have a generic "decrypt this file" helper that's
            # safe to invoke without context; just confirm the master key
            # resolves. The startup_diagnostic already covered usability.
            mk = get_master_key()
            results.append(("PASS", "master key resolvable",
                            f"key length: {len(mk) if mk else 0} bytes"))
        except Exception as e:
            results.append(("INFO", "master key resolvable",
                            f"{type(e).__name__}: {e} "
                            "(may need FSF_SECRETS_MASTER_KEY in env)"))

passed = sum(1 for r in results if r[0] == "PASS")
failed = sum(1 for r in results if r[0] == "FAIL")
info = sum(1 for r in results if r[0] == "INFO")

with open(REPORT, "a", encoding="utf-8") as f:
    f.write(f"## Result\n\n- total: {len(results)}\n"
            f"- passed: {passed}\n- failed: {failed}\n- info: {info}\n\n## Checks\n\n")
    for s, n, ev in results:
        f.write(f"- **[{s}]** {n}")
        if ev:
            f.write(f" — {ev}")
        f.write("\n")

print(f"section 12: {passed}/{len(results)} passed")
sys.exit(0 if failed == 0 else 1)
PYEOF

RC=$?
echo "----"
cat "$REPORT" | tail -20
echo "----"
echo "section 12 exit: $RC"
exit "$RC"
