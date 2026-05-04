"""Plugin repository — filesystem operations for ADR-0043 T2.

The repository is the only piece that touches ``~/.forest/plugins/``
disk state. All higher layers (CLI, T3 daemon hot-reload, T4 audit
emit) go through it.

Operations in v0.5 (T2):

  list()                    — enumerate installed + disabled plugins
  load(name)                — read manifest for one
  install_from_dir(src)     — copy a local directory into installed/
  uninstall(name)           — remove an installed/disabled plugin
  enable(name)              — move from disabled/ to installed/
  disable(name)             — move from installed/ to disabled/
  verify_binary(name)       — re-check entry-point sha256 against disk

T3 will add ``walk_for_runtime()`` — a daemon-facing diff against
the live tool catalog. T4 will add audit-emit hooks. Keep those
out of T2 so the layer stays testable without a daemon.
"""
from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from forest_soul_forge.plugins.errors import (
    PluginAlreadyInstalled,
    PluginError,
    PluginNotFound,
    PluginValidationError,
)
from forest_soul_forge.plugins.manifest import PluginManifest, load_manifest
from forest_soul_forge.plugins.paths import (
    PluginDirectories,
    plugin_directories,
)


class PluginState(str, Enum):
    INSTALLED = "installed"
    DISABLED = "disabled"


@dataclass(frozen=True)
class PluginInfo:
    """Summary record for one plugin. CLI's ``fsf plugin list``
    formats these into a table; daemon endpoints (T3) serialize
    them to JSON."""

    name: str
    state: PluginState
    manifest: PluginManifest
    directory: Path

    def display_label(self) -> str:
        return self.manifest.display_label()


class PluginRepository:
    """Filesystem layer over ``~/.forest/plugins/``.

    Construct once per CLI invocation or per daemon lifespan. The
    repository materializes its directory structure on first use —
    no separate ``init()`` needed; calling any method on a fresh
    plugin root creates ``installed/`` + ``disabled/`` + ``secrets/``
    automatically.
    """

    def __init__(self, root: Path | None = None) -> None:
        self._dirs: PluginDirectories = plugin_directories(root)
        self._ensure_layout()

    @property
    def directories(self) -> PluginDirectories:
        return self._dirs

    # ---- layout ----------------------------------------------------

    def _ensure_layout(self) -> None:
        """Idempotent. Creates root + installed/ + disabled/ +
        secrets/. registry-cache.json is not pre-created — it lands
        on first registry fetch (T5)."""
        self._dirs.installed.mkdir(parents=True, exist_ok=True)
        self._dirs.disabled.mkdir(parents=True, exist_ok=True)
        self._dirs.secrets.mkdir(parents=True, exist_ok=True)

    def _dir_for(self, state: PluginState) -> Path:
        return (
            self._dirs.installed if state == PluginState.INSTALLED
            else self._dirs.disabled
        )

    def _resolve(self, name: str) -> tuple[Path, PluginState]:
        """Return (directory, state) for ``name`` if installed or
        disabled. Raises :class:`PluginNotFound` otherwise.

        ``installed`` wins if a plugin somehow exists in both
        directories (operator surgery gone wrong); the conflict is
        not silently masked — :meth:`list` reports it as a separate
        warning surface in T3.
        """
        installed = self._dirs.installed / name
        disabled = self._dirs.disabled / name
        if installed.is_dir():
            return installed, PluginState.INSTALLED
        if disabled.is_dir():
            return disabled, PluginState.DISABLED
        raise PluginNotFound(f"no plugin named {name!r}")

    # ---- read ------------------------------------------------------

    def list(self) -> list[PluginInfo]:
        """Enumerate every plugin across installed/ + disabled/.

        Plugins that fail manifest validation are SKIPPED with a
        log message — operators shouldn't have one bad plugin
        block all the others. The CLI's ``fsf plugin verify``
        surface (T4) re-walks and explicitly reports failures.
        """
        out: list[PluginInfo] = []
        for state in (PluginState.INSTALLED, PluginState.DISABLED):
            base = self._dir_for(state)
            for entry in sorted(base.iterdir()):
                if not entry.is_dir():
                    continue
                manifest_path = entry / "plugin.yaml"
                try:
                    manifest = load_manifest(manifest_path)
                except PluginValidationError:
                    # Log + skip; don't break the whole listing.
                    continue
                out.append(PluginInfo(
                    name=manifest.name,
                    state=state,
                    manifest=manifest,
                    directory=entry,
                ))
        return out

    def load(self, name: str) -> PluginInfo:
        """Get one plugin's info. Raises PluginNotFound if missing."""
        directory, state = self._resolve(name)
        manifest = load_manifest(directory / "plugin.yaml")
        return PluginInfo(
            name=manifest.name,
            state=state,
            manifest=manifest,
            directory=directory,
        )

    # ---- write -----------------------------------------------------

    def install_from_dir(
        self,
        src: Path,
        *,
        force: bool = False,
    ) -> PluginInfo:
        """Copy a local directory containing a plugin.yaml into
        ``installed/<plugin-name>/``.

        v0.5 / T2 only supports installing from a local directory.
        Registry-from-Git lands in T5.

        :param src: Directory holding the plugin's plugin.yaml +
            entry-point binary.
        :param force: If True, overwrite an existing plugin at the
            target path. Default False raises PluginAlreadyInstalled.
        :raises PluginValidationError: source has no plugin.yaml or
            its manifest fails validation.
        :raises PluginAlreadyInstalled: target exists and force=False.
        """
        if not src.is_dir():
            raise PluginValidationError(f"source is not a directory: {src}")
        manifest = load_manifest(src / "plugin.yaml")
        target = self._dirs.installed / manifest.name
        disabled_target = self._dirs.disabled / manifest.name
        if target.exists() or disabled_target.exists():
            if not force:
                raise PluginAlreadyInstalled(
                    f"plugin {manifest.name!r} already exists at "
                    f"{target if target.exists() else disabled_target}; "
                    "pass force=True to overwrite"
                )
            if target.exists():
                shutil.rmtree(target)
            if disabled_target.exists():
                shutil.rmtree(disabled_target)
        shutil.copytree(src, target, symlinks=False, ignore_dangling_symlinks=True)
        return self.load(manifest.name)

    def uninstall(self, name: str) -> PluginInfo:
        """Remove a plugin (installed or disabled). Returns the
        info from before the removal so callers can log details."""
        info = self.load(name)
        shutil.rmtree(info.directory)
        return info

    def enable(self, name: str) -> PluginInfo:
        """Move from disabled/ to installed/. No-op if already
        installed. Raises PluginNotFound if neither."""
        directory, state = self._resolve(name)
        if state == PluginState.INSTALLED:
            return self.load(name)
        target = self._dirs.installed / name
        if target.exists():
            # Shouldn't happen per _resolve precedence, but defensive.
            raise PluginError(
                f"both installed/ and disabled/ contain {name!r}; "
                "operator must resolve manually"
            )
        directory.rename(target)
        return self.load(name)

    def disable(self, name: str) -> PluginInfo:
        """Move from installed/ to disabled/. No-op if already
        disabled. Raises PluginNotFound if neither."""
        directory, state = self._resolve(name)
        if state == PluginState.DISABLED:
            return self.load(name)
        target = self._dirs.disabled / name
        if target.exists():
            raise PluginError(
                f"both installed/ and disabled/ contain {name!r}; "
                "operator must resolve manually"
            )
        directory.rename(target)
        return self.load(name)

    # ---- verify ----------------------------------------------------

    def verify_binary(self, name: str) -> bool:
        """Re-compute the entry-point binary's sha256 and compare
        against the manifest's pinned value.

        Returns True if they match; False if not. Does NOT raise on
        mismatch — callers (CLI / T3 hot-reload) want to handle
        mismatches in their own way (warn, skip, refuse).

        Raises PluginNotFound if the plugin doesn't exist or
        PluginValidationError if the entry-point file is missing
        from the install directory.
        """
        info = self.load(name)
        binary_path = self._resolve_entry_point(info)
        if not binary_path.exists():
            raise PluginValidationError(
                f"plugin {name!r}: entry_point.command resolves to "
                f"{binary_path}, which does not exist"
            )
        actual = _sha256_file(binary_path)
        return actual == info.manifest.entry_point.sha256

    def _resolve_entry_point(self, info: PluginInfo) -> Path:
        """Resolve the manifest's entry_point.command relative to
        the plugin directory. Absolute paths are kept as-is."""
        cmd = info.manifest.entry_point.command
        path = Path(cmd)
        if path.is_absolute():
            return path
        return (info.directory / cmd).resolve()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
