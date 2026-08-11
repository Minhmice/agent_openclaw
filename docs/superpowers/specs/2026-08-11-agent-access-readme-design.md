# Agent Access README Design

## Goal

Create a self-contained project runbook that lets a new agent understand and access the remote OpenClaw host without storing the SSH password or any other secret in Git.

## Context

The repository currently contains no application source, README, or committed project files. The runbook is based on the remote audit performed on 2026-08-03 and must label those observations as a last-known baseline rather than current live state.

The remote baseline identified:

- Host: `100.121.246.38`
- SSH user: `minhmice`
- OpenClaw version: `2026.7.1-2`
- User-level systemd service: `openclaw-gateway.service`
- Gateway listener: `127.0.0.1:18789`
- Discord channel: configured and connected during the audit
- Config/state/workspace paths under `/home/minhmice/.openclaw`
- Security warnings around gateway password storage, unsandboxed runtime/filesystem access, reverse-proxy trust configuration, and unpinned Discord plugin installation

## Security boundary

`README.md` may contain connection metadata, paths, commands, operational warnings, and environment-variable names. It must not contain:

- The SSH password
- Gateway passwords, Discord tokens, model-provider keys, cookies, or session data
- A copied private key
- A command that places a secret directly in a process argument

Credential handling will use these conventions:

- `.env.example` documents variable names with empty values or placeholders.
- `.gitignore` excludes `.env`, `.env.local`, key files, and local secret directories.
- Agents may inject `OPENCLAW_SSH_HOST`, `OPENCLAW_SSH_PORT`, `OPENCLAW_SSH_USER`, `OPENCLAW_SSH_PASSWORD`, and optionally `OPENCLAW_SSH_KEY` through their environment or secret manager.
- Native SSH helpers use an SSH key when `OPENCLAW_SSH_KEY` is set and otherwise leave password entry to the interactive SSH prompt. They do not pass `OPENCLAW_SSH_PASSWORD` on the command line.
- The README explicitly recommends rotating credentials that have been exposed in chat and using SSH keys for routine access.

## Files and responsibilities

### `README.md`

The main agent runbook. It will contain:

1. Project purpose and scope.
2. Credential policy and quick-start setup.
3. SSH access for PowerShell, Bash, and agent integrations.
4. OpenClaw architecture and service lifecycle.
5. Paths for config, state, sessions, workspace, backups, and logs.
6. Read-only health checks and smoke tests.
7. Discord/provider verification.
8. Backup and restore expectations.
9. Firewall and network exposure notes.
10. Security findings and recommended remediation order.
11. Last-known audit baseline with its date.
12. Troubleshooting and escalation rules.
13. Explicit operations that require user approval before execution.

### `.env.example`

An untracked-environment template with non-secret connection variables and an empty password placeholder. It will not contain usable credentials.

### `.gitignore`

Ignore local environment files, private keys, certificate material, and secret directories so a new agent does not accidentally stage them.

### `scripts/openclaw-ssh.ps1`

PowerShell helper that validates host/user/port variables, optionally adds an SSH identity file, and forwards remaining arguments to the native `ssh` client. It must preserve interactive password prompting and return SSH's exit code.

### `scripts/openclaw-ssh.sh`

Bash equivalent of the PowerShell helper. It must quote all connection arguments, support an optional identity file, forward extra arguments, and return SSH's exit code.

## Operational design

The README will separate three kinds of information:

- **Stable instructions:** how to load credentials, connect, inspect the service, and troubleshoot.
- **Last-known observations:** facts observed during the 2026-08-03 audit, clearly dated.
- **Approval-gated actions:** updates, config changes, firewall changes, restarts, credential rotation, and backup creation.

Read-only examples will be preferred. Commands that can mutate state will be marked and will not be presented as automatic first steps.

## Error handling

The helper scripts will fail early when the host or user is missing, show a concise usage message, preserve the SSH client's own diagnostic output, and propagate its exit status. They will not echo environment values or print password variables.

The README will explain the following common failures:

- Missing environment variables.
- SSH key missing or wrong permissions.
- Password authentication rejected.
- Gateway service running only as a user service.
- Gateway reachable only from loopback.
- Discord connected state differing from the dated baseline.
- Missing `operator.read` scope during deep OpenClaw probes.

## Verification

After implementation:

1. Confirm all expected files exist.
2. Search tracked files for the known password and common secret markers.
3. Verify `.gitignore` excludes `.env.local`, private keys, and secret directories.
4. Run the PowerShell helper in a safe validation mode or with a deliberately invalid host and confirm it fails without printing secrets.
5. Run shell syntax validation for the Bash helper.
6. Inspect `git diff --check` and the final status.
7. Confirm README commands reference the actual service/path names from the audit and label dated facts correctly.

## Out of scope

This change will not:

- Connect to or modify the remote host.
- Change OpenClaw configuration, service state, firewall rules, packages, or credentials.
- Create a real `.env.local` file.
- Commit any secret.
- Claim that the 2026-08-03 operational baseline is still current without a fresh audit.
