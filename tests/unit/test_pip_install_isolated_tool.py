"""Tests for pip_install_isolated.v1 (Phase G.1.A tenth/closing primitive)."""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.pip_install_isolated import (
    DEFAULT_MAX_LOG_LINES,
    DEFAULT_TIMEOUT_SECONDS,
    PIP_INSTALL_TIMEOUT_HARD_CAP,
    PIP_INSTALL_MAX_LOG_LINES_HARD_CAP,
    PipInstallError,
    PipInstallIsolatedTool,
    PipNotFoundError,
    VenvInvalidError,
    _cap_log,
    _is_valid_pkg_spec,
    _is_within_any,
    _locate_venv_python,
    _parse_pip_output,
    _resolve_allowlist,
)


def _run(coro):
    return asyncio.run(coro)


def _make_fake_venv(tmp_path: Path, name: str = "venv") -> Path:
    """Create a directory that looks like a POSIX venv (has bin/python)
    so _locate_venv_python finds it."""
    venv = tmp_path / name
    bin_dir = venv / "bin"
    bin_dir.mkdir(parents=True)
    py = bin_dir / "python"
    py.write_text("#!/usr/bin/env python\n")
    py.chmod(0o755)
    return venv


@pytest.fixture
def env(tmp_path):
    venv = _make_fake_venv(tmp_path)
    ctx = ToolContext(
        instance_id="i1", agent_dna="d" * 12,
        role="code_engineer", genre="actuator",
        session_id="s1",
        constraints={"allowed_paths": [str(tmp_path)]},
    )
    return ctx, venv


# ===========================================================================
# Validation
# ===========================================================================
class TestValidate:
    def test_missing_venv_path(self):
        with pytest.raises(ToolValidationError, match="venv_path"):
            PipInstallIsolatedTool().validate({"packages": ["x"]})

    def test_empty_venv_path(self):
        with pytest.raises(ToolValidationError, match="venv_path"):
            PipInstallIsolatedTool().validate(
                {"venv_path": "  ", "packages": ["x"]},
            )

    def test_missing_packages(self):
        with pytest.raises(ToolValidationError, match="packages"):
            PipInstallIsolatedTool().validate({"venv_path": "/tmp"})

    def test_empty_packages_list(self):
        with pytest.raises(ToolValidationError, match="packages"):
            PipInstallIsolatedTool().validate(
                {"venv_path": "/tmp", "packages": []},
            )

    def test_packages_must_be_list_of_strings(self):
        with pytest.raises(ToolValidationError, match="packages"):
            PipInstallIsolatedTool().validate(
                {"venv_path": "/tmp", "packages": "requests"},
            )
        with pytest.raises(ToolValidationError, match="packages"):
            PipInstallIsolatedTool().validate(
                {"venv_path": "/tmp", "packages": [42]},
            )

    def test_invalid_pkg_spec_rejected(self):
        # Dash-prefixed (flag injection)
        with pytest.raises(ToolValidationError, match="package spec"):
            PipInstallIsolatedTool().validate(
                {"venv_path": "/tmp", "packages": ["-r requirements.txt"]},
            )
        # Shell metachars
        with pytest.raises(ToolValidationError, match="package spec"):
            PipInstallIsolatedTool().validate(
                {"venv_path": "/tmp", "packages": ["evil; rm -rf /"]},
            )
        # Embedded backtick
        with pytest.raises(ToolValidationError, match="package spec"):
            PipInstallIsolatedTool().validate({
                "venv_path": "/tmp", "packages": ["evil`whoami`"],
            })

    def test_invalid_upgrade_type(self):
        with pytest.raises(ToolValidationError, match="upgrade"):
            PipInstallIsolatedTool().validate({
                "venv_path": "/tmp", "packages": ["x"], "upgrade": "yes",
            })

    def test_invalid_timeout(self):
        with pytest.raises(ToolValidationError, match="timeout_seconds"):
            PipInstallIsolatedTool().validate({
                "venv_path": "/tmp", "packages": ["x"], "timeout_seconds": 0,
            })
        with pytest.raises(ToolValidationError, match="timeout_seconds"):
            PipInstallIsolatedTool().validate({
                "venv_path": "/tmp", "packages": ["x"],
                "timeout_seconds": PIP_INSTALL_TIMEOUT_HARD_CAP + 1,
            })

    def test_invalid_max_log_lines(self):
        with pytest.raises(ToolValidationError, match="max_log_lines"):
            PipInstallIsolatedTool().validate({
                "venv_path": "/tmp", "packages": ["x"], "max_log_lines": 0,
            })

    def test_valid_minimal(self):
        PipInstallIsolatedTool().validate({
            "venv_path": "/tmp/venv", "packages": ["requests"],
        })

    def test_valid_full(self):
        PipInstallIsolatedTool().validate({
            "venv_path": "/tmp/venv",
            "packages": ["requests==2.31.0", "fastapi[standard]>=0.110,<1.0"],
            "upgrade": True,
            "no_deps": False,
            "timeout_seconds": 600,
            "max_log_lines": 500,
        })

    def test_valid_vcs_form(self):
        PipInstallIsolatedTool().validate({
            "venv_path": "/tmp/venv",
            "packages": ["mylib @ git+https://github.com/foo/bar.git"],
        })


# ===========================================================================
# _is_valid_pkg_spec
# ===========================================================================
class TestIsValidPkgSpec:
    @pytest.mark.parametrize("spec", [
        "requests",
        "requests==2.31.0",
        "fastapi>=0.110",
        "pkg-with-dashes",
        "pkg.with.dots",
        "pkg_with_underscores",
        "fastapi[standard]",
        "fastapi[standard,extras]",
        "fastapi[standard]>=0.110,<1.0",
        "numpy~=1.24.0",
        "mylib @ git+https://github.com/foo/bar.git",
        "mylib @ git+https://github.com/foo/bar.git#egg=mylib",
        "mylib @ git+ssh://git@github.com/foo/bar.git@v1.0",
    ])
    def test_valid_specs(self, spec):
        assert _is_valid_pkg_spec(spec) is True

    @pytest.mark.parametrize("spec", [
        "",
        "  ",
        "-r requirements.txt",
        "--upgrade",
        "evil; rm -rf /",
        "evil`whoami`",
        "evil$(echo hi)",
        "evil|cat /etc/passwd",
        "evil>/tmp/x",
    ])
    def test_invalid_specs(self, spec):
        assert _is_valid_pkg_spec(spec) is False


# ===========================================================================
# _locate_venv_python
# ===========================================================================
class TestLocateVenvPython:
    def test_finds_posix_layout(self, tmp_path):
        venv = _make_fake_venv(tmp_path)
        py = _locate_venv_python(venv)
        assert py is not None
        assert py.name == "python"

    def test_returns_none_for_invalid_venv(self, tmp_path):
        d = tmp_path / "not-a-venv"
        d.mkdir()
        assert _locate_venv_python(d) is None


# ===========================================================================
# _parse_pip_output
# ===========================================================================
class TestParsePipOutput:
    def test_installed_line_parsed(self):
        out = "Collecting requests\nSuccessfully installed requests-2.31.0 idna-3.4\n"
        installed, skipped = _parse_pip_output(out)
        assert installed == ["requests-2.31.0", "idna-3.4"]
        assert skipped == []

    def test_already_satisfied_parsed(self):
        out = (
            "Requirement already satisfied: requests in /venv/lib/python3.11/site-packages\n"
            "Requirement already satisfied: idna in /venv/...\n"
        )
        installed, skipped = _parse_pip_output(out)
        assert installed == []
        assert skipped == ["requests", "idna"]

    def test_mixed(self):
        out = (
            "Requirement already satisfied: requests in ...\n"
            "Successfully installed fastapi-0.110.0\n"
        )
        installed, skipped = _parse_pip_output(out)
        assert installed == ["fastapi-0.110.0"]
        assert skipped == ["requests"]

    def test_empty(self):
        assert _parse_pip_output("") == ([], [])


# ===========================================================================
# _cap_log
# ===========================================================================
class TestCapLog:
    def test_under_cap(self):
        text = "a\nb\nc\n"
        capped, trunc = _cap_log(text, 100)
        assert capped == text
        assert trunc is False

    def test_over_cap(self):
        text = "\n".join(f"line{i}" for i in range(20))
        capped, trunc = _cap_log(text, 5)
        assert trunc is True
        assert capped.count("\n") == 4   # 5 lines = 4 newlines

    def test_empty(self):
        assert _cap_log("", 10) == ("", False)


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
# execute()
# ===========================================================================
class TestExecute:
    def test_required_initiative_level_l4(self):
        # Class-level attribute check — confirms governance pipeline
        # will gate this tool at L4 per ADR-0021-am §5.
        assert PipInstallIsolatedTool.required_initiative_level == "L4"
        assert PipInstallIsolatedTool.side_effects == "filesystem"

    def test_successful_install(self, env):
        ctx, venv = env
        ok = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="Collecting requests\nSuccessfully installed requests-2.31.0\n",
            stderr="",
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.pip_install_isolated.subprocess.run",
            return_value=ok,
        ):
            result = _run(PipInstallIsolatedTool().execute({
                "venv_path": str(venv),
                "packages": ["requests"],
            }, ctx))
        assert result.output["exit_code"] == 0
        assert "requests-2.31.0" in result.output["installed"]

    def test_already_satisfied(self, env):
        ctx, venv = env
        ok = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="Requirement already satisfied: requests in ...\n",
            stderr="",
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.pip_install_isolated.subprocess.run",
            return_value=ok,
        ):
            result = _run(PipInstallIsolatedTool().execute({
                "venv_path": str(venv),
                "packages": ["requests"],
            }, ctx))
        assert "requests" in result.output["skipped"]

    def test_failed_install_returns_with_exit_code(self, env):
        ctx, venv = env
        # pip exit 1 means errors but we surface to caller; not a refusal.
        bad = subprocess.CompletedProcess(
            args=[], returncode=1,
            stdout="ERROR: Could not find a version that satisfies the requirement nonexistent-pkg\n",
            stderr="",
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.pip_install_isolated.subprocess.run",
            return_value=bad,
        ):
            result = _run(PipInstallIsolatedTool().execute({
                "venv_path": str(venv),
                "packages": ["nonexistent-pkg"],
            }, ctx))
        assert result.output["exit_code"] == 1
        assert result.output["installed"] == []

    def test_log_truncation(self, env):
        ctx, venv = env
        big_log = "\n".join(f"line {i}" for i in range(1000))
        proc = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=big_log + "\nSuccessfully installed pkg-1.0\n",
            stderr=big_log,
        )
        with mock.patch(
            "forest_soul_forge.tools.builtin.pip_install_isolated.subprocess.run",
            return_value=proc,
        ):
            result = _run(PipInstallIsolatedTool().execute({
                "venv_path": str(venv),
                "packages": ["pkg"],
                "max_log_lines": 50,
            }, ctx))
        assert result.output["stdout_truncated"] is True
        assert result.output["stderr_truncated"] is True

    def _capture_install_argv(self, env, args):
        """Run execute() and return the install-call argv (the first
        subprocess.run, before _detect_pip_version's call)."""
        ctx, venv = env
        ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        all_calls: list[list[str]] = []

        def fake_run(argv, **kw):
            all_calls.append(list(argv))
            return ok

        with mock.patch(
            "forest_soul_forge.tools.builtin.pip_install_isolated.subprocess.run",
            side_effect=fake_run,
        ):
            _run(PipInstallIsolatedTool().execute({
                "venv_path": str(venv), **args,
            }, ctx))
        # First call is the install; subsequent are pip --version detection.
        return all_calls[0]

    def test_upgrade_flag_added(self, env):
        argv = self._capture_install_argv(env, {
            "packages": ["pkg"], "upgrade": True,
        })
        assert "--upgrade" in argv

    def test_no_deps_flag_added(self, env):
        argv = self._capture_install_argv(env, {
            "packages": ["pkg"], "no_deps": True,
        })
        assert "--no-deps" in argv

    def test_packages_after_doubledash(self, env):
        """Packages must come after `--` so pip doesn't try to interpret
        them as flags. Defense against any package spec the validator
        let through that might still confuse pip."""
        argv = self._capture_install_argv(env, {
            "packages": ["requests", "idna"],
        })
        idx = argv.index("--")
        assert argv[idx + 1:] == ["requests", "idna"]

    def test_missing_allowed_paths(self, tmp_path):
        venv = _make_fake_venv(tmp_path)
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s", constraints={},
        )
        with pytest.raises(PipInstallError, match="allowed_paths"):
            _run(PipInstallIsolatedTool().execute({
                "venv_path": str(venv), "packages": ["x"],
            }, ctx))

    def test_outside_allowed(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        venv_outside = _make_fake_venv(tmp_path, name="outside-venv")
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(allowed)]},
        )
        with pytest.raises(PipInstallError, match="outside"):
            _run(PipInstallIsolatedTool().execute({
                "venv_path": str(venv_outside), "packages": ["x"],
            }, ctx))

    def test_nonexistent_venv(self, tmp_path):
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        with pytest.raises(PipInstallError, match="does not exist"):
            _run(PipInstallIsolatedTool().execute({
                "venv_path": str(tmp_path / "nope"), "packages": ["x"],
            }, ctx))

    def test_invalid_venv_structure(self, tmp_path):
        # A directory that's not a venv (no bin/python or Scripts/python.exe)
        d = tmp_path / "fake"
        d.mkdir()
        ctx = ToolContext(
            instance_id="i", agent_dna="d" * 12, role="r", genre="g",
            session_id="s",
            constraints={"allowed_paths": [str(tmp_path)]},
        )
        with pytest.raises(VenvInvalidError):
            _run(PipInstallIsolatedTool().execute({
                "venv_path": str(d), "packages": ["x"],
            }, ctx))

    def test_timeout_refuses(self, env):
        ctx, venv = env
        with mock.patch(
            "forest_soul_forge.tools.builtin.pip_install_isolated.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["pip"], timeout=300),
        ):
            with pytest.raises(PipInstallError, match="timed out"):
                _run(PipInstallIsolatedTool().execute({
                    "venv_path": str(venv), "packages": ["x"],
                }, ctx))

    def test_pip_not_found_refuses(self, env):
        ctx, venv = env
        with mock.patch(
            "forest_soul_forge.tools.builtin.pip_install_isolated.subprocess.run",
            side_effect=FileNotFoundError(2, "No such file"),
        ):
            with pytest.raises(PipNotFoundError):
                _run(PipInstallIsolatedTool().execute({
                    "venv_path": str(venv), "packages": ["x"],
                }, ctx))

    def test_metadata_records(self, env):
        ctx, venv = env
        ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with mock.patch(
            "forest_soul_forge.tools.builtin.pip_install_isolated.subprocess.run",
            return_value=ok,
        ):
            result = _run(PipInstallIsolatedTool().execute({
                "venv_path": str(venv), "packages": ["x"],
            }, ctx))
        assert "venv_python" in result.metadata
        assert result.metadata["upgrade"] is False
        assert result.metadata["argv"][0] == str(venv / "bin" / "python")


# ===========================================================================
# Registration
# ===========================================================================
class TestRegistration:
    def test_tool_registered(self):
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.builtin import register_builtins

        registry = ToolRegistry()
        register_builtins(registry)
        tool = registry.get("pip_install_isolated", "1")
        assert tool is not None
        assert tool.side_effects == "filesystem"
        assert tool.required_initiative_level == "L4"

    def test_catalog_entry_present(self):
        import yaml
        catalog_path = (
            Path(__file__).parent.parent.parent
            / "config" / "tool_catalog.yaml"
        )
        with open(catalog_path) as f:
            catalog = yaml.safe_load(f)
        entry = catalog["tools"]["pip_install_isolated.v1"]
        assert entry["side_effects"] == "filesystem"
        assert entry["required_initiative_level"] == "L4"
        assert "actuator" in entry["archetype_tags"]
