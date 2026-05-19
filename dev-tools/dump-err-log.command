#!/usr/bin/env bash
# Dump the last 300 lines of the daemon err log to a workspace file
# the Cowork sandbox can read.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
tail -300 /tmp/forest-daemon.err.log > _diagnostic_err_dump.txt 2>&1
echo "wrote $(wc -l < _diagnostic_err_dump.txt | tr -d ' ') lines to _diagnostic_err_dump.txt"
echo "first 5 lines:"
head -5 _diagnostic_err_dump.txt
echo ""
echo "Press return to close."
read -r _
