"""Conformance §3 — Plugin manifest schema v1.

Spec: docs/spec/kernel-api-v0.6.md §3.

Two layers of testing:
  1. Live daemon's /plugins endpoint exposes installed plugins with
     the documented shape.
  2. Sample manifests in fixtures/plugin_manifests/ validate against
     the canonical JSON Schema. Valid samples must pass; invalid
     samples must fail with the documented field violations. This
     catches drift between the spec text and the schema fixture.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import jsonschema
import pytest
import yaml


_FIXTURES = Path(__file__).parent / "fixtures"
_SCHEMA = json.loads((_FIXTURES / "plugin_manifest_v1.schema.json").read_text())
_VALID_DIR = _FIXTURES / "plugin_manifests"


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


# ----- §3 — plugin endpoint reachable + per-plugin shape ----------------


def test_section3_plugins_endpoint_reachable(client: httpx.Client) -> None:
    """§3: GET /plugins responds 200 with a documented body shape.

    Per spec §5.3, this is a read endpoint. The response contains the
    list of installed plugins (may be empty on a fresh install).
    """
    resp = client.get("/plugins")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "plugins" in body, f"response missing 'plugins' field: {body}"
    assert isinstance(body["plugins"], list)


def test_section3_per_plugin_shape(client: httpx.Client) -> None:
    """§3.1: each installed plugin exposes the documented top-level fields.

    Per spec §3.1: schema_version, name, version, type, side_effects,
    trust_tier are all part of the v1 manifest contract. The /plugins
    endpoint surfaces them.
    """
    body = client.get("/plugins").json()
    if not body["plugins"]:
        # No plugins installed — vacuously conformant.
        return

    schema_version_seen_one = False
    side_effects_allowed = {"read_only", "network", "filesystem", "external"}

    for plugin in body["plugins"]:
        # §3.2: name regex
        name = plugin.get("name", "")
        assert re.match(r"^[a-z][a-z0-9-]{1,62}[a-z0-9]$", name), (
            f"plugin name {name!r} doesn't match spec §3.2 regex"
        )

        # §3.1: version is a string
        assert "version" in plugin, f"plugin {name} missing version"
        assert isinstance(plugin["version"], str)

        # §3.1: side_effects in allowed set
        assert plugin["side_effects"] in side_effects_allowed, (
            f"plugin {name} has invalid side_effects {plugin['side_effects']!r}"
        )

        # §3.2: schema_version must equal 1 at v0.6
        sv = plugin.get("schema_version")
        if sv == 1:
            schema_version_seen_one = True
        else:
            # Future schema_version 2 is reserved per spec §3.2;
            # at v0.6, all plugins must report schema_version 1.
            raise AssertionError(
                f"plugin {name} reports schema_version {sv!r}; "
                f"v0.6 spec requires 1 (§3.2)."
            )

        # §3.3: trust_tier is int 0-5 if present
        if "trust_tier" in plugin:
            tier = plugin["trust_tier"]
            assert isinstance(tier, int)
            assert 0 <= tier <= 5, (
                f"plugin {name} trust_tier={tier} out of range [0,5] per §3.2"
            )

    assert schema_version_seen_one, (
        "at least one installed plugin should report schema_version 1 at v0.6"
    )


# ----- §3 schema validation against fixtures ----------------------------
#
# These tests validate sample manifests against the canonical JSON Schema
# (fixtures/plugin_manifest_v1.schema.json). They run without the daemon
# — useful for kernel builds that don't yet have a daemon endpoint
# but want to verify their manifest validator's behavior.


@pytest.mark.parametrize(
    "fixture_name",
    ["valid_minimal.yaml", "valid_full.yaml"],
)
def test_section3_valid_manifest_passes_schema(fixture_name: str) -> None:
    """§3.1-§3.3: documented-valid manifests pass JSON Schema validation."""
    manifest = _load_yaml(_VALID_DIR / fixture_name)
    # Should not raise — if it does, the fixture or the schema is wrong.
    jsonschema.validate(manifest, _SCHEMA)


@pytest.mark.parametrize(
    "fixture_name,expected_field",
    [
        ("invalid_schema_version.yaml", "schema_version"),
        ("invalid_name_uppercase.yaml", "name"),
        ("invalid_bad_semver.yaml", "version"),
        ("invalid_missing_sha256.yaml", "entry_point"),
        ("invalid_bad_side_effects.yaml", "side_effects"),
    ],
)
def test_section3_invalid_manifest_fails_schema(
    fixture_name: str, expected_field: str
) -> None:
    """§3.2-§3.3: each documented violation rule actually rejects.

    For every documented constraint in the spec, we have one fixture
    that violates it and assert the schema validator catches it.
    Drift between spec text and schema implementation surfaces here.
    """
    manifest = _load_yaml(_VALID_DIR / fixture_name)
    with pytest.raises(jsonschema.ValidationError) as exc_info:
        jsonschema.validate(manifest, _SCHEMA)
    # The error path or message should reference the violated field.
    err = exc_info.value
    err_str = str(err).lower() + " " + " ".join(str(p) for p in err.absolute_path)
    assert expected_field.lower() in err_str, (
        f"expected violation to reference field {expected_field!r}; got: {err.message}"
    )
