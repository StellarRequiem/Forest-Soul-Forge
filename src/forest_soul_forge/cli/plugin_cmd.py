"""``fsf plugin ...`` CLI subparser — ADR-0043 T2.

T2 surface (this burst):
  fsf plugin list                  # enumerate installed + disabled
  fsf plugin info <name>           # dump one plugin's manifest
  fsf plugin install <path>        # copy a local directory into installed/
  fsf plugin uninstall <name>      # remove an installed/disabled plugin
  fsf plugin enable <name>         # move disabled/<n>/ → installed/<n>/
  fsf plugin disable <name>        # move installed/<n>/ → disabled/<n>/
  fsf plugin verify <name>         # re-check entry-point sha256

Deferred to later tranches:
  fsf plugin install <git-url>     # T5 — registry / Git-backed install
  fsf plugin secrets ...           # T2.5 — operator-set secrets
  fsf plugin search                # T5 — registry catalog
  fsf plugin reload                # T3 — daemon-side hot-reload
  fsf plugin update                # T5 — registry refresh
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from forest_soul_forge.plugins import (
    PluginAlreadyInstalled,
    PluginError,
    PluginNotFound,
    PluginRepository,
    PluginState,
    PluginValidationError,
)


# ---------------------------------------------------------------------------
# Subparser registration (called from cli/main.py)
# ---------------------------------------------------------------------------

def add_subparser(parent_subparsers: argparse._SubParsersAction) -> None:
    """Wire ``fsf plugin ...`` into the root CLI."""
    plugin = parent_subparsers.add_parser(
        "plugin",
        help="Install / list / enable / disable Forest plugins (ADR-0043).",
    )
    plugin_sub = plugin.add_subparsers(dest="plugin_cmd", metavar="<subcmd>")
    plugin_sub.required = True

    # --plugin-root flag is shared across all subcommands. Operators
    # rarely need it; tests use it heavily.
    def _add_root_flag(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--plugin-root",
            default=None,
            help=(
                "Override the plugin root path. Defaults to "
                "$FSF_PLUGIN_ROOT or ~/.forest/plugins."
            ),
        )

    # ---- list ------------------------------------------------------
    p_list = plugin_sub.add_parser(
        "list",
        help="Enumerate installed + disabled plugins.",
    )
    _add_root_flag(p_list)
    p_list.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Emit machine-readable JSON instead of the table.",
    )
    p_list.set_defaults(_run=_run_list)

    # ---- info ------------------------------------------------------
    p_info = plugin_sub.add_parser(
        "info",
        help="Show one plugin's manifest + filesystem state.",
    )
    _add_root_flag(p_info)
    p_info.add_argument("name", help="Plugin name")
    p_info.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Emit machine-readable JSON.",
    )
    p_info.set_defaults(_run=_run_info)

    # ---- install ---------------------------------------------------
    p_install = plugin_sub.add_parser(
        "install",
        help=(
            "Install a plugin from a local directory holding a "
            "plugin.yaml. Registry-backed install lands in T5."
        ),
    )
    _add_root_flag(p_install)
    p_install.add_argument(
        "source",
        help="Path to the directory containing plugin.yaml.",
    )
    p_install.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing plugin at the target path.",
    )
    p_install.set_defaults(_run=_run_install)

    # ---- uninstall -------------------------------------------------
    p_uninstall = plugin_sub.add_parser(
        "uninstall",
        help="Remove an installed or disabled plugin.",
    )
    _add_root_flag(p_uninstall)
    p_uninstall.add_argument("name")
    p_uninstall.set_defaults(_run=_run_uninstall)

    # ---- enable / disable ------------------------------------------
    p_enable = plugin_sub.add_parser(
        "enable",
        help="Move a disabled plugin back to installed/. No daemon "
             "reload is performed (T3 will add that).",
    )
    _add_root_flag(p_enable)
    p_enable.add_argument("name")
    p_enable.set_defaults(_run=_run_enable)

    p_disable = plugin_sub.add_parser(
        "disable",
        help="Move an installed plugin to disabled/.",
    )
    _add_root_flag(p_disable)
    p_disable.add_argument("name")
    p_disable.set_defaults(_run=_run_disable)

    # ---- verify ----------------------------------------------------
    p_verify = plugin_sub.add_parser(
        "verify",
        help=(
            "Re-compute the entry-point binary's sha256 and compare "
            "against the manifest's pinned value. Exits 0 on match, "
            "1 on mismatch."
        ),
    )
    _add_root_flag(p_verify)
    p_verify.add_argument("name")
    p_verify.set_defaults(_run=_run_verify)

    # ---- grant / revoke / grants list (ADR-0043 fu#2 — Burst 113b) -
    # These talk to the running daemon's HTTP surface (the audit
    # event must land in the chain, which lives daemon-side). The
    # other plugin subcommands above operate directly on the
    # filesystem repository because plugin install/uninstall is a
    # disk operation; grants are agent-state mutations and so live
    # in the daemon.
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
                "no token (works against deployments that don't "
                "require auth)."
            ),
        )

    p_grant = plugin_sub.add_parser(
        "grant",
        help=(
            "Issue a plugin grant to an agent. Adds the plugin to the "
            "agent's effective allowed_mcp_servers without rebirthing."
        ),
    )
    _add_daemon_flag(p_grant)
    p_grant.add_argument("plugin_name")
    p_grant.add_argument(
        "--to", dest="instance_id", required=True,
        help="Target agent's instance_id.",
    )
    p_grant.add_argument(
        "--tier", dest="trust_tier", default="yellow",
        choices=("green", "yellow", "red"),
        help=(
            "Trust tier for the grant. Forward-compat per ADR-0045; "
            "enforcement lands in Burst 115."
        ),
    )
    p_grant.add_argument("--reason", default=None)
    p_grant.set_defaults(_run=_run_grant)

    p_revoke = plugin_sub.add_parser(
        "revoke",
        help="Revoke an active plugin grant.",
    )
    _add_daemon_flag(p_revoke)
    p_revoke.add_argument("plugin_name")
    p_revoke.add_argument(
        "--from", dest="instance_id", required=True,
        help="Target agent's instance_id.",
    )
    p_revoke.add_argument("--reason", default=None)
    p_revoke.set_defaults(_run=_run_revoke)

    p_grants = plugin_sub.add_parser(
        "grants",
        help="List active plugin grants for an agent.",
    )
    _add_daemon_flag(p_grants)
    p_grants.add_argument(
        "--for", dest="instance_id", required=True,
        help="Target agent's instance_id.",
    )
    p_grants.add_argument(
        "--history", action="store_true",
        help="Include revoked grants in the output.",
    )
    p_grants.set_defaults(_run=_run_grants_list)


# ---------------------------------------------------------------------------
# Runners. Exit codes per errors.py docstring.
# ---------------------------------------------------------------------------

def _repo(args: argparse.Namespace) -> PluginRepository:
    root = Path(args.plugin_root).expanduser() if args.plugin_root else None
    return PluginRepository(root=root)


def _run_list(args: argparse.Namespace) -> int:
    try:
        infos = _repo(args).list()
    except PluginError as e:
        print(f"fsf plugin: {e}", file=sys.stderr)
        return 7
    if args.as_json:
        print(json.dumps([
            {
                "name": i.name,
                "state": i.state.value,
                "version": i.manifest.version,
                "type": i.manifest.type.value,
                "directory": str(i.directory),
            }
            for i in infos
        ], indent=2))
        return 0
    if not infos:
        print("(no plugins installed)")
        return 0
    # Plain table. Width-padding kept simple — ANSI prettiness deferred.
    print(f"{'NAME':<32} {'STATE':<10} {'TYPE':<14} {'VERSION':<10}")
    for i in infos:
        print(
            f"{i.name:<32} {i.state.value:<10} "
            f"{i.manifest.type.value:<14} {i.manifest.version:<10}"
        )
    return 0


def _run_info(args: argparse.Namespace) -> int:
    try:
        info = _repo(args).load(args.name)
    except PluginNotFound as e:
        print(f"fsf plugin info: {e}", file=sys.stderr)
        return 4
    except PluginError as e:
        print(f"fsf plugin info: {e}", file=sys.stderr)
        return 7
    if args.as_json:
        print(json.dumps({
            "name": info.name,
            "state": info.state.value,
            "directory": str(info.directory),
            "manifest": info.manifest.model_dump(mode="json"),
        }, indent=2))
        return 0
    m = info.manifest
    print(f"name:          {m.name}")
    print(f"display_name:  {m.display_label()}")
    print(f"version:       {m.version}")
    print(f"type:          {m.type.value}")
    print(f"state:         {info.state.value}")
    print(f"author:        {m.author or '(unset)'}")
    print(f"license:       {m.license or '(unset)'}")
    print(f"side_effects:  {m.side_effects.value}")
    print(f"capabilities:  {len(m.capabilities)} declared")
    for cap in m.capabilities:
        print(f"  - {cap}")
    print(f"required_secrets: {len(m.required_secrets)}")
    for s in m.required_secrets:
        print(f"  - {s.name} (env: {s.env_var})")
    print(f"directory:     {info.directory}")
    return 0


def _run_install(args: argparse.Namespace) -> int:
    src = Path(args.source).expanduser().resolve()
    try:
        info = _repo(args).install_from_dir(src, force=args.force)
    except PluginAlreadyInstalled as e:
        print(f"fsf plugin install: {e}", file=sys.stderr)
        return 5
    except PluginValidationError as e:
        print(f"fsf plugin install: {e}", file=sys.stderr)
        return 6
    except PluginError as e:
        print(f"fsf plugin install: {e}", file=sys.stderr)
        return 7
    print(f"installed: {info.name} v{info.manifest.version} "
          f"({info.manifest.type.value}) → {info.directory}")
    print()
    print("Note: T2 only stages the manifest + binary on disk. The")
    print("daemon doesn't pick up new plugins until T3 (Burst 105)")
    print("lands hot-reload. Restart the daemon to register tools now.")
    return 0


def _run_uninstall(args: argparse.Namespace) -> int:
    try:
        info = _repo(args).uninstall(args.name)
    except PluginNotFound as e:
        print(f"fsf plugin uninstall: {e}", file=sys.stderr)
        return 4
    except PluginError as e:
        print(f"fsf plugin uninstall: {e}", file=sys.stderr)
        return 7
    print(f"removed: {info.name} (was {info.state.value})")
    return 0


def _run_enable(args: argparse.Namespace) -> int:
    try:
        info = _repo(args).enable(args.name)
    except PluginNotFound as e:
        print(f"fsf plugin enable: {e}", file=sys.stderr)
        return 4
    except PluginError as e:
        print(f"fsf plugin enable: {e}", file=sys.stderr)
        return 7
    print(f"enabled: {info.name} (now in installed/)")
    return 0


def _run_disable(args: argparse.Namespace) -> int:
    try:
        info = _repo(args).disable(args.name)
    except PluginNotFound as e:
        print(f"fsf plugin disable: {e}", file=sys.stderr)
        return 4
    except PluginError as e:
        print(f"fsf plugin disable: {e}", file=sys.stderr)
        return 7
    print(f"disabled: {info.name} (now in disabled/)")
    return 0


def _run_verify(args: argparse.Namespace) -> int:
    try:
        ok = _repo(args).verify_binary(args.name)
    except PluginNotFound as e:
        print(f"fsf plugin verify: {e}", file=sys.stderr)
        return 4
    except PluginValidationError as e:
        print(f"fsf plugin verify: {e}", file=sys.stderr)
        return 6
    except PluginError as e:
        print(f"fsf plugin verify: {e}", file=sys.stderr)
        return 7
    if ok:
        print(f"verify: {args.name} sha256 matches manifest")
        return 0
    print(
        f"verify: {args.name} sha256 MISMATCH — binary differs from "
        f"manifest's pinned hash. Refusing to run plugins until "
        f"the operator resolves.",
        file=sys.stderr,
    )
    return 1


# ---- HTTP-backed grant runners (ADR-0043 fu#2 — Burst 113b) ---------------
#
# These hit the daemon HTTP surface so the audit chain emit happens
# server-side. Same urllib pattern as cli/triune.py + cli/install.py.
# Operator authentication: --api-token flag overrides $FSF_API_TOKEN
# overrides nothing (works on no-auth deployments).

def _http_call(args, method: str, path: str,
               body: dict | None = None) -> tuple[int, dict | None]:
    """Issue an HTTP request to the daemon. Returns (status, body_json).
    Body is None on non-JSON responses. Network failures map to
    (-1, None) with the error printed to stderr."""
    import json as _json
    import os as _os
    import urllib.error as _ue
    import urllib.request as _ur

    token = (
        getattr(args, "api_token", None)
        or _os.environ.get("FSF_API_TOKEN")
    )
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = args.daemon_url.rstrip("/") + path
    data = _json.dumps(body).encode("utf-8") if body is not None else None
    req = _ur.Request(url, data=data, headers=headers, method=method)
    try:
        with _ur.urlopen(req, timeout=10.0) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, _json.loads(raw) if raw else None
            except _json.JSONDecodeError:
                return resp.status, None
    except _ue.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, _json.loads(raw) if raw else None
        except _json.JSONDecodeError:
            return e.code, {"detail": raw}
    except _ue.URLError as e:
        print(
            f"fsf plugin: cannot reach daemon at {args.daemon_url}: {e}",
            file=sys.stderr,
        )
        return -1, None


def _run_grant(args: argparse.Namespace) -> int:
    body = {
        "plugin_name": args.plugin_name,
        "trust_tier": args.trust_tier,
    }
    if args.reason:
        body["reason"] = args.reason
    status_code, payload = _http_call(
        args, "POST",
        f"/agents/{args.instance_id}/plugin-grants",
        body=body,
    )
    if status_code == -1:
        return 7
    if status_code == 200 and payload and payload.get("ok"):
        g = payload["grant"]
        print(
            f"granted: {g['plugin_name']} (tier={g['trust_tier']}) "
            f"to {g['instance_id']}"
        )
        return 0
    detail = (payload or {}).get("detail", f"HTTP {status_code}")
    print(f"fsf plugin grant: {detail}", file=sys.stderr)
    return 7 if status_code >= 500 else 4


def _run_revoke(args: argparse.Namespace) -> int:
    body = {"reason": args.reason} if args.reason else None
    status_code, payload = _http_call(
        args, "DELETE",
        f"/agents/{args.instance_id}/plugin-grants/{args.plugin_name}",
        body=body,
    )
    if status_code == -1:
        return 7
    if status_code == 200 and payload and payload.get("ok"):
        print(
            f"revoked: {payload['plugin_name']} from {payload['instance_id']}"
        )
        return 0
    detail = (payload or {}).get("detail", f"HTTP {status_code}")
    print(f"fsf plugin revoke: {detail}", file=sys.stderr)
    return 7 if status_code >= 500 else 4


def _run_grants_list(args: argparse.Namespace) -> int:
    suffix = "?history=true" if args.history else ""
    status_code, payload = _http_call(
        args, "GET",
        f"/agents/{args.instance_id}/plugin-grants{suffix}",
    )
    if status_code == -1:
        return 7
    if status_code == 200 and payload:
        grants = payload.get("grants", [])
        if not grants:
            print(f"no grants for {args.instance_id}")
            return 0
        for g in grants:
            state = "active" if g.get("is_active") else "revoked"
            tier = g.get("trust_tier", "?")
            print(
                f"{g['plugin_name']:30s} {tier:6s} {state:8s} "
                f"granted_at={g.get('granted_at', '?')}"
            )
        return 0
    detail = (payload or {}).get("detail", f"HTTP {status_code}")
    print(f"fsf plugin grants: {detail}", file=sys.stderr)
    return 7 if status_code >= 500 else 4
