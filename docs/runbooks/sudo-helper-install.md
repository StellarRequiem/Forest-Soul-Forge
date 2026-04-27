# Sudo helper install — `fsf-priv`

ADR-0033 A6. The Forest daemon is non-root by design. A small
allowlisted helper at `/usr/local/sbin/fsf-priv` runs under sudo
NOPASSWD to perform the four privileged operations the swarm needs:

- `kill-pid <PID>` — for `isolate_process.v1` (Phase B mid tier)
- `pf-add <ANCHOR> <RULE>` — for `dynamic_policy.v1` (Phase B high tier)
- `pf-drop <ANCHOR>` — for `dynamic_policy.v1` revoke
- `read-protected <PATH>` — for `tamper_detect.v1` (Phase B high tier) reading SIP-protected paths

This runbook installs the helper + the sudoers rule that lets the
daemon's user invoke it without a password prompt.

## What gets installed

| File | Owner | Mode | Purpose |
|------|-------|------|---------|
| `/usr/local/sbin/fsf-priv` | root:wheel | 0755 | The helper script |
| `/etc/sudoers.d/fsf` | root:wheel | 0440 | NOPASSWD rule |
| `/var/log/fsf-priv.log` | root:wheel | 0600 | Per-invocation audit log |

## Pre-flight

Confirm the daemon's user. If you run the daemon under your normal
account, that's the user the sudoers rule grants. Replace
`${DAEMON_USER}` with that name in the steps below.

```sh
# Find the daemon user (defaults to the current login on most setups):
echo "$USER"
```

## Install steps

From the repo root:

```sh
# 1. Helper script — owned by root, world-executable, not writable.
sudo install -m 0755 -o root -g wheel scripts/fsf-priv /usr/local/sbin/fsf-priv

# 2. Sudoers rule — substitute your daemon user, then validate.
sudo sh -c "sed 's/\${DAEMON_USER}/$USER/g' scripts/fsf-sudoers > /etc/sudoers.d/fsf"
sudo chmod 0440 /etc/sudoers.d/fsf
sudo chown root:wheel /etc/sudoers.d/fsf
sudo visudo -c -f /etc/sudoers.d/fsf

# 3. Pre-create the audit log so the helper can append without asking.
sudo touch /var/log/fsf-priv.log
sudo chmod 0600 /var/log/fsf-priv.log
sudo chown root:wheel /var/log/fsf-priv.log
```

## Verify

```sh
# 1. Helper exits 0 on a no-op (kill-pid against a nonexistent PID
# returns refused → exit 2; that's the right answer).
sudo -n /usr/local/sbin/fsf-priv kill-pid 99999999
echo "exit=$?  (2 = expected refusal — helper installed correctly)"

# 2. The daemon's user can invoke without prompting:
sudo -n /usr/local/sbin/fsf-priv kill-pid 99999999
# If that prompts for a password, the sudoers rule didn't apply —
# `sudo visudo -c -f /etc/sudoers.d/fsf` should pass and the user
# in the rule must match $USER.

# 3. Audit log has the call:
sudo tail -2 /var/log/fsf-priv.log
```

## Uninstall

```sh
sudo rm /usr/local/sbin/fsf-priv
sudo rm /etc/sudoers.d/fsf
# Keep /var/log/fsf-priv.log for forensics, or:
sudo rm /var/log/fsf-priv.log
```

After uninstall, the daemon's `PrivClient.assert_available()` will
raise `HelperMissing` and the privileged Phase B tools degrade to
"advisor mode" — they emit a refusal explaining the helper is
absent rather than crashing.

## Security notes

- The helper script is stdlib-only Python so it can be audited in
  one sitting. Read it before installing.
- The sudoers rule restricts to four exact subcommands; new ops
  require editing both `/usr/local/sbin/fsf-priv` AND the sudoers
  rule. Adding an op to one without the other is harmless — the
  helper refuses unknown subcommands and the sudoers rule limits
  what arguments can flow through.
- `Defaults!/usr/local/sbin/fsf-priv env_reset` strips the caller's
  env so PATH-injection attacks via the daemon's environment can't
  influence the helper's binary lookup. The helper resolves `pfctl`
  via `shutil.which` against the secure PATH set in the sudoers
  rule.
- Every invocation appends to `/var/log/fsf-priv.log` (or syslog
  fallback). The daemon's audit chain records the same call from
  its side; cross-referencing the two surfaces tampering with
  either log.

## Operator decisions still open

These were greenlit earlier but bite at Phase B build time:

- **HSM hardware** for `key_rotate.v1` (VaultWarden) — confirm
  model when we hit Phase B3.
- **External products** to adapter-wrap (Wazuh / Suricata / etc.) —
  name what's installed when we hit Phase C.
