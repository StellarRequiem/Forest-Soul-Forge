# Phase E — verification + cleanup verdicts

**Date:** 2026-04-30
**Author:** Forest Soul Forge harness
**Inputs:** comprehensive repo audit findings D-1 through M-9
**Operating principle:** §0 Hippocratic gate — every removal verified
before action; default to "keep with comment" when verification is
inconclusive.

## What this records

Phase E walked every "Cleanup" candidate from the comprehensive
repo audit (`docs/audits/2026-04-30-comprehensive-repo-audit.md`).
Each candidate got the 4-step §0 gate applied:

1. **Prove harm** — what test fails / feature breaks / user is misled?
2. **Prove non-load-bearing** — grep for imports + references; check
   ADRs for forward references.
3. **Prove the alternative is strictly better** — fix in place vs.
   deprecate-and-migrate vs. remove.
4. **Only if 1+2+3 all pass:** remove + record.

When verification was inconclusive, the default was "keep with
comment" rather than delete.

## Per-candidate verdicts

### E.1 — `src/forest_soul_forge/agents/` and `src/.../ui/` empty packages — VERDICT: **KEEP-WITH-COMMENT**

**§0 gate:**
1. Harm: none — empty packages don't break tests or runtime
2. Non-load-bearing: yes — zero imports anywhere in src/ or tests/
3. Alternative strictly better: KEEP wins because the package names
   carry forward intent for v0.3+ (agent factory, UI bridge).
   Deletion erases that intent without concrete benefit.

**Action taken:** Added explicit placeholder docstrings to:
- `src/forest_soul_forge/agents/__init__.py`
- `src/forest_soul_forge/agents/blue_team/__init__.py`
- `src/forest_soul_forge/ui/__init__.py`

Each docstring records:
- Status: empty by design at v0.1
- Why the package was scaffolded
- What forward-looking work it anchors
- That removal goes through §0 verification

### E.2 — `scripts/initial_push.sh` — VERDICT: **KEEP-WITH-GUARD**

**§0 gate:**
1. Harm: latent — the script contains `rm -rf .git`. Catastrophic
   if accidentally run; otherwise dormant.
2. Non-load-bearing: yes — bootstrap-era one-shot, repo init is done.
3. Alternative strictly better: a guarded version (header explaining
   historical-only status + `exit 1` early in the script) is
   strictly safer than either keeping the foot-gun or deleting the
   historical record.

**Action taken:** Added a header comment marking it HISTORICAL ONLY
+ an `exit 1` after the initial echo. The script can never run again
without an operator deliberately commenting out the guard. The
historical content (how the repo was bootstrapped) is preserved for
archaeology.

### E.3 — `t4-tests.command` README reference — VERDICT: **NO ACTION** (audit was a false positive)

The earlier audit reported the README mentions `t4-tests.command`
but the file isn't at root. Re-check with `ls t4-tests.command`
showed the file IS present; the original audit's `ls *.command`
output was truncated. Reference is correct.

### E.4 — `.env` tracked — VERDICT: **KEEP**

**§0 gate:**
1. Harm: none — content is non-sensitive (model name + narrative
   tuning, no secrets).
2. Non-load-bearing: it IS load-bearing — Docker Compose reads it
   for daemon config.
3. Alternative strictly better: untracking would break "clone and
   start" — strictly worse for new operators.

**Action taken:** None. Verified the .env contains only model name
+ FSF_NARRATIVE_MAX_TOKENS + FSF_NARRATIVE_TEMPERATURE. No API keys,
no auth tokens, no host paths. The `.env.example` is the broader
template; the live `.env` is a small operator-default override.

### E.5 — Default registry path — VERDICT: **FIX-IN-PLACE**

**§0 gate:** Fixes don't go through the removal gate. Just a
behavior improvement.

**Action taken:** Changed `DaemonSettings.registry_db_path` default
from `Path("registry.sqlite")` → `Path("data/registry.sqlite")` in
`src/forest_soul_forge/daemon/config.py`. The `data/` directory is
gitignored, so a casual daemon launch no longer leaves a stray
`registry.sqlite` at repo root. Tests use `tmp_path` so none broke.

Suite verification: 1439 passed before + after.

### E.6 — `.command` naming pass — VERDICT: **DEFER** (no concrete harm)

**§0 gate:**
1. Harm: none — naming "drift" doesn't break anything.
2. Stylistic only.

**Action taken:** None. Document in `docs/runbooks/command-scripts-index.md`
notes "There's some legacy drift documented in the audit but not yet
renamed. Don't rename without a matching update to scripts/docs that
reference them." Future cleanup pass can revisit if needed.

### E.7 — `docs/PROGRESS.md` — VERDICT: **ARCHIVE-NOT-DELETE**

**§0 gate:**
1. Harm: mild — file is dated 2026-04-24, misleads new contributors
   reading it for current state.
2. Non-load-bearing: yes — superseded by STATE.md + CHANGELOG.md.
3. Alternative strictly better: archive (preserves history) is
   strictly better than delete (loses history) and strictly better
   than keep (still misleads).

**Action taken:**
- Moved `docs/PROGRESS.md` → `docs/_archive/PROGRESS-2026-04-24.md`
- Created `docs/_archive/README.md` with a table explaining what's
  in the archive and why
- Live docs surface no longer has the stale file

### E.8 — `scripts/verify_*.py` — VERDICT: **KEEP** (revised default)

**§0 gate:**
1. Harm: none — scripts serve no-pytest sandbox use case.
2. Non-load-bearing: yes — not referenced by tests or daemon.
3. Alternative strictly better: KEEP wins because pytest isn't
   always available (sandbox / fresh-clone scenarios) and the
   parallel scripts cover that gap.

**Action taken:** None. The earlier audit's "retire" recommendation
was wrong-by-default — these scripts are a low-cost backup path.

### E.9 — Wire `web-research-demo` into `load-scenario.command` — VERDICT: **NO ACTION** (already wired)

**§0 gate:** N/A — this was an additive item, not a removal. The
audit report's premise was wrong: the loader script is generic
(`scenarios/$1/`), not enumerated. Any directory under `scenarios/`
loads with `./scenarios/load-scenario.command <name>`. The
web-research-demo dir has bare-bones content (README + synthetic
RFC), but the loader handles that fine.

### M-3 — Stray `registry.sqlite*` at repo root — RESOLVED via E.5

The fix-in-place for the default registry path (E.5) addresses the
M-3 finding — future daemon launches without an explicit override
land in `data/registry.sqlite` instead of repo root.

## Summary

| Candidate | Verdict | Files touched |
|---|---|---|
| E.1 agents/ + ui/ empty packages | KEEP-WITH-COMMENT | 3 `__init__.py` files |
| E.2 initial_push.sh | KEEP-WITH-GUARD | 1 script |
| E.3 t4-tests.command reference | NO ACTION (false positive) | none |
| E.4 .env tracked | KEEP | none |
| E.5 registry default path | FIX-IN-PLACE | `daemon/config.py` |
| E.6 .command naming | DEFER | none |
| E.7 PROGRESS.md | ARCHIVE | moved to `docs/_archive/` + new README |
| E.8 verify_*.py scripts | KEEP | none |
| E.9 web-research-demo wiring | NO ACTION (already works) | none |

**Net effect:** zero deletions of load-bearing or potentially-load-
bearing code. Two additions of documentation and guard rails. One
fix-in-place behavior improvement. One archive move.

The §0 Hippocratic gate held. No casual rip-outs happened.

## What's in `docs/_archive/` now

```
docs/_archive/
├── README.md                       # explains what's archived and why
└── PROGRESS-2026-04-24.md          # was docs/PROGRESS.md
```

Future archives go here with the same rule: snapshot date in the
filename, README updated to record the move + reason.
