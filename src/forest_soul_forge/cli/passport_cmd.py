"""``fsf passport ...`` — ADR-0061 T7 (Burst 248) operator CLI.

Three subcommands today:

  - ``fsf passport mint <instance_id>``: HTTP POST to the new
    ``/agents/{id}/passport`` endpoint. Operator passes
    ``--authorize-fingerprint`` one or more times to build the
    authorized list + optional ``--expires-at``. Replaces any
    existing ``passport.json``.

  - ``fsf passport show <instance_id>``: print the existing
    ``passport.json`` next to the agent (read directly off disk;
    no HTTP). Useful for verification + scripting.

  - ``fsf passport fingerprint``: print this machine's hardware
    fingerprint so the operator can add it to another machine's
    mint command. Equivalent to:
    ``python -c 'from forest_soul_forge.core.hardware import \
    compute_hardware_fingerprint; print(compute_hardware_fingerprint().fingerprint)'``
    but the spelling shouldn't be that ugly.

Same urllib HTTP pattern as ``agent_cmd.py`` so we don't pull in
``requests`` for one subcommand.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def add_subparser(parent_subparsers: argparse._SubParsersAction) -> None:
    passport = parent_subparsers.add_parser(
        "passport",
        help="Mint / inspect agent passports (ADR-0061).",
    )
    passport_sub = passport.add_subparsers(
        dest="passport_cmd", metavar="<subcmd>",
    )
    passport_sub.required = True

    def _add_daemon_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--daemon-url",
            default="http://127.0.0.1:7423",
            help="Daemon base URL (default: http://127.0.0.1:7423).",
        )
        p.add_argument(
            "--api-token",
            default=None,
            help=(
                "Daemon API token. Falls back to $FSF_API_TOKEN, "
                "then no token."
            ),
        )

    # ---- mint --------------------------------------------------------------
    p_mint = passport_sub.add_parser(
        "mint",
        help=(
            "Mint a passport authorizing the agent to run on the "
            "given hardware fingerprints."
        ),
    )
    _add_daemon_flags(p_mint)
    p_mint.add_argument(
        "instance_id",
        help="Agent instance_id (full, e.g. 'companion_abc123abc123').",
    )
    p_mint.add_argument(
        "--authorize-fingerprint", "-f",
        dest="authorize_fingerprints",
        action="append", required=True,
        metavar="FINGERPRINT",
        help=(
            "Hardware fingerprint to authorize. Pass multiple times "
            "to authorize multiple machines: "
            "-f <birth-fp> -f <laptop-fp>."
        ),
    )
    p_mint.add_argument(
        "--expires-at", default=None,
        help=(
            "RFC 3339 / ISO-8601 UTC expiration "
            "('2026-08-12T00:00:00Z'). Omit for open-ended."
        ),
    )
    p_mint.add_argument(
        "--operator-id", default=None,
        help="Operator label recorded in the audit event.",
    )
    p_mint.add_argument(
        "--reason", default=None,
        help="Free-text reason recorded with the mint event.",
    )
    p_mint.set_defaults(_run=_run_mint)

    # ---- show --------------------------------------------------------------
    p_show = passport_sub.add_parser(
        "show",
        help=(
            "Print the passport.json sitting next to the agent's "
            "constitution (no HTTP — reads off disk)."
        ),
    )
    p_show.add_argument(
        "instance_id",
        help="Agent instance_id.",
    )
    p_show.add_argument(
        "--souls-dir", default="data/souls",
        help=(
            "Override the souls directory. Defaults to data/souls "
            "(matches the daemon's default soul_install_dir)."
        ),
    )
    p_show.set_defaults(_run=_run_show)

    # ---- fingerprint -------------------------------------------------------
    p_fp = passport_sub.add_parser(
        "fingerprint",
        help=(
            "Print this machine's hardware fingerprint. Useful when "
            "an operator on machine-A is minting a passport for an "
            "agent that will roam to machine-B."
        ),
    )
    p_fp.set_defaults(_run=_run_fingerprint)


def _http(args, method: str, path: str,
          body: dict | None = None) -> tuple[int, dict | None]:
    token = (
        getattr(args, "api_token", None)
        or os.environ.get("FSF_API_TOKEN")
    )
    headers = {"Content-Type": "application/json"}
    if token:
        # Daemon's require_api_token reads X-FSF-Token, NOT Authorization
        # — mirror the same header agent_cmd should be using; we use the
        # daemon's actual contract here.
        headers["X-FSF-Token"] = token
    url = args.daemon_url.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, headers=headers, method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30.0) as resp:
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
            f"fsf passport: cannot reach daemon at {args.daemon_url}: {e}",
            file=sys.stderr,
        )
        return -1, None


def _run_mint(args: argparse.Namespace) -> int:
    body: dict = {"authorized_fingerprints": list(args.authorize_fingerprints)}
    if args.expires_at:
        body["expires_at"] = args.expires_at
    if args.operator_id:
        body["operator_id"] = args.operator_id
    if args.reason:
        body["reason"] = args.reason

    status_code, payload = _http(
        args, "POST", f"/agents/{args.instance_id}/passport", body=body,
    )
    if status_code == -1:
        return 7
    if status_code == 200 and payload:
        print(f"passport minted for {args.instance_id}")
        print(f"  issuer:       {payload['issuer_public_key']}")
        print(
            "  authorized:   "
            + ", ".join(payload["authorized_fingerprints"])
        )
        print(f"  issued_at:    {payload['issued_at']}")
        print(f"  expires_at:   {payload.get('expires_at') or 'never'}")
        print(f"  passport_path: {payload['passport_path']}")
        print(f"  audit_seq:    {payload['seq']}")
        return 0
    detail = (payload or {}).get("detail", f"HTTP {status_code}")
    print(f"fsf passport mint: {detail}", file=sys.stderr)
    return 7 if status_code >= 500 else 4


def _run_show(args: argparse.Namespace) -> int:
    agent_dir = Path(args.souls_dir) / args.instance_id
    passport_path = agent_dir / "passport.json"
    if not passport_path.exists():
        print(
            f"fsf passport show: no passport.json at {passport_path}",
            file=sys.stderr,
        )
        return 4
    try:
        text = passport_path.read_text(encoding="utf-8")
    except Exception as e:
        print(
            f"fsf passport show: failed to read {passport_path}: {e}",
            file=sys.stderr,
        )
        return 7
    # Round-trip through json to pretty-print + catch parse errors.
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        print(
            f"fsf passport show: {passport_path} is not valid JSON: {e}",
            file=sys.stderr,
        )
        return 7
    print(json.dumps(data, indent=2))
    return 0


def _run_fingerprint(args: argparse.Namespace) -> int:
    try:
        from forest_soul_forge.core.hardware import compute_hardware_fingerprint
        fp = compute_hardware_fingerprint()
    except Exception as e:
        print(
            f"fsf passport fingerprint: failed to compute: {e}",
            file=sys.stderr,
        )
        return 7
    # Print the fingerprint on stdout (script-friendly) + the source
    # on stderr (operator info, doesn't pollute pipes).
    print(fp.fingerprint)
    print(f"(source: {fp.source})", file=sys.stderr)
    return 0
