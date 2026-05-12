"""Shared integration-test fixtures (Burst 236).

Closes the X-FSF-Token gate gap the B206 fixture migration left open.

## Backstory

B148 (T25 security hardening) added auto-token-generation: if
``FSF_API_TOKEN`` is unset at first boot, the daemon generates a
random token, writes it to ``.env``, and uses it. Every write
endpoint then requires the matching token via the ``X-FSF-Token``
header (see ``daemon/deps.py::require_api_token``).

B206 migrated the **unit-test** fixtures to bypass this by passing
``api_token=None`` explicitly to every ``DaemonSettings(...)``
constructor, overriding whatever's in ``.env``. 62 failing unit
tests → 0.

The **integration tests** were not migrated. ``DaemonSettings`` is
a ``pydantic-settings`` class configured with
``SettingsConfigDict(env_file=".env", ...)``, which reads the
repo-root ``.env`` directly from disk on every instantiation. The
operator's live token lands in ``DaemonSettings.api_token`` and
the integration HTTP clients (TestClient, no header) hit 401.

Note: ``os.environ`` manipulation alone (``monkeypatch.delenv``)
does NOT close the gap because pydantic-settings reads ``.env``
file content independently of the process env. The fix has to
target either the ``env_file`` config or the construction path.

## The fix

Session-scoped autouse fixture that **rebinds**
``DaemonSettings.model_config["env_file"]`` to a nonexistent path
inside a tmp directory. Pydantic-settings silently skips a missing
env_file, so ``api_token`` falls back to its
``Field(default=None)`` — which the auth gate at
``daemon/deps.py::require_api_token`` treats as "auth disabled,
pass through."

The fixture also clears ``FSF_API_TOKEN`` from ``os.environ`` (in
case the operator's shell happens to export it) and sets
``FSF_INSECURE_NO_TOKEN=true`` as a belt-and-suspenders signal in
case some future code path consults ``insecure_no_token``
directly rather than checking ``api_token is None``.

Session scope keeps cost minimal; ``autouse`` keeps future
integration tests honest without each test needing to remember to
pass ``api_token=None``.

A future integration test that specifically wants to exercise the
auth path (asserting that 401 fires) can override locally by
setting ``api_token=...`` on its own ``DaemonSettings`` (the init
arg wins over both env and env_file).
"""
from __future__ import annotations

import pytest

from forest_soul_forge.daemon.config import DaemonSettings


@pytest.fixture(scope="session", autouse=True)
def _disable_b148_token_for_integration(tmp_path_factory):
    """Strip the B148 auto-token so integration tests can hit
    write endpoints without an ``X-FSF-Token`` header.

    Rebinds ``DaemonSettings.model_config['env_file']`` to a
    nonexistent path for the session. Mirrors the B206 unit-fixture
    fix, applied centrally so the four integration files don't
    each need to remember to pass ``api_token=None``.
    """
    monkeypatch = pytest.MonkeyPatch()

    # 1. Belt: scrub the live env (defense if the operator's shell
    #    exports FSF_API_TOKEN).
    monkeypatch.delenv("FSF_API_TOKEN", raising=False)
    monkeypatch.setenv("FSF_INSECURE_NO_TOKEN", "true")

    # 2. Suspenders: point pydantic-settings at a nonexistent env
    #    file so the repo-root .env content cannot leak into
    #    DaemonSettings.api_token. Pydantic-settings silently skips
    #    a missing env_file.
    empty_dir = tmp_path_factory.mktemp("integration-no-env")
    nonexistent_env = empty_dir / ".env"  # never created

    new_config = dict(DaemonSettings.model_config)
    new_config["env_file"] = str(nonexistent_env)
    monkeypatch.setattr(DaemonSettings, "model_config", new_config)

    try:
        yield
    finally:
        monkeypatch.undo()
