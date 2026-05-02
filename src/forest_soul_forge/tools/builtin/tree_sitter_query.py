"""``tree_sitter_query.v1`` — AST-level structural queries via tree-sitter.

Side effects: read_only. Tree-sitter parses source files into ASTs;
nothing is mutated. Pure inspection primitive.

Eighth Phase G.1.A programming primitive. Where the previous tools
(ruff/mypy/semgrep) operate at line-and-token level, tree_sitter
operates at the syntax-tree level. The S-expression query language
lets the agent ask 'find every function definition that calls
deprecated_func' or 'find all class methods missing docstrings' —
the kind of structural pattern that's awkward in regex but trivial
in tree-sitter.

Per-agent constitution must populate:
  allowed_paths: [/abs/path/to/repo, ...]

Path discipline mirrors prior G.1.A primitives:
  - Resolve to absolute symlink-free form
  - File or directory both supported (we recursively scan dirs)
  - Extension-based filtering matches the requested language

Lazy import strategy:
  - tree_sitter and tree_sitter_languages are imported INSIDE
    execute() so the daemon boots without the optional dep
  - When the import fails we raise TreeSitterNotInstalledError
    with a helpful install hint
  - Same dep applies for the language grammar — we lazy-load
    grammars by name so installing the package without the
    specific grammar doesn't crash the daemon

Argument-injection defense:
  - language must match an allowlist of known grammar names
    (prevents arbitrary string from reaching the grammar loader)
  - query is passed verbatim to tree-sitter's parser; the parser
    rejects invalid S-expressions with a clear error
  - file extension allowlist per language gates what files we
    bother parsing (so a .py query doesn't try to parse .png)

Truncation:
  - max_matches (default 100, ceiling 1000) caps total matches
  - max_files (default 200, ceiling 2000) caps files scanned in
    directory mode (prevents pathological dirs from holding the
    dispatch budget)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_MAX_MATCHES = 100
TS_MAX_MATCHES_HARD_CAP = 1000
DEFAULT_MAX_FILES = 200
TS_MAX_FILES_HARD_CAP = 2000
DEFAULT_TIMEOUT_SECONDS = 30
TS_TIMEOUT_HARD_CAP = 300
TS_TIMEOUT_MIN = 1

# Languages we know how to load. Each maps to (grammar_name, [extensions]).
# The grammar_name is what tree_sitter_languages.get_language() expects.
LANGUAGE_REGISTRY: dict[str, tuple[str, tuple[str, ...]]] = {
    "python":     ("python",     (".py", ".pyi")),
    "javascript": ("javascript", (".js", ".mjs", ".cjs", ".jsx")),
    "typescript": ("typescript", (".ts", ".tsx")),
    "rust":       ("rust",       (".rs",)),
    "go":         ("go",         (".go",)),
    "java":       ("java",       (".java",)),
    "c":          ("c",          (".c", ".h")),
    "cpp":        ("cpp",        (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx")),
    "ruby":       ("ruby",       (".rb",)),
    "yaml":       ("yaml",       (".yaml", ".yml")),
    "json":       ("json",       (".json",)),
    "bash":       ("bash",       (".sh", ".bash")),
}


class TreeSitterQueryError(ToolValidationError):
    """Raised by tree_sitter_query for path-allowlist or invocation failures."""


class TreeSitterNotInstalledError(TreeSitterQueryError):
    """Raised when tree_sitter / tree_sitter_languages are not installed."""


class TreeSitterGrammarMissingError(TreeSitterQueryError):
    """Raised when the requested grammar isn't bundled in tree_sitter_languages."""


class TreeSitterQueryParseError(TreeSitterQueryError):
    """Raised when the supplied query string is not valid tree-sitter syntax."""


class TreeSitterQueryTool:
    """Args:
      path (str, required): file or directory to query.
      query (str, required): tree-sitter S-expression query string.
      language (str, required): one of LANGUAGE_REGISTRY keys.
      max_matches (int, optional): cap on matches returned. Default
        100, max 1000.
      max_files (int, optional): cap on files scanned in directory
        mode. Default 200, max 2000.
      timeout_seconds (int, optional): wall-clock budget. Default
        30, max 300.

    Output:
      {
        "path":          str,
        "language":      str,
        "matches_count": int,
        "truncated":     bool,
        "files_scanned": int,
        "matches": [
          {
            "filename":  str,
            "captures": [
              {
                "name":        str,
                "text":        str,
                "start_line":  int,
                "end_line":    int,
                "start_col":   int,
                "end_col":     int,
              }, ...
            ]
          }, ...
        ]
      }

    Constraints (read from ctx.constraints):
      allowed_paths: list[str]   # required, absolute paths
    """

    name = "tree_sitter_query"
    version = "1"
    side_effects = "read_only"
    # No required_initiative_level — read_only passes any L.

    def validate(self, args: dict[str, Any]) -> None:
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ToolValidationError(
                "path is required and must be a non-empty string"
            )

        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolValidationError(
                "query is required and must be a non-empty string"
            )

        language = args.get("language")
        if language not in LANGUAGE_REGISTRY:
            raise ToolValidationError(
                f"language must be one of {sorted(LANGUAGE_REGISTRY)}; "
                f"got {language!r}"
            )

        max_matches = args.get("max_matches", DEFAULT_MAX_MATCHES)
        if (
            not isinstance(max_matches, int)
            or max_matches < 1
            or max_matches > TS_MAX_MATCHES_HARD_CAP
        ):
            raise ToolValidationError(
                f"max_matches must be a positive int <= "
                f"{TS_MAX_MATCHES_HARD_CAP}; got {max_matches!r}"
            )

        max_files = args.get("max_files", DEFAULT_MAX_FILES)
        if (
            not isinstance(max_files, int)
            or max_files < 1
            or max_files > TS_MAX_FILES_HARD_CAP
        ):
            raise ToolValidationError(
                f"max_files must be a positive int <= "
                f"{TS_MAX_FILES_HARD_CAP}; got {max_files!r}"
            )

        timeout = args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        if (
            not isinstance(timeout, int)
            or timeout < TS_TIMEOUT_MIN
            or timeout > TS_TIMEOUT_HARD_CAP
        ):
            raise ToolValidationError(
                f"timeout_seconds must be in [{TS_TIMEOUT_MIN}, "
                f"{TS_TIMEOUT_HARD_CAP}]; got {timeout!r}"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        raw_path: str = args["path"]
        query_str: str = args["query"]
        language: str = args["language"]
        max_matches = int(args.get("max_matches", DEFAULT_MAX_MATCHES))
        max_files = int(args.get("max_files", DEFAULT_MAX_FILES))
        timeout_seconds = int(args.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))

        allowed_paths_raw = ctx.constraints.get("allowed_paths") or ()
        if not allowed_paths_raw:
            raise TreeSitterQueryError(
                "agent has no allowed_paths in its constitution — "
                "tree_sitter_query.v1 refuses to touch the filesystem"
            )

        allowed_roots = tuple(_resolve_allowlist(allowed_paths_raw))

        try:
            target = Path(raw_path).resolve(strict=True)
        except FileNotFoundError:
            raise TreeSitterQueryError(f"path does not exist: {raw_path!r}")
        except OSError as e:
            raise TreeSitterQueryError(f"path resolution failed: {e}") from e

        if not _is_within_any(target, allowed_roots):
            raise TreeSitterQueryError(
                f"path {str(target)!r} is outside the agent's "
                f"allowed_paths ({[str(p) for p in allowed_roots]})"
            )

        grammar_name, exts = LANGUAGE_REGISTRY[language]

        # Lazy import. tree_sitter_languages bundles many grammars and
        # is the most reliable way to load one without per-grammar
        # build setup. If the operator hasn't installed the dep we
        # surface a clear refusal with the install hint.
        parser, language_obj = _load_parser(grammar_name)

        # Compile the query. This validates S-expression syntax up-
        # front (better error than 'Query.matches blew up').
        try:
            compiled_query = language_obj.query(query_str)
        except Exception as e:
            raise TreeSitterQueryParseError(
                f"query is not valid tree-sitter syntax for language "
                f"{language!r}: {e}"
            ) from e

        # Enumerate files to scan. Directory mode walks recursively
        # and applies the extension allowlist. File mode just verifies
        # the extension matches.
        files = _enumerate_files(target, exts, max_files)

        import time
        deadline = time.monotonic() + timeout_seconds

        all_matches: list[dict[str, Any]] = []
        files_scanned = 0
        for fp in files:
            if time.monotonic() >= deadline:
                raise TreeSitterQueryError(
                    f"tree_sitter_query timed out after {timeout_seconds}s "
                    f"after scanning {files_scanned} files"
                )
            files_scanned += 1
            try:
                source_bytes = fp.read_bytes()
            except OSError:
                continue
            tree = parser.parse(source_bytes)
            captures = compiled_query.captures(tree.root_node)
            # tree_sitter's API for captures has shifted across versions:
            # newer versions return a dict {name: [nodes]}; older return
            # a list of (node, name) tuples. Handle both.
            normalized_captures = _normalize_captures(captures, source_bytes)
            if normalized_captures:
                all_matches.append({
                    "filename": str(fp),
                    "captures": normalized_captures,
                })
                if len(all_matches) > max_matches:
                    break

        actual = len(all_matches)
        truncated = actual > max_matches
        kept = all_matches[:max_matches]

        return ToolResult(
            output={
                "path":          str(target),
                "language":      language,
                "matches_count": len(kept),
                "truncated":     truncated,
                "files_scanned": files_scanned,
                "matches":       kept,
            },
            metadata={
                "allowed_roots": [str(p) for p in allowed_roots],
                "actual_count":  actual,
                "max_matches":   max_matches,
                "max_files":     max_files,
                "grammar_name":  grammar_name,
                "extensions":    list(exts),
            },
            side_effect_summary=(
                f"tree_sitter_query[{language}]: {len(kept)} matches "
                f"across {files_scanned} files (truncated={truncated})"
            ),
        )


# ---------------------------------------------------------------------------
# Helpers — module-level for testability
# ---------------------------------------------------------------------------
def _load_parser(grammar_name: str):
    """Lazy-load the tree_sitter parser + language. Raises clean
    refusal errors with install hints when something's missing."""
    try:
        import tree_sitter  # noqa: F401  -- import surface check
    except ImportError as e:
        raise TreeSitterNotInstalledError(
            "tree_sitter is not installed. Install via "
            "`pip install tree_sitter tree_sitter_languages`."
        ) from e

    try:
        from tree_sitter_languages import get_language, get_parser
    except ImportError as e:
        raise TreeSitterNotInstalledError(
            "tree_sitter_languages is not installed. Install via "
            "`pip install tree_sitter_languages` (it bundles many grammars)."
        ) from e

    try:
        parser = get_parser(grammar_name)
        language = get_language(grammar_name)
    except Exception as e:
        raise TreeSitterGrammarMissingError(
            f"tree_sitter_languages does not have grammar {grammar_name!r}: {e}"
        ) from e
    return parser, language


def _enumerate_files(
    target: Path, exts: tuple[str, ...], max_files: int,
) -> list[Path]:
    """If target is a file with a matching extension, return [target].
    If target is a directory, walk it (rglob-style, but bounded) and
    return up to max_files files with matching extensions. We sort by
    posix path so iteration is deterministic."""
    if target.is_file():
        if target.suffix.lower() in exts:
            return [target]
        return []
    out: list[Path] = []
    for p in sorted(target.rglob("*")):
        if len(out) >= max_files:
            break
        if not p.is_file():
            continue
        if p.suffix.lower() not in exts:
            continue
        # Skip hidden directories like .git, .mypy_cache, .venv.
        if any(part.startswith(".") for part in p.parts):
            continue
        out.append(p)
    return out


def _normalize_captures(captures: Any, source: bytes) -> list[dict[str, Any]]:
    """Tree-sitter's captures() return shape varies by version. Handle:
      - list[tuple[Node, str]]            (older API)
      - dict[str, list[Node]]             (newer API)
    Either way, normalize into a flat list of capture dicts."""
    out: list[dict[str, Any]] = []
    if isinstance(captures, dict):
        items = []
        for name, nodes in captures.items():
            for node in nodes:
                items.append((node, name))
    else:
        items = list(captures)

    for node, name in items:
        try:
            text = source[node.start_byte:node.end_byte].decode(
                "utf-8", errors="replace"
            )
        except Exception:
            text = ""
        start = node.start_point
        end = node.end_point
        out.append({
            "name":       name,
            "text":       text,
            "start_line": start[0] + 1,    # tree-sitter is 0-indexed
            "end_line":   end[0] + 1,
            "start_col":  start[1],
            "end_col":    end[1],
        })
    return out


def _resolve_allowlist(paths: Any) -> tuple[Path, ...]:
    out: list[Path] = []
    for raw in paths:
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            out.append(Path(raw).resolve(strict=False))
        except OSError:
            continue
    return tuple(out)


def _is_within_any(target: Path, roots: tuple[Path, ...]) -> bool:
    for root in roots:
        try:
            if target == root or target.is_relative_to(root):
                return True
        except (ValueError, AttributeError):
            continue
    return False


__all__ = [
    "TreeSitterQueryTool",
    "TreeSitterQueryError",
    "TreeSitterNotInstalledError",
    "TreeSitterGrammarMissingError",
    "TreeSitterQueryParseError",
    "DEFAULT_MAX_MATCHES",
    "TS_MAX_MATCHES_HARD_CAP",
    "DEFAULT_MAX_FILES",
    "DEFAULT_TIMEOUT_SECONDS",
    "LANGUAGE_REGISTRY",
]
