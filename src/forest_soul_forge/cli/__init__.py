"""Forest Soul Forge command-line interface.

Single ``fsf`` entry point declared in pyproject.toml's
``[project.scripts]``. Subcommands hang off ``cli.main``:

    fsf forge tool "describe a tool"        # ADR-0030 Tool Forge
    fsf forge skill "describe a skill"      # ADR-0031 Skill Forge (future)

The CLI is intentionally thin — each subcommand delegates to a module
that does the real work, keeping ``main.py`` to argument routing.
"""
