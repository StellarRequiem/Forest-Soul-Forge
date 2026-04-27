"""Unit tests for the Tool Forge sandbox runner — ADR-0030 T3b.

The sandbox actually invokes pytest in a subprocess against staged
files. These tests skip cleanly when pytest isn't on PATH (the
function reports it as ran=False, summary="not available", etc.).

We do not test the in-test side effects of the generated tool —
that would couple this layer to specific tool implementations.
We test the outcome categories: passed, failed, missing test file,
timeout, pytest unavailable.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from forest_soul_forge.forge.sandbox import (
    TestRunResult,
    prepare_test_environment,
    run_staged_tests,
)


_PASSING_TEST = textwrap.dedent('''
    def test_obvious_truth():
        assert 1 + 1 == 2
''').lstrip()


_FAILING_TEST = textwrap.dedent('''
    def test_will_fail():
        assert 1 == 2
''').lstrip()


def _stage(tmp_path, test_body, *, name="test_thing.py"):
    staged = tmp_path / "stage"
    staged.mkdir()
    test_path = staged / name
    test_path.write_text(test_body, encoding="utf-8")
    prepare_test_environment(staged)
    return staged, test_path


class TestRunStagedTests:
    def test_no_test_file_returns_did_not_run(self, tmp_path):
        result = run_staged_tests(staged_dir=tmp_path, test_path=None)
        assert result.ran is False
        assert result.passed is None
        assert "no test file" in result.summary

    def test_missing_test_file_returns_did_not_run(self, tmp_path):
        result = run_staged_tests(
            staged_dir=tmp_path, test_path=tmp_path / "missing.py",
        )
        assert result.ran is False

    def test_passing_test_reports_passed(self, tmp_path):
        pytest.importorskip("pytest")
        staged, test_path = _stage(tmp_path, _PASSING_TEST)
        result = run_staged_tests(staged_dir=staged, test_path=test_path)
        if not result.ran:
            pytest.skip(f"pytest not runnable in this env: {result.summary}")
        assert result.passed is True
        assert result.exit_code == 0

    def test_failing_test_reports_failed(self, tmp_path):
        pytest.importorskip("pytest")
        staged, test_path = _stage(tmp_path, _FAILING_TEST)
        result = run_staged_tests(staged_dir=staged, test_path=test_path)
        if not result.ran:
            pytest.skip(f"pytest not runnable in this env: {result.summary}")
        assert result.passed is False
        assert result.exit_code != 0

    def test_timeout_reports_failed(self, tmp_path):
        pytest.importorskip("pytest")
        slow = textwrap.dedent('''
            import time
            def test_slow():
                time.sleep(60)
        ''').lstrip()
        staged, test_path = _stage(tmp_path, slow)
        result = run_staged_tests(
            staged_dir=staged, test_path=test_path, timeout_s=1.0,
        )
        # Either the timeout fires (passed=False) or pytest isn't
        # available; we accept both.
        if not result.ran:
            pytest.skip("pytest not runnable in this env")
        assert result.passed is False
        assert "timed out" in result.summary or "1 failed" in result.summary


class TestPrepareTestEnvironment:
    def test_writes_conftest(self, tmp_path):
        prepare_test_environment(tmp_path)
        conftest = tmp_path / "conftest.py"
        assert conftest.exists()
        body = conftest.read_text()
        assert "sys.path.insert" in body

    def test_idempotent(self, tmp_path):
        prepare_test_environment(tmp_path)
        first = (tmp_path / "conftest.py").read_text()
        prepare_test_environment(tmp_path)
        second = (tmp_path / "conftest.py").read_text()
        assert first == second
