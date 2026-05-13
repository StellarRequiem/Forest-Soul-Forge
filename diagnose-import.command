#!/bin/bash
# Diagnose why `import forest_soul_forge.daemon.app` fails on the host.
# start.command suppresses stderr from the same check; this script
# surfaces the actual traceback so we can fix it.

set +e
cd "$(dirname "$0")"

echo "=== diagnose-import ==="
echo "cwd:    $(pwd)"
echo "python: $(.venv/bin/python --version 2>&1)"
echo ""
echo "--- attempting: import forest_soul_forge.daemon.app ---"
.venv/bin/python -c "import forest_soul_forge.daemon.app; print('OK: import succeeded')"
rc=$?
echo ""
echo "rc=$rc"
echo ""
echo "Press return to close."
read -r
