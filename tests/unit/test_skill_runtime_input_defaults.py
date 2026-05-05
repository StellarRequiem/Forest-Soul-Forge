"""Burst 133 — JSONSchema input defaults at runtime in the skill engine.

Tests the ``_apply_schema_defaults`` helper added to ``skill_runtime``
in Burst 133, closing the long-standing gap documented in STATE.md's
'Items in queue' for the v0.6 kernel arc.

Pre-fix: declared defaults in skill manifest input schemas weren't
applied at runtime; manifest authors hard-coded values inline or
required operators to pass every input explicitly.

Post-fix: operator-supplied values always win; defaults fill only
for keys the operator omitted.
"""
from __future__ import annotations

from forest_soul_forge.forge.skill_runtime import _apply_schema_defaults


def test_empty_schema_returns_inputs_unchanged() -> None:
    """No properties in schema → inputs pass through."""
    inputs = {"a": 1, "b": "two"}
    result = _apply_schema_defaults(inputs, {"type": "object"})
    assert result == inputs
    # Defensive copy — caller shouldn't observe side-effects.
    assert result is not inputs


def test_default_filled_for_missing_key() -> None:
    """A schema-declared default fills in if operator omitted the key."""
    schema = {
        "type": "object",
        "properties": {
            "threshold": {"type": "integer", "default": 5},
        },
    }
    result = _apply_schema_defaults({}, schema)
    assert result == {"threshold": 5}


def test_operator_value_wins_over_default() -> None:
    """If the operator passed a value, the default does NOT overwrite."""
    schema = {
        "type": "object",
        "properties": {
            "threshold": {"type": "integer", "default": 5},
        },
    }
    result = _apply_schema_defaults({"threshold": 99}, schema)
    assert result == {"threshold": 99}


def test_falsy_operator_value_still_wins() -> None:
    """Operator-passed 0 / "" / [] / None / False all suppress the default.

    'Operator passed it' is keyed on presence in the dict, not truthiness.
    Otherwise threshold=0 (a perfectly valid integer choice) would silently
    get overwritten by the default — which is exactly the bug we're
    fixing in this burst, just at a different layer.
    """
    schema = {
        "type": "object",
        "properties": {
            "threshold": {"type": "integer", "default": 5},
            "label": {"type": "string", "default": "default-label"},
            "tags": {"type": "array", "default": ["a", "b"]},
            "config": {"type": "object", "default": {"k": "v"}},
            "enabled": {"type": "boolean", "default": True},
        },
    }
    result = _apply_schema_defaults(
        {"threshold": 0, "label": "", "tags": [], "config": {}, "enabled": False},
        schema,
    )
    assert result == {
        "threshold": 0,
        "label": "",
        "tags": [],
        "config": {},
        "enabled": False,
    }


def test_partial_fill() -> None:
    """Operator passes some keys; defaults fill the rest."""
    schema = {
        "type": "object",
        "properties": {
            "threshold": {"type": "integer", "default": 5},
            "window_minutes": {"type": "integer", "default": 60},
            "label": {"type": "string", "default": "default"},
        },
    }
    result = _apply_schema_defaults({"threshold": 10}, schema)
    assert result == {"threshold": 10, "window_minutes": 60, "label": "default"}


def test_property_without_default_skipped() -> None:
    """Properties without a 'default' key don't appear in the result."""
    schema = {
        "type": "object",
        "properties": {
            "threshold": {"type": "integer", "default": 5},
            "must_be_provided": {"type": "string"},
        },
    }
    result = _apply_schema_defaults({}, schema)
    # Only threshold gets filled; must_be_provided remains unset.
    assert result == {"threshold": 5}


def test_complex_default_value() -> None:
    """JSON Schema permits any JSON value as default — pass through."""
    schema = {
        "type": "object",
        "properties": {
            "complex": {
                "type": "object",
                "default": {"nested": {"deeply": [1, 2, 3]}},
            },
        },
    }
    result = _apply_schema_defaults({}, schema)
    assert result == {"complex": {"nested": {"deeply": [1, 2, 3]}}}


def test_non_dict_schema_returns_inputs_copy() -> None:
    """Defensive: if inputs_schema is not a dict, return a copy of inputs."""
    inputs = {"x": 1}
    result = _apply_schema_defaults(inputs, "not-a-dict")  # type: ignore[arg-type]
    assert result == {"x": 1}
    assert result is not inputs


def test_no_properties_key() -> None:
    """Schema with type but no properties → inputs unchanged."""
    inputs = {"a": 1}
    result = _apply_schema_defaults(inputs, {"type": "object"})
    assert result == inputs


def test_properties_not_dict() -> None:
    """Defensive: if 'properties' is not a dict, no defaults apply."""
    inputs = {"a": 1}
    result = _apply_schema_defaults(inputs, {"properties": "broken"})
    assert result == inputs


def test_property_schema_not_dict() -> None:
    """Defensive: if a property's schema is not a dict, skip it."""
    schema = {
        "type": "object",
        "properties": {
            "good": {"default": 5},
            "bad": "not-a-dict-schema",
        },
    }
    result = _apply_schema_defaults({}, schema)
    assert result == {"good": 5}
