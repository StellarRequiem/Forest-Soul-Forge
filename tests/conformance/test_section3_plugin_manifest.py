"""Conformance §3 — Plugin manifest schema v1.

Spec: docs/spec/kernel-api-v0.6.md §3.
"""
from __future__ import annotations

import re

import httpx


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
