#!/usr/bin/env python3
"""
ADR-0056 E7 — operator-side cycle dispatch helper.

Codifies the prompt-engineering pattern that cycle 1 surfaced
(prior-cycle threading + verbatim-block markers) into a single
reusable CLI. No daemon changes; audit chain unchanged; no new
schema. The helper just builds the JSON body the operator was
constructing manually in cycle 1's smith-cycle-1-plan.command,
then POSTs it to /agents/{id}/tools/call.

USAGE
-----

  python3 dev-tools/cycle_dispatch.py \
      --agent-id experimenter_1de20e0840a2 \
      --session-id smith-cycle-2-plan-v1 \
      --mode work \
      --task-kind conversation \
      --max-tokens 4000 \
      --prompt-from dev-tools/smith-cycle-2-prompt.md \
      --prior-response-from dev-tools/smith-cycle-1-plan-response-v6.json \
      --verbatim-from dev-tools/smith-cycle-2-verbatim.json \
      --save-response-to dev-tools/smith-cycle-2-plan-response-v1.json

Args
----

--agent-id ID
    Agent instance_id to dispatch against. Required.

--session-id S
    Session identifier (free string, used by the daemon for
    rate-limit + memory-scope grouping). Required.

--mode {work,explore,display}
    ADR-0056 E2 task_caps.mode. Default 'work'.

--task-kind KIND
    llm_think task_kind. One of conversation/generate/classify/
    safety_check/tool_use. Default 'conversation'.

--max-tokens N
    Upper bound on response length. Default 4000.

--usage-cap-tokens N
    Per-task usage cap (operator brake). Default 50000.

--prompt-from PATH
    Plain text/Markdown file with the BASE prompt (without prior-
    cycle context or verbatim blocks — the helper splices those
    in). Required.

--prior-response-from PATH
    Path to a previous llm_think response JSON (the file the
    helper itself wrote on a prior run, or any saved daemon
    response). Helper extracts ``result.output.response`` and
    splices it into the prompt under <prior_cycle>.

--verbatim-from PATH
    JSON file with a list of {"id": str, "content": str} objects.
    Each gets wrapped in <copy_verbatim id="..."> ... </copy_verbatim>
    tags and prepended after the prior-cycle block. Smith's
    cycle-1.6 finding: explicit verbatim markers stop the agent
    from paraphrasing the contents.

--save-response-to PATH
    Where to write the daemon's response JSON. Default is
    auto-derived from session-id (replaces 'plan' with
    'plan-response' if present, else appends '-response.json').

--token-from-env VAR
    Env var to read the FSF API token from. Default 'FSF_API_TOKEN'.
    The .env file in the repo root is also auto-sourced if present.

--daemon-url URL
    Daemon base URL. Default 'http://127.0.0.1:7423'.

--print-prompt-only
    Build and print the constructed prompt (with all splicing
    applied) but do NOT dispatch. Useful for review before firing.

INTEGRATION NOTES
-----------------

The constructed prompt has this shape:

    <constructed prompt> = base_prompt
      [+ "\n\n<prior_cycle>\n" + prior_response + "\n</prior_cycle>"
       if prior_response_path provided]
      [+ "\n\n<copy_verbatim id=\"X\">\n" + content + "\n</copy_verbatim>"
       for each verbatim block]

The base prompt is responsible for telling the agent how to USE
the prior_cycle / copy_verbatim blocks (revise minimally,
copy character-for-character, etc.). The helper just splices —
it doesn't add framing language.

This file is operator tooling; not loaded by the daemon. The
daemon sees the same llm_think.v1 dispatch shape it always has.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# .env loader (no python-dotenv dep — keep this script vendor-free)
# ---------------------------------------------------------------------------

def _load_env_file(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict. Tolerant: skips comments + blank
    lines; trims surrounding quotes; ignores malformed lines."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            out[k] = v
    return out


def _resolve_token(repo_root: Path, env_var: str) -> str:
    """Read the API token from env, falling back to .env in repo root."""
    if env_var in os.environ and os.environ[env_var]:
        return os.environ[env_var]
    env_file = _load_env_file(repo_root / ".env")
    if env_var in env_file and env_file[env_var]:
        return env_file[env_var]
    raise SystemExit(
        f"ERROR: {env_var} not in env or {repo_root}/.env. "
        f"Set the token before dispatching."
    )


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _extract_prior_response(path: Path) -> str:
    """Pull result.output.response out of a saved llm_think response
    JSON. Tolerant about shape: if the file is a raw response string,
    returns it as-is. Errors loud if the JSON is malformed (operator
    misnamed the file)."""
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        raise SystemExit(f"ERROR: {path} is empty")
    # First try parsing as a daemon response JSON.
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SystemExit(
            f"ERROR: {path} is not valid JSON: {e}. "
            f"Pass plain text via --prompt-from instead if that's "
            f"what you meant."
        ) from e
    # Standard llm_think response shape.
    try:
        return obj["result"]["output"]["response"]
    except (KeyError, TypeError):
        pass
    # Fall back: maybe it's already an unwrapped {"response": "..."}
    if isinstance(obj, dict) and isinstance(obj.get("response"), str):
        return obj["response"]
    # Last resort: stringified content.
    if isinstance(obj, str):
        return obj
    raise SystemExit(
        f"ERROR: {path} doesn't look like a saved llm_think response "
        f"(no result.output.response or top-level response field)."
    )


def _load_verbatim_blocks(path: Path) -> list[tuple[str, str]]:
    """Parse a verbatim-blocks JSON file. Expected shape:
    [{"id": "helper", "content": "..."}, ...]"""
    raw = path.read_text(encoding="utf-8")
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SystemExit(
            f"ERROR: {path} is not valid JSON: {e}"
        ) from e
    if not isinstance(obj, list):
        raise SystemExit(
            f"ERROR: {path} must be a JSON array of "
            f'{{"id": str, "content": str}} objects'
        )
    blocks: list[tuple[str, str]] = []
    for i, item in enumerate(obj):
        if not isinstance(item, dict):
            raise SystemExit(
                f"ERROR: {path}[{i}] must be an object"
            )
        block_id = item.get("id")
        content = item.get("content")
        if not isinstance(block_id, str) or not block_id:
            raise SystemExit(
                f"ERROR: {path}[{i}].id must be a non-empty string"
            )
        if not isinstance(content, str):
            raise SystemExit(
                f"ERROR: {path}[{i}].content must be a string"
            )
        blocks.append((block_id, content))
    return blocks


def build_prompt(
    base: str,
    *,
    prior_response: str | None = None,
    verbatim_blocks: list[tuple[str, str]] | None = None,
) -> str:
    """Splice base prompt with optional prior-cycle + verbatim blocks.

    Order: base text, then <prior_cycle> if any, then each
    <copy_verbatim> block. Operators who want a different order
    inline the wrappers in their base prompt directly and skip
    the helper splicing.
    """
    parts = [base.rstrip()]
    if prior_response:
        parts.append(
            "\n\n<prior_cycle>\n" + prior_response.rstrip() + "\n</prior_cycle>"
        )
    if verbatim_blocks:
        for block_id, content in verbatim_blocks:
            parts.append(
                f'\n\n<copy_verbatim id="{block_id}">\n'
                + content.rstrip()
                + "\n</copy_verbatim>"
            )
    return "".join(parts)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def dispatch(
    daemon_url: str,
    agent_id: str,
    token: str,
    body: dict[str, Any],
    timeout_s: int = 90,
) -> dict[str, Any]:
    """POST to /agents/{id}/tools/call. Returns the parsed response."""
    url = f"{daemon_url.rstrip('/')}/agents/{agent_id}/tools/call"
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-FSF-Token": token,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        raise SystemExit(
            f"HTTP {e.code} from daemon: {body_text}"
        ) from e
    except urllib.error.URLError as e:
        raise SystemExit(
            f"Could not reach {url}: {e.reason}. Is the daemon up?"
        ) from e
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise SystemExit(
            f"Daemon returned non-JSON: {raw[:500]}..."
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_save_path(session_id: str) -> Path:
    """Derive a sensible response-file path from session_id."""
    if "plan" in session_id:
        return Path("dev-tools") / f"{session_id.replace('plan', 'plan-response')}.json"
    return Path("dev-tools") / f"{session_id}-response.json"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "ADR-0056 E7 — dispatch a cycle plan/work request "
            "with prior-cycle + verbatim-block splicing."
        ),
    )
    p.add_argument("--agent-id", required=True)
    p.add_argument("--session-id", required=True)
    p.add_argument(
        "--mode", default="work", choices=["work", "explore", "display"],
    )
    p.add_argument(
        "--task-kind", default="conversation",
        choices=["conversation", "generate", "classify",
                 "safety_check", "tool_use"],
    )
    p.add_argument("--max-tokens", type=int, default=4000)
    p.add_argument("--usage-cap-tokens", type=int, default=50000)
    p.add_argument("--prompt-from", required=True)
    p.add_argument("--prior-response-from", default=None)
    p.add_argument("--verbatim-from", default=None)
    p.add_argument("--save-response-to", default=None)
    p.add_argument("--token-from-env", default="FSF_API_TOKEN")
    p.add_argument("--daemon-url", default="http://127.0.0.1:7423")
    p.add_argument("--print-prompt-only", action="store_true")
    args = p.parse_args(argv)

    repo_root = Path(__file__).resolve().parent.parent
    base_prompt = Path(args.prompt_from).read_text(encoding="utf-8")

    prior_response = None
    if args.prior_response_from:
        prior_response = _extract_prior_response(
            Path(args.prior_response_from)
        )

    verbatim_blocks: list[tuple[str, str]] = []
    if args.verbatim_from:
        verbatim_blocks = _load_verbatim_blocks(Path(args.verbatim_from))

    full_prompt = build_prompt(
        base_prompt,
        prior_response=prior_response,
        verbatim_blocks=verbatim_blocks,
    )

    if args.print_prompt_only:
        print(full_prompt)
        return 0

    token = _resolve_token(repo_root, args.token_from_env)

    body = {
        "tool_name": "llm_think",
        "tool_version": "1",
        "session_id": args.session_id,
        "args": {
            "prompt": full_prompt,
            "task_kind": args.task_kind,
            "max_tokens": args.max_tokens,
        },
        "task_caps": {
            "mode": args.mode,
            "usage_cap_tokens": args.usage_cap_tokens,
        },
    }

    print("=" * 60)
    print(f"E7 cycle dispatch")
    print("=" * 60)
    print(f"  agent:           {args.agent_id}")
    print(f"  session_id:      {args.session_id}")
    print(f"  mode:            {args.mode}")
    print(f"  task_kind:       {args.task_kind}")
    print(f"  max_tokens:      {args.max_tokens}")
    print(f"  prompt_chars:    {len(full_prompt):,}")
    if prior_response is not None:
        print(f"  prior_response:  {len(prior_response):,} chars from "
              f"{args.prior_response_from}")
    if verbatim_blocks:
        ids = ", ".join(b[0] for b in verbatim_blocks)
        print(f"  verbatim_blocks: {len(verbatim_blocks)} ({ids})")
    print()
    print("POSTing dispatch (this may take 30-60s on the local model)...")
    print()

    result = dispatch(args.daemon_url, args.agent_id, token, body)

    save_to = (
        Path(args.save_response_to)
        if args.save_response_to
        else _default_save_path(args.session_id)
    )
    save_to.parent.mkdir(parents=True, exist_ok=True)
    save_to.write_text(json.dumps(result), encoding="utf-8")

    # Pretty-print key fields (full body in the saved file).
    output = (result.get("result") or {}).get("output") or {}
    metadata = (result.get("result") or {}).get("metadata") or {}
    print(f"Status:          {result.get('status', '?')}")
    print(f"Tool key:        {result.get('tool_key', '?')}")
    print(f"Audit seq:       {result.get('audit_seq', '?')}")
    print(f"Model:           {output.get('model', '?')}")
    print(f"Elapsed:         {output.get('elapsed_ms', '?')} ms")
    print(f"Prompt chars:    {metadata.get('prompt_chars', '?')}")
    print(f"Response chars:  {metadata.get('response_chars', '?')}")
    print(f"Usage capped:    {metadata.get('usage_cap_clipped', '?')}")
    print()
    print(f"Saved to:        {save_to}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
