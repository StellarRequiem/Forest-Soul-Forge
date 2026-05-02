"""Tests for tree_sitter_query.v1 (Phase G.1.A eighth programming primitive).

Most tests use mocked _load_parser so they don't depend on tree_sitter
being installed. A separate path exercises the real tree_sitter import
when it's available; otherwise it skips."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest import mock

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.tree_sitter_query import (
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_MATCHES,
    DEFAULT_TIMEOUT_SECONDS,
    LANGUAGE_REGISTRY,
    TS_MAX_MATCHES_HARD_CAP,
    TreeSitterGrammarMissingError,
    TreeSitterNotInstalledError,
    TreeSitterQueryError,
    TreeSitterQueryParseError,
    TreeSitterQueryTool,
    _enumerate_files,
    _is_within_any,
    _normalize_captures,
    _resolve_allowlist,
)


def _run(coro):
    return asyncio.run(coro)


# ===========================================================================
# Validation
# ===========================================================================
class TestValidate:
    def test_missing_path_rejected(self):
        with pytest.raises(ToolValidationError, match="path"):
            TreeSitterQueryTool().validate(
                {"query": "(x)", "language": "python"},
            )

    def test_missing_query_rejected(self):
        with pytest.raises(ToolValidationError, match="query"):
            TreeSitterQueryTool().validate(
                {"path": "/tmp", "language": "python"},
            )

    def test_invalid_language_rejected(self):
        with pytest.raises(ToolValidationError, match="language"):
            TreeSitterQueryTool().validate({
                "path": "/tmp", "query": "(x)", "language": "cobol",
            })

    def test_max_matches_bounds(self):
        for bad in (0, TS_MAX_MATCHES_HARD_CAP + 1):
            with pytest.raises(ToolValidationError, match="max_matches"):
                TreeSitterQueryTool().validate({
                    "path": "/tmp", "query": "(x)", "language": "python",
                    "max_matches": bad,
                })

    def test_max_files_bounds(self):
        with pytest.raises(ToolValidationError, match="max_files"):
            TreeSitterQueryTool().validate({
                "path": "/tmp", "query": "(x)", "language": "python",
                "max_files": 0,
            })

    def test_timeout_bounds(self):
        with pytest.raises(ToolValidationError, match="timeout_seconds"):
            TreeSitterQueryTool().validate({
                "path": "/tmp", "query": "(x)", "language": "python",
                "timeout_seconds": 0,
            })

    def test_valid_minimal(self):
        TreeSitterQueryTool().validate({
            "path": "/tmp", "query": "(x)", "language": "python",
        })

    def test_valid_full(self):
        TreeSitterQueryTool().validate({
            "path": "/tmp/repo",
            "query": "(function_definition name: (identifier) @name)",
            "language": "python",
            "max_matches": 50,
            "max_files": 100,
            "timeout_seconds": 60,
        })

    def test_language_registry_has_expected_entries(self):
        for lang in (
            "python", "javascript", "typescript", "rust", "go",
            "java", "c", "cpp", "ruby", "yaml", "json", "bash",
        ):
            assert lang in LANGUAGE_REGISTRY


# ===========================================================================
# _enumerate_files
# ===========================================================================
class TestEnumerateFiles:
    def test_single_file_matches_extension(self, tmp_path):
        f = tmp_path / "x.py"
        f.write_text("hi")
        out = _enumerate_files(f, (".py",), 100)
        assert out == [f]

    def test_single_file_wrong_extension_excluded(self, tmp_path):
        f = tmp_path / "x.txt"
        f.write_text("hi")
        assert _enumerate_files(f, (".py",), 100) == []

    def test_directory_walk(self, tmp_path):
        (tmp_path / "a.py").write_text("a")
        (tmp_path / "b.py").write_text("b")
        (tmp_path / "c.txt").write_text("c")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "d.py").write_text("d")
        out = _enumerate_files(tmp_path, (".py",), 100)
        assert {p.name for p in out} == {"a.py", "b.py", "d.py"}

    def test_max_files_cap(self, tmp_path):
        for i in range(10):
            (tmp_path / f"f{i}.py").write_text("x")
        out = _enumerate_files(tmp_path, (".py",), 3)
        assert len(out) == 3

    def test_skips_hidden_dirs(self, tmp_path):
        # File at top level — included
        (tmp_path / "main.py").write_text("hi")
        # File inside hidden dir — excluded
        hidden = tmp_path / ".venv"
        hidden.mkdir()
        (hidden / "lib.py").write_text("hi")
        out = _enumerate_files(tmp_path, (".py",), 100)
        assert {p.name for p in out} == {"main.py"}


# ===========================================================================
# _normalize_captures
# ===========================================================================
class TestNormalizeCaptures:
    def _fake_node(self, sb, eb, sp, ep):
        n = mock.MagicMock()
        n.start_byte = sb
        n.end_byte = eb
        n.start_point = sp
        n.end_point = ep
        return n

    def test_list_form(self):
        source = b"abcdef"
        n = self._fake_node(0, 3, (0, 0), (0, 3))
        out = _normalize_captures([(n, "name")], source)
        assert out == [{
            "name": "name",
            "text": "abc",
            "start_line": 1,
            "end_line": 1,
            "start_col": 0,
            "end_col": 3,
        }]

    def test_dict_form(self):
        source = b"hello"
        n = self._fake_node(0, 5, (2, 1), (2, 6))
        out = _normalize_captures({"greet": [n]}, source)
        assert out[0]["name"] == "greet"
        assert out[0]["start_line"] == 3   # 0-indexed → 1-indexed

    def test_empty(self):
        assert _normalize_captures([], b"") == []
        assert _normalize_captures({}, b"") == []


# ===========================================================================
# Path allowlist
# ===========================================================================
class TestPathAllowlist:
    def test_within(self, tmp_path):
        roots = _resolve_allowlist([str(tmp_path)])
        assert _is_within_any(tmp_path.resolve(), roots) is True

    def test_outside(self, tmp_path):
        roots = _resolve_allowlist([str(tmp_path)])
        outside = (tmp_path.parent / "elsewhere").resolve()
        assert _is_within_any(outside, roots) is False


# ===========================================================================
# execute() — using mocked _load_parser so we don't depend on tree_sitter
# ===========================================================================
class TestExecute:
    def _ctx(self, tmp_path):
        return ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )

    def _mock_parser_with_match(self, source, capture_name="match", positions=None):
        """Return (parser, language) mocks where query.captures returns
        a single capture covering a known byte range."""
        if positions is None:
            positions = (0, len(source), (0, 0), (0, len(source)))
        sb, eb, sp, ep = positions
        node = mock.MagicMock()
        node.start_byte = sb
        node.end_byte = eb
        node.start_point = sp
        node.end_point = ep

        tree = mock.MagicMock()
        tree.root_node = mock.MagicMock()

        parser = mock.MagicMock()
        parser.parse = mock.MagicMock(return_value=tree)

        compiled_query = mock.MagicMock()
        compiled_query.captures = mock.MagicMock(return_value=[(node, capture_name)])

        language = mock.MagicMock()
        language.query = mock.MagicMock(return_value=compiled_query)
        return parser, language

    def test_single_file_match(self, tmp_path):
        f = tmp_path / "a.py"
        source = b"def foo(): pass\n"
        f.write_bytes(source)
        ctx = self._ctx(tmp_path)
        parser, language = self._mock_parser_with_match(
            source, capture_name="fn",
            positions=(4, 7, (0, 4), (0, 7)),
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.tree_sitter_query._load_parser",
            return_value=(parser, language),
        ):
            result = _run(TreeSitterQueryTool().execute({
                "path": str(f),
                "query": "(function_definition name: (identifier) @fn)",
                "language": "python",
            }, ctx))
        assert result.output["matches_count"] == 1
        m = result.output["matches"][0]
        assert m["filename"] == str(f)
        cap = m["captures"][0]
        assert cap["name"] == "fn"
        assert cap["text"] == "foo"
        assert cap["start_line"] == 1

    def test_directory_walk(self, tmp_path):
        for i in range(3):
            (tmp_path / f"f{i}.py").write_bytes(b"x = 1\n")
        ctx = self._ctx(tmp_path)
        parser, language = self._mock_parser_with_match(b"x = 1\n")
        with mock.patch(
            "forest_soul_forge.tools.builtin.tree_sitter_query._load_parser",
            return_value=(parser, language),
        ):
            result = _run(TreeSitterQueryTool().execute({
                "path": str(tmp_path),
                "query": "(any)",
                "language": "python",
            }, ctx))
        assert result.output["files_scanned"] == 3
        assert result.output["matches_count"] == 3

    def test_max_matches_truncation(self, tmp_path):
        for i in range(5):
            (tmp_path / f"f{i}.py").write_bytes(b"x")
        ctx = self._ctx(tmp_path)
        parser, language = self._mock_parser_with_match(b"x")
        with mock.patch(
            "forest_soul_forge.tools.builtin.tree_sitter_query._load_parser",
            return_value=(parser, language),
        ):
            result = _run(TreeSitterQueryTool().execute({
                "path": str(tmp_path),
                "query": "(any)",
                "language": "python",
                "max_matches": 2,
            }, ctx))
        assert result.output["matches_count"] == 2
        assert result.output["truncated"] is True

    def test_invalid_query_refuses(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_bytes(b"x")
        ctx = self._ctx(tmp_path)
        language = mock.MagicMock()
        language.query = mock.MagicMock(side_effect=Exception("bad sexp"))
        parser = mock.MagicMock()
        with mock.patch(
            "forest_soul_forge.tools.builtin.tree_sitter_query._load_parser",
            return_value=(parser, language),
        ):
            with pytest.raises(TreeSitterQueryParseError):
                _run(TreeSitterQueryTool().execute({
                    "path": str(f),
                    "query": "(((",
                    "language": "python",
                }, ctx))

    def test_tree_sitter_not_installed_refuses(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_bytes(b"x")
        ctx = self._ctx(tmp_path)
        with mock.patch(
            "forest_soul_forge.tools.builtin.tree_sitter_query._load_parser",
            side_effect=TreeSitterNotInstalledError("not installed"),
        ):
            with pytest.raises(TreeSitterNotInstalledError):
                _run(TreeSitterQueryTool().execute({
                    "path": str(f),
                    "query": "(x)",
                    "language": "python",
                }, ctx))

    def test_grammar_missing_refuses(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_bytes(b"x")
        ctx = self._ctx(tmp_path)
        with mock.patch(
            "forest_soul_forge.tools.builtin.tree_sitter_query._load_parser",
            side_effect=TreeSitterGrammarMissingError("no grammar"),
        ):
            with pytest.raises(TreeSitterGrammarMissingError):
                _run(TreeSitterQueryTool().execute({
                    "path": str(f),
                    "query": "(x)",
                    "language": "python",
                }, ctx))

    def test_missing_allowed_paths_refuses(self, tmp_path):
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s", constraints={},
        )
        with pytest.raises(TreeSitterQueryError, match="allowed_paths"):
            _run(TreeSitterQueryTool().execute({
                "path": str(tmp_path), "query": "(x)", "language": "python",
            }, ctx))

    def test_outside_allowed_blocked(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(allowed)]},
        )
        with pytest.raises(TreeSitterQueryError, match="outside"):
            _run(TreeSitterQueryTool().execute({
                "path": str(outside), "query": "(x)", "language": "python",
            }, ctx))

    def test_nonexistent_path_refuses(self, tmp_path):
        ctx = self._ctx(tmp_path)
        with pytest.raises(TreeSitterQueryError, match="does not exist"):
            _run(TreeSitterQueryTool().execute({
                "path": str(tmp_path / "nope"),
                "query": "(x)", "language": "python",
            }, ctx))

    def test_metadata_records_invocation(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_bytes(b"x")
        ctx = self._ctx(tmp_path)
        parser, language = self._mock_parser_with_match(b"x")
        with mock.patch(
            "forest_soul_forge.tools.builtin.tree_sitter_query._load_parser",
            return_value=(parser, language),
        ):
            result = _run(TreeSitterQueryTool().execute({
                "path": str(f), "query": "(x)", "language": "python",
            }, ctx))
        assert result.metadata["grammar_name"] == "python"
        assert result.metadata["max_matches"] == DEFAULT_MAX_MATCHES
        assert ".py" in result.metadata["extensions"]


# ===========================================================================
# Registration
# ===========================================================================
class TestRegistration:
    def test_tool_registered(self):
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.builtin import register_builtins

        registry = ToolRegistry()
        register_builtins(registry)
        tool = registry.get("tree_sitter_query", "1")
        assert tool is not None
        assert tool.side_effects == "read_only"

    def test_catalog_entry_present(self):
        import yaml
        catalog_path = (
            Path(__file__).parent.parent.parent
            / "config" / "tool_catalog.yaml"
        )
        with open(catalog_path) as f:
            catalog = yaml.safe_load(f)
        entry = catalog["tools"]["tree_sitter_query.v1"]
        assert entry["side_effects"] == "read_only"
        assert "required_initiative_level" not in entry
