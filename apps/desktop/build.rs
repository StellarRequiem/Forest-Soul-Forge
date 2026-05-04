// Tauri build script. Generates the platform-specific resources
// (bundle metadata, signing config, etc.) that the Rust crate
// links against at compile time.
//
// ADR-0042 T3 / Burst 99. Copy of the Tauri 2.x default — we
// don't customize anything here yet. Future work (T5, code
// signing) might add env-var-driven flags for notarization /
// release-vs-dev build differences.
fn main() {
    tauri_build::build()
}
