"""Static analysis for Tool Forge generated code — ADR-0030 T2.

Two flag kinds:

- **Hard flags** block install. The runtime would refuse to dispatch the
  tool, or the file is malformed enough that operator review can't make
  it safe. The forge writes the file but marks the staged folder
  ``REJECTED.md``; ``--force`` overrides for development.
- **Soft flags** are signal, not policy. They surface in the forge log
  + CLI output so the operator reviews them before installing. A
  soft-flag-heavy file might still be correct (subprocess use in a
  filesystem-tier tool is legitimate); we want the operator's eyes on
  it, not the runtime's veto.

Implementation is **AST-only**, no external deps. Bandit's defaults are
broader than the project's threat model and tuning it is more work
than the bespoke pass.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Iterable

# Mirror tool_catalog.SIDE_EFFECT_VALUES so the side_effects validation
# stays in one place. Re-imported elsewhere for the side-effects ranking.
_SIDE_EFFECTS_VALUES = ("read_only", "network", "filesystem", "external")


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Flag:
    """One static-analysis finding.

    ``kind`` is "hard" or "soft". ``rule`` is the rule id (stable
    across versions; tests grep for it). ``message`` is operator-facing
    prose. ``line`` is best-effort source line; 0 when not applicable.
    """

    kind: str
    rule: str
    message: str
    line: int = 0


@dataclass(frozen=True)
class AnalysisResult:
    flags: tuple[Flag, ...]
    parsed_tree: ast.Module | None  # None when the source didn't parse

    @property
    def hard_flags(self) -> tuple[Flag, ...]:
        return tuple(f for f in self.flags if f.kind == "hard")

    @property
    def soft_flags(self) -> tuple[Flag, ...]:
        return tuple(f for f in self.flags if f.kind == "soft")

    @property
    def install_blocked(self) -> bool:
        """True iff any hard flag fired. The CLI maps this to a refusal
        unless --force was passed."""
        return any(f.kind == "hard" for f in self.flags)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def analyze(source: str, *, declared_side_effects: str) -> AnalysisResult:
    """Run every check on the generated tool source.

    ``declared_side_effects`` comes from the ToolSpec — the analyzer
    uses it to flag mismatches (e.g. a tool that calls ``urllib`` but
    the spec says ``read_only``).
    """
    flags: list[Flag] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        flags.append(Flag(
            kind="hard",
            rule="parse_error",
            message=f"source does not parse: {e.msg} (line {e.lineno})",
            line=e.lineno or 0,
        ))
        return AnalysisResult(flags=tuple(flags), parsed_tree=None)

    # Order: shape checks first (a tool that doesn't have a class
    # called Foo with name/version/side_effects is malformed in a way
    # that makes other checks meaningless). Then forbidden imports +
    # forbidden calls. Then soft-flag checks that depend on
    # declared_side_effects.
    flags.extend(_check_tool_class_shape(tree))
    flags.extend(_check_side_effects_value(tree))
    flags.extend(_check_forbidden_calls(tree))
    flags.extend(_check_forbidden_attribute_access(tree))
    flags.extend(_check_network_imports(
        tree, declared_side_effects=declared_side_effects,
    ))
    flags.extend(_check_filesystem_writes(
        tree, declared_side_effects=declared_side_effects,
    ))
    flags.extend(_check_subprocess_use(tree))
    flags.extend(_check_missing_tokens_plumbing(tree))
    flags.extend(_check_todo_markers(source))
    return AnalysisResult(flags=tuple(flags), parsed_tree=tree)


# ---------------------------------------------------------------------------
# Hard checks
# ---------------------------------------------------------------------------
def _check_tool_class_shape(tree: ast.Module) -> Iterable[Flag]:
    """The module must define exactly one class with the Tool Protocol
    shape: name + version + side_effects class attrs, validate +
    execute methods (execute async).

    A module with no Tool class at all is hard-rejected (the runtime
    can't dispatch it). A module with a Tool class missing one of
    those four members is also hard-rejected. Multiple Tool-shaped
    classes is suspicious but soft (operator may have duplicated by
    mistake).
    """
    candidates: list[tuple[str, ast.ClassDef]] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            attrs = _class_attribute_names(node)
            methods = _class_method_names(node)
            if {"name", "version", "side_effects"}.issubset(attrs) and \
               {"validate", "execute"}.issubset(methods):
                candidates.append((node.name, node))
    if not candidates:
        return [Flag(
            kind="hard",
            rule="no_tool_class",
            message=(
                "no class with the Tool Protocol shape found "
                "(needs name + version + side_effects class attributes "
                "and validate + execute methods)"
            ),
        )]
    if len(candidates) > 1:
        return [Flag(
            kind="soft",
            rule="multiple_tool_classes",
            message=(
                f"{len(candidates)} classes match the Tool Protocol shape "
                f"({', '.join(name for name, _ in candidates)}); "
                "the registry registers one — operator should pick"
            ),
            line=candidates[1][1].lineno,
        )]
    cls = candidates[0][1]
    out: list[Flag] = []
    # Verify execute is actually async — sync execute breaks the runtime.
    for item in cls.body:
        if isinstance(item, (ast.AsyncFunctionDef, ast.FunctionDef)) \
                and item.name == "execute":
            if not isinstance(item, ast.AsyncFunctionDef):
                out.append(Flag(
                    kind="hard",
                    rule="execute_must_be_async",
                    message=(
                        f"class {cls.name}.execute is sync; the runtime "
                        "always awaits it. Add `async`."
                    ),
                    line=item.lineno,
                ))
    return out


def _check_side_effects_value(tree: ast.Module) -> Iterable[Flag]:
    """The class attribute ``side_effects`` must be one of the four
    valid values."""
    out: list[Flag] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if isinstance(item, ast.Assign):
                for tgt in item.targets:
                    if isinstance(tgt, ast.Name) and tgt.id == "side_effects":
                        val = _literal_str(item.value)
                        if val is not None and val not in _SIDE_EFFECTS_VALUES:
                            out.append(Flag(
                                kind="hard",
                                rule="invalid_side_effects",
                                message=(
                                    f"class {node.name}.side_effects = "
                                    f"{val!r}; must be one of "
                                    f"{list(_SIDE_EFFECTS_VALUES)}"
                                ),
                                line=item.lineno,
                            ))
    return out


_FORBIDDEN_BUILTINS = {"eval", "exec", "compile"}


def _check_forbidden_calls(tree: ast.Module) -> Iterable[Flag]:
    """``eval``, ``exec``, ``compile`` are sandbox-escape primitives.
    No tool needs them; LLM use of them is almost always a bug."""
    out: list[Flag] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in _FORBIDDEN_BUILTINS:
                out.append(Flag(
                    kind="hard",
                    rule="forbidden_builtin",
                    message=f"call to {fn.id}() is forbidden in tools",
                    line=node.lineno,
                ))
            elif isinstance(fn, ast.Attribute) and fn.attr == "system" \
                    and isinstance(fn.value, ast.Name) and fn.value.id == "os":
                out.append(Flag(
                    kind="hard",
                    rule="os_system",
                    message="os.system(...) is forbidden in tools",
                    line=node.lineno,
                ))
    return out


def _check_forbidden_attribute_access(tree: ast.Module) -> Iterable[Flag]:
    """Dynamic attribute lookup for sandbox escape: getattr(__builtins__,
    ...), __import__(...), etc."""
    out: list[Flag] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id == "__import__":
                out.append(Flag(
                    kind="hard",
                    rule="dynamic_import",
                    message=(
                        "__import__() is forbidden in tools — declare "
                        "imports at module top so static analysis can see "
                        "them"
                    ),
                    line=node.lineno,
                ))
        if isinstance(node, ast.Name) and node.id == "__builtins__":
            out.append(Flag(
                kind="hard",
                rule="builtins_access",
                message=(
                    "__builtins__ access is forbidden in tools — used in "
                    "sandbox escapes"
                ),
                line=node.lineno,
            ))
    return out


# ---------------------------------------------------------------------------
# Soft checks (depend on declared_side_effects)
# ---------------------------------------------------------------------------
_NETWORK_MODULES = {
    "urllib", "urllib.request", "urllib3", "requests", "httpx", "aiohttp",
    "socket", "http", "http.client", "smtplib", "ftplib", "telnetlib",
    "paramiko",
}


def _check_network_imports(
    tree: ast.Module, *, declared_side_effects: str,
) -> Iterable[Flag]:
    """Network-shaped imports in a tool the spec says is read-only is
    a soft flag — the spec is probably wrong. Operator confirms before
    install."""
    if declared_side_effects != "read_only":
        return []
    out: list[Flag] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _NETWORK_MODULES or alias.name in _NETWORK_MODULES:
                    out.append(Flag(
                        kind="soft",
                        rule="network_import_in_read_only",
                        message=(
                            f"import {alias.name} suggests a network call, "
                            "but spec says read_only — review the side_effects "
                            "classification"
                        ),
                        line=node.lineno,
                    ))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            root = mod.split(".")[0]
            if root in _NETWORK_MODULES or mod in _NETWORK_MODULES:
                out.append(Flag(
                    kind="soft",
                    rule="network_import_in_read_only",
                    message=(
                        f"from {mod} import ... suggests a network call, "
                        "but spec says read_only — review the side_effects "
                        "classification"
                    ),
                    line=node.lineno,
                ))
    return out


def _check_filesystem_writes(
    tree: ast.Module, *, declared_side_effects: str,
) -> Iterable[Flag]:
    """Calls that write to disk in a tool the spec says doesn't have
    filesystem side effects. open(..., 'w'), Path(...).write_text /
    write_bytes, shutil.copy/move, os.remove, etc."""
    if declared_side_effects in ("filesystem", "external"):
        return []
    out: list[Flag] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            # open(..., 'w' / 'a' / 'wb' / 'xb')
            if isinstance(fn, ast.Name) and fn.id == "open":
                mode = _literal_str(_get_arg(node, 1, "mode"))
                if mode and any(c in mode for c in ("w", "a", "x")):
                    out.append(Flag(
                        kind="soft",
                        rule="filesystem_write_in_low_tier",
                        message=(
                            f"open(..., {mode!r}) writes to disk, but spec "
                            f"says {declared_side_effects} — bump tier or "
                            "remove the write"
                        ),
                        line=node.lineno,
                    ))
            # Path(...).write_text / write_bytes
            if isinstance(fn, ast.Attribute) and fn.attr in (
                "write_text", "write_bytes", "mkdir", "rmdir", "unlink", "touch",
            ):
                out.append(Flag(
                    kind="soft",
                    rule="filesystem_write_in_low_tier",
                    message=(
                        f"{fn.attr}(...) modifies the filesystem, but spec "
                        f"says {declared_side_effects} — bump tier or remove"
                    ),
                    line=node.lineno,
                ))
    return out


def _check_subprocess_use(tree: ast.Module) -> Iterable[Flag]:
    """subprocess at any tier is potentially fine (some tools genuinely
    shell out) but always worth a second look."""
    out: list[Flag] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    out.append(Flag(
                        kind="soft",
                        rule="subprocess_used",
                        message=(
                            "import subprocess — review for shell-injection "
                            "risk and confirm the side_effects tier reflects "
                            "what the subprocess does"
                        ),
                        line=node.lineno,
                    ))
        elif isinstance(node, ast.ImportFrom):
            if node.module == "subprocess":
                out.append(Flag(
                    kind="soft",
                    rule="subprocess_used",
                    message=(
                        "from subprocess import ... — review for "
                        "shell-injection risk"
                    ),
                    line=node.lineno,
                ))
    return out


def _check_missing_tokens_plumbing(tree: ast.Module) -> Iterable[Flag]:
    """If the tool's execute() calls ``provider.complete(...)`` or
    similar but returns ToolResult without tokens_used, the cost won't
    show on the character sheet. Soft because some LLM-wrapping tools
    legitimately don't have the token count handy (e.g. local
    providers that don't surface it)."""
    out: list[Flag] = []
    calls_provider = False
    returns_with_tokens = True
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr == "complete":
                # ctx.provider.complete(...)
                if _attr_chain_contains(fn, "provider"):
                    calls_provider = True
        if isinstance(node, ast.Return) and node.value is not None:
            ret = node.value
            if isinstance(ret, ast.Call) and \
                    isinstance(ret.func, ast.Name) and \
                    ret.func.id == "ToolResult":
                # Look at keyword args.
                kw_names = {kw.arg for kw in ret.keywords if kw.arg}
                if "tokens_used" not in kw_names:
                    returns_with_tokens = False
    if calls_provider and not returns_with_tokens:
        out.append(Flag(
            kind="soft",
            rule="missing_tokens_plumbing",
            message=(
                "execute() calls a provider but ToolResult is constructed "
                "without tokens_used — accounting won't reflect this call's "
                "cost on the character sheet"
            ),
        ))
    return out


def _check_todo_markers(source: str) -> Iterable[Flag]:
    """LLMs sometimes hedge with TODO/FIXME comments. Soft flag so the
    operator notices the placeholder."""
    out: list[Flag] = []
    for i, line in enumerate(source.splitlines(), start=1):
        upper = line.upper()
        if "TODO" in upper or "FIXME" in upper or "XXX" in upper:
            out.append(Flag(
                kind="soft",
                rule="hedge_marker",
                message=(
                    "TODO/FIXME/XXX marker present — codegen left a "
                    "placeholder; review and replace"
                ),
                line=i,
            ))
    return out


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------
def _class_attribute_names(cls: ast.ClassDef) -> set[str]:
    out: set[str] = set()
    for node in cls.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    out.add(tgt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def _class_method_names(cls: ast.ClassDef) -> set[str]:
    return {
        item.name for item in cls.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _literal_str(node: ast.AST | None) -> str | None:
    """Return the string value of a literal node, or None if not a string
    literal. Avoids ast.literal_eval for safety (tool source is
    untrusted)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _get_arg(call: ast.Call, position: int, keyword: str) -> ast.AST | None:
    """Get arg by position or keyword name from a call node."""
    if position < len(call.args):
        return call.args[position]
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    return None


def _attr_chain_contains(attr: ast.Attribute, name: str) -> bool:
    """Walk an attribute chain (a.b.c.d) checking if any segment equals
    ``name``."""
    cur: ast.AST = attr
    while isinstance(cur, ast.Attribute):
        if cur.attr == name:
            return True
        cur = cur.value
    if isinstance(cur, ast.Name) and cur.id == name:
        return True
    return False
