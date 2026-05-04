// Forest Soul Forge desktop — Tauri shell entry point.
//
// ADR-0042 T3 (Burst 99). The Rust shell that:
//   1. Spawns the daemon process on app launch (currently
//      `python -m forest_soul_forge.daemon`; T4 / Burst 101
//      replaces this with the bundled binary).
//   2. Opens a window pointing at the bundled frontend.
//   3. Stops the daemon on app quit.
//
// What this commit does NOT do:
//   - Bundle the daemon binary. T4 lands PyOxidizer / pyinstaller;
//     until then the operator's host needs Python 3.11+ on PATH.
//   - Auto-update wiring (T5 / Bursts 102-103). Tauri's updater
//     plugin is added at that point; this scaffolding leaves room.
//   - Custom IPC commands. The frontend talks to the daemon over
//     plain HTTP (the same way it does in browser-mode), so we
//     don't need Tauri-side commands yet.
//
// Run path:
//   `cargo tauri dev` → spawns daemon, opens dev-server window
//   `cargo tauri build` → produces signed installer (T5)

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use tauri::Manager;

// Daemon process handle, scoped to the app's lifetime via Tauri's
// state container. We Mutex-wrap because Tauri requires Send +
// Sync state and Child is !Sync.
struct DaemonHandle(Mutex<Option<Child>>);

fn spawn_daemon() -> Result<Child, std::io::Error> {
    // Dev mode: invoke the project's Python module directly. The
    // operator's PATH must have python3 + the package installed
    // (e.g., `pip install -e .` from the repo root).
    //
    // T4 replaces this with a bundled binary path:
    //   let bin = std::env::current_exe()?
    //       .parent().unwrap()
    //       .join("forest-soul-forge-daemon");
    //   Command::new(bin)
    //
    // For now: assume `forest-soul-forge` CLI is on PATH (set by
    // `pip install -e .`).
    Command::new("python3")
        .args(["-m", "forest_soul_forge.daemon", "--port", "7423"])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
}

#[tauri::command]
fn daemon_status(state: tauri::State<DaemonHandle>) -> String {
    // Operator-facing introspection. Frontend can call this via
    // window.__TAURI__.invoke('daemon_status') if needed; right
    // now it's just here to prove the IPC bridge works end-to-end.
    let guard = state.0.lock().unwrap();
    match guard.as_ref() {
        Some(child) => format!("daemon pid={}", child.id()),
        None => "daemon not running".to_string(),
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            // Spawn daemon as a sidecar. If it fails (Python not
            // installed, port in use, etc.), log to stderr and
            // continue — the frontend will surface the
            // unreachable-daemon state via /healthz failure, same
            // as in browser mode.
            match spawn_daemon() {
                Ok(child) => {
                    eprintln!("forest-soul-forge: daemon spawned, pid={}", child.id());
                    app.manage(DaemonHandle(Mutex::new(Some(child))));
                }
                Err(e) => {
                    eprintln!("forest-soul-forge: daemon spawn failed: {}", e);
                    eprintln!("  Install with: pip install -e .");
                    app.manage(DaemonHandle(Mutex::new(None)));
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            // Best-effort daemon shutdown when the window closes.
            // Tauri's WindowEvent::CloseRequested fires before the
            // window is destroyed; we kill the daemon process so it
            // doesn't linger orphaned.
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                if let Some(state) = window.try_state::<DaemonHandle>() {
                    if let Some(mut child) = state.0.lock().unwrap().take() {
                        let _ = child.kill();
                        eprintln!("forest-soul-forge: daemon stopped");
                    }
                }
            }
        })
        .invoke_handler(tauri::generate_handler![daemon_status])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

fn main() {
    run();
}
