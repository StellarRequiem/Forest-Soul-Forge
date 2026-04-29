"""``fsf triune`` — bond three peer-root agents into a sealed triune.

ADR-003X K4. v1 ships the bond-only flow: the operator passes three
already-birthed instance_ids and a bond_name, and the CLI calls the
daemon's ``POST /triune/bond`` endpoint. The daemon patches each
agent's constitution YAML and emits one ``triune.bonded`` ceremony
event.

Future: ``--auto-birth`` flag that births Heartwood/Branch/Leaf with
default trait profiles before bonding. Out of scope for v1 — the
operator should pick birth-time roles + trait profiles deliberately.

Usage:

    fsf triune bond --name aurora --instances <id_h> <id_b> <id_l>

    fsf triune bond --name aurora \\
        --instances <id_h> <id_b> <id_l> \\
        --no-restrict          # opt out of the safety default

The CLI talks to the daemon at ``$FSF_DAEMON_URL`` (default
``http://127.0.0.1:8000``). Exits 0 on success, 1 on any failure.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from forest_soul_forge.cli._common import resolve_operator


def _daemon_url() -> str:
    return os.environ.get("FSF_DAEMON_URL", "http://127.0.0.1:8000").rstrip("/")


def _post(url: str, body: dict, timeout_s: float = 30.0) -> dict:
    """Tiny POST helper — keeps the CLI free of a `requests` dep.

    Raises SystemExit with a readable error on any failure path so the
    operator sees one line, not a stack trace.
    """
    payload = json.dumps(body).encode("utf-8")
    req = Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("detail", "")
        except Exception:
            detail = ""
        raise SystemExit(
            f"daemon returned HTTP {e.code} from {url}: {detail or e.reason}"
        ) from e
    except URLError as e:
        raise SystemExit(
            f"could not reach daemon at {url}: {e.reason}"
        ) from e


def run_bond(args: argparse.Namespace) -> int:
    if len(args.instances) != 3:
        print(
            "fsf triune bond: --instances requires exactly 3 instance ids",
            file=sys.stderr,
        )
        return 2
    if len(set(args.instances)) != 3:
        print(
            "fsf triune bond: the three instance ids must be distinct",
            file=sys.stderr,
        )
        return 2

    body = {
        "bond_name": args.name,
        "instance_ids": list(args.instances),
        "operator_id": args.operator or resolve_operator(),
        "restrict_delegations": not args.no_restrict,
    }
    url = f"{_daemon_url()}/triune/bond"
    print(f"→ POST {url}")
    print(f"  bond_name={body['bond_name']!r}")
    print(f"  instance_ids={body['instance_ids']}")
    print(f"  restrict_delegations={body['restrict_delegations']}")
    print(f"  operator_id={body['operator_id']!r}")
    resp = _post(url, body)
    print()
    print("✓ triune bonded")
    print(f"  bond_name:           {resp['bond_name']}")
    print(f"  restrict_delegations: {resp['restrict_delegations']}")
    print(f"  ceremony seq:        {resp['ceremony_seq']}")
    print(f"  ceremony timestamp:  {resp['ceremony_timestamp']}")
    return 0


def add_subparser(parent_sub: argparse._SubParsersAction) -> None:
    """Register ``fsf triune ...`` under the root parser.

    Called from ``cli/main.py::_build_parser``. Centralizing the
    add-subparser dance here means every triune-CLI change is one file.
    """
    triune = parent_sub.add_parser(
        "triune",
        help="Bond peer-root agents into a sealed triune (ADR-003X K4).",
    )
    triune_sub = triune.add_subparsers(dest="triune_cmd", metavar="<action>")
    triune_sub.required = True

    bond = triune_sub.add_parser(
        "bond",
        help="Seal three already-birthed agents into a triune.",
    )
    bond.add_argument(
        "--name", required=True,
        help="Bond name (e.g. 'aurora'). Shared by all three sisters.",
    )
    bond.add_argument(
        "--instances", nargs=3, metavar="ID", required=True,
        help="Three distinct instance_ids — the sisters of the triune.",
    )
    bond.add_argument(
        "--operator", default=None,
        help=(
            "Operator id recorded in the triune.bonded ceremony event. "
            "Defaults to $USER (or $USERNAME on Windows, or 'operator')."
        ),
    )
    bond.add_argument(
        "--no-restrict", action="store_true",
        help=(
            "Opt out of the safety default. When set, restrict_delegations=false "
            "so delegate.v1 will NOT refuse cross-triune calls. Use only when "
            "the operator deliberately wants a porous triune (default is sealed)."
        ),
    )
    bond.set_defaults(_run=run_bond)
