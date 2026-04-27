"""Security module — privileged-ops client + future swarm primitives.

ADR-0033 A6 lives here as ``priv_client``: the daemon-side wrapper
around the ``/usr/local/sbin/fsf-priv`` helper script. Tools that
need privileged operations (Phase B's ``isolate_process.v1``,
``dynamic_policy.v1``, ``tamper_detect.v1`` SIP path) reach for
``PrivClient`` rather than shelling out themselves, so the
allowlist + audit shape lives in one place.
"""
