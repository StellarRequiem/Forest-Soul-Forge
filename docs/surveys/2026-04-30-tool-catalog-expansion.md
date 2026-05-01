# Tool catalog expansion survey — open-source candidates

**Date:** 2026-04-30
**Author:** Forest Soul Forge harness
**Status:** Draft. Operator (Alex) signs off on which to wrap; harness builds the wrappers in priority order.

## Why this exists

Today the catalog has 46 tools (31 read_only / 5 network / 4 filesystem / 6 external). The defensive plane has decent coverage, the open-web plane just landed (web_fetch / browser_action / mcp_call), the SW-track has read/edit/exec primitives. **The next phase is breadth** — agents that can be assigned real cross-domain work need a wider catalog of dispatchable tools.

This doc is **not** a wishlist. Every candidate below is annotated with:

- **License** — Apache 2.0 / BSD / MIT preferred. GPL clearly noted because it changes our distribution story for any wrapper that subprocesses it.
- **Side-effect class** — read_only / network / filesystem / external. Determines genre ceiling + approval graduation.
- **Natural genre claim** — which existing genre would carry this tool in its kit. New genre needed → flagged.
- **Install path** — pip / homebrew / apt / cargo / vendored single-file / Docker. Cross-platform install pain is a real cost.
- **Gating implications** — does it need `requires_human_approval`? Allowlist scoping? Per-call quotas?
- **Known caveats** — CVE history, abandonware, heavy native deps, anti-features (telemetry, phone-home).

Anything that can't be cleanly mapped into the side-effects ladder doesn't get wrapped — period. The audit chain depends on every tool declaring its side-effect class honestly.

## Threat-model framing for the offensive plane

ADR-0033 only covered defense. Adding red-team primitives shifts the threat model. Four hard rules before any offensive tool ships:

1. **Operator-authorized targets only.** Every offensive tool takes a `target_scope` arg validated against an operator-curated allowlist (your homelab IP range, your own domains, registered CTF environments). Out-of-scope targets refuse with `target_out_of_scope` — never proceed with a warning.
2. **Genre confined.** New genre family `red_team_*` (low / mid / high) parallel to the security tiers, with `external` ceiling and `requires_human_approval` floor. Existing security_* genres do NOT get offensive tools — separation of duty.
3. **Audit chain mandatory.** Every offensive call appends `red_team_action` event with target + scope + operator_id + reason. No silent dispatch. Operator can produce a clean attestation log on demand.
4. **Default off.** Daemon won't load offensive tools unless `FSF_ENABLE_RED_TEAM=true` AND a `red_team_targets.yaml` allowlist file is present. Mirror of the PrivClient pattern (ADR-0033 A4).

If any of those four can't be enforced for a given tool, it doesn't ship.

---

## Bucket 1 — Blue team (defensive security)

The defensive plane already has 26 of 27 ADR-0033 tools. Gaps are mostly external integrations + threat intel + SIEM glue.

### Threat intelligence + reputation
| Candidate | License | Side-effects | Genre | Install | Gating | Caveats |
|---|---|---|---|---|---|---|
| `mitre_attck_lookup.v1` (MITRE ATT&CK STIX bundle, local copy) | Apache 2.0 | read_only | security_low+ | pip `attackcti` or vendored STIX | none | bundle is ~20MB JSON; refresh via separate sync job |
| `cve_lookup.v1` (NIST NVD JSON feed, local mirror) | public domain | read_only | security_low+ | sync via cron from nvd.nist.gov | none | feeds are large (~5GB); incremental sync needed |
| `osv_lookup.v1` (Google OSV.dev API) | Apache 2.0 | network | security_mid | pip `osv` or direct REST | per-call rate limit; allowlist ecosystems | OSV is the cleanest vuln-db API today |
| `abuseipdb_check.v1` (AbuseIPDB reputation) | their TOS | network | security_mid | direct REST + API key | API key in secrets store; rate-limited; gate on `requires_human_approval` for bulk | requires account |
| `whois_lookup.v1` | various | network | investigator | python `python-whois` | per-domain rate limit | upstream registries vary in compliance |

### Endpoint + log integrations
| Candidate | License | Side-effects | Genre | Install | Gating | Caveats |
|---|---|---|---|---|---|---|
| `osquery_query.v1` (osquery as system inspector) | Apache 2.0 | read_only (queries) / network (osqueryd remote) | security_mid | brew `osquery`, allowlisted SQL prefixes | constrain to SELECT-only; reject `JOIN` against sensitive tables | binary not pip-installable |
| `auditd_query.v1` (Linux auditd log reader) | LGPL | read_only | security_low | system tool | linux only; macOS skips | LGPL is fine for subprocess wrap |
| `wazuh_query.v1` (Wazuh API) | GPL-2 | network | security_mid | self-hosted Wazuh + token | tenant-scoped; never write rules | gated on operator install |
| `suricata_eve_tail.v1` (read Suricata eve.json) | GPL-2 | read_only | security_mid | self-hosted Suricata | local file path allowlist | stream-mode tool (long-running) |
| `falco_event_stream.v1` (Falco runtime-security alerts) | Apache 2.0 | read_only | security_mid | linux/k8s-only | event filter; rate-limit on emit | not macOS |

### SIEM + ticketing
| Candidate | License | Side-effects | Genre | Install | Gating | Caveats |
|---|---|---|---|---|---|---|
| `splunk_search.v1` | proprietary client; FSF wrapper Apache | network | security_mid | pip `splunk-sdk` + endpoint URL + token | search-only; never push admin commands | requires Splunk license |
| `elastic_search.v1` (Elasticsearch DSL) | Elastic License (non-OSI but free for FSF use) | network | security_mid | pip `elasticsearch` | read-only role | watch license drift |
| `pagerduty_create_incident.v1` | proprietary | external | security_mid actuator | pip `pdpyras` | `requires_human_approval`; scope to one team's service | mirrors actuator-class action |
| `jira_create_ticket.v1` | proprietary | external | actuator | pip `jira` | `requires_human_approval`; project allowlist | duplicate-detect to avoid spam |

### Memory forensics + integrity
| Candidate | License | Side-effects | Genre | Install | Gating | Caveats |
|---|---|---|---|---|---|---|
| `volatility3_run.v1` (memory image analysis) | Volatility License (BSD-style) | read_only | security_high | pip `volatility3` + memory image path | image path allowlist; CPU-heavy | analysis-only, never live capture |
| `chkrootkit_scan.v1` | GPL-2 | read_only | security_low | brew/apt | linux/macOS only | flag false-positive history |
| `rkhunter_scan.v1` | GPL-2 | read_only | security_low | brew/apt | linux/macOS | similar caveat |
| `clamav_scan.v1` | GPL-2 | read_only | security_low | brew/apt + freshclam | scan path allowlist | sig db needs daily refresh |

### Network detection
| Candidate | License | Side-effects | Genre | Install | Gating | Caveats |
|---|---|---|---|---|---|---|
| `tshark_pcap_query.v1` (Wireshark CLI) | GPL-2 | read_only | security_mid | brew/apt `wireshark` | pcap path allowlist | GPL subprocess fine |
| `zeek_log_query.v1` | BSD | read_only | security_mid | brew `zeek` | log path allowlist | best run on a sensor box |
| `mitmproxy_capture.v1` (record HTTPS w/ install cert) | MIT | network | security_high | pip `mitmproxy` | `requires_human_approval`; cert-install is operator-only | TLS-MITM tool — high gating |

---

## Bucket 2 — Red team (offensive security, authorized targets only)

Rules from the threat-model framing apply to every entry. Default-off, allowlist-scoped, mandatory audit. New `red_team_low / red_team_mid / red_team_high` genre family.

### Reconnaissance (red_team_low — read_only/network)
| Candidate | License | Side-effects | Genre | Install | Gating | Caveats |
|---|---|---|---|---|---|---|
| `nmap_scan.v1` (port + service discovery) | NPL (special) | network | red_team_low | brew/apt `nmap` | target_scope allowlist; rate-limit; never scan public internet | NPL has commercial restriction; FSF use is fine |
| `masscan_quick.v1` (very fast port sweep) | AGPL-3 | network | red_team_low | brew/apt | as nmap; AGPL flagged | AGPL means we link by subprocess, not import |
| `subfinder.v1` (passive subdomain enum) | MIT | network | red_team_low | go install `projectdiscovery/subfinder` | API keys in secrets store | passive only |
| `amass_enum.v1` | Apache 2.0 | network | red_team_low | go install | as subfinder | rate-limited |
| `theharvester.v1` (OSINT on emails/subdomains) | GPL-2 | network | red_team_low | pip `theHarvester` | scope to operator-owned domains | rate limits per source |
| `shodan_query.v1` (search Shodan index) | proprietary | network | red_team_low | pip `shodan` + API key | secrets store; query allowlist | quota costs $$ |
| `censys_query.v1` | proprietary | network | red_team_low | pip `censys` + key | as Shodan | quota costs |

### Vulnerability identification (red_team_mid — network)
| Candidate | License | Side-effects | Genre | Install | Gating | Caveats |
|---|---|---|---|---|---|---|
| `nuclei_template_scan.v1` (templated vuln scanner) | MIT | network | red_team_mid | go install `projectdiscovery/nuclei` | target_scope; template allowlist (no DoS templates); `requires_human_approval` for severity≥high | templates are user-extensible — community templates need review before allowlist |
| `nikto_web_scan.v1` (web-server vuln scanner) | GPL-2 | network | red_team_mid | brew/apt | target_scope; aggressive flags forbidden | noisy logs |
| `dirsearch.v1` (web path bruteforce) | GPL-2 | network | red_team_mid | pip | wordlist allowlist; rate-limit; `requires_human_approval` | will hit WAFs |
| `sslyze_scan.v1` (TLS config audit) | AGPL-3 | network | red_team_low | pip `sslyze` | no aggressive cipher tests | AGPL via subprocess |
| `testssl_sh.v1` (TLS config audit) | GPL-2 | network | red_team_low | brew | as sslyze | shell wrap |

### Web app testing (red_team_mid — network)
| Candidate | License | Side-effects | Genre | Install | Gating | Caveats |
|---|---|---|---|---|---|---|
| `sqlmap_query.v1` (SQL-injection probe) | GPL-2 | network | red_team_mid | brew/apt `sqlmap` | target_scope mandatory; `--risk=1` cap; `requires_human_approval` for any non-readonly | will modify target DB if unconstrained — STRICT gating |
| `wpscan.v1` (WordPress audit) | wpscan license (free for non-commercial) | network | red_team_mid | gem install | target_scope; rate-limit; API token in secrets | license requires attribution |
| `zap_baseline.v1` (OWASP ZAP baseline scan only) | Apache 2.0 | network | red_team_mid | brew `zap-cli` | target_scope; passive-only by default | full active scan needs separate gate |

### Credential testing (red_team_high — external)
| Candidate | License | Side-effects | Genre | Install | Gating | Caveats |
|---|---|---|---|---|---|---|
| `hydra_login_attempt.v1` (network login bruteforce) | GPL-2 | external | red_team_high | brew/apt `hydra` | target_scope; max 3 attempts/sec; lockout-detection abort; `requires_human_approval` MANDATORY | very destructive if misused |
| `john_the_ripper.v1` (offline hash cracking) | GPL-2 | filesystem | red_team_high | brew `john` | input file allowlist; no rainbow-table mode without explicit op approval | offline only — never online |
| `hashcat.v1` (GPU hash cracking) | MIT | filesystem | red_team_high | brew/apt | as john; resource quota | GPU heavy |

### Post-exploitation framework integration (red_team_high — external)
| Candidate | License | Side-effects | Genre | Install | Gating | Caveats |
|---|---|---|---|---|---|---|
| `metasploit_module_exec.v1` (msfconsole RPC) | BSD-3 (framework) | external | red_team_high | brew + msfrpcd | module allowlist (no encoders); `requires_human_approval` MANDATORY; session-scoped | full framework — limit to specific exploit/aux modules per call |
| `bloodhound_query.v1` (AD attack-path graph) | GPL-3 | network | red_team_mid | npm `bloodhound` + neo4j | read-only Cypher; target_scope by AD domain | static graph after collector run |

### What we deliberately do NOT wrap
- C2 frameworks (Cobalt Strike, Sliver, Mythic) — too easy to misuse, blast radius exceeds operator authorization model
- Kernel exploits — outside FSF mission scope
- Phishing kits / social-engineering tooling — protect-the-user mission directly conflicts
- Exfiltration tooling (Empire post-modules, custom dropper builders) — direct conflict with audit chain integrity claims

---

## Bucket 3 — Programming tools

The SW-track has `code_read.v1`, `code_edit.v1`, `shell_exec.v1`. Engineers need more verbs.

### Static analysis + linting
| Candidate | License | Side-effects | Genre | Install | Gating | Caveats |
|---|---|---|---|---|---|---|
| `ruff_lint.v1` (Python linter+formatter, fast) | MIT | read_only | software_engineer | pip `ruff` | path allowlist | swallows pylint+flake8+black+isort |
| `mypy_typecheck.v1` | MIT | read_only | software_engineer | pip `mypy` | path allowlist | needs project-local config |
| `pyright_check.v1` (faster type checker) | MIT | read_only | software_engineer | npm `pyright` | path allowlist | competitor to mypy |
| `eslint_check.v1` (JS/TS linter) | MIT | read_only | software_engineer | npm `eslint` | path allowlist | requires project config |
| `prettier_format.v1` | MIT | filesystem | software_engineer | npm `prettier` | path allowlist; `requires_human_approval` for repo-wide | writes files |
| `gofmt_check.v1` | BSD | read_only | software_engineer | go toolchain | none | trivial wrap |
| `clippy_run.v1` (Rust linter) | MIT/Apache | read_only | software_engineer | rustup component | path allowlist | needs cargo project |
| `shellcheck_check.v1` | GPL-3 | read_only | software_engineer | brew/apt | path allowlist | GPL subprocess |
| `bandit_security_scan.v1` (Python security linter) | Apache 2.0 | read_only | software_engineer / code_reviewer | pip | path allowlist | useful for review |
| `semgrep_scan.v1` (multi-lang SAST rules) | LGPL-2.1 | read_only | code_reviewer | pip `semgrep` | rule allowlist; per-call timeout | rule packs are extensible — review before allowlist |

### Test runners
| Candidate | License | Side-effects | Genre | Install | Gating | Caveats |
|---|---|---|---|---|---|---|
| `pytest_run.v1` | MIT | filesystem (tests can write) | software_engineer | pip | path allowlist; per-call timeout; `requires_human_approval` for `--lf`/`--ff` | side-effect varies wildly with test code; treat as filesystem |
| `jest_run.v1` | MIT | filesystem | software_engineer | npm | as pytest | tests can hit network — separate gate |
| `go_test.v1` | BSD | filesystem | software_engineer | go toolchain | path allowlist | trivial wrap |
| `cargo_test.v1` | MIT/Apache | filesystem | software_engineer | rustup | path allowlist | first build is slow |
| `playwright_test.v1` (browser e2e tests) | Apache 2.0 | external | software_engineer | npm + chromium | URL allowlist; `requires_human_approval` | already have browser_action — different verb |

### AST + refactoring
| Candidate | License | Side-effects | Genre | Install | Gating | Caveats |
|---|---|---|---|---|---|---|
| `tree_sitter_query.v1` (multi-lang parser, structural search) | MIT | read_only | system_architect | pip `tree-sitter` + grammar packs | path allowlist | grammar per-language |
| `libcst_transform.v1` (Python concrete-syntax editing) | MIT | filesystem | software_engineer | pip `libcst` | path allowlist; `requires_human_approval` for >10 files | safer than regex refactor |
| `rope_refactor.v1` (Python refactoring lib) | LGPL-3 | filesystem | software_engineer | pip | path allowlist; `requires_human_approval` | LGPL via import — fine |
| `comby_rewrite.v1` (multi-lang structural rewrite) | Apache 2.0 | filesystem | software_engineer | brew | path allowlist; `requires_human_approval` | very powerful; can wreck a repo |

### Build / package
| Candidate | License | Side-effects | Genre | Install | Gating | Caveats |
|---|---|---|---|---|---|---|
| `pip_install_isolated.v1` (resolve in venv, never apply) | MIT (pip itself) | filesystem | software_engineer | system | venv-scoped; `requires_human_approval`; never apply to system Python | dry-run-only by default |
| `npm_audit.v1` | Artistic 2.0 | network (resolves) | code_reviewer | npm | path allowlist | exposes deps |
| `cargo_audit.v1` (RustSec advisories) | MIT/Apache | network | code_reviewer | cargo install | path allowlist | safe |
| `safety_check.v1` (Python deps vs CVE) | MIT | network | code_reviewer | pip | path allowlist | known DB lag |
| `trivy_fs_scan.v1` (filesystem vuln scan) | Apache 2.0 | read_only | code_reviewer | brew `aquasecurity/tap/trivy` | path allowlist | also does container/IaC |

### Git + VCS
| Candidate | License | Side-effects | Genre | Install | Gating | Caveats |
|---|---|---|---|---|---|---|
| `git_log_read.v1` | GPL-2 | read_only | system_architect | system | path allowlist | trivial |
| `git_blame_read.v1` | GPL-2 | read_only | system_architect | system | path allowlist | trivial |
| `git_diff_read.v1` | GPL-2 | read_only | code_reviewer | system | path allowlist | trivial |
| `git_branch_create.v1` | GPL-2 | filesystem | software_engineer | system | repo allowlist; `requires_human_approval` | mutates repo state |
| `git_commit.v1` | GPL-2 | filesystem | software_engineer | system | repo allowlist; `requires_human_approval` MANDATORY; commit-msg validation | high blast radius |
| `git_push.v1` | GPL-2 | external | software_engineer | system | remote allowlist; `requires_human_approval` MANDATORY | publishes to internet |

### Profiling + debugging
| Candidate | License | Side-effects | Genre | Install | Gating | Caveats |
|---|---|---|---|---|---|---|
| `py_spy_sample.v1` (sampling profiler, no PID modification) | MIT | read_only | software_engineer | pip + ptrace permissions | PID allowlist (operator's own) | needs sudo on macOS |
| `austin_profile.v1` | GPL-3 | read_only | software_engineer | brew | as py_spy | GPL via subprocess |
| `pprof_analyze.v1` (Go profile reader) | BSD | read_only | software_engineer | go toolchain | profile path allowlist | static-only |

### Code intelligence
| Candidate | License | Side-effects | Genre | Install | Gating | Caveats |
|---|---|---|---|---|---|---|
| `pyright_lsp_query.v1` (LSP definition / references) | MIT | read_only | system_architect | npm | path allowlist | needs running LSP — long-lived process |
| `ctags_index.v1` (universal-ctags symbol index) | GPL-2 | filesystem (writes index) | system_architect | brew | path allowlist | small filesystem footprint |
| `ast_grep.v1` (AST-based grep, multi-lang) | MIT | read_only | system_architect | brew/cargo | path allowlist | promising newer tool |

---

## Bucket 4 — Web reach + open-internet integration

`web_fetch.v1` and `browser_action.v1` are wired. Gaps: search, structured extraction, feeds, identity flows, content moderation.

### Search APIs (read_only / network)
| Candidate | License | Side-effects | Genre | Install | Gating | Caveats |
|---|---|---|---|---|---|---|
| `searx_query.v1` (federate search via SearXNG instance) | AGPL-3 | network | web_observer | self-hosted instance | instance-allowlist; rate-limit | requires hosting; no API keys |
| `brave_search.v1` (Brave Search API) | proprietary | network | web_observer | API key in secrets | quota; allowlist topics | free tier 2K req/mo |
| `kagi_search.v1` | proprietary | network | web_observer | API key | as Brave; paid | high quality, paid only |
| `duckduckgo_html.v1` (HTML scrape, no API key) | their TOS | network | web_observer | pip `duckduckgo-search` | rate-limit (their TOS) | unstable — they break scrapers |
| `google_custom_search.v1` (CSE API) | proprietary | network | web_observer | API key + cse_id | quota; cse must be operator-curated | $5/1k queries above free tier |

### Content extraction
| Candidate | License | Side-effects | Genre | Install | Gating | Caveats |
|---|---|---|---|---|---|---|
| `readability_extract.v1` (Mozilla Readability port) | Apache 2.0 | read_only | web_researcher | pip `readability-lxml` | input is web_fetch output | works on the Document we already have |
| `trafilatura_extract.v1` (multilingual content + metadata) | GPL-3 / Apache 2.0 dual | read_only | web_researcher | pip `trafilatura` | input is web_fetch output | best-in-class for boilerplate stripping |
| `unfurl_url.v1` (resolve redirects, decode shorteners) | Apache 2.0 | network | web_observer | pip | `requires_human_approval` for unknown domains | follows links — exfiltration risk |
| `boilerpy3_extract.v1` | Apache 2.0 | read_only | web_researcher | pip | input is web_fetch output | older, simpler |
| `html2text_render.v1` | GPL-3 | read_only | web_researcher | pip | input is web_fetch output | trivial wrap |
| `pypdf_text_extract.v1` | BSD-3 | read_only | web_researcher | pip `pypdf` | path or URL allowlist | already use pypdf — extend |
| `pandoc_convert.v1` (universal doc converter) | GPL-2 | filesystem | web_researcher | brew `pandoc` | path allowlist; output dir restricted | very powerful — careful with input |

### Feeds + structured data
| Candidate | License | Side-effects | Genre | Install | Gating | Caveats |
|---|---|---|---|---|---|---|
| `rss_atom_fetch.v1` | various | network | web_observer | pip `feedparser` | URL allowlist | trivial wrap |
| `sitemap_walk.v1` (parse sitemap.xml) | various | network | web_observer | pip `usp.tools` | URL allowlist; depth cap | depth limits to avoid sprawl |
| `oembed_resolve.v1` (Twitter/YouTube/etc. embed metadata) | various | network | web_observer | direct REST | provider allowlist | declining standard but useful |
| `microdata_parse.v1` (JSON-LD / schema.org) | MIT | read_only | web_researcher | pip `extruct` | input is web_fetch output | structured-data goldmine |
| `archive_org_lookup.v1` (Wayback Machine API) | their TOS | network | web_researcher | direct REST | rate-limit; per-day quota | great for time-shifted research |

### Identity + auth (high gating)
| Candidate | License | Side-effects | Genre | Install | Gating | Caveats |
|---|---|---|---|---|---|---|
| `oauth2_token_fetch.v1` (RFC 6749 client-credentials only) | Apache 2.0 | network | web_actuator | pip `authlib` | provider allowlist; `requires_human_approval` MANDATORY; secrets store | never user-flow OAuth — always client-creds |
| `mtls_post.v1` (HTTPS with operator-supplied client cert) | various | external | web_actuator | python stdlib | cert in secrets; URL allowlist; `requires_human_approval` | enterprise webhook callbacks |

### Content moderation + safety
| Candidate | License | Side-effects | Genre | Install | Gating | Caveats |
|---|---|---|---|---|---|---|
| `safebrowsing_check.v1` (Google Safe Browsing API) | their TOS | network | web_observer / guardian | API key | quota; cache responses | first-stop URL safety check |
| `phishtank_lookup.v1` | their TOS | network | web_observer / guardian | direct DB or API | rate-limit | community-maintained, useful |

### Webhooks + outbound (high gating)
| Candidate | License | Side-effects | Genre | Install | Gating | Caveats |
|---|---|---|---|---|---|---|
| `slack_webhook_post.v1` | proprietary | external | actuator | requests | webhook URL in secrets; `requires_human_approval` MANDATORY for unknown channels | already in scope per ADR-003X |
| `discord_webhook_post.v1` | proprietary | external | actuator | requests | as slack | as slack |
| `webhook_post_generic.v1` (operator-allowlisted endpoints only) | n/a | external | actuator | requests | URL allowlist mandatory; `requires_human_approval` | umbrella tool — risk = config |

---

## First batch to wrap (recommended)

If we wrap these 10 first, we cover the highest-leverage gaps without inviting net-new threat surface beyond what's already gated:

| # | Tool | Bucket | Why first |
|---|---|---|---|
| 1 | `ruff_lint.v1` | Programming | SW-track engineer needs lint feedback to do real work; ruff is one binary, MIT, fast |
| 2 | `pytest_run.v1` | Programming | engineer needs to run tests it just wrote; gate at filesystem |
| 3 | `git_log_read.v1` + `git_diff_read.v1` + `git_blame_read.v1` | Programming | architect + reviewer need git context; all read_only, trivial |
| 4 | `mitre_attck_lookup.v1` | Blue team | swarm needs structured threat-intel framing; pure data lookup |
| 5 | `osv_lookup.v1` | Blue team | bridges blue team + SW-track (vulnerable deps); pip + REST |
| 6 | `trafilatura_extract.v1` | Web reach | web_researcher's missing verb — turns web_fetch HTML into clean prose; pure read_only post-fetch |
| 7 | `readability_extract.v1` | Web reach | second extractor; agents pick best result; same gating as 6 |
| 8 | `rss_atom_fetch.v1` | Web reach | web_observer's missing verb; trivial wrap |
| 9 | `semgrep_scan.v1` | Programming | code_reviewer's killer feature; rule allowlist needs design |
| 10 | `tree_sitter_query.v1` | Programming | architect-grade structural search; multi-lang |

**Deliberately deferred to a follow-on tranche:**
- All red-team tools — needs `red_team_*` genre family + targets allowlist + `FSF_ENABLE_RED_TEAM` flag wired first. Roughly a week of design work before the first nmap wrapper ships.
- All SIEM/ticketing integrations (Splunk, Elastic, PD, Jira) — gated on operator install of the upstream, so they'd ship dormant
- Webhook posters — need ADR-003X Phase C1 secrets store landed first

## What I need from you

For the 10 above, three sign-off questions:

1. **Order.** The ranking above is my read. Reorder freely.
2. **Scope.** Should I draft the full 10 wrappers in one push, or 3 at a time with a smoke-test between batches?
3. **Red-team kickoff.** Do you want me to draft the `red_team_*` genre family + targets-allowlist ADR now (so the design is ready when we get to it), or hold until the first batch is in?

After we settle those three, I'll start with #1 — ruff_lint.v1 — wire it into `software_engineer`'s kit, write the unit tests, and add it to the integration trio so the chain is exercised end-to-end.
