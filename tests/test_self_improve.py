"""Unit tests for scripts/self_improve.py.

The self-improvement harness is split into pure functions
(audit/analyze/report) and a thin I/O orchestrator. These tests
exercise the pure layer with synthetic fixtures — no daemon,
no network, no real filesystem mutation outside tmp_path.

Coverage targets the four sensitive surfaces called out in the
spec:
  1. Audit-phase output parsing       (parse_pytest_output)
  2. Analyzer severity classification (classify_severity / complexity)
  3. Fixer drift detection            (check_config_drift)
  4. Validator regression detection   (compute_regression)

Plus the supporting helpers: time stamps, finding/outcome data
shapes, report rendering, skill-manifest checks, syntax-error
detection, and grouping.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import self_improve as si  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers: build synthetic YAML configs in tmp_path
# ---------------------------------------------------------------------------

def _write_yaml(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _make_minimal_repo(
    root: Path,
    *,
    trait_roles=("network_watcher",),
    genre_roles=("network_watcher",),
    constitution_roles=("network_watcher",),
    catalog_tools=("audit_chain_verify.v1",),
    extra_trait: str = "",
) -> None:
    """Create a synthetic repo skeleton with the four config files
    populated to match the given role/tool sets. Used by drift
    tests.
    """
    tt = "version: 0.2\nschema_version: 2\nroles:\n"
    for r in trait_roles:
        tt += f"  {r}:\n    description: 'stub'\n    domain_weights:\n      communication: 1.0\n      cognitive: 1.0\n      security: 1.0\n      audit: 1.0\n      emotional: 1.0\n      embodiment: 0.5\n"
    if extra_trait:
        tt += extra_trait
    _write_yaml(root / "config" / "trait_tree.yaml", tt)

    gn = "version: '0.1'\ngenres:\n  specialist:\n    description: stub\n    roles:\n"
    for r in genre_roles:
        gn += f"      - {r}\n"
    _write_yaml(root / "config" / "genres.yaml", gn)

    ct = "schema_version: 1\nrole_base:\n"
    for r in constitution_roles:
        ct += f"  {r}:\n    policies: []\n"
    _write_yaml(root / "config" / "constitution_templates.yaml", ct)

    tc = "version: '0.1'\ntools:\n"
    for t in catalog_tools:
        tc += f"  {t}:\n    name: {t.split('.v')[0]}\n    version: '{t.split('.v')[1]}'\n"
    _write_yaml(root / "config" / "tool_catalog.yaml", tc)


# ---------------------------------------------------------------------------
# 1. parse_pytest_output  (5 tests)
# ---------------------------------------------------------------------------

def test_parse_pytest_passed_only():
    """Summary with only passed tests should give count and zero failures."""
    out = si.parse_pytest_output("==== 42 passed in 1.23s ====")
    assert out["passed"] == 42
    assert out["failed"] == 0
    assert out["errors"] == 0
    assert out["failed_tests"] == []


def test_parse_pytest_with_failures():
    text = (
        "FAILED tests/unit/test_foo.py::test_bar - AssertionError: oh no\n"
        "FAILED tests/unit/test_baz.py::test_qux - TypeError: nope\n"
        "==== 100 passed, 2 failed in 5.55s ===="
    )
    out = si.parse_pytest_output(text)
    assert out["passed"] == 100
    assert out["failed"] == 2
    ids = [t["id"] for t in out["failed_tests"]]
    assert "tests/unit/test_foo.py::test_bar" in ids
    assert "tests/unit/test_baz.py::test_qux" in ids


def test_parse_pytest_with_errors_and_skipped():
    text = "==== 10 passed, 1 failed, 2 errors, 3 skipped in 0.10s ===="
    out = si.parse_pytest_output(text)
    assert out["passed"] == 10
    assert out["failed"] == 1
    assert out["errors"] == 2
    assert out["skipped"] == 3


def test_parse_pytest_picks_last_summary_when_multiple():
    """Pytest sometimes prints a short summary AND a final
    summary; the final one is authoritative.

    Pytest's summary line always orders counts as passed,failed,
    errors,skipped — that's the canonical order, so our regex
    relies on it. This test checks that when two summary-shaped
    lines appear in the output, the harness takes the last one."""
    text = (
        "==== 1 passed in 0.01s ====\n"  # spurious earlier line
        "==== 10 passed, 5 failed in 1.0s ===="
    )
    out = si.parse_pytest_output(text)
    assert out["passed"] == 10
    assert out["failed"] == 5


def test_parse_pytest_handles_xfail_and_warnings():
    text = "==== 5 passed, 2 xfailed, 1 xpassed, 3 warnings in 1.0s ===="
    out = si.parse_pytest_output(text)
    assert out["passed"] == 5
    assert out["xfailed"] == 2
    assert out["xpassed"] == 1
    assert out["warnings"] == 3


def test_parse_pytest_with_error_line():
    """ERROR lines (collection errors) are picked up too."""
    text = (
        "ERROR tests/unit/test_broken.py - ImportError: no module\n"
        "==== 1 error in 0.05s ===="
    )
    out = si.parse_pytest_output(text)
    assert out["errors"] == 1
    assert out["failed_tests"][0]["kind"] == "ERROR"
    assert out["failed_tests"][0]["id"] == "tests/unit/test_broken.py"


def test_parse_pytest_empty_input():
    """Empty / malformed input shouldn't raise."""
    out = si.parse_pytest_output("")
    assert out["passed"] == 0
    assert out["failed_tests"] == []


def test_parse_pytest_real_shape_failed_first():
    """Pin the exact shape pytest emits when failures are present
    (`failed` comes before `passed`). Regression guard — we had a
    parser version that only matched passed-first order."""
    text = (
        "==== 76 failed, 5345 passed, 19 skipped, 1 xfailed, "
        "34 warnings in 182.86s ===="
    )
    out = si.parse_pytest_output(text)
    assert out["failed"] == 76
    assert out["passed"] == 5345
    assert out["skipped"] == 19
    assert out["xfailed"] == 1
    assert out["warnings"] == 34


def test_parse_pytest_bare_summary_no_borders():
    """The harness runs pytest with -q --no-header, which strips
    the ==== borders from the final summary line. Regression
    guard — initial parser required the borders and silently
    returned zeros on real harness output."""
    text = "FAILED tests/unit/test_x.py::test_y - assert\n1 failed, 66 passed in 0.45s\n"
    out = si.parse_pytest_output(text)
    assert out["passed"] == 66
    assert out["failed"] == 1


def test_parse_pytest_with_wallclock_annotation():
    """Pytest appends ` (H:MM:SS)` to the duration when a run
    exceeds ~1 minute. Regression guard for the v3 dry-run on the
    full suite: parser silently returned zeros because the
    annotation broke the end-of-line anchor."""
    text = (
        "FAILED some/test.py::x - err\n"
        "76 failed, 5347 passed, 19 skipped, 1 xfailed, "
        "34 warnings in 179.51s (0:02:59)\n"
    )
    out = si.parse_pytest_output(text)
    assert out["failed"] == 76
    assert out["passed"] == 5347
    assert out["skipped"] == 19
    assert out["xfailed"] == 1
    assert out["warnings"] == 34


# ---------------------------------------------------------------------------
# 2. classify_severity / classify_complexity (5 tests)
# ---------------------------------------------------------------------------

def test_classify_complexity_known_kinds():
    cases = {
        "missing_role_in_genres": si.COMPLEXITY_SIMPLE,
        "missing_role_in_constitution": si.COMPLEXITY_SIMPLE,
        "trait_floor_violation": si.COMPLEXITY_TRIVIAL,
        "version_string_prefixed": si.COMPLEXITY_TRIVIAL,
        "test_failure": si.COMPLEXITY_COMPLEX,
        "syntax_error": si.COMPLEXITY_COMPLEX,
        "lint": si.COMPLEXITY_TRIVIAL,
    }
    for kind, expected in cases.items():
        f = si.Finding(kind=kind, severity=si.SEVERITY_MEDIUM, summary="x")
        assert si.classify_complexity(f) == expected, kind


def test_classify_complexity_unknown_kind_is_complex():
    """Unknown kinds must default to COMPLEX so the harness
    fail-safes by flagging."""
    f = si.Finding(kind="never_seen_kind", severity=si.SEVERITY_LOW, summary="x")
    assert si.classify_complexity(f) == si.COMPLEXITY_COMPLEX


def test_auto_fixable_complexity_membership():
    """Only TRIVIAL and SIMPLE may be auto-fixed."""
    assert si.COMPLEXITY_TRIVIAL in si.AUTO_FIXABLE_COMPLEXITY
    assert si.COMPLEXITY_SIMPLE in si.AUTO_FIXABLE_COMPLEXITY
    assert si.COMPLEXITY_MODERATE not in si.AUTO_FIXABLE_COMPLEXITY
    assert si.COMPLEXITY_COMPLEX not in si.AUTO_FIXABLE_COMPLEXITY


def test_classify_severity_is_passthrough():
    """Phase 1 sets severity; classify_severity respects it."""
    f = si.Finding(kind="test_failure", severity=si.SEVERITY_HIGH, summary="x")
    assert si.classify_severity(f) == si.SEVERITY_HIGH


def test_severity_ladder_order():
    """Ladder must be CRITICAL < HIGH < MEDIUM < LOW (numeric)."""
    o = si.SEVERITY_ORDER
    assert o[si.SEVERITY_CRITICAL] < o[si.SEVERITY_HIGH]
    assert o[si.SEVERITY_HIGH] < o[si.SEVERITY_MEDIUM]
    assert o[si.SEVERITY_MEDIUM] < o[si.SEVERITY_LOW]


# ---------------------------------------------------------------------------
# 3. check_config_drift (8 tests)
# ---------------------------------------------------------------------------

def test_drift_no_drift_clean_configs(tmp_path):
    _make_minimal_repo(tmp_path)
    findings = si.check_config_drift(tmp_path)
    assert findings == []


def test_drift_missing_role_in_genres(tmp_path):
    """Role in trait_tree but not claimed by any genre."""
    _make_minimal_repo(
        tmp_path,
        trait_roles=("network_watcher", "ghost_role"),
        genre_roles=("network_watcher",),
        constitution_roles=("network_watcher", "ghost_role"),
    )
    findings = si.check_config_drift(tmp_path)
    kinds = {f.kind for f in findings}
    assert "missing_role_in_genres" in kinds
    targets = [
        f for f in findings
        if f.kind == "missing_role_in_genres"
    ]
    assert any(f.details.get("role") == "ghost_role" for f in targets)


def test_drift_missing_role_in_constitution(tmp_path):
    """Role in trait_tree but no constitution template."""
    _make_minimal_repo(
        tmp_path,
        trait_roles=("network_watcher", "new_role"),
        genre_roles=("network_watcher", "new_role"),
        constitution_roles=("network_watcher",),
    )
    findings = si.check_config_drift(tmp_path)
    kinds = {f.kind for f in findings}
    assert "missing_role_in_constitution" in kinds


def test_drift_yaml_parse_error_is_critical(tmp_path):
    """Malformed YAML in trait_tree should surface a CRITICAL finding."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "trait_tree.yaml").write_text(
        "this: is: not: valid: yaml: [unclosed\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "genres.yaml").write_text("genres: {}\n")
    (tmp_path / "config" / "constitution_templates.yaml").write_text("role_base: {}\n")
    findings = si.check_config_drift(tmp_path)
    assert any(f.kind == "yaml_parse_error" for f in findings)
    assert any(f.severity == si.SEVERITY_CRITICAL for f in findings)


def test_drift_trait_floor_violation_detected(tmp_path):
    """embodiment < 0.4 must surface as trait_floor_violation."""
    extra = (
        "  bad_role:\n"
        "    description: stub\n"
        "    domain_weights:\n"
        "      communication: 1.0\n"
        "      cognitive: 1.0\n"
        "      security: 1.0\n"
        "      audit: 1.0\n"
        "      emotional: 1.0\n"
        "      embodiment: 0.3\n"
    )
    _make_minimal_repo(
        tmp_path,
        trait_roles=("network_watcher",),
        genre_roles=("network_watcher", "bad_role"),
        constitution_roles=("network_watcher", "bad_role"),
        extra_trait=extra,
    )
    findings = si.check_config_drift(tmp_path)
    floor = [f for f in findings if f.kind == "trait_floor_violation"]
    assert len(floor) == 1
    assert floor[0].details["role"] == "bad_role"
    assert floor[0].details["domain"] == "embodiment"
    assert floor[0].details["floor"] == 0.4


def test_drift_non_positive_trait_value(tmp_path):
    extra = (
        "  zero_role:\n"
        "    description: stub\n"
        "    domain_weights:\n"
        "      communication: 0\n"
        "      cognitive: 1.0\n"
        "      security: 1.0\n"
        "      audit: 1.0\n"
        "      emotional: 1.0\n"
        "      embodiment: 0.5\n"
    )
    _make_minimal_repo(
        tmp_path,
        trait_roles=("network_watcher",),
        genre_roles=("network_watcher", "zero_role"),
        constitution_roles=("network_watcher", "zero_role"),
        extra_trait=extra,
    )
    findings = si.check_config_drift(tmp_path)
    invalid = [f for f in findings if f.kind == "trait_value_invalid"]
    assert any(f.details["role"] == "zero_role" for f in invalid)


def test_drift_aspirational_role_in_genres_is_allowed(tmp_path):
    """Role appearing in genres but not in trait_tree is allowed
    (the genres loader explicitly permits aspirational roles)."""
    _make_minimal_repo(
        tmp_path,
        trait_roles=("network_watcher",),
        genre_roles=("network_watcher", "aspirational_role"),
        constitution_roles=("network_watcher",),
    )
    findings = si.check_config_drift(tmp_path)
    # Should not raise any kind about aspirational_role.
    msgs = " ".join(f.summary for f in findings)
    assert "aspirational_role" not in msgs


def test_drift_findings_have_required_fields(tmp_path):
    """Every Finding must carry kind, severity, summary, source."""
    _make_minimal_repo(
        tmp_path,
        trait_roles=("a", "b"),
        genre_roles=("a",),
        constitution_roles=("a",),
    )
    findings = si.check_config_drift(tmp_path)
    assert findings
    for f in findings:
        assert f.kind
        assert f.severity
        assert f.summary
        assert f.source


# ---------------------------------------------------------------------------
# 4. compute_regression (validator) — 5 tests
# ---------------------------------------------------------------------------

def test_regression_none_when_identical():
    s = {"passed": 100, "failed": 2, "errors": 0, "skipped": 5,
         "failed_tests": [{"id": "t1"}, {"id": "t2"}]}
    diff = si.compute_regression(s, s)
    assert diff["broken_tests"] == []
    assert diff["fixed_tests"] == []
    assert diff["still_failing"] == ["t1", "t2"]


def test_regression_detected():
    before = {"passed": 100, "failed": 0, "errors": 0, "skipped": 0,
              "failed_tests": []}
    after = {"passed": 99, "failed": 1, "errors": 0, "skipped": 0,
             "failed_tests": [{"id": "tests/unit/test_x.py::test_broken"}]}
    diff = si.compute_regression(before, after)
    assert diff["broken_tests"] == ["tests/unit/test_x.py::test_broken"]
    assert diff["delta"]["failed_delta"] == 1
    assert diff["delta"]["passed_delta"] == -1


def test_regression_fixes_tracked():
    before = {"passed": 99, "failed": 1, "errors": 0, "skipped": 0,
              "failed_tests": [{"id": "test_old.py::test_y"}]}
    after = {"passed": 100, "failed": 0, "errors": 0, "skipped": 0,
             "failed_tests": []}
    diff = si.compute_regression(before, after)
    assert diff["fixed_tests"] == ["test_old.py::test_y"]
    assert diff["broken_tests"] == []
    assert diff["delta"]["passed_delta"] == 1


def test_regression_mixed_fixes_and_breaks():
    before = {"passed": 50, "failed": 2,
              "failed_tests": [{"id": "a"}, {"id": "b"}]}
    after = {"passed": 50, "failed": 2,
             "failed_tests": [{"id": "a"}, {"id": "c"}]}
    diff = si.compute_regression(before, after)
    assert diff["fixed_tests"] == ["b"]
    assert diff["broken_tests"] == ["c"]
    assert diff["still_failing"] == ["a"]


def test_regression_empty_inputs():
    diff = si.compute_regression({}, {})
    assert diff["broken_tests"] == []
    assert diff["fixed_tests"] == []
    assert diff["delta"]["failed_delta"] == 0


# ---------------------------------------------------------------------------
# Phase 2 grouping — 3 tests
# ---------------------------------------------------------------------------

def test_group_findings_small_set_unchanged():
    findings = [
        si.Finding("test_failure", si.SEVERITY_HIGH, "x", details={"id": "m1::t1"}),
        si.Finding("test_failure", si.SEVERITY_HIGH, "y", details={"id": "m2::t2"}),
    ]
    grouped, notes = si.group_findings(findings)
    assert len(grouped) == 2  # small enough, unchanged
    assert notes == []


def test_group_findings_large_set_grouped_by_module():
    findings = [
        si.Finding("test_failure", si.SEVERITY_HIGH, f"f{i}",
                   details={"id": "tests/unit/test_x.py::test_{}".format(i)})
        for i in range(10)
    ]
    grouped, notes = si.group_findings(findings)
    # 10 originals -> 1 grouped finding
    assert any(f.kind == "test_failure_group" for f in grouped)
    grp = [f for f in grouped if f.kind == "test_failure_group"][0]
    assert grp.details["count"] == 10
    assert "tests/unit/test_x.py" in grp.details["module"]
    assert notes


def test_group_findings_preserves_non_test_findings():
    findings = [
        si.Finding("missing_role_in_genres", si.SEVERITY_MEDIUM, "role x",
                   details={"role": "x"}),
        *[
            si.Finding("test_failure", si.SEVERITY_HIGH, f"f{i}",
                       details={"id": "m::t{}".format(i)})
            for i in range(5)
        ],
    ]
    grouped, _notes = si.group_findings(findings)
    assert any(f.kind == "missing_role_in_genres" for f in grouped)


# ---------------------------------------------------------------------------
# Phase 2 analyze (partition) — 2 tests
# ---------------------------------------------------------------------------

def test_phase2_partitions_auto_vs_flagged():
    audit = si.AuditResult(findings=[
        si.Finding("missing_role_in_genres", si.SEVERITY_MEDIUM, "x",
                   details={"role": "r1"}),
        si.Finding("test_failure", si.SEVERITY_HIGH, "y",
                   details={"id": "m::t"}),
        si.Finding("trait_floor_violation", si.SEVERITY_HIGH, "z",
                   details={"role": "r", "domain": "embodiment", "floor": 0.4}),
    ])
    plan = si.phase2_analyze(audit)
    kinds_auto = {f.kind for f in plan.auto_fix}
    kinds_flagged = {f.kind for f in plan.flagged}
    assert "missing_role_in_genres" in kinds_auto
    assert "trait_floor_violation" in kinds_auto
    assert "test_failure" in kinds_flagged


def test_phase2_sorts_by_severity():
    audit = si.AuditResult(findings=[
        si.Finding("missing_role_in_genres", si.SEVERITY_LOW, "x",
                   details={"role": "r1"}),
        si.Finding("trait_floor_violation", si.SEVERITY_CRITICAL, "z",
                   details={"role": "r", "domain": "embodiment", "floor": 0.4}),
    ])
    plan = si.phase2_analyze(audit)
    # CRITICAL must come before LOW in auto_fix list.
    assert plan.auto_fix[0].severity == si.SEVERITY_CRITICAL


# ---------------------------------------------------------------------------
# check_skill_manifests — 3 tests
# ---------------------------------------------------------------------------

def test_skill_manifest_clean(tmp_path):
    (tmp_path / "examples" / "skills").mkdir(parents=True)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "tool_catalog.yaml").write_text(
        "version: '0.1'\ntools:\n  audit_chain_verify.v1:\n    name: audit_chain_verify\n    version: '1'\n"
    )
    (tmp_path / "examples" / "skills" / "ok.v1.yaml").write_text(
        "schema_version: 1\nname: ok\nversion: '1'\nrequires:\n  - audit_chain_verify.v1\n"
    )
    findings = si.check_skill_manifests(tmp_path)
    assert findings == []


def test_skill_manifest_unknown_tool(tmp_path):
    (tmp_path / "examples" / "skills").mkdir(parents=True)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "tool_catalog.yaml").write_text(
        "version: '0.1'\ntools:\n  audit_chain_verify.v1:\n    name: audit_chain_verify\n    version: '1'\n"
    )
    (tmp_path / "examples" / "skills" / "bad.v1.yaml").write_text(
        "schema_version: 1\nname: bad\nrequires:\n  - nonexistent_tool.v1\n"
    )
    findings = si.check_skill_manifests(tmp_path)
    assert any(f.kind == "skill_unknown_tool_ref" for f in findings)


def test_skill_manifest_missing_schema_version(tmp_path):
    (tmp_path / "examples" / "skills").mkdir(parents=True)
    (tmp_path / "examples" / "skills" / "old.v1.yaml").write_text(
        "name: old\nversion: '1'\nrequires: []\n"
    )
    findings = si.check_skill_manifests(tmp_path)
    assert any(f.kind == "skill_missing_schema_version" for f in findings)


# ---------------------------------------------------------------------------
# check_syntax_errors — 2 tests
# ---------------------------------------------------------------------------

def test_syntax_errors_clean(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "ok.py").write_text("def foo():\n    return 1\n")
    findings = si.check_syntax_errors(tmp_path)
    assert findings == []


def test_syntax_errors_broken(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "bad.py").write_text("def foo(:\n  return 1\n")
    findings = si.check_syntax_errors(tmp_path)
    assert any(f.kind == "syntax_error" for f in findings)
    assert findings[0].severity == si.SEVERITY_CRITICAL


# ---------------------------------------------------------------------------
# check_tool_registration — 2 tests
# ---------------------------------------------------------------------------

def test_tool_registration_clean(tmp_path):
    builtin = tmp_path / "src" / "forest_soul_forge" / "tools" / "builtin"
    builtin.mkdir(parents=True)
    (builtin / "audit_chain_verify.py").write_text(
        "class T:\n    name='audit_chain_verify'\n    version='1'\n"
    )
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "tool_catalog.yaml").write_text(
        "version: '0.1'\ntools:\n  audit_chain_verify.v1:\n    name: audit_chain_verify\n    version: '1'\n"
    )
    findings = si.check_tool_registration(tmp_path)
    assert findings == []


def test_tool_registration_missing_module(tmp_path):
    builtin = tmp_path / "src" / "forest_soul_forge" / "tools" / "builtin"
    builtin.mkdir(parents=True)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "tool_catalog.yaml").write_text(
        "version: '0.1'\ntools:\n  phantom_tool.v1:\n    name: phantom_tool\n    version: '1'\n"
    )
    findings = si.check_tool_registration(tmp_path)
    assert any(f.kind == "tool_module_missing" for f in findings)


# ---------------------------------------------------------------------------
# Fixer functions — 4 tests
# ---------------------------------------------------------------------------

def test_fix_missing_role_in_genres(tmp_path):
    _make_minimal_repo(tmp_path)
    ok, msg, files = si.fix_missing_role_in_genres(tmp_path, "new_role")
    assert ok, msg
    text = (tmp_path / "config" / "genres.yaml").read_text()
    assert "- new_role" in text
    assert "config/genres.yaml" in files


def test_fix_missing_role_in_constitution(tmp_path):
    _make_minimal_repo(tmp_path)
    ok, msg, files = si.fix_missing_role_in_constitution(tmp_path, "new_role")
    assert ok, msg
    text = (tmp_path / "config" / "constitution_templates.yaml").read_text()
    assert "new_role:" in text
    assert "placeholder_human_review_required" in text


def test_fix_trait_floor_violation(tmp_path):
    extra = (
        "  bad_role:\n"
        "    description: stub\n"
        "    domain_weights:\n"
        "      communication: 1.0\n"
        "      cognitive: 1.0\n"
        "      security: 1.0\n"
        "      audit: 1.0\n"
        "      emotional: 1.0\n"
        "      embodiment: 0.3\n"
    )
    _make_minimal_repo(tmp_path, extra_trait=extra)
    ok, msg, files = si.fix_trait_floor_violation(
        tmp_path, "bad_role", "embodiment", 0.4
    )
    assert ok, msg
    text = (tmp_path / "config" / "trait_tree.yaml").read_text()
    # The line "embodiment: 0.3" must now be 0.4.
    assert "embodiment: 0.4" in text
    assert "embodiment: 0.3" not in text


def test_fix_version_string_prefixed(tmp_path):
    p = tmp_path / "mod.py"
    p.write_text('class T:\n    _VERSION = "v1"\n')
    ok, msg, files = si.fix_version_string_prefixed(tmp_path, "mod.py")
    assert ok, msg
    text = p.read_text()
    assert '_VERSION = "1"' in text
    assert '_VERSION = "v1"' not in text


# ---------------------------------------------------------------------------
# apply_fix dispatch — 2 tests
# ---------------------------------------------------------------------------

def test_apply_fix_dispatches_to_correct_fixer(tmp_path):
    _make_minimal_repo(tmp_path)
    f = si.Finding(
        kind="missing_role_in_genres",
        severity=si.SEVERITY_MEDIUM,
        summary="x",
        details={"role": "new_role"},
    )
    outcome = si.apply_fix(tmp_path, f)
    assert outcome.status == "FIXED"
    assert outcome.changed_files


def test_apply_fix_unknown_kind_is_skipped(tmp_path):
    f = si.Finding(
        kind="totally_unknown_kind",
        severity=si.SEVERITY_LOW,
        summary="x",
    )
    outcome = si.apply_fix(tmp_path, f)
    assert outcome.status == "SKIPPED"
    assert "no fixer" in outcome.error


# ---------------------------------------------------------------------------
# Time stamps — 2 tests
# ---------------------------------------------------------------------------

def test_stamp_filename_format():
    s = si.stamp_filename()
    # YYYY-MM-DD-HHMMSS = 17 chars exactly, with three dashes.
    assert len(s) == 17, s
    assert s.count("-") == 3


def test_stamp_log_iso_form():
    s = si.stamp_log()
    # Must include 'T' separator (ISO format).
    assert "T" in s
    # Year > 2025
    assert int(s[:4]) >= 2025


# ---------------------------------------------------------------------------
# render_report — 2 tests
# ---------------------------------------------------------------------------

def test_render_report_no_findings():
    audit = si.AuditResult(findings=[], pytest_summary={"passed": 100})
    plan = si.FixPlan()
    text = si.render_report(
        branch_name="self-improve/2026-05-24-100000",
        audit=audit, plan=plan, outcomes=[], validation={}, audit_only=False,
    )
    assert "Executive summary" in text
    assert "No findings" in text
    assert "self-improve/2026-05-24-100000" in text


def test_render_report_with_fixes_and_flagged():
    f1 = si.Finding("missing_role_in_genres", si.SEVERITY_MEDIUM, "role x",
                    details={"role": "x"})
    f2 = si.Finding("test_failure", si.SEVERITY_HIGH, "broken",
                    details={"id": "m::t"})
    audit = si.AuditResult(findings=[f1, f2], pytest_summary={
        "passed": 99, "failed": 1, "errors": 0, "skipped": 0,
    })
    plan = si.FixPlan(auto_fix=[f1], flagged=[f2])
    outcomes = [si.FixOutcome(finding=f1, status="FIXED",
                              changed_files=["config/genres.yaml"],
                              diff="added")]
    validation = {
        "before": {"passed": 99, "failed": 1, "errors": 0, "skipped": 0},
        "after": {"passed": 100, "failed": 0, "errors": 0, "skipped": 0},
        "broken_tests": [], "fixed_tests": ["m::t"], "still_failing": [],
        "delta": {"passed_delta": 1, "failed_delta": -1, "errors_delta": 0},
    }
    text = si.render_report(
        branch_name="test-branch", audit=audit, plan=plan,
        outcomes=outcomes, validation=validation, audit_only=False,
    )
    assert "missing_role_in_genres" in text
    assert "Flagged for human review" in text
    assert "Test counts" in text
    assert "config/genres.yaml" in text


# ---------------------------------------------------------------------------
# Dataclass shape — 1 test
# ---------------------------------------------------------------------------

def test_finding_to_dict_round_trip():
    f = si.Finding(
        kind="x", severity="HIGH", summary="s",
        details={"a": 1}, source="t",
    )
    d = f.to_dict()
    assert d["kind"] == "x"
    assert d["details"] == {"a": 1}
