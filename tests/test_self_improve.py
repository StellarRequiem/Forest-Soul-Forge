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


# ===========================================================================
# v2 upgrade — Ollama-backed fixes, sandbox validation, rollback safety
# ===========================================================================
#
# The tests below cover the four pillars added in the v2 upgrade:
#   - OllamaClient + model picker + diff extraction + prompt build
#   - SessionState + attempt-log journal + circuit breaker
#   - New mechanical fixers (stale_version_constant, missing_init_py,
#     yaml_schema_mismatch, unused_import)
#   - Catastrophic regression detection + survival summary +
#     --rollback CLI handler
#
# Mocking strategy: the Ollama client and git wrappers are dependency-
# injected at the call site (no patching of urllib / subprocess at the
# global level). Tests pass in test-double objects via the same kwargs
# the real harness uses.


# ---------------------------------------------------------------------------
# OllamaClient — model discovery + picker (8 tests)
# ---------------------------------------------------------------------------

class _StubOllamaClient(si.OllamaClient):
    """Test double that bypasses HTTP and returns pre-canned data.

    The real client's transport methods (`_get`, `_post`) are the
    only things we override; the public surface (`available`,
    `list_models`, `generate`, `pick_model`) is exercised through
    the real code paths.
    """

    def __init__(
        self,
        *,
        tags=None,
        generate_response=None,
        unavailable=False,
    ):
        super().__init__()
        self._tags = tags
        self._gen = generate_response
        self._unavailable = unavailable
        self.calls = []  # log of (path, payload) for assertions

    def _get(self, path):
        self.calls.append(("GET", path, None))
        if self._unavailable:
            return None
        if path == "/api/tags":
            return self._tags
        return None

    def _post(self, path, payload):
        self.calls.append(("POST", path, payload))
        if self._unavailable:
            return None
        if path == "/api/generate":
            if isinstance(self._gen, Exception):
                raise self._gen
            return {"response": self._gen or ""}
        return None


def test_ollama_pick_model_prefers_coding_model():
    """When both coding and general models are available, prefer
    the coding one."""
    names = ["llama3:8b", "qwen2.5-coder:14b", "nomic-embed-text:latest"]
    picked = si.OllamaClient.pick_model(names)
    assert picked == "qwen2.5-coder:14b"


def test_ollama_pick_model_skips_embedding_models():
    """Embedding models cannot produce diffs — must be skipped even
    if they're the only thing installed."""
    picked = si.OllamaClient.pick_model(["nomic-embed-text:latest"])
    assert picked is None


def test_ollama_pick_model_falls_back_to_first_non_embedding():
    """When no coding model is in the preference list, return the
    first non-embedding model installed."""
    picked = si.OllamaClient.pick_model(
        ["nomic-embed-text:latest", "mistral:7b"]
    )
    assert picked == "mistral:7b"


def test_ollama_pick_model_empty_list_returns_none():
    assert si.OllamaClient.pick_model([]) is None


def test_ollama_pick_model_qwen_priority():
    """qwen3:8b (locally available) should be picked when no
    explicit coding variant is present — it's in the fallback
    preference list."""
    picked = si.OllamaClient.pick_model(["qwen3:8b"])
    assert picked == "qwen3:8b"


def test_ollama_client_available_returns_true_when_tags_respond():
    stub = _StubOllamaClient(tags={"models": []})
    assert stub.available() is True


def test_ollama_client_available_returns_false_when_unreachable():
    stub = _StubOllamaClient(unavailable=True)
    assert stub.available() is False


def test_ollama_client_list_models_parses_tags_response():
    stub = _StubOllamaClient(tags={"models": [
        {"name": "qwen3:8b"},
        {"name": "nomic-embed-text:latest"},
    ]})
    models = stub.list_models()
    assert "qwen3:8b" in models
    assert "nomic-embed-text:latest" in models


# ---------------------------------------------------------------------------
# Diff extraction + prompt construction (5 tests)
# ---------------------------------------------------------------------------

def test_extract_unified_diff_from_fenced_block():
    response = (
        "Here is the fix:\n"
        "```diff\n"
        "--- a/src/foo.py\n"
        "+++ b/src/foo.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x = 1\n"
        "+x = 2\n"
        "```\n"
        "Hope that helps."
    )
    diff = si.extract_unified_diff(response)
    assert "--- a/src/foo.py" in diff
    assert "+x = 2" in diff
    assert diff.endswith("\n")


def test_extract_unified_diff_from_raw_text():
    """Some models return the diff with no fences — accept that too."""
    response = (
        "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"
    )
    diff = si.extract_unified_diff(response)
    assert "--- a/x.py" in diff


def test_extract_unified_diff_returns_empty_for_no_diff():
    assert si.extract_unified_diff("I cannot fix this.") == ""
    assert si.extract_unified_diff("") == ""


def test_extract_unified_diff_picks_first_valid_fenced_block():
    """If the model returns multiple fenced blocks, take the first
    one that actually contains a diff header."""
    response = (
        "```python\n# not a diff\n```\n"
        "```diff\n--- a/y.py\n+++ b/y.py\n@@ -1 +1 @@\n-1\n+2\n```\n"
    )
    diff = si.extract_unified_diff(response)
    assert "--- a/y.py" in diff


def test_build_ollama_fix_prompt_includes_required_sections():
    prompt = si.build_ollama_fix_prompt(
        test_id="tests/unit/test_x.py::test_y",
        error="AssertionError: oh no",
        test_source="def test_y():\n    assert foo() == 1\n",
        related_sources={
            "src/foo.py": "def foo():\n    return 2\n",
        },
    )
    assert "FAILING TEST: tests/unit/test_x.py::test_y" in prompt
    assert "AssertionError: oh no" in prompt
    assert "TEST SOURCE" in prompt
    assert "src/foo.py" in prompt
    assert "unified diff" in prompt
    assert "NO_FIX" in prompt


def test_files_touched_by_patch_extracts_paths():
    diff = (
        "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-x\n+y\n"
        "--- a/src/b.py\n+++ b/src/b.py\n@@ -1 +1 @@\n-1\n+2\n"
    )
    touched = si._files_touched_by_patch(diff)
    assert touched == ["src/a.py", "src/b.py"]


def test_files_touched_by_patch_empty_for_no_headers():
    assert si._files_touched_by_patch("not a diff") == []


# ---------------------------------------------------------------------------
# SessionState — attempt log + circuit breaker (6 tests)
# ---------------------------------------------------------------------------

def test_session_state_records_attempt():
    s = si.SessionState(start_commit_hash="abc123", branch_name="b")
    s.record(kind="missing_role_in_genres", action="APPLIED", reason="ok")
    assert len(s.attempts) == 1
    a = s.attempts[0]
    assert a.kind == "missing_role_in_genres"
    assert a.action == "APPLIED"
    assert a.reason == "ok"
    assert a.timestamp  # populated


def test_session_state_consecutive_failures_trip_circuit_breaker():
    """Three consecutive failures must trigger the abort flag."""
    s = si.SessionState(start_commit_hash="abc", branch_name="b")
    assert s.note_failure() is False
    assert s.note_failure() is False
    assert s.note_failure() is True  # third strike
    assert s.aborted is True
    assert "consecutive fix failures" in s.abort_reason


def test_session_state_success_resets_counter():
    """A successful fix breaks the failure streak."""
    s = si.SessionState(start_commit_hash="abc", branch_name="b")
    s.note_failure()
    s.note_failure()
    s.note_success()
    # After reset, we need three more failures to abort.
    assert s.note_failure() is False
    assert s.note_failure() is False
    assert s.note_failure() is True
    assert s.aborted is True


def test_session_state_attempt_to_dict_serializable():
    """Attempts must round-trip through asdict for JSON output."""
    s = si.SessionState(start_commit_hash="abc", branch_name="b")
    s.record(
        kind="test_failure", action="REVERTED",
        reason="test still red", files=["src/x.py"],
        test_id="t::x", model="qwen3:8b",
    )
    d = s.attempts[0].to_dict()
    assert d["kind"] == "test_failure"
    assert d["model"] == "qwen3:8b"
    assert d["files"] == ["src/x.py"]


def test_session_state_limit_constant():
    """The threshold is a class constant — test that it's the
    documented value (3 strikes) so future changes are explicit."""
    assert si.SessionState.CONSECUTIVE_FAILURE_LIMIT == 3


def test_attempt_log_entry_default_fields():
    a = si.AttemptLogEntry(timestamp="t", kind="k", action="APPLIED")
    assert a.reason == ""
    assert a.files == []
    assert a.test_id == ""
    assert a.model == ""


# ---------------------------------------------------------------------------
# Mechanical fixers (v2) — stale_version_constant (3 tests)
# ---------------------------------------------------------------------------

def test_check_stale_version_constant_detects_drift(tmp_path):
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "a.py").write_text(
        "MEMORY_SCHEMA_VERSION = 22\n"
    )
    (tmp_path / "src" / "pkg" / "b.py").write_text(
        "MEMORY_SCHEMA_VERSION = 23\n"
    )
    findings = si.check_stale_version_constants(tmp_path)
    stale = [f for f in findings if f.kind == "stale_version_constant"]
    assert len(stale) == 1
    assert stale[0].details["stale_value"] == 22
    assert stale[0].details["canonical_value"] == 23
    assert stale[0].details["constant"] == "MEMORY_SCHEMA_VERSION"


def test_check_stale_version_constant_clean_when_aligned(tmp_path):
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "a.py").write_text(
        "MEMORY_SCHEMA_VERSION = 23\n"
    )
    (tmp_path / "src" / "pkg" / "b.py").write_text(
        "MEMORY_SCHEMA_VERSION = 23\n"
    )
    findings = si.check_stale_version_constants(tmp_path)
    assert findings == []


def test_fix_stale_version_constant_bumps_value(tmp_path):
    p = tmp_path / "src" / "x.py"
    p.parent.mkdir(parents=True)
    p.write_text("CHAIN_SCHEMA_VERSION = 5  # older format\n")
    ok, msg, files = si.fix_stale_version_constant(
        tmp_path, "src/x.py", "CHAIN_SCHEMA_VERSION", 5, 7,
    )
    assert ok, msg
    text = p.read_text()
    assert "CHAIN_SCHEMA_VERSION = 7" in text
    assert "older format" in text  # comment preserved
    assert files == ["src/x.py"]


# ---------------------------------------------------------------------------
# Mechanical fixers (v2) — missing_init_py (3 tests)
# ---------------------------------------------------------------------------

def test_check_missing_init_py_detects_gap(tmp_path):
    pkg = tmp_path / "src" / "newpkg"
    pkg.mkdir(parents=True)
    (pkg / "mod.py").write_text("x = 1\n")
    findings = si.check_missing_init_imports(tmp_path)
    missing = [f for f in findings if f.kind == "missing_init_py"]
    assert missing
    assert "newpkg" in missing[0].details["path"]


def test_check_missing_init_py_clean_when_init_present(tmp_path):
    pkg = tmp_path / "src" / "okpkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text("x = 1\n")
    findings = si.check_missing_init_imports(tmp_path)
    assert [f for f in findings if f.kind == "missing_init_py"] == []


def test_fix_missing_init_py_creates_empty_init(tmp_path):
    pkg = tmp_path / "src" / "p"
    pkg.mkdir(parents=True)
    (pkg / "mod.py").write_text("x = 1\n")
    ok, msg, files = si.fix_missing_init_py(tmp_path, "src/p")
    assert ok, msg
    init = pkg / "__init__.py"
    assert init.exists()
    assert init.read_text() == ""  # empty by default
    assert "src/p/__init__.py" in files[0]


def test_fix_missing_init_py_refuses_if_exists(tmp_path):
    pkg = tmp_path / "src" / "p"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("already here\n")
    ok, _, _ = si.fix_missing_init_py(tmp_path, "src/p")
    assert ok is False
    # We don't overwrite existing content.
    assert (pkg / "__init__.py").read_text() == "already here\n"


# ---------------------------------------------------------------------------
# Mechanical fixers (v2) — yaml_schema_mismatch (3 tests)
# ---------------------------------------------------------------------------

def test_check_yaml_schema_mismatch_detects_drift(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "constitution_templates.yaml").write_text(
        "schema_version: 1\nrole_base: {}\n"
    )
    (tmp_path / "config" / "genres.yaml").write_text(
        "schema_version: 2\ngenres: {}\n"
    )
    findings = si.check_yaml_schema_mismatch(tmp_path)
    mismatch = [f for f in findings if f.kind == "yaml_schema_mismatch"]
    assert mismatch
    stale_paths = [f.details["path"] for f in mismatch]
    assert any("constitution_templates.yaml" in p for p in stale_paths)


def test_check_yaml_schema_mismatch_clean_when_aligned(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "constitution_templates.yaml").write_text(
        "schema_version: 2\nrole_base: {}\n"
    )
    (tmp_path / "config" / "genres.yaml").write_text(
        "schema_version: 2\ngenres: {}\n"
    )
    findings = si.check_yaml_schema_mismatch(tmp_path)
    assert findings == []


def test_fix_yaml_schema_mismatch_bumps_value(tmp_path):
    p = tmp_path / "config" / "x.yaml"
    p.parent.mkdir()
    p.write_text("schema_version: 1\nrole_base: {}\n")
    ok, msg, files = si.fix_yaml_schema_mismatch(tmp_path, "config/x.yaml", 1, 2)
    assert ok, msg
    text = p.read_text()
    assert "schema_version: 2" in text
    assert "schema_version: 1" not in text


# ---------------------------------------------------------------------------
# Mechanical fixers (v2) — unused_import via ruff (2 tests)
# ---------------------------------------------------------------------------

def test_fix_unused_imports_with_ruff_no_ruff_returns_skip(tmp_path, monkeypatch):
    """If ruff is not on PATH the fixer must report False cleanly."""
    monkeypatch.setattr(si.shutil, "which", lambda name: None)
    ok, msg, files = si.fix_unused_imports_with_ruff(tmp_path)
    assert ok is False
    assert "ruff not on PATH" in msg
    assert files == []


def test_fix_unused_imports_with_ruff_no_targets_returns_skip(tmp_path, monkeypatch):
    """If none of the candidate target directories exist, skip."""
    monkeypatch.setattr(si.shutil, "which", lambda name: "/usr/bin/ruff")
    ok, msg, _ = si.fix_unused_imports_with_ruff(tmp_path)
    assert ok is False
    assert "no target paths exist" in msg


# ---------------------------------------------------------------------------
# Catastrophic regression detection (5 tests)
# ---------------------------------------------------------------------------

def test_detect_catastrophic_count_drop():
    """Total test count dropping signals a collection error."""
    before = {"passed": 100, "failed": 0, "errors": 0, "skipped": 5,
              "failed_tests": []}
    after = {"passed": 90, "failed": 0, "errors": 0, "skipped": 5,
             "failed_tests": []}
    is_cata, reason = si.detect_catastrophic_regression(before, after)
    assert is_cata is True
    assert "total test count dropped" in reason


def test_detect_catastrophic_new_failures():
    """New failures absent from baseline are catastrophic."""
    before = {"passed": 100, "failed": 0, "skipped": 0, "failed_tests": []}
    after = {"passed": 99, "failed": 1, "skipped": 0,
             "failed_tests": [{"id": "tests/test_new.py::test_x"}]}
    is_cata, reason = si.detect_catastrophic_regression(before, after)
    assert is_cata is True
    assert "newly-failing" in reason


def test_detect_catastrophic_clean_when_identical():
    s = {"passed": 100, "failed": 0, "skipped": 0, "failed_tests": []}
    is_cata, reason = si.detect_catastrophic_regression(s, s)
    assert is_cata is False
    assert reason == ""


def test_detect_catastrophic_clean_when_only_fixes():
    """Going from 1 failure -> 0 failures is fine, not catastrophic."""
    before = {"passed": 99, "failed": 1, "skipped": 0,
              "failed_tests": [{"id": "t::x"}]}
    after = {"passed": 100, "failed": 0, "skipped": 0, "failed_tests": []}
    is_cata, _ = si.detect_catastrophic_regression(before, after)
    assert is_cata is False


def test_detect_catastrophic_count_drop_outranks_fix_signal():
    """If total count drops, we treat it as catastrophic even if
    the failed count also went to zero — count drop = something
    failed to collect."""
    before = {"passed": 100, "failed": 0, "skipped": 0, "failed_tests": []}
    after = {"passed": 50, "failed": 0, "skipped": 0, "failed_tests": []}
    is_cata, _ = si.detect_catastrophic_regression(before, after)
    assert is_cata is True


# ---------------------------------------------------------------------------
# Survival summary + reclassification + rollback handler (5 tests)
# ---------------------------------------------------------------------------

def test_build_survival_summary_partitions_outcomes():
    f1 = si.Finding("missing_role_in_genres", si.SEVERITY_MEDIUM, "x")
    f2 = si.Finding("test_failure", si.SEVERITY_HIGH, "y")
    f3 = si.Finding("trait_floor_violation", si.SEVERITY_HIGH, "z")
    outcomes = [
        si.FixOutcome(finding=f1, status="FIXED",
                      changed_files=["a.yaml"], diff="ok"),
        si.FixOutcome(finding=f2, status="REVERTED",
                      changed_files=["src/x.py"], error="test red"),
        si.FixOutcome(finding=f3, status="SKIPPED",
                      error="no fixer"),
    ]
    session = si.SessionState(start_commit_hash="abc", branch_name="b")
    summary = si.build_survival_summary(outcomes, session)
    assert summary["attempted_total"] == 3
    assert len(summary["survived"]) == 1
    assert len(summary["reverted"]) == 1
    assert len(summary["skipped"]) == 1
    assert summary["survived"][0]["kind"] == "missing_role_in_genres"
    assert summary["reverted"][0]["error"] == "test red"


def test_build_survival_summary_handles_no_session():
    f1 = si.Finding("missing_role_in_genres", si.SEVERITY_MEDIUM, "x")
    outcomes = [
        si.FixOutcome(finding=f1, status="FIXED",
                      changed_files=["a.yaml"], diff="ok"),
    ]
    summary = si.build_survival_summary(outcomes, None)
    assert summary["attempts"] == []
    assert summary["circuit_breaker"] is False
    assert summary["attempted_total"] == 1


def test_reclassify_for_ollama_promotes_test_failure():
    """When Ollama is in scope, test_failure findings move from
    flagged to auto_fix."""
    f_test = si.Finding("test_failure", si.SEVERITY_HIGH, "x",
                        details={"id": "t::x"})
    f_other = si.Finding("syntax_error", si.SEVERITY_CRITICAL, "y")
    plan = si.FixPlan(auto_fix=[], flagged=[f_test, f_other])
    new_plan = si.reclassify_for_ollama(plan)
    auto_kinds = {f.kind for f in new_plan.auto_fix}
    flagged_kinds = {f.kind for f in new_plan.flagged}
    assert "test_failure" in auto_kinds
    assert "syntax_error" in flagged_kinds  # stays flagged
    assert "test_failure" not in flagged_kinds


def test_reclassify_for_ollama_preserves_existing_auto_fix():
    """Promoting test_failure should NOT lose findings already in
    auto_fix."""
    f_role = si.Finding("missing_role_in_genres", si.SEVERITY_MEDIUM, "r")
    f_test = si.Finding("test_failure", si.SEVERITY_HIGH, "x",
                        details={"id": "t::x"})
    plan = si.FixPlan(auto_fix=[f_role], flagged=[f_test])
    new_plan = si.reclassify_for_ollama(plan)
    auto_kinds = [f.kind for f in new_plan.auto_fix]
    assert "missing_role_in_genres" in auto_kinds
    assert "test_failure" in auto_kinds


def test_handle_rollback_no_branches(tmp_path, monkeypatch):
    """Rollback with no self-improve branches exits 1."""
    monkeypatch.setattr(
        si, "git_list_self_improve_branches", lambda r: []
    )
    rc = si.handle_rollback(tmp_path)
    assert rc == 1


# ---------------------------------------------------------------------------
# phase3_fix integration — Ollama dispatch (5 tests)
# ---------------------------------------------------------------------------

def test_phase3_fix_skips_test_failure_without_ollama(tmp_path):
    """test_failure findings short-circuit to SKIPPED when no Ollama
    client is provided."""
    f = si.Finding("test_failure", si.SEVERITY_HIGH, "x",
                   details={"id": "t::x"})
    plan = si.FixPlan(auto_fix=[f])
    session = si.SessionState(start_commit_hash="abc", branch_name="b")
    outcomes = si.phase3_fix(
        tmp_path, plan,
        session=session, ollama=None, ollama_model=None,
    )
    assert len(outcomes) == 1
    assert outcomes[0].status == "SKIPPED"
    assert "Ollama" in outcomes[0].error


def test_phase3_fix_circuit_breaker_skips_remaining(tmp_path):
    """Once the breaker trips on three real fix-attempt failures,
    subsequent findings are recorded with action=ABORTED.

    We use 5 test_failure findings against test files that exist
    (so the dispatch reaches the Ollama call) with a stub Ollama
    that returns NO_FIX — three NO_FIX responses count as three
    fix-attempt failures and trip the breaker.
    """
    # Make 5 real test files so the Ollama path is reachable.
    findings = []
    for i in range(5):
        tf = tmp_path / "tests" / "unit" / f"test_x{i}.py"
        tf.parent.mkdir(parents=True, exist_ok=True)
        tf.write_text(f"def test_y{i}():\n    assert 1 == 2\n")
        findings.append(si.Finding(
            "test_failure", si.SEVERITY_HIGH, f"x{i}",
            details={"id": f"tests/unit/test_x{i}.py::test_y{i}",
                     "error": "AssertionError"},
        ))
    plan = si.FixPlan(auto_fix=findings)
    session = si.SessionState(start_commit_hash="abc", branch_name="b")
    stub = _StubOllamaClient(
        tags={"models": [{"name": "qwen3:8b"}]},
        generate_response="NO_FIX",
    )
    outcomes = si.phase3_fix(
        tmp_path, plan,
        session=session, ollama=stub, ollama_model="qwen3:8b",
    )
    # All five recorded as outcomes.
    assert len(outcomes) == 5
    # Breaker trips after 3 consecutive failures; remaining are ABORTED.
    aborted_count = sum(
        1 for a in session.attempts if a.action == "ABORTED"
    )
    assert aborted_count >= 1, (
        f"expected ABORTED entries, got actions: "
        f"{[a.action for a in session.attempts]}"
    )
    assert session.aborted is True


def test_phase3_fix_records_applied_in_session(tmp_path):
    """A successful mechanical fix should leave an APPLIED entry
    in session.attempts."""
    _make_minimal_repo(tmp_path,
                       trait_roles=("a", "b"),
                       genre_roles=("a",),
                       constitution_roles=("a", "b"))
    f = si.Finding("missing_role_in_genres", si.SEVERITY_MEDIUM, "y",
                   details={"role": "b"})
    plan = si.FixPlan(auto_fix=[f])
    session = si.SessionState(start_commit_hash="abc", branch_name="b")
    # Provide a no-op verifier by monkey-patching at call time below.
    outcomes = si.phase3_fix(
        tmp_path, plan,
        session=session, ollama=None, ollama_model=None,
    )
    # The mechanical fix should APPLY (no targeted tests to fail on
    # a synthetic tmp_path layout).
    actions = [a.action for a in session.attempts]
    assert "APPLIED" in actions


def test_phase3_fix_records_failure_in_session(tmp_path):
    """A test_failure dispatch with no Ollama should record SKIPPED
    in the attempt log."""
    f = si.Finding("test_failure", si.SEVERITY_HIGH, "x",
                   details={"id": "t::x"})
    plan = si.FixPlan(auto_fix=[f])
    session = si.SessionState(start_commit_hash="abc", branch_name="b")
    si.phase3_fix(
        tmp_path, plan,
        session=session, ollama=None, ollama_model=None,
    )
    assert session.attempts
    assert any(a.action == "SKIPPED" for a in session.attempts)


def test_phase3_fix_no_session_does_not_crash(tmp_path):
    """Backward compatibility — session is optional."""
    _make_minimal_repo(tmp_path,
                       trait_roles=("a", "b"),
                       genre_roles=("a",),
                       constitution_roles=("a", "b"))
    f = si.Finding("missing_role_in_genres", si.SEVERITY_MEDIUM, "y",
                   details={"role": "b"})
    plan = si.FixPlan(auto_fix=[f])
    outcomes = si.phase3_fix(tmp_path, plan)
    assert len(outcomes) == 1
    assert outcomes[0].status in {"FIXED", "REVERTED", "SKIPPED"}


# ---------------------------------------------------------------------------
# Render-report integration with v2 features (2 tests)
# ---------------------------------------------------------------------------

def test_render_report_includes_attempt_log_when_session_present():
    f1 = si.Finding("missing_role_in_genres", si.SEVERITY_MEDIUM, "role x",
                    details={"role": "x"})
    audit = si.AuditResult(findings=[f1])
    plan = si.FixPlan(auto_fix=[f1])
    outcomes = [si.FixOutcome(
        finding=f1, status="FIXED",
        changed_files=["config/genres.yaml"], diff="added",
    )]
    session = si.SessionState(
        start_commit_hash="abc123def", branch_name="self-improve/x",
    )
    session.record(
        kind="missing_role_in_genres", action="APPLIED", reason="added",
    )
    text = si.render_report(
        branch_name="self-improve/x",
        audit=audit, plan=plan, outcomes=outcomes,
        validation={}, audit_only=False, session=session,
    )
    assert "Attempt log" in text
    assert "APPLIED" in text


def test_render_report_marks_catastrophic_in_validation():
    f1 = si.Finding("missing_role_in_genres", si.SEVERITY_MEDIUM, "x",
                    details={"role": "x"})
    audit = si.AuditResult(findings=[f1])
    plan = si.FixPlan(auto_fix=[f1])
    outcomes = [si.FixOutcome(
        finding=f1, status="REVERTED",
        changed_files=["config/genres.yaml"],
        error="catastrophic",
    )]
    session = si.SessionState(
        start_commit_hash="abc", branch_name="b",
    )
    session.record(
        kind="phase4", action="REVERTED", reason="hard reset",
    )
    validation = {
        "catastrophic": True,
        "catastrophic_reason": "1 newly-failing test",
        "before": {"passed": 100, "failed": 0, "skipped": 0, "errors": 0},
        "after": {"passed": 99, "failed": 1, "skipped": 0, "errors": 0},
        "broken_tests": ["new::test"],
        "fixed_tests": [],
        "still_failing": [],
        "delta": {"passed_delta": -1, "failed_delta": 1, "errors_delta": 0},
    }
    text = si.render_report(
        branch_name="b", audit=audit, plan=plan, outcomes=outcomes,
        validation=validation, audit_only=False, session=session,
    )
    assert "CATASTROPHIC" in text


# ---------------------------------------------------------------------------
# Ollama-fix end-to-end with patch application (3 tests)
# ---------------------------------------------------------------------------

def test_fix_test_failure_returns_skip_when_no_test_id(tmp_path):
    """A finding without an id field can't be dispatched."""
    f = si.Finding("test_failure", si.SEVERITY_HIGH, "x", details={})
    stub = _StubOllamaClient(tags={"models": []})
    ok, msg, files = si.fix_test_failure_with_ollama(
        tmp_path, f, stub, "qwen3:8b",
    )
    assert ok is False
    assert "no test id" in msg


def test_fix_test_failure_returns_skip_when_test_file_missing(tmp_path):
    f = si.Finding("test_failure", si.SEVERITY_HIGH, "x",
                   details={"id": "tests/unit/test_does_not_exist.py::test_y"})
    stub = _StubOllamaClient(tags={"models": []})
    ok, msg, _ = si.fix_test_failure_with_ollama(
        tmp_path, f, stub, "qwen3:8b",
    )
    assert ok is False
    assert "test file" in msg


def test_fix_test_failure_returns_skip_when_model_returns_no_fix(tmp_path):
    """If the model responds with literal `NO_FIX` we must skip cleanly."""
    test_path = tmp_path / "tests" / "unit" / "test_x.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text("def test_y():\n    assert 1 == 2\n")
    f = si.Finding(
        "test_failure", si.SEVERITY_HIGH, "x",
        details={"id": "tests/unit/test_x.py::test_y",
                 "error": "AssertionError"},
    )
    stub = _StubOllamaClient(
        tags={"models": [{"name": "qwen3:8b"}]},
        generate_response="NO_FIX",
    )
    session = si.SessionState(start_commit_hash="abc", branch_name="b")
    ok, msg, _ = si.fix_test_failure_with_ollama(
        tmp_path, f, stub, "qwen3:8b", session=session,
    )
    assert ok is False
    assert "NO_FIX" in msg
    # Session should record the skip.
    assert any(a.action == "SKIPPED" for a in session.attempts)
