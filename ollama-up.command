#!/usr/bin/env bash
# Ollama verification launcher — Task #19 path B.
#
# Double-click from Finder. Runs with your real network (sandbox can't
# reach docker hub or github). Does:
#   1. bring up ollama service via --profile llm
#   2. pull llama3.1:8b (the daemon's default)
#   3. verify daemon container can reach ollama:11434 on compose net
#   4. confirm /runtime/provider reports local=ok, model loaded
#   5. ad-hoc completion probe — runs LocalProvider.complete() *inside
#      the daemon container* so we exercise the real code path
#
# This script is a throwaway verifier. Not committed (gitignored via
# *.command in .gitignore? check — if not, we remove after green run).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

bar() { printf '\n=== %s ===\n' "$1"; }

bar "0. pre-flight: docker + compose stack state"
docker --version
docker compose ps

bar "1. bring up daemon + frontend + ollama (profile llm)"
# Profile flag brings up profile-gated services (ollama) AND non-gated ones
# (daemon, frontend). Already-running services are left alone — idempotent.
docker compose --profile llm up -d

bar "2. wait for ollama /api/tags (up to 120s)"
for i in $(seq 1 60); do
    if curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
        echo "ollama responsive after ${i}x2s"
        break
    fi
    printf '.'
    sleep 2
done
echo

bar "3. pull llama3.2:1b (~1.3GB — fits in default Docker 2.8 GiB)"
# llama3.1:8b needs 4.8 GiB at runtime; default Docker Desktop memory
# (~2 GiB) can't load it. Use a small model to verify the wire; bump
# Docker memory in Settings -> Resources before running 8B+ models.
docker exec fsf-ollama ollama pull llama3.2:1b

bar "4. ollama list (verify model present inside the container)"
docker exec fsf-ollama ollama list

bar "5. daemon -> ollama reachability over compose network"
docker exec fsf-daemon curl -fsS http://ollama:11434/api/tags | head -c 400
echo

bar "6. GET /runtime/provider (should show local=ok, llama3.1:8b loaded)"
curl -fsS http://127.0.0.1:7423/runtime/provider | python3 -m json.tool

bar "7. ad-hoc completion probe via LocalProvider.complete()"
# Runs inside the daemon container to exercise the real Python path
# (httpx client, URL building, response-shape validation) against the
# live Ollama backend. Not an HTTP endpoint — we already established
# the daemon has no completion endpoint. This is the closest we can
# get to proof-of-wire without shipping new surface area.
docker exec fsf-daemon python -c '
import asyncio
from forest_soul_forge.daemon.providers.local import LocalProvider
from forest_soul_forge.daemon.providers import TaskKind

async def main():
    p = LocalProvider(
        base_url="http://ollama:11434",
        models={k: "llama3.2:1b" for k in TaskKind},
        timeout_s=120.0,
    )
    # Healthcheck first — should report OK with model loaded.
    h = await p.healthcheck()
    print("healthcheck:", h.status, "loaded:", h.details.get("loaded"), "missing:", h.details.get("missing"))
    # Then the completion.
    out = await p.complete(
        "Reply with exactly: FSF wire check OK",
        task_kind=TaskKind.CONVERSATION,
        system="You are a terse diagnostic echo. Respond with the exact phrase you are asked to repeat, nothing else.",
        max_tokens=32,
    )
    print("---PROVIDER OUTPUT---")
    print(out)
    print("---END---")

asyncio.run(main())
'

bar "DONE"
echo "Press return to close."
read -r _
