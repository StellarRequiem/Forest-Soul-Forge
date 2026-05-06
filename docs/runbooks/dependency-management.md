# Dependency Management Runbook

**Status:** Active. Created 2026-05-05 (B150 / T26 security hardening).

The 2026-05-05 outside security review flagged Forest's lack of pinned
dependencies + missing SBOM as supply-chain hardening gaps. This
runbook is the operator's guide to closing those gaps in practice.

## Why this matters

Forest ships an `pyproject.toml` with `>=` constraints on dependencies
(e.g., `fastapi>=0.110`). That's friendly for development — installs
pull whatever's latest and compatible — but it means:

- **Two operators running the same Forest commit can have different
  trees installed** (one on fastapi 0.110.0, another on 0.115.4).
  Bug reports are harder to reproduce.
- **No hash verification.** If a PyPI package gets compromised
  (typosquatting, account takeover), `pip install` happily fetches
  the bad version.
- **No transitive visibility.** Forest's `pyproject.toml` lists ~10
  direct deps; the actual installed tree is ~80+ packages. A CVE in
  a transitive dep is invisible without an SBOM.

This runbook addresses both via `dev-tools/pin-deps.command` (lockfile
generation) and `dev-tools/generate-sbom.command` (SBOM generation).

## Workflow at a glance

```
pyproject.toml (loose constraints, source of truth)
         │
         ├─→ dev-tools/pin-deps.command
         │     (uses pip-tools / pip-compile)
         │     → requirements.txt              (core deps)
         │     → requirements-daemon.txt       (with daemon extras)
         │     → requirements-dev.txt          (with dev extras)
         │     → requirements-browser.txt      (with browser extras)
         │     → requirements-conformance.txt  (with conformance extras)
         │
         └─→ dev-tools/generate-sbom.command
               (uses cyclonedx-bom)
               → dependencies/sbom.json (CycloneDX 1.5 JSON)
```

## Generating lockfiles (`pin-deps.command`)

Re-run any time `pyproject.toml`'s dependencies change.

```bash
./dev-tools/pin-deps.command
```

What it does:
1. Verifies `.venv/bin/python` exists (else bootstrap via
   `start.command` first)
2. Installs `pip-tools` into the venv if missing
3. Runs `pip-compile --generate-hashes --strip-extras` per extras-
   group, producing one `requirements-<extra>.txt` per group plus
   the core `requirements.txt`
4. Each requirements file lists every transitive dep at an exact
   version with its sha256 hash (multiple hashes per dep cover
   multiple wheels/source archives)

After running, **commit the generated `requirements*.txt`** so CI and
external integrators have the same lockfile.

### CI usage (after lockfiles are committed)

Replace `pip install -e ".[daemon]"` with:

```bash
pip install -r requirements-daemon.txt
```

This:
- Installs the EXACT versions tested at commit time
- Verifies hashes (refuses to install if any package's sha256
  doesn't match the lockfile)
- Fails fast if upstream yanked a version

### Updating a single dep

```bash
# 1. Edit pyproject.toml — bump the constraint or add a new dep
# 2. Re-run pin-deps to regenerate
./dev-tools/pin-deps.command
# 3. Inspect the diff
git diff requirements*.txt
# 4. Run tests against the new tree
./run-tests.command
# 5. Commit pyproject.toml + requirements*.txt together
```

## Generating SBOM (`generate-sbom.command`)

```bash
./dev-tools/generate-sbom.command
```

What it does:
1. Installs `cyclonedx-bom` into the venv if missing
2. Runs `cyclonedx_py environment` against the active venv
3. Outputs `dependencies/sbom.json` (CycloneDX 1.5 format)

**Important:** the SBOM reflects what's INSTALLED in the venv, not
what's listed in `pyproject.toml`. To get a complete picture, install
all extras first:

```bash
.venv/bin/pip install -e ".[daemon,dev,browser,conformance]"
./dev-tools/generate-sbom.command
```

### CVE response workflow

When a CVE drops for some Python package, grep the SBOM to check
if Forest is affected:

```bash
cat dependencies/sbom.json \
  | jq -r '.components[] | "\(.name) \(.version)"' \
  | grep -i <package-name>
```

If hit, regenerate after upgrading the affected dep:

```bash
.venv/bin/pip install --upgrade <package-name>
./dev-tools/generate-sbom.command
./dev-tools/pin-deps.command
```

### Inspecting the SBOM

```bash
# Component count
cat dependencies/sbom.json | jq '.components | length'

# All deps with PURLs (Package URLs — standard locator)
cat dependencies/sbom.json | jq '.components[] | {name, version, purl}'

# Just name/version pairs
cat dependencies/sbom.json | jq -r '.components[] | "\(.name)==\(.version)"'

# Filter to direct deps only (excludes transitive)
cat dependencies/sbom.json | jq '.components[] | select(.scope == "required")'
```

## What's NOT covered yet

- **Wheel-level SBOM** — the current SBOM lists what's installed, not
  the per-wheel provenance (sigstore, attestations). T27 (per-event
  signatures ADR) will surface a similar problem in a different
  context.
- **OSV / NVD lookup automation** — the SBOM is a static document.
  A CVE-monitoring loop (e.g., `pip-audit`, `trivy fs .`) is a
  separate operator workflow not yet shipped.
- **Cross-platform pinning** — `pip-compile` resolves for the platform
  it's run on (macOS/Linux). Windows operators get a different tree.
  Forest's local-first model assumes the operator's machine is the
  target, so this is acceptable; cross-platform CI matrix would need
  per-platform lockfiles.
- **Pinning Ollama models** — `pin-deps.command` only covers Python
  packages. The LLM models are operator-managed via `ollama pull`.
  Model integrity is its own concern (sha256 of model files exposed
  via `ollama show <model> --json`).

## What this runbook does NOT promise

- Reproducible builds in the strict Bazel/Nix sense — `pip-tools`
  with hashes gives 95% of the value but doesn't address compiler
  determinism, system library variance, or Python interpreter
  reproducibility.
- Defense against compromised PyPI accounts that re-publish under
  the same hash. The hash-pin defends against most attack shapes
  (typosquatting, dependency confusion, post-publication tampering)
  but not against an attacker who controls the publishing key from
  the start.
- Automated CVE alerting. The SBOM is a TOOL for response; the
  monitoring loop is operator/CI responsibility.

## References

- ADR-0044 — Kernel positioning + SoulUX (the kernel commits to
  backward compatibility on 7 ABI surfaces; pinned deps protect the
  kernel implementation, not the ABI)
- 2026-05-05 outside security review — the original "no SBOM, no
  pinned hashes" finding
- B148 — T25 auth hardening (the previous Phase 4 item)
- pip-tools docs: https://pip-tools.readthedocs.io/
- CycloneDX spec: https://cyclonedx.org/specification/overview/
- OWASP SBOM guide: https://owasp.org/www-project-software-component-verification-standard/
