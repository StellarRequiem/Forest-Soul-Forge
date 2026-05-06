#!/bin/bash
# Burst 179 — ADR-0054 T2 — embedding adapter. Wires
# nomic-embed-text into the procedural-shortcut path so T1's
# ProceduralShortcutsTable.search_by_cosine has actual query
# vectors to match against stored shortcuts.
#
# Per ADR-0054 D2 the cosine + reinforcement gate already lives
# at the table layer (T1, B178). T2 is the input plumbing —
# operator-turn text becomes a unit-norm float32 numpy array via
# Ollama /api/embeddings.
#
# Scope: local-only. The provider Protocol exposes complete()
# which every backend implements; embed() is added as
# LocalProvider-specific in v0.1. Frontier providers fall
# through to llm_think (the procedural-shortcut path silently
# degrades to no-shortcut for frontier-only operators, which
# matches the "local-first" framing throughout Forest).
#
# What ships:
#
#   src/forest_soul_forge/daemon/providers/local.py:
#     LocalProvider gains an async embed(text, *, model=None)
#     method. POSTs {"model": ..., "prompt": text} to
#     /api/embeddings, parses the {"embedding": [float, ...]}
#     response. Default model nomic-embed-text:latest matches
#     Forest's standing baseline (visible in /healthz). Coerces
#     entries to float; ProviderError on non-float values
#     defenses against future Ollama API drift. Same error
#     vocabulary as complete() — ProviderUnavailable for
#     unreachable, ProviderError for non-2xx and malformed shape.
#
#   src/forest_soul_forge/core/memory/procedural_embedding.py
#     (NEW): embed_situation(provider, text, *, model=None) ->
#     numpy.ndarray. Calls the provider's embed(), validates
#     shape, normalizes to unit norm at the embedding seam (so
#     the table's stored vectors and the query vector are both
#     pre-normalized — cosine reduces to a single dot product
#     in search_by_cosine). All failure modes collapse to a
#     single EmbeddingError so the dispatcher's
#     ProceduralShortcutStep (T3) can catch one type. Refuses:
#       - empty / whitespace text
#       - non-string text
#       - provider without embed() (frontier providers fail fast)
#       - any provider exception (wrapped as EmbeddingError with
#         the underlying class + message preserved)
#       - malformed response shape (non-list, empty, non-1D)
#       - all-zero vectors (degenerate model output — better to
#         fall through to llm_think than match nothing forever)
#
#   tests/unit/test_procedural_embedding.py (NEW): 14 unit
#   tests with stubbed httpx + a fake-provider helper:
#     - LocalProvider.embed default model + payload shape
#       (POST /api/embeddings with model + prompt)
#     - explicit model override
#     - httpx RequestError → ProviderUnavailable
#     - HTTP 4xx/5xx → ProviderError
#     - malformed response (no embedding key) → ProviderError
#     - non-float entries in embedding → ProviderError
#     - embed_situation happy path (3,4) → unit-norm (0.6, 0.8)
#     - model arg passes through
#     - empty / whitespace / non-string text rejected
#     - provider without embed() rejected
#     - provider exception wrapped as EmbeddingError
#     - malformed response shapes (None, empty, non-list)
#     - all-zero vector rejected as degenerate
#
# Per ADR-0044 D3: zero kernel ABI surface changes. embed() is a
# new optional method on LocalProvider; ModelProvider Protocol
# stays as-is (the Protocol declares the minimum every provider
# must satisfy; LocalProvider can carry extras). The shortcut
# adapter checks hasattr(provider, 'embed') at runtime — a
# frontier-only deployment doesn't crash, it just doesn't get
# shortcuts.
#
# Verification:
#   PYTHONPATH=src:. pytest tests/unit/test_procedural_embedding.py
#                                tests/unit/test_procedural_shortcuts.py
#                                tests/unit/test_registry.py
#                                tests/unit/test_registry_concurrency.py
#   -> 67 passed (14 T2-new + 25 T1 + 28 cross-touch), 1
#      documented pre-existing xfail
#
# Substrate ready for T3 (ProceduralShortcutStep). The pipeline
# step calls embed_situation() to get the query vector + hands
# it to ProceduralShortcutsTable.search_by_cosine + emits a
# StepResult.shortcut(...) on a high-confidence match.
#
# Remaining ADR-0054 tranches:
#   T3 — ProceduralShortcutStep + StepResult.shortcut verdict
#   T4 — audit emission (tool_call_shortcut event type)
#   T5 — reinforcement tools (memory_tag_outcome.v1) + chat-tab
#        thumbs surface
#   T6 — settings UI + operator safety guide

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/providers/local.py \
        src/forest_soul_forge/core/memory/procedural_embedding.py \
        tests/unit/test_procedural_embedding.py \
        dev-tools/commit-bursts/commit-burst179-adr0054-t2-embedding-adapter.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(memory): ADR-0054 T2 — embedding adapter (B179)

Burst 179. Wires nomic-embed-text into the procedural-shortcut
path so T1's ProceduralShortcutsTable.search_by_cosine has
query vectors to match against stored shortcuts.

LocalProvider gains an async embed(text, model=None) method
calling Ollama /api/embeddings. Default model
nomic-embed-text:latest. ProviderUnavailable on unreachable,
ProviderError on non-2xx / malformed shape / non-float entries.

embed_situation(provider, text) helper at
src/forest_soul_forge/core/memory/procedural_embedding.py
returns a unit-norm float32 1-D numpy array. Normalization at
the embedding seam means table-stored and query vectors are
both pre-normalized; cosine reduces to a single dot product in
search_by_cosine. All failure modes collapse to EmbeddingError
so the dispatcher's ProceduralShortcutStep (T3) catches one
type. Refuses empty text, non-string text, providers without
embed(), wrapped provider exceptions, malformed response
shapes, and all-zero vectors.

14 unit tests with stubbed httpx + fake-provider helper.

Per ADR-0044 D3: zero kernel ABI surface changes. embed() is
LocalProvider-specific; ModelProvider Protocol unchanged.
Frontier-only deployments fall through to llm_think — the
shortcut surface silently degrades to no-shortcut, matching
Forest's local-first framing.

Verification: 67 passed across the touched-modules sweep.

Substrate ready for T3 (ProceduralShortcutStep) — the pipeline
step calls embed_situation, hands the vector to
search_by_cosine, emits StepResult.shortcut on a match."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 179 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
