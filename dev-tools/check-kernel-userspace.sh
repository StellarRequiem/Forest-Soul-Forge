#!/usr/bin/env bash
# Kernel/userspace boundary sentinel — ADR-0044 Phase 1.3 (Burst 120).
#
# Verifies the boundary contract from
# docs/architecture/kernel-userspace-boundary.md:
#
#   1. Kernel Python (src/forest_soul_forge/) imports only stdlib,
#      declared third-party deps, or other forest_soul_forge.* modules.
#      It does NOT import from apps/, frontend/, dist/, examples/.
#
#   2. Kernel Python doesn't have actual code references to userspace
#      paths (apps/desktop, frontend/, dist/) — comments are fine.
#
#   3. Userspace (apps/desktop/ Rust + frontend/ JS) doesn't have
#      code references to kernel src/ paths — comments are fine.
#
# Known carve-outs (allowed by design, documented):
#   - examples/audit_chain.jsonl is the live audit chain DEFAULT
#     PATH per daemon/config.py. Override via FSF_AUDIT_CHAIN_PATH.
#   - Markdown docs (.md) routinely cross-reference both sides for
#     orientation; they're documentation, not coupling.
#   - Python comments (# ...) and docstrings often describe
#     userspace shape for future maintainers. Documentation, not
#     coupling.
#
# Implementation note:
#   Check 1 uses Python's ast.parse to detect REAL import statements
#   (not docstring prose that happens to start with 'from '). Checks
#   2 and 3 filter comment lines + skip markdown by default.
#
# Exit code:
#   0 — clean
#   1 — violations found

set -uo pipefail

cd "$(dirname "$0")/.."

violations=0

# -----------------------------------------------------------------------
# Check 1: kernel Python imports — AST-based.
# -----------------------------------------------------------------------

check_kernel_imports() {
    local found
    found=$(
        python3 - <<'PYEOF'
import ast
import pathlib
import sys

ALLOWED_STDLIB = {
    "abc", "argparse", "ast", "asyncio", "atexit", "base64", "bisect",
    "collections", "concurrent", "contextlib", "copy", "csv", "dataclasses",
    "datetime", "decimal", "email", "enum", "errno", "fnmatch",
    "fractions", "functools", "gc", "glob", "hashlib", "heapq", "hmac",
    "html", "http", "imaplib", "importlib", "inspect", "io", "ipaddress",
    "itertools", "json", "locale", "logging", "math", "multiprocessing",
    "numbers", "operator", "os", "pathlib", "pickle", "platform",
    "pprint", "queue", "random", "re", "secrets", "select", "selectors",
    "shlex", "shutil", "signal", "smtplib", "socket", "sqlite3", "ssl",
    "stat", "statistics", "string", "struct", "subprocess", "sys",
    "tarfile", "tempfile", "textwrap", "threading", "time", "tomllib",
    "traceback", "types", "typing", "unicodedata", "unittest", "urllib",
    "uuid", "warnings", "weakref", "xml", "zipfile", "zoneinfo",
    "_thread",
}

ALLOWED_THIRDPARTY = {
    # Core daemon + schema deps.
    "yaml", "pydantic", "pydantic_settings", "fastapi", "starlette",
    "httpx", "uvicorn", "requests", "click", "rich", "jinja2",
    "pytest", "aiohttp", "aiosqlite", "cryptography", "httpcore",
    "anyio", "ollama", "openai", "anthropic",
    # Tool-specific deps (only the tool that needs them imports them):
    "playwright",          # browser_action.v1 (browser automation)
    "tree_sitter",         # tree_sitter_query.v1 (AST queries)
    "tree_sitter_languages",
}

violations = []
src_root = pathlib.Path("src/forest_soul_forge")
for py in src_root.rglob("*.py"):
    try:
        tree = ast.parse(py.read_text(encoding="utf-8"))
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            # node.module is None for relative imports (from . import x)
            if node.level > 0 or node.module is None:
                continue
            modules = [node.module]
        else:
            continue
        for m in modules:
            top = m.split(".")[0]
            if top == "forest_soul_forge":
                continue
            if top == "__future__":
                continue
            if top in ALLOWED_STDLIB:
                continue
            if top in ALLOWED_THIRDPARTY:
                continue
            violations.append(f"{py}:{node.lineno}: imports {m!r}")

if violations:
    print("\n".join(violations))
    sys.exit(1)
sys.exit(0)
PYEOF
    )
    local rc=$?
    if [[ $rc -ne 0 ]]; then
        echo "❌ Check 1 FAIL — kernel Python imports outside the allowlist:"
        echo "$found"
        echo ""
        violations=$((violations + 1))
    else
        echo "✓ Check 1 — kernel Python imports stay within stdlib + third-party + forest_soul_forge.*"
    fi
}

# -----------------------------------------------------------------------
# Check 2: kernel Python actual-code references to userspace paths.
# -----------------------------------------------------------------------
# Strip Python comments (# ...) before grepping. Use ast to filter
# string literals that are inside docstrings (those would otherwise
# look like code references). The pragmatic heuristic: comments are
# fine, real string literals are flagged.

check_kernel_userspace_refs() {
    local found
    found=$(
        python3 - <<'PYEOF'
import ast
import pathlib
import sys

USERSPACE_PATTERNS = ("apps/desktop", "frontend/", "dist/")
src_root = pathlib.Path("src/forest_soul_forge")
violations = []
for py in src_root.rglob("*.py"):
    try:
        tree = ast.parse(py.read_text(encoding="utf-8"))
    except SyntaxError:
        continue
    # Walk every string literal — but skip those that are docstrings
    # (the first stmt of a module / class / function body, when it's
    # a bare expression of a string).
    docstring_nodes = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                docstring_nodes.add(id(first.value))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in docstring_nodes:
            continue
        for pat in USERSPACE_PATTERNS:
            if pat in node.value:
                violations.append(f"{py}:{node.lineno}: literal contains {pat!r}")
                break

if violations:
    print("\n".join(violations))
    sys.exit(1)
sys.exit(0)
PYEOF
    )
    local rc=$?
    if [[ $rc -ne 0 ]]; then
        echo "❌ Check 2 FAIL — kernel Python has code references to userspace paths:"
        echo "$found"
        echo ""
        violations=$((violations + 1))
    else
        echo "✓ Check 2 — kernel Python has no code references to apps/, frontend/, or dist/"
    fi

    # Examples/ check, with the audit_chain.jsonl carve-out.
    local examples_refs
    examples_refs=$(
        python3 - <<'PYEOF'
import ast
import pathlib
import sys

src_root = pathlib.Path("src/forest_soul_forge")
violations = []
for py in src_root.rglob("*.py"):
    try:
        tree = ast.parse(py.read_text(encoding="utf-8"))
    except SyntaxError:
        continue
    docstring_nodes = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                docstring_nodes.add(id(first.value))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in docstring_nodes:
            continue
        if "examples/" not in node.value:
            continue
        # Carve-out: examples/audit_chain.jsonl is the live default.
        if "examples/audit_chain.jsonl" in node.value:
            continue
        violations.append(f"{py}:{node.lineno}: literal contains examples/ ({node.value!r})")

if violations:
    print("\n".join(violations))
    sys.exit(1)
sys.exit(0)
PYEOF
    )
    local rc=$?
    if [[ $rc -ne 0 ]]; then
        echo "❌ Check 2b FAIL — kernel Python references examples/ outside the audit_chain carve-out:"
        echo "$examples_refs"
        echo ""
        violations=$((violations + 1))
    else
        echo "✓ Check 2b — kernel Python references examples/ only via the audit_chain carve-out"
    fi
}

# -----------------------------------------------------------------------
# Check 3: userspace code references to kernel src/ paths.
# -----------------------------------------------------------------------
# Strip JS line comments (// ...) and Rust line comments (// ...).
# Markdown files (.md) are pure documentation — skip them entirely.

check_userspace_kernel_refs() {
    local found
    found=$(
        # Rust: strip // comments before greping. JS: same.
        # awk '!/^[[:space:]]*\/\//' filters comment-only lines.
        for f in $(find apps frontend -type f \
            \( -name "*.rs" -o -name "*.js" -o -name "*.ts" \
               -o -name "*.html" -o -name "*.css" -o -name "*.json" \
               -o -name "*.toml" \) 2>/dev/null); do
            # Strip line comments. Block comments not handled — fine
            # for our purposes since src/forest_soul_forge references
            # in block comments are still documentation.
            stripped=$(sed 's://.*$::g' "$f")
            if echo "$stripped" | grep -q "src/forest_soul_forge"; then
                # Re-grep with line numbers, skipping comment-only lines.
                grep -n "src/forest_soul_forge" "$f" \
                    | grep -vE '^\s*[0-9]+:\s*(//|#|\*|<!--)'
                # Output won't have file path — prefix it.
                # Actually grep -n gives "line:content" without filename
                # for a single file; we need the filename:
                grep -nH "src/forest_soul_forge" "$f" \
                    | awk -F: '{
                        line=$0
                        # Remove comment-only lines.
                        sub(/^[^:]+:[0-9]+:[[:space:]]*/, "", $0)
                        if ($0 !~ /^(\/\/|#|\*|<!--)/) print line
                    }'
            fi
        done | sort -u || true
    )
    if [[ -n "$found" ]]; then
        echo "❌ Check 3 FAIL — userspace has code references to kernel src/ paths:"
        echo "$found"
        echo ""
        violations=$((violations + 1))
    else
        echo "✓ Check 3 — userspace (apps/, frontend/) has no code references to kernel src/ paths"
    fi
}

# -----------------------------------------------------------------------
# Run all checks.
# -----------------------------------------------------------------------

echo "=== Kernel/userspace boundary sentinel (ADR-0044 Phase 1.3) ==="
echo ""
check_kernel_imports
check_kernel_userspace_refs
check_userspace_kernel_refs
echo ""

if [[ $violations -eq 0 ]]; then
    echo "All checks pass. Boundary contract preserved."
    exit 0
else
    echo "$violations violation group(s) found."
    echo ""
    echo "Fix the violations OR document a new carve-out in"
    echo "docs/architecture/kernel-userspace-boundary.md and update"
    echo "this script's allowlist."
    exit 1
fi
