#!/bin/bash
# Install the missing `cryptography` runtime dep. Daemon import chain
# hit ModuleNotFoundError: No module named 'cryptography' even though
# pyproject lists it. This pulls it directly into the venv.

set -e
cd "$(dirname "$0")"

echo "=== installing cryptography into venv ==="
.venv/bin/pip install "cryptography>=42.0"
echo ""
echo "=== verifying import ==="
.venv/bin/python -c "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey; print('cryptography OK')"
echo ""
echo "=== verifying daemon app import ==="
.venv/bin/python -c "import forest_soul_forge.daemon.app; print('daemon.app OK')"
echo ""
echo "Done. Now run force-restart-daemon.command to bring the daemon back up."
echo "Press return to close."
read -r
