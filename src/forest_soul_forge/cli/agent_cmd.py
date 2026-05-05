"""``fsf agent ...`` subcommand — operator-facing agent state
commands. Currently scopes to posture (ADR-0045 T2 / Burst 114b).
Future tranches may add archive / status / etc. here.

Talks to the daemon HTTP surface so audit chain emits land
server-side. Same urllib pattern as plugin_cmd's grant/revoke
runners.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def add_subparser(parent_subparsers: argparse._SubParsersAction) -> None:
    agent = parent_subparsers.add_parser(
        "agent",
        help="Agent runtime state — posture (ADR-0045) etc.",
    )
    agent_sub = agent.add_subparsers(dest="agent_cmd", metavar="<subcmd>")

    # ---- posture --------------------------------------------------
    p_posture = agent_sub.add_parser(
        "posture",
        help="Read or set the agent's traffic-light posture.",
    )
    posture_sub = p_posture.add_subparsers(
        dest="posture_cmd", metavar="<action>",
    )

    def _add_daemon_flag(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--daemon-url",
            default="http://127.0.0.1:7423",
            help="Daemon base URL (default: http://127.0.0.1:7423).",
        )
        p.add_argument(
            "--api-token",
            default=None,
            help=(
                "Daemon API token. Falls back to $FSF_API_TOKEN, then "
                "no token."
            ),
        )

    p_get = posture_sub.add_parser(
        "get", help="Print the agent's current posture.",
    )
    _add_daemon_flag(p_get)
    p_get.add_argument("instance_id")
    p_get.set_defaults(_run=_run_posture_get)

    p_set = posture_sub.add_parser(
        "set", help="Flip the agent's posture (green/yellow/red).",
    )
    _add_daemon_flag(p_set)
    p_set.add_argument("instance_id")
    p_set.add_argument(
        "--tier", required=True,
        choices=("green", "yellow", "red"),
        help="Target posture.",
    )
    p_set.add_argument("--reason", default=None)
    p_set.set_defaults(_run=_run_posture_set)


def _http(args, method: str, path: str,
          body: dict | None = None) -> tuple[int, dict | None]:
    token = (
        getattr(args, "api_token", None)
        or os.environ.get("FSF_API_TOKEN")
    )
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = args.daemon_url.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, headers=headers, method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return e.code, {"detail": raw}
    except urllib.error.URLError as e:
        print(
            f"fsf agent: cannot reach daemon at {args.daemon_url}: {e}",
            file=sys.stderr,
        )
        return -1, None


def _run_posture_get(args: argparse.Namespace) -> int:
    status_code, payload = _http(
        args, "GET", f"/agents/{args.instance_id}/posture",
    )
    if status_code == -1:
        return 7
    if status_code == 200 and payload:
        print(payload["posture"])
        return 0
    detail = (payload or {}).get("detail", f"HTTP {status_code}")
    print(f"fsf agent posture get: {detail}", file=sys.stderr)
    return 7 if status_code >= 500 else 4


def _run_posture_set(args: argparse.Namespace) -> int:
    body = {"posture": args.tier}
    if args.reason:
        body["reason"] = args.reason
    status_code, payload = _http(
        args, "POST", f"/agents/{args.instance_id}/posture", body=body,
    )
    if status_code == -1:
        return 7
    if status_code == 200 and payload and payload.get("ok"):
        prior = payload.get("prior_posture", "?")
        print(f"posture: {args.instance_id}  {prior} → {args.tier}")
        return 0
    detail = (payload or {}).get("detail", f"HTTP {status_code}")
    print(f"fsf agent posture set: {detail}", file=sys.stderr)
    return 7 if status_code >= 500 else 4
