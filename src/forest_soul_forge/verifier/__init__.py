"""ADR-0036 Verifier Loop — auto-detected memory contradictions.

The Verifier is a Guardian-genre agent (T1, role + constitutional
template) born via standard /birth. Its action surface is
memory_flag_contradiction.v1 (T2). This package owns T3-T5: the
scan runner that walks pairs from Memory.find_candidate_pairs,
classifies them via an LLM, and stamps contradictions when
high-confidence cases surface.

Modules:
- scan.py — VerifierScan class + run_scan() runner (T3b)

Future modules (queued):
- scheduler.py — per-Verifier cron via existing scheduled-task surface (T4)
- endpoint.py wired into daemon/routers — /verifier/scan on-demand (T5)
"""
from forest_soul_forge.verifier.scan import (
    ClassificationResult,
    ScanResult,
    VerifierScan,
    build_classification_prompt,
    parse_llm_classification,
)

__all__ = [
    "ClassificationResult",
    "ScanResult",
    "VerifierScan",
    "build_classification_prompt",
    "parse_llm_classification",
]
