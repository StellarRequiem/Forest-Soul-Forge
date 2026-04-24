"""FastAPI daemon for the Forest Soul Forge.

Design: ADR-0007 (daemon as frontend backend) + ADR-0008 (local-first
model provider). Canonical artifacts on disk are authoritative; the
daemon serves a SQLite index over them.

Import style: module-level imports here would fail if the ``[daemon]``
extra isn't installed (FastAPI / pydantic-settings). Callers that need
the daemon import ``forest_soul_forge.daemon.app`` directly — the
failure is then localized and clear.
"""
