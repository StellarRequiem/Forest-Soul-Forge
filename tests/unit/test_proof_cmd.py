"""Tests for `fsf proof` — the standing scorecard against STANDARD.md."""
import json
from argparse import Namespace

from forest_soul_forge.cli import proof_cmd


def test_count_tests(tmp_path):
    (tmp_path / "test_a.py").write_text("def test_x():\n    pass\ndef test_y():\n    pass\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "test_b.py").write_text("def test_z():\n    pass\n")
    (tmp_path / "not_a_test.py").write_text("def helper():\n    pass\n")
    nf, ntf = proof_cmd._count_tests(str(tmp_path))
    assert nf == 2          # only test_*.py files
    assert ntf == 3         # only def test_* functions


def test_count_tests_empty_dir(tmp_path):
    assert proof_cmd._count_tests(str(tmp_path)) == (0, 0)


def test_run_json_structure_and_exit(tmp_path, capsys, monkeypatch):
    # Deterministic: mock the canon subprocess; point audit at missing paths so
    # the Audited criterion fails -> below standard -> exit 1.
    monkeypatch.setattr(proof_cmd, "_canon_ok", lambda: True)
    rc = proof_cmd.run(Namespace(
        chain=str(tmp_path / "nope.jsonl"),
        db=str(tmp_path / "nope.sqlite"),
        json=True,
    ))
    out = json.loads(capsys.readouterr().out)
    assert set(out) >= {"score", "total", "criteria"}
    assert out["total"] == 5
    names = {c["name"] for c in out["criteria"]}
    assert names == {"Tested", "Audited", "Integrity-enforced", "Canon-true", "Standard-bound"}
    assert any(c["name"] == "Audited" and c["pass"] is False for c in out["criteria"])
    assert rc == 1  # not full marks


def test_run_human_output(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(proof_cmd, "_canon_ok", lambda: True)
    proof_cmd.run(Namespace(
        chain=str(tmp_path / "nope.jsonl"),
        db=str(tmp_path / "nope.sqlite"),
        json=False,
    ))
    out = capsys.readouterr().out
    assert "proof of standing" in out
    assert "SCORE:" in out
    assert "verify yourself:" in out  # every line carries an independent-check pointer
