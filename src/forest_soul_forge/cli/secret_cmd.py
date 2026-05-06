"""``fsf secret ...`` subcommand — manage plugin secrets via the
ADR-0052 pluggable backend (resolved per FSF_SECRET_STORE).

Subcommands:

    fsf secret put <name>         # prompts for value (no echo)
    fsf secret get <name>         # masked print; --reveal for plain
    fsf secret delete <name>      # confirmation prompt
    fsf secret list               # names only, no values
    fsf secret backend            # show active backend + config

Talks DIRECTLY to the resolved SecretStoreProtocol — does NOT go
through the daemon HTTP surface. Forest's daemon doesn't proxy
secret operations because the operator + the backend live on
the same host (the daemon would just be passing bytes around).
This means the CLI works even when the daemon is offline; it
also means audit-chain emission for `secret_put` / `secret_delete`
events fires once T4 wires audit-callable into the resolver path.
"""
from __future__ import annotations

import argparse
import getpass
import os
import platform
import sys

from forest_soul_forge.security.secrets import (
    SecretStoreError,
    resolve_secret_store,
)


def add_subparser(parent_subparsers: argparse._SubParsersAction) -> None:
    secret = parent_subparsers.add_parser(
        "secret",
        help="Manage plugin secrets (ADR-0052 pluggable backend).",
    )
    secret_sub = secret.add_subparsers(dest="secret_cmd", metavar="<action>")
    secret_sub.required = True

    # ---- put -----------------------------------------------------------
    p_put = secret_sub.add_parser(
        "put",
        help="Store a secret. Prompts for the value with no echo.",
    )
    p_put.add_argument(
        "name",
        help=(
            "Secret name (e.g., 'openai_key'). Plugins reference this "
            "in their plugin.yaml's required_secrets list."
        ),
    )
    p_put.add_argument(
        "--from-stdin", action="store_true",
        help=(
            "Read the value from stdin instead of prompting. Useful "
            "for piping: `echo $TOKEN | fsf secret put openai_key "
            "--from-stdin`. The piped value MUST end with a newline; "
            "the trailing newline is stripped."
        ),
    )
    p_put.set_defaults(_run=_run_put)

    # ---- get -----------------------------------------------------------
    p_get = secret_sub.add_parser(
        "get",
        help="Retrieve a secret. Masks the value by default.",
    )
    p_get.add_argument("name")
    p_get.add_argument(
        "--reveal", action="store_true",
        help=(
            "Print the actual value to stdout (unmasked). Use with "
            "care — anything that's not piped to your shell may end "
            "up in scrollback. Default behavior masks all but the "
            "first 4 + last 4 characters."
        ),
    )
    p_get.set_defaults(_run=_run_get)

    # ---- delete --------------------------------------------------------
    p_del = secret_sub.add_parser(
        "delete",
        help="Remove a secret. Asks for confirmation.",
    )
    p_del.add_argument("name")
    p_del.add_argument(
        "--yes", action="store_true",
        help="Skip the confirmation prompt (for scripts).",
    )
    p_del.set_defaults(_run=_run_delete)

    # ---- list ----------------------------------------------------------
    p_list = secret_sub.add_parser(
        "list",
        help="List all secret names this backend can serve. No values.",
    )
    p_list.set_defaults(_run=_run_list)

    # ---- backend -------------------------------------------------------
    p_backend = secret_sub.add_parser(
        "backend",
        help="Show the active secret-store backend + how it was selected.",
    )
    p_backend.set_defaults(_run=_run_backend)


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------

def _run_put(args: argparse.Namespace) -> int:
    try:
        store = resolve_secret_store()
    except SecretStoreError as e:
        print(f"fsf secret: backend not available: {e}", file=sys.stderr)
        return 7

    if args.from_stdin:
        # Read whole input; strip exactly one trailing newline.
        value = sys.stdin.read()
        if value.endswith("\n"):
            value = value[:-1]
        if not value:
            print(
                "fsf secret put: stdin was empty. Provide the secret "
                "via the prompt (omit --from-stdin) or pipe a value.",
                file=sys.stderr,
            )
            return 4
    else:
        try:
            value = getpass.getpass(prompt=f"value for {args.name!r}: ")
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            return 1
        if not value:
            print(
                "fsf secret put: empty value rejected. To delete a "
                "secret, use `fsf secret delete`.",
                file=sys.stderr,
            )
            return 4

    try:
        store.put(args.name, value)
    except SecretStoreError as e:
        print(f"fsf secret put: {e}", file=sys.stderr)
        return 5

    print(f"stored {args.name!r} via backend={store.name}")
    return 0


def _run_get(args: argparse.Namespace) -> int:
    try:
        store = resolve_secret_store()
    except SecretStoreError as e:
        print(f"fsf secret: backend not available: {e}", file=sys.stderr)
        return 7

    try:
        value = store.get(args.name)
    except SecretStoreError as e:
        print(f"fsf secret get: {e}", file=sys.stderr)
        return 5

    if value is None:
        print(
            f"fsf secret get: {args.name!r} not stored in backend "
            f"{store.name!r}. Use `fsf secret put {args.name}` first.",
            file=sys.stderr,
        )
        return 6

    if args.reveal:
        # No newline so piping to other commands stays clean.
        sys.stdout.write(value)
        return 0

    masked = _mask(value)
    print(f"{args.name}: {masked}  (use --reveal to print plaintext)")
    return 0


def _run_delete(args: argparse.Namespace) -> int:
    try:
        store = resolve_secret_store()
    except SecretStoreError as e:
        print(f"fsf secret: backend not available: {e}", file=sys.stderr)
        return 7

    if not args.yes:
        # Confirmation matches the existing `fsf agent` revoke pattern.
        try:
            answer = input(
                f"Delete {args.name!r} from backend={store.name!r}? "
                f"[type 'yes' to confirm]: "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            return 1
        if answer != "yes":
            print("aborted; nothing changed")
            return 0

    try:
        store.delete(args.name)
    except SecretStoreError as e:
        print(f"fsf secret delete: {e}", file=sys.stderr)
        return 5

    # Idempotent — even if the name wasn't stored, the call succeeded.
    # That's the contract per ADR-0052 §protocol.
    print(f"deleted {args.name!r} (or already absent) from backend={store.name}")
    return 0


def _run_list(args: argparse.Namespace) -> int:
    try:
        store = resolve_secret_store()
    except SecretStoreError as e:
        print(f"fsf secret: backend not available: {e}", file=sys.stderr)
        return 7

    try:
        names = store.list_names()
    except SecretStoreError as e:
        print(f"fsf secret list: {e}", file=sys.stderr)
        return 5

    if not names:
        print(
            f"(no secrets stored via backend={store.name}). Use "
            f"`fsf secret put <name>` to add one."
        )
        return 0

    for n in sorted(names):
        print(n)
    return 0


def _run_backend(args: argparse.Namespace) -> int:
    explicit = os.environ.get("FSF_SECRET_STORE", "").strip()
    try:
        store = resolve_secret_store()
    except SecretStoreError as e:
        print(f"fsf secret: backend resolution failed: {e}", file=sys.stderr)
        return 7

    if explicit:
        source = f"explicit (FSF_SECRET_STORE={explicit!r})"
    else:
        source = f"platform default for {platform.system()!r}"

    print(f"backend: {store.name}")
    print(f"selected via: {source}")

    # Backend-specific config hints — non-essential but operators
    # debugging "why isn't my secret showing up" appreciate the
    # path information.
    if store.name == "file":
        path_attr = getattr(store, "_path", None)
        if path_attr is not None:
            print(f"file path: {path_attr}")
            override = os.environ.get("FSF_FILE_SECRETS_PATH")
            if override:
                print(f"  (overridden via FSF_FILE_SECRETS_PATH={override!r})")
    elif store.name == "keychain":
        print("service prefix: forest-soul-forge:")
        print("account:        forest-soul-forge")
        print("(stored in the user's login keychain on macOS)")
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mask(value: str) -> str:
    """Mask a secret for display, keeping enough characters for a
    human to recognize the right secret without exposing it.

    Format: first 4 + ' … ' + last 4 + ' (N chars)' for values
    longer than 12; otherwise '*' * len(value) so we never reveal
    too much of a short token.
    """
    if not value:
        return "(empty)"
    n = len(value)
    if n <= 12:
        return "*" * n + f" ({n} chars)"
    return f"{value[:4]}…{value[-4:]} ({n} chars)"
