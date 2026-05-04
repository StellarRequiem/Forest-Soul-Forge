# Forest Soul Forge — desktop icons

Tauri's `bundle.icon` config (in `../tauri.conf.json`) expects:

- `32x32.png` — small icon for window/taskbar
- `128x128.png` — medium icon
- `128x128@2x.png` — retina medium icon
- `icon.icns` — macOS bundle icon
- `icon.ico` — Windows installer icon

These are **placeholders** for v0.5 dev builds. Before shipping a
public release (T5 / Burst 102-103), generate real branded icons
via Tauri's icon generator:

```bash
# from apps/desktop/
cargo tauri icon path/to/source-icon-1024x1024.png
```

That writes all five formats from one source PNG. Recommended
source format: 1024×1024 PNG with transparent background, the
forge tree silhouette in the project's accent green
(`#4caf50` per `frontend/css/style.css`'s `--accent`).

Until those land, `cargo tauri build` will fail with "icon not
found." `cargo tauri dev` (used during this scaffolding burst)
doesn't need bundle icons — only production builds do.
