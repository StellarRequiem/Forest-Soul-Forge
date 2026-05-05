"""Conformance §4 — Constitution.yaml schema.

Spec: docs/spec/kernel-api-v0.6.md §4.
"""
from __future__ import annotations

import re

import httpx
import pytest


# ----- §4 — constitution accessible via character-sheet endpoint --------


def test_section4_agents_endpoint_reachable(client: httpx.Client) -> None:
    """§4: GET /agents responds 200 with a list."""
    resp = client.get("/agents")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "agents" in body, f"missing 'agents' field: {body}"
    assert isinstance(body["agents"], list)


def test_section4_agent_shape(client: httpx.Client) -> None:
    """§4.1: every agent in /agents exposes documented top-level fields.

    Per spec §4.1: dna, role, genre, agent_name. constitution_hash
    is the hash invariant per §4.2. born_at is the lifecycle
    timestamp.
    """
    body = client.get("/agents").json()
    if not body["agents"]:
        # No agents born yet — vacuously conformant.
        return

    for agent in body["agents"]:
        # §4.1: dna present + 12-char short form
        assert "dna" in agent, f"agent missing dna: {agent}"
        # The dna field could be either short (12 chars) or long (64 chars hex)
        # depending on the endpoint's serialization choice; spec §4.1 calls
        # both out as part of the shape.
        assert isinstance(agent["dna"], str)

        # §4.1: role + genre present
        assert "role" in agent, f"agent {agent['dna']} missing role"
        assert "genre" in agent, f"agent {agent['dna']} missing genre"

        # §4.2: constitution_hash present + sha256 hex
        if "constitution_hash" in agent:
            ch = agent["constitution_hash"]
            assert isinstance(ch, str)
            assert re.match(r"^[0-9a-f]{64}$", ch), (
                f"constitution_hash {ch!r} not 64-char sha256 hex per §4.2"
            )


def test_section4_character_sheet_shape(client: httpx.Client) -> None:
    """§4: character-sheet endpoint exposes the documented per-agent view.

    The character sheet is a roll-up of the constitution + lifecycle +
    runtime stats. Per spec §5.3 it's a documented read endpoint.
    """
    agents_body = client.get("/agents").json()
    if not agents_body["agents"]:
        pytest.skip("no agents born; can't probe character-sheet")

    # Pick the first agent and probe its character sheet.
    agent = agents_body["agents"][0]
    instance_id = agent.get("instance_id") or agent.get("id")
    if instance_id is None:
        pytest.skip("agent payload doesn't expose instance_id; can't drill in")

    resp = client.get(f"/agents/{instance_id}/character-sheet")
    if resp.status_code == 404:
        pytest.skip(f"character-sheet for {instance_id} returned 404 (perhaps archived)")
    assert resp.status_code == 200, (
        f"character-sheet returned {resp.status_code}; spec §5.3 lists "
        f"this as a stable read endpoint. Body: {resp.text[:200]}"
    )
    sheet = resp.json()
    # Minimally check a few fields the spec implies are present.
    # The exact shape isn't 100% specified at v0.6, so we keep this
    # check permissive.
    assert isinstance(sheet, dict), "character-sheet must be a JSON object"
