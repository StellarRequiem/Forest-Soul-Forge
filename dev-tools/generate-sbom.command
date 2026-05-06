#!/usr/bin/env bash
# Forest Soul Forge — generate Software Bill of Materials (SBOM).
#
# B150 (T26 security hardening). Uses cyclonedx-py to generate a
# CycloneDX-format SBOM from the active venv's installed packages.
# CycloneDX is the OWASP-backed industry standard for SBOMs.
#
# Output: dependencies/sbom.json (CycloneDX 1.5 JSON)
#
# Why an SBOM matters:
#   - Supply-chain audit: external integrators see exactly what
#     transitive deps Forest pulls in
#   - CVE response: when a CVE drops, grep the SBOM to see if Forest
#     is affected without re-downloading the dep tree
#   - Compliance: SOC2 / FedRAMP / similar frameworks require SBOMs
#     for any software you ship
#
# The SBOM reflects what's INSTALLED in the venv right now. To get
# a complete picture, install ALL extras first:
#   pip install -e ".[daemon,dev,browser,conformance]"
# then run this script.
#
# Pairs with pin-deps.command — that script generates the LOCKFILES;
# this generates the inventory of what's actually installed.

set -uo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

bar() { printf '\n========== %s ==========\n' "$1"; }

bar "1. preflight: venv + cyclonedx-py"
if [[ ! -x ".venv/bin/python" ]]; then
  echo "  ✗ no .venv/bin/python — run start.command first to bootstrap"
  echo ""; echo "Press return to close."; read -r _; exit 1
fi
PY=".venv/bin/python"
PIP=".venv/bin/pip"

if ! "$PY" -c "import cyclonedx" >/dev/null 2>&1; then
  echo "  installing cyclonedx-bom into .venv (provides cyclonedx-py CLI)..."
  "$PIP" install --quiet cyclonedx-bom >/dev/null
fi
CYCLONEDX_VER=$("$PY" -c 'import cyclonedx_py; print(getattr(cyclonedx_py,"__version__","unknown"))' 2>/dev/null || echo "unknown")
echo "  ✓ cyclonedx-py available (version=$CYCLONEDX_VER)"

bar "2. ensure dependencies/ exists"
mkdir -p dependencies
echo "  ✓ dependencies/ ready"

bar "3. generate SBOM (CycloneDX 1.5 JSON)"
"$PY" -m cyclonedx_py environment \
    --output-format JSON \
    --output-file dependencies/sbom.json \
    --schema-version 1.5 \
    --of JSON \
    2>/dev/null \
  && echo "  ✓ dependencies/sbom.json ($(wc -c < dependencies/sbom.json | tr -d ' ') bytes)" \
  || {
    # Fallback: older cyclonedx_py CLI shape
    "$PY" -m cyclonedx_py -e -o dependencies/sbom.json --format json 2>/dev/null \
      && echo "  ✓ dependencies/sbom.json (fallback CLI shape)" \
      || { echo "  ✗ cyclonedx_py CLI failed both invocations"; exit 1; }
  }

bar "4. summary"
echo "  Top-level component count:"
"$PY" -c "
import json
with open('dependencies/sbom.json') as f:
    sbom = json.load(f)
comps = sbom.get('components', [])
print(f'    {len(comps)} components')
print(f'    schema: {sbom.get(\"specVersion\", \"?\")}, format: {sbom.get(\"bomFormat\", \"?\")}')
print(f'    serialNumber: {sbom.get(\"serialNumber\", \"?\")[:60]}…')
"

cat <<'EOF'

  Inspect:
      cat dependencies/sbom.json | jq '.components[] | {name, version, purl}'

  CVE-grep:
      cat dependencies/sbom.json | jq -r '.components[] | "\(.name) \(.version)"' \
        | xargs -I{} echo "check: {}"

  Re-run after changing installed packages (e.g., pip install / upgrade).
EOF

echo ""
echo "Done. Press return to close."
read -r _
