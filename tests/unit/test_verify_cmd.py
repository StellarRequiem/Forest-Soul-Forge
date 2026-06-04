"""Tests for `fsf verify` — the operator's independent verification command.

Exercises the on-disk checks directly (no daemon): audit-chain hash verification,
DB integrity, and the exit-code contract (0 iff every integrity check passes).
"""
import json
import sqlite3
from argparse import Namespace

from forest_soul_forge.cli import verify_cmd
from forest_soul_forge.core.audit_chain import AuditChain


def _clean_chain(tmp_path):
    p = tmp_path / "chain.jsonl"
    c = AuditChain(p)
    c.append("agent_created", {"agent_name": "A"})
    c.append("tool_call_succeeded", {"tool_name": "llm_think.v1"})
    return p


def _good_db(tmp_path):
    db = tmp_path / "reg.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    conn.close()
    return db


def test_check_chain_clean(tmp_path):
    p = _clean_chain(tmp_path)
    info, recent = verify_cmd._check_chain(str(p), 10)
    assert info["ok"] is True
    assert info["entries"] >= 3  # genesis + 2 appends
    assert recent and recent[-1]["event"] == "tool_call_succeeded"


def test_check_chain_detects_tamper(tmp_path):
    p = _clean_chain(tmp_path)
    lines = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    lines[1]["event_data"]["agent_name"] = "HACKED"  # break the hash
    p.write_text(
        "\n".join(json.dumps(o, sort_keys=True, separators=(",", ":")) for o in lines)
        + "\n"
    )
    info, _ = verify_cmd._check_chain(str(p), 10)
    assert info["ok"] is False
    assert info["reason"]


def test_check_chain_missing_file(tmp_path):
    info, recent = verify_cmd._check_chain(str(tmp_path / "nope.jsonl"), 10)
    assert info["ok"] is False
    assert recent == []


def test_check_db_ok(tmp_path):
    res = verify_cmd._check_db(str(_good_db(tmp_path)))
    assert res["ok"] is True
    assert res["result"] == "ok"


def test_check_db_missing(tmp_path):
    res = verify_cmd._check_db(str(tmp_path / "nope.sqlite"))
    assert res["ok"] is False


def test_run_returns_zero_on_clean(tmp_path, capsys):
    p = _clean_chain(tmp_path)
    db = _good_db(tmp_path)
    rc = verify_cmd.run(Namespace(chain=str(p), db=str(db), recent=5, json=False))
    assert rc == 0
    assert "VERIFIED" in capsys.readouterr().out


def test_run_returns_one_on_failure(tmp_path):
    rc = verify_cmd.run(Namespace(
        chain=str(tmp_path / "nope.jsonl"),
        db=str(tmp_path / "nope.sqlite"),
        recent=5, json=True,
    ))
    assert rc == 1
