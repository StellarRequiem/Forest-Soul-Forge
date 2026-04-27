"""Skill catalog loader — ADR-0031 T5.

Walks the configured ``skill_install_dir`` at lifespan, loads each
``<name>.v<version>.yaml`` manifest, validates via
``skill_manifest.parse_manifest``, and exposes the resulting
:class:`SkillCatalog` on ``app.state.skill_catalog``.

The catalog is read-only at runtime. T7 will add an install path
that mutates it; until then operators move staged manifests into
the install dir manually.

Failure mode: a malformed manifest in install_dir is logged as a
startup_diagnostic + skipped (other skills still load). Empty
catalog (no manifests, no install_dir) is benign — daemon stays
up; ``GET /skills`` returns ``count=0``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from forest_soul_forge.forge.skill_manifest import (
    ManifestError,
    SkillDef,
    parse_manifest,
)


@dataclass(frozen=True)
class SkillCatalog:
    """Loaded skill manifests, indexed by ``name.v<version>``.

    Mirrors the shape of ``ToolCatalog`` so endpoints can use the
    same access patterns.
    """

    skills: dict[str, SkillDef]
    source_dir: Path | None = None

    @property
    def count(self) -> int:
        return len(self.skills)


def empty_catalog() -> SkillCatalog:
    return SkillCatalog(skills={}, source_dir=None)


def load_catalog(install_dir: Path) -> tuple[SkillCatalog, list[str]]:
    """Load every ``*.yaml`` under ``install_dir``.

    Returns (catalog, errors). ``errors`` is a list of operator-facing
    strings — each malformed manifest gets one, plus any directory
    issues. The catalog contains only the manifests that loaded
    cleanly so the daemon stays usable when one skill is broken.
    """
    errors: list[str] = []
    if not install_dir.exists():
        return SkillCatalog(skills={}, source_dir=install_dir), errors
    if not install_dir.is_dir():
        errors.append(f"skill install dir is not a directory: {install_dir}")
        return SkillCatalog(skills={}, source_dir=install_dir), errors

    skills: dict[str, SkillDef] = {}
    for path in sorted(install_dir.glob("*.yaml")):
        try:
            text = path.read_text(encoding="utf-8")
            sd = parse_manifest(text)
        except ManifestError as e:
            errors.append(f"{path.name}: {e.path}: {e.detail}")
            continue
        except OSError as e:
            errors.append(f"{path.name}: {type(e).__name__}: {e}")
            continue
        key = f"{sd.name}.v{sd.version}"
        if key in skills:
            errors.append(
                f"{path.name}: duplicate {key} (also at "
                f"{install_dir / (skills[key].name + '.v' + skills[key].version + '.yaml')})"
            )
            continue
        skills[key] = sd
    return SkillCatalog(skills=skills, source_dir=install_dir), errors
