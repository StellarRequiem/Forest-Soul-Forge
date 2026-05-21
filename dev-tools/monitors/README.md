# Forest Soul Forge — Local Monitors

Six bash monitoring scripts with matching launchd plists. These replace
Claude scheduled tasks that exceeded the 15/day routine cap.

## Monitors

| Script | Plist Label | Schedule | What it does |
|---|---|---|---|
| `health-pulse.sh` | `dev.forest.monitor.health-pulse` | Every 2 hours | Checks daemon API, frontend, Ollama endpoints + process liveness |
| `scheduler-lag-monitor.sh` | `dev.forest.monitor.scheduler-lag` | Every hour | Scans audit chain for slow scheduler ticks (>2000ms) |
| `docker-stack-health.sh` | `dev.forest.monitor.docker-health` | Every 4 hours | Docker container status + memory usage vs limits |
| `disk-memory-audit.sh` | `dev.forest.monitor.disk-memory` | Daily 11:00 AM | Disk, Ollama models, Docker disk, audit chain size, system memory |
| `stale-process-cleanup.sh` | `dev.forest.monitor.stale-processes` | Daily 12:00 PM | Detects orphan/zombie processes (report only, no auto-kill) |
| `reality-anchor-check.sh` | `dev.forest.monitor.reality-anchor` | Daily 10:30 AM | Scans audit chain for `reality_anchor_flagged` / `reality_anchor_repeat_offender` events |

## Log files

All output goes to `data/monitor-logs/`:

- `health-pulse.log`, `scheduler-lag.log`, etc. — per-monitor append logs
- `ALERTS.log` — consolidated alert file (anything that needs attention)
- `launchd-*.out.log` / `launchd-*.err.log` — launchd stdout/stderr capture
- `.reality-anchor-seq` — last reality-anchor audit-chain seq seen (hidden state file, not a log)

## Installation

### 1. Symlink plists into LaunchAgents

```bash
mkdir -p ~/Library/LaunchAgents

for plist in dev-tools/launchd/*.plist; do
    ln -sf "$(pwd)/${plist}" ~/Library/LaunchAgents/$(basename "${plist}")
done
```

### 2. Load them

```bash
launchctl load ~/Library/LaunchAgents/dev.forest.monitor.health-pulse.plist
launchctl load ~/Library/LaunchAgents/dev.forest.monitor.scheduler-lag.plist
launchctl load ~/Library/LaunchAgents/dev.forest.monitor.docker-health.plist
launchctl load ~/Library/LaunchAgents/dev.forest.monitor.disk-memory.plist
launchctl load ~/Library/LaunchAgents/dev.forest.monitor.stale-processes.plist
launchctl load ~/Library/LaunchAgents/dev.forest.monitor.reality-anchor.plist
```

### 3. Verify they're registered

```bash
launchctl list | grep dev.forest.monitor
```

### 4. Run one manually to test

```bash
bash dev-tools/monitors/health-pulse.sh
cat data/monitor-logs/health-pulse.log
```

## Unloading

```bash
launchctl unload ~/Library/LaunchAgents/dev.forest.monitor.health-pulse.plist
# ... etc for each
```

Or unload all:

```bash
for plist in ~/Library/LaunchAgents/dev.forest.monitor.*.plist; do
    launchctl unload "${plist}"
done
```

## Notes

- Scripts are report-only. `stale-process-cleanup.sh` identifies stale
  processes but never kills anything — review `stale-processes.log` and
  act manually.
- `ALERTS.log` is append-only. Rotate or truncate it periodically.
- The `reality-anchor-check` records a baseline audit-chain seq on
  first run. Subsequent runs alert only on reality-anchor events
  newer than that seq, then advance it — each flag alerts once.
- All scripts handle missing directories/commands gracefully (skip with
  a log message rather than crash).
- The `PATH` in each plist includes `/opt/homebrew/bin` for Apple Silicon
  Homebrew installs (docker, python3, etc.).
