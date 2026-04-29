# Scenario: fresh-forge

Empty registry, no audit chain, no soul artifacts. The launcher creates
the directory scaffolding so the daemon can boot and write the genesis
event itself on first start.

Best for: hands-on demos where the audience drives the Forge tab from
scratch. See the [presenter script](../scripts/fresh-forge.md).

This scenario contains no `audit_chain.jsonl` or `registry.sqlite` —
the daemon initializes both on first boot. The empty `data/`
subdirectories survive git via `.gitkeep` files.
