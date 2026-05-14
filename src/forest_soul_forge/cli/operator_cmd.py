"""``fsf operator ...`` — ADR-0068 T1 (B277) operator profile CLI.

Three subcommands:

  - fsf operator profile show
      Print the current profile (or report missing/encrypted).

  - fsf operator profile verify
      Load + validate the profile file. Exit 0 on valid, non-zero
      with a clear error message on invalid. Useful in CI / pre-
      daemon-boot health checks.

  - fsf operator profile init [--name NAME --email EMAIL ...]
      Bootstrap a fresh profile.yaml from defaults + CLI overrides.
      Refuses to overwrite an existing profile unless --force.

T2 will add ``fsf operator profile set <field> <value>`` once the
write-path tool ships with its approval gate.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from forest_soul_forge.core.operator_profile import (
    OperatorProfile,
    OperatorProfileError,
    WorkHours,
    default_operator_profile_path,
    load_operator_profile,
    save_operator_profile,
)


def add_subparser(parent_subparsers: argparse._SubParsersAction) -> None:
    op = parent_subparsers.add_parser(
        "operator",
        help="Manage the operator profile (ADR-0068).",
    )
    op_sub = op.add_subparsers(dest="operator_cmd", metavar="<subcmd>")
    op_sub.required = True

    profile = op_sub.add_parser(
        "profile",
        help="Operator profile subcommands.",
    )
    profile_sub = profile.add_subparsers(
        dest="profile_cmd", metavar="<subcmd>",
    )
    profile_sub.required = True

    p_show = profile_sub.add_parser(
        "show",
        help="Print the current operator profile as JSON.",
    )
    p_show.add_argument(
        "--profile-path", default=None,
        help="Override profile path (default: data/operator/profile.yaml).",
    )
    p_show.set_defaults(_run=_run_show)

    p_verify = profile_sub.add_parser(
        "verify",
        help="Validate the profile schema + values. Non-zero exit on failure.",
    )
    p_verify.add_argument(
        "--profile-path", default=None,
        help="Override profile path.",
    )
    p_verify.set_defaults(_run=_run_verify)

    p_init = profile_sub.add_parser(
        "init",
        help=(
            "Bootstrap a fresh operator profile. Refuses to "
            "overwrite an existing profile without --force."
        ),
    )
    p_init.add_argument("--operator-id", required=True)
    p_init.add_argument("--name", required=True)
    p_init.add_argument("--preferred-name", default=None,
                        help="Defaults to --name when omitted.")
    p_init.add_argument("--email", required=True)
    p_init.add_argument("--timezone", default="America/New_York")
    p_init.add_argument("--locale", default="en-US")
    p_init.add_argument("--work-hours-start", default="09:00")
    p_init.add_argument("--work-hours-end", default="17:00")
    p_init.add_argument(
        "--profile-path", default=None,
        help="Override target path.",
    )
    p_init.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing profile.",
    )
    p_init.set_defaults(_run=_run_init)


def _run_show(args: argparse.Namespace) -> int:
    path = Path(args.profile_path) if args.profile_path else None
    try:
        profile = load_operator_profile(path)
    except OperatorProfileError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(json.dumps({
        "schema_version": profile.schema_version,
        "operator_id": profile.operator_id,
        "name": profile.name,
        "preferred_name": profile.preferred_name,
        "email": profile.email,
        "timezone": profile.timezone,
        "locale": profile.locale,
        "work_hours": {
            "start": profile.work_hours.start,
            "end": profile.work_hours.end,
        },
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
        "extra": profile.extra,
    }, indent=2))
    return 0


def _run_verify(args: argparse.Namespace) -> int:
    path = Path(args.profile_path) if args.profile_path else None
    try:
        profile = load_operator_profile(path)
    except OperatorProfileError as e:
        print(f"INVALID: {e}", file=sys.stderr)
        return 1
    print(
        f"OK: operator_profile schema v{profile.schema_version} "
        f"for operator_id={profile.operator_id!r}"
    )
    return 0


def _run_init(args: argparse.Namespace) -> int:
    from datetime import datetime, timezone as _tz
    target = (
        Path(args.profile_path) if args.profile_path
        else default_operator_profile_path()
    )
    if target.exists() and not args.force:
        print(
            f"REFUSED: {target} already exists. Pass --force to "
            f"overwrite (operator-edit the file directly to update "
            f"selected fields).",
            file=sys.stderr,
        )
        return 2

    now = datetime.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    profile = OperatorProfile(
        schema_version=1,
        operator_id=args.operator_id,
        name=args.name,
        preferred_name=args.preferred_name or args.name,
        email=args.email,
        timezone=args.timezone,
        locale=args.locale,
        work_hours=WorkHours(
            start=args.work_hours_start, end=args.work_hours_end,
        ),
        created_at=now,
        updated_at=now,
    )
    # Validate by round-tripping through save+load.
    try:
        written = save_operator_profile(profile, target)
        load_operator_profile(written)
    except OperatorProfileError as e:
        print(f"INVALID: {e}", file=sys.stderr)
        return 1
    print(f"wrote {written}")
    return 0
