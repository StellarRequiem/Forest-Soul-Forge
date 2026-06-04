# Show HN: A local-first agent runtime where you can *prove* what every agent did

**Repo:** https://github.com/StellarRequiem/Forest-Soul-Forge ·
**60-second demo:** `python demo/golden/golden_demo.py` ·
**Cast:** [`demo/golden/golden-demo.cast`](golden-demo.cast) (`asciinema play golden-demo.cast`)

---

AI agents are starting to *act* — write files, send mail, run commands, drive browsers.
The funded layer around this (Cisco just bought Astrix for ~$400M; Oasis raised $120M;
GitGuardian $50M) mostly secures an agent's **secrets**. None of it answers the question
that actually matters when something goes wrong: **can you prove what an agent did, under
whose approval — and would you catch it if someone edited the record?**

Forest Soul Forge is a local-first agent runtime built around that question. Everything
runs on `127.0.0.1` against your own model (Ollama/Qwen) — no cloud, no API key. Every
agent gets a cryptographic identity; every privileged action flows through one governed
pipeline; every action is written to a **hash-chained, ed25519-signed, tamper-evident
audit log**.

## The 60-second demo (`golden_demo.py`)

It runs FSF's *real* primitives — the actual `AuditChain` and the same `cryptography`
ed25519 the daemon uses — in a throwaway temp dir, in about a second:

```
🔨 FORGE  → a constitution from trait sliders → content-addressed agent DNA
👶 BIRTH  → an ed25519 keypair; pubkey + DNA = the agent's passport
🏃 RUN    → agent requests file_delete on customer records → ⛔ gated on approval → ✅ approved
📜 AUDIT  → every step hash-linked; agent actions ed25519-signed at emit
🔍 VERIFY → links + signatures valid
😈 TAMPER → an insider rewrites the log — twice:
              lazy edit                          → 🚨 the hash chain catches it
              expert edit that recomputes the hash → 🚨 the SIGNATURE catches it
```

That last beat is the whole point. A hash chain catches a careless edit. But an attacker
who controls the log file can recompute the hash. They **still** can't forge the ed25519
signature — it was made over the original action, and they don't have the agent's private
key. **That's provenance a credential vault doesn't give you: you cannot fake what an
agent did.**

There's also a `golden_demo_live.py` that drives the *actual running daemon* over its HTTP
API — births a real agent, the governance gate fires for real, you approve over the API,
and the daemon's real audit chain is verified and tamper-tested.

## The part I'm most proud of

Building the live demo, I drove the real daemon hard enough to find a **real
approval-bypass bug in my own governance kernel**: runtime-granted filesystem/external
tools were skipping the unconditional "always require human approval" rule (they resolved
to catalog defaults instead of running through the policy). A granted file-writer ran
*without approval* under the default posture.

I fixed it at the dispatch choke point ([ADR-0094](../../docs/decisions/ADR-0094-runtime-grant-approval-invariant.md)),
added regression tests, and verified the fix on the live daemon. The whole thing — find,
root-cause, fix, document, test, reproduce — is in the repo. I'd rather show you the bug I
caught than pretend the kernel was perfect.

## Why local-first + provenance

The agent-governance market is consolidating into identity/security suites that assume
**cloud** agents reachable via an IdP. Almost nobody covers the intersection that
regulation and privacy actually push toward: **agents that run locally** and produce
**cryptographic, tamper-evident proof of every action** — the kind of evidence the EU AI
Act (Article 12: automatic lifetime logging) and the OWASP Agentic Top-10 (Identity &
Privilege Abuse, Rogue Agents) are written around. That intersection is what FSF is.

## Try it

```sh
git clone https://github.com/StellarRequiem/Forest-Soul-Forge
cd Forest-Soul-Forge && python -m venv .venv && .venv/bin/pip install -e ".[daemon]"
.venv/bin/python demo/golden/golden_demo.py        # ~1s, no cloud, no API key
```

It's source-available (Elastic License 2.0), single-developer, and honest about what's
solid vs. in-progress (there's a machine-checked `STATE.md` that fails CI if it drifts
from the code). Feedback — especially adversarial — welcome.

---

*Maintainer notes — to publish the recording:*
- *Play locally:* `asciinema play demo/golden/golden-demo.cast`
- *Publish:* `asciinema upload demo/golden/golden-demo.cast` (gives a shareable asciinema.org link)
- *GIF for the README:* `agg demo/golden/golden-demo.cast demo/golden/golden-demo.gif` (needs [`agg`](https://github.com/asciinema/agg))
