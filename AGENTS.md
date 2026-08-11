# Agent Instructions

This repository is an operational runbook for a remote OpenClaw instance. Before taking any action:

1. Read [README.md](README.md) completely.
2. Check whether `OPENCLAW_SSH_HOST`, `OPENCLAW_SSH_USER`, and `OPENCLAW_SSH_PORT` are available in the agent environment or configured secret provider.
3. Prefer `OPENCLAW_SSH_KEY` or the host's SSH agent. If a password prompt is required, use the interactive SSH prompt; never print, commit, or place the password in a command argument.
4. Run the documented read-only health checks before diagnosing or changing anything.

## Operating rules

- Treat the remote host as the source of truth. The dated status in README is only a last-known baseline.
- A user request authorizes only the requested scope. Do not broaden a config, firewall, package, service, backup, or credential change.
- Explain the intended mutation, affected paths, expected impact, rollback, and verification before making a remote change.
- Wait for explicit user approval before mutating the remote host. A read-only inspection is allowed while discussing the plan.
- Do not run `openclaw security audit --fix`, `openclaw doctor --repair`, package updates, service restarts, firewall changes, credential rotation, or backup deletion without that approval.
- Back up a config before editing it, preserve permissions, and validate the service after the change.
- Never expose values from `openclaw.json`, environment variables, session stores, logs, or private keys in chat or commits.
- Do not claim the remote system is healthy from the README alone; use the live checks.

## Access entry points

PowerShell:

```powershell
./scripts/openclaw-ssh.ps1
```

Bash:

```bash
./scripts/openclaw-ssh.sh
```

Both wrappers use native OpenSSH, support an optional identity file, and intentionally leave password entry to the interactive SSH prompt. See [README.md](README.md) for the complete runbook.
