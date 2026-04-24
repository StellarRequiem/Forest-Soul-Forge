#!/usr/bin/env python3
"""Minimal stdlib-only test runner.

The project's real tests are pytest-based (tests/unit/*). The sandbox can't
install pytest, so this script replays the same assertions in plain Python.
It runs every top-level pytest.* decorated method by introspection:
  - pytest fixtures are resolved by name → we inject the engine / generator
    instances manually.
  - @pytest.mark.parametrize is flattened by eval'ing the argvalues list.
  - pytest.raises / pytest.approx are implemented as shims.

This is not a replacement for pytest. It's a CI-lite smoke check so we can
catch regressions before the user runs `pytest` on their own machine.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
import traceback
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))


# ------- pytest shim -----------------------------------------------------
class _ApproxShim:
    def __init__(self, expected: float, rel: float = 1e-6):
        self.expected = expected
        self.rel = rel

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, (int, float)):
            return NotImplemented
        if self.expected == 0:
            return abs(other) <= 1e-9
        return abs(other - self.expected) / abs(self.expected) <= self.rel

    def __repr__(self) -> str:
        return f"approx({self.expected})"


class _SkipModule(Exception):
    """Signal that the current test module should be skipped wholesale.

    Emitted by :meth:`_PytestShim.importorskip` when a required dependency
    (e.g. FastAPI) isn't installed in the sandbox.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _PytestShim(ModuleType):
    def __init__(self) -> None:
        super().__init__("pytest")
        self.fixture = self._fixture
        self.mark = self._Mark()

    @staticmethod
    def _fixture(*args, **kwargs):
        def decorator(fn):
            fn._is_fixture = True
            return fn

        if args and callable(args[0]):
            return decorator(args[0])
        return decorator

    class _Mark:
        @staticmethod
        def parametrize(argnames, argvalues):
            def decorator(fn):
                fn._parametrize = (argnames, list(argvalues))
                return fn
            return decorator

    @staticmethod
    def approx(expected, rel: float = 1e-6) -> _ApproxShim:
        return _ApproxShim(expected, rel)

    @staticmethod
    @contextmanager
    def raises(exc_type, match: str | None = None):
        try:
            yield
        except exc_type as e:
            if match is not None and match not in str(e):
                raise AssertionError(
                    f"Exception {exc_type.__name__} raised but '{match}' not in message: {e}"
                )
            return
        raise AssertionError(f"Expected {exc_type.__name__} to be raised")

    @staticmethod
    def importorskip(modname: str, reason: str | None = None):
        try:
            return importlib.import_module(modname)
        except ImportError as e:
            raise _SkipModule(
                reason or f"requires {modname} (not installed in sandbox): {e}"
            ) from e


sys.modules["pytest"] = _PytestShim()


# ------- runner ----------------------------------------------------------
def _load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build_fixtures(mod: ModuleType, *, fresh_tmp_path: bool = False) -> dict[str, object]:
    # Built-in pytest fixtures the harness provides itself.
    # pytest gives every test its own tmp_path; we mirror that when
    # ``fresh_tmp_path`` is true. At module scan time we build once to resolve
    # user fixtures with stable engines, then regenerate tmp_path per test.
    import tempfile
    fixtures: dict[str, object] = {
        "tmp_path": Path(tempfile.mkdtemp(prefix="fsf_test_")),
    }
    if fresh_tmp_path:
        return fixtures
    # Resolve user fixtures in dependency order. Simple: iterate until nothing new.
    remaining = {
        name: obj
        for name, obj in vars(mod).items()
        if callable(obj) and getattr(obj, "_is_fixture", False)
    }
    while remaining:
        progress = False
        for name in list(remaining):
            fn = remaining[name]
            argnames = fn.__code__.co_varnames[: fn.__code__.co_argcount]
            if all(a in fixtures for a in argnames):
                fixtures[name] = fn(*[fixtures[a] for a in argnames])
                del remaining[name]
                progress = True
        if not progress:
            # The stdlib harness doesn't simulate every pytest fixture
            # (e.g. monkeypatch, TestClient lifecycle). Rather than crash
            # the whole run, report the module as skipped — pytest will
            # cover it when the [daemon] extra is installed.
            raise _SkipModule(
                "requires pytest fixtures the stdlib harness can't simulate: "
                f"{sorted(remaining)}"
            )
    return fixtures


def _run_method(cls_obj, method_name: str, method, fixtures: dict[str, object]) -> list[str]:
    """Run a method, possibly parametrized. Returns a list of test IDs that ran."""
    test_ids: list[str] = []
    argnames = list(method.__code__.co_varnames[: method.__code__.co_argcount])
    if argnames and argnames[0] == "self":
        argnames = argnames[1:]

    # Fresh tmp_path per invocation mirrors pytest semantics: tests that
    # write to tmp_path / "chain.jsonl" must not see state from previous tests.
    if "tmp_path" in argnames:
        import tempfile
        fixtures = dict(fixtures)
        fixtures["tmp_path"] = Path(tempfile.mkdtemp(prefix="fsf_test_"))

    param_spec = getattr(method, "_parametrize", None)
    if param_spec is None:
        # No parametrize: resolve args from fixtures.
        kwargs = {a: fixtures[a] for a in argnames if a in fixtures}
        method(cls_obj, **kwargs)
        test_ids.append(method_name)
    else:
        pnames_str, pvalues = param_spec
        pnames = [n.strip() for n in pnames_str.split(",")]
        for i, pvals in enumerate(pvalues):
            if not isinstance(pvals, tuple):
                pvals = (pvals,)
            kwargs = {n: v for n, v in zip(pnames, pvals)}
            for a in argnames:
                if a not in kwargs and a in fixtures:
                    kwargs[a] = fixtures[a]
            method(cls_obj, **kwargs)
            test_ids.append(f"{method_name}[{i}]")
    return test_ids


def run_module(path: Path) -> tuple[int, int, list[str], str | None]:
    """Return ``(passed, failed, failure_names, skip_reason_or_None)``."""
    try:
        mod = _load_module(path)
        fixtures = _build_fixtures(mod)
    except _SkipModule as e:
        return 0, 0, [], e.reason

    passed = 0
    failed_names: list[str] = []

    test_classes = [
        (name, cls) for name, cls in vars(mod).items()
        if isinstance(cls, type) and name.startswith("Test")
    ]
    # Module-level test functions too.
    test_funcs = [
        (name, fn) for name, fn in vars(mod).items()
        if callable(fn) and name.startswith("test_") and not getattr(fn, "_is_fixture", False)
    ]

    for cls_name, cls in test_classes:
        instance = cls()
        for name in dir(cls):
            if not name.startswith("test_"):
                continue
            method = getattr(cls, name)
            try:
                ids = _run_method(instance, name, method, fixtures)
                for tid in ids:
                    passed += 1
                    # print(f"  PASS  {cls_name}::{tid}")
            except Exception:
                failed_names.append(f"{path.name}::{cls_name}::{name}")
                print(f"  FAIL  {cls_name}::{name}")
                traceback.print_exc()

    for name, fn in test_funcs:
        try:
            _run_method(None, name, fn, fixtures)
            passed += 1
        except Exception:
            failed_names.append(f"{path.name}::{name}")
            print(f"  FAIL  {name}")
            traceback.print_exc()

    return passed, len(failed_names), failed_names, None


def main() -> int:
    tests_dir = REPO_ROOT / "tests" / "unit"
    test_files = sorted(tests_dir.glob("test_*.py"))
    total_pass = 0
    total_fail = 0
    total_skipped_modules = 0
    all_failed: list[str] = []
    for f in test_files:
        print(f"--- {f.relative_to(REPO_ROOT)} ---")
        p, fc, failed, skip_reason = run_module(f)
        if skip_reason is not None:
            total_skipped_modules += 1
            print(f"  SKIPPED: {skip_reason}")
            continue
        total_pass += p
        total_fail += fc
        all_failed.extend(failed)
    print()
    print(
        f"Passed: {total_pass}   Failed: {total_fail}   "
        f"Skipped modules: {total_skipped_modules}"
    )
    if all_failed:
        print("Failures:")
        for f in all_failed:
            print(f"  {f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
