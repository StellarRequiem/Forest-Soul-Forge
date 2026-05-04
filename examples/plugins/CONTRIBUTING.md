# Contributing a plugin to the Forest registry

ADR-0043 T5 (Burst 108). The Forest plugin registry is a
separate Git repository (`forest-plugins`) that operators'
installers fetch from. This file documents the contribution
flow for authors who want to publish a plugin to the public
catalog.

## Status

The registry repository does not exist yet. This document
describes the intended flow so the contribution-shape decision
is locked + reviewable before infrastructure is stood up.

When the registry repo exists, this file becomes its
`CONTRIBUTING.md` and the example plugins in
`examples/plugins/` move there as the seed catalog.

## Submission flow

1. **Author the plugin locally.** Use `examples/plugins/forest-echo/`
   as a starting template. Test it with
   `fsf plugin install /path/to/your-plugin` against your
   local Forest daemon before submitting.

2. **Verify governance posture.** Confirm the manifest's
   `side_effects` matches the plugin's actual reach (network /
   filesystem / external). Confirm `requires_human_approval`
   gates every mutating tool. The reviewer runs the same checks
   manually + via static analysis; getting these right speeds
   review.

3. **Open a PR against the `forest-plugins` registry repo.** The
   PR adds a directory `plugins/<your-plugin-name>/` containing:
   - `plugin.yaml` (the manifest)
   - `README.md` (human-facing description: what it does, what
     secrets it needs, links to the underlying MCP server)
   - `LICENSE` (the plugin's own license; permissive recommended)
   - NO binaries — the registry stores manifests + signatures,
     not the executables themselves. Operators download binaries
     from the upstream source the manifest points at, and the
     sha256 pin verifies authenticity.

4. **Pass review.** The registry maintainers verify:
   - Manifest parses cleanly (`fsf plugin install` against a
     copy of the directory succeeds locally)
   - Underlying MCP server is from a reputable upstream (or the
     author maintains it themselves under an established
     handle)
   - `side_effects` classification is honest
   - sha256 pin matches the upstream's distributed binary
   - No naming collision with an existing registry plugin
   - License is compatible with redistribution

5. **Maintainer signs the manifest.** On merge, a registry
   maintainer adds `verified_at` + `verified_by_sha256` to the
   manifest. This is the registry's signature; operator's
   `fsf plugin install` checks it before staging.

## Registry signature scheme

Until sigstore / cosign integration lands (deferred per
ADR-0043 §"Open questions"), the registry uses a simple
maintainer-signed approach:

- Each registry maintainer holds an Ed25519 keypair.
- The PR-merger signs the manifest's content hash with their
  key.
- Forest's installer holds the maintainers' public keys and
  verifies signatures against them.

This keeps the registry low-infrastructure (just a Git repo +
public-key list) while still preventing registry-side typosquat
attacks. Sigstore / cosign integration is the eventual upgrade
path and lands as an ADR amendment when the registry has
enough signed plugins to justify the migration cost.

## Unverified plugins

Plugins WITHOUT a registry signature are still installable —
the operator just sees an "unverified plugin" prompt and must
explicitly type `yes` to proceed. This lets community authors
share their plugins via direct directory transfer (`fsf plugin
install /path/to/their-plugin`) without going through the
registry's review process.

The verification status is stored in the manifest itself
(`verified_at` + `verified_by_sha256` fields). Forest does NOT
hide unverified plugins from `fsf plugin list`; they show up
with a `(unverified)` tag.

## Naming + namespacing rules

Plugin names:
- Lowercase letters, digits, hyphens
- Must start with a letter
- Max 80 chars
- Must be unique within the registry
- Should NOT include "forest" or "fsf" prefix unless the plugin
  is officially maintained by the Forest team

Capability names:
- Convention: `mcp.<plugin-name>.<tool-name>`
- Forest's bridge strips the `mcp.<plugin-name>.` prefix when
  populating the runtime registry
- Non-conventional names pass through verbatim (the bridge
  warns but doesn't refuse)

## Quality bar

Submissions are judged on three axes:

1. **Honesty** — manifest accurately describes what the plugin
   does. side_effects matches the actual reach. Mutating tools
   gated.
2. **Stability** — the underlying MCP server is reasonably
   stable. The author commits to keeping the manifest's sha256
   pin current with new upstream releases.
3. **Usefulness** — the plugin solves a real problem an
   operator would encounter. "Hello world" demos go in
   `examples/plugins/` of the main repo, not the public
   registry.

Reviewers will close + decline submissions that don't meet
these. Resubmission is welcome after fixing.

## Maintenance

After merge:

- Plugin authors are responsible for updating the manifest's
  `version` + `sha256` when upstream ships a new release.
- A maintainer re-signs the updated manifest.
- Forest's `fsf plugin update <name>` (T6+) will fetch the
  newer version from the registry.

Plugins that go unmaintained for >12 months get an
`abandoned: true` flag in their manifest. The installer warns
on install but doesn't refuse — the operator can still pin to
a frozen version.

## Code of conduct

The registry follows the same code of conduct as the main
Forest Soul Forge project. Plugins authored to harass, mislead,
or facilitate illegal activity will be removed. The maintainers
reserve the right to refuse any submission for any reason.

## References

- ADR-0043 — Plugin protocol design
- `examples/plugins/README.md` — Manifest format reference
- ADR-0042 — v0.5 product direction (informs which plugins fit
  the Forest thesis)
- ADR-003X Phase C4 — MCP threat model + sha256 verification
  posture
