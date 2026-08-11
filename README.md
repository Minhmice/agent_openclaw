# Agent OpenClaw

Operational runbook for the OpenClaw instance hosted at `100.121.246.38`.

This repository is intentionally documentation-first. It does not contain the remote OpenClaw application source. A new agent should start with this file and [AGENTS.md](AGENTS.md), load access credentials from its environment or secret provider, inspect the live host, and then follow the approval-gated change workflow below.

## Quick start for a new agent

### 1. Load non-secret connection metadata

Use `.env.example` as a variable-name reference. The real values must come from the agent runner's environment, an SSH agent, or an approved secret provider. Do not commit `.env.local`, paste a password into this README, or put a password in an SSH command argument.

Required variables:

```text
OPENCLAW_SSH_HOST=100.121.246.38
OPENCLAW_SSH_PORT=22
OPENCLAW_SSH_USER=minhmice
```

Preferred authentication:

```text
OPENCLAW_SSH_KEY=<path supplied by the agent environment>
```

`OPENCLAW_SSH_PASSWORD` may be supplied to an agent's programmatic SSH integration through its secret provider, but the included native OpenSSH wrappers deliberately do not read or pass that variable. They use an SSH key/agent first and otherwise let OpenSSH prompt interactively. This keeps the password out of process listings and shell history.

### 2. Connect

PowerShell:

```powershell
./scripts/openclaw-ssh.ps1
```

Bash:

```bash
chmod 700 ./scripts/openclaw-ssh.sh
./scripts/openclaw-ssh.sh
```

To run one remote read-only command:

```powershell
./scripts/openclaw-ssh.ps1 'openclaw health'
```

```bash
./scripts/openclaw-ssh.sh 'openclaw health'
```

If the environment is not configured, stop and ask for the approved credential-loading method. Do not ask the user to paste the password into a repository file or expose it in chat.

## Agent workflow

Every new task involving the remote OpenClaw should follow this sequence:

1. Translate the user's request into a concrete scope.
2. Connect using the configured SSH environment or key.
3. Run read-only status and health checks.
4. Compare live state with the last-known baseline in this document.
5. Explain the intended change, affected files/services, risk, rollback, and verification.
6. Wait for explicit approval before any mutation. The approval applies only to the named scope.
7. Back up the relevant config before editing it.
8. Make the smallest change needed.
9. Validate OpenClaw, Discord, service state, and the affected subsystem.
10. Report exactly what changed, what was verified, and any remaining risk.

Read-only investigation is allowed while discussing. The following always require explicit approval: editing OpenClaw config, changing agent permissions, enabling/disabling sandboxing, changing systemd units, restarting services, installing/updating packages, changing firewall rules, changing credentials, creating/deleting backups, or modifying Docker/Coolify.

### Discord owner and exec-approval parity

If Minh and Wien must have the same OpenClaw owner and Discord exec-approval rights, configure both Discord IDs in both authorization layers. The workflow coordinator's `/approve` permission is separate and does not grant OpenClaw host-exec approval.

The intended non-secret shape is:

```json5
{
  commands: {
    ownerAllowFrom: [
      "discord:620891893659598850",
      "discord:859783610625556480",
    ],
  },
  channels: {
    discord: {
      execApprovals: {
        enabled: true,
        approvers: [
          "620891893659598850",
          "859783610625556480",
        ],
      },
    },
  },
}
```

Before applying this live, inspect the current merged policy and preserve unrelated config:

```bash
openclaw approvals get --gateway
openclaw config get commands.ownerAllowFrom
openclaw config get channels.discord.execApprovals
```

After the narrow merge, validate both policy layers and the Discord path. Do not use `exec-policy preset yolo`, set `security: "full"`, or add `"*"` to an allowlist merely to make the button work. See the [OpenClaw exec-approvals documentation](https://docs.openclaw.ai/tools/exec-approvals) and [Discord channel configuration](https://docs.openclaw.ai/gateway/config-channels).

## Remote system map

### Host and runtime

| Item | Value |
|---|---|
| Host | `100.121.246.38` |
| SSH user | `minhmice` |
| OS observed | Ubuntu 24.04.4 LTS |
| OpenClaw CLI observed | `2026.7.1-2` |
| Node.js observed | `22.23.1` |
| OpenClaw install | Global npm package at `/usr/lib/node_modules/openclaw` |
| Gateway port | `18789` |
| Gateway bind | `127.0.0.1` only |
| OpenClaw service | User-level `openclaw-gateway.service` |

### Important paths

| Purpose | Path |
|---|---|
| OpenClaw config | `/home/minhmice/.openclaw/openclaw.json` |
| Last-known good config | `/home/minhmice/.openclaw/openclaw.json.last-good` |
| Local config snapshots | `/home/minhmice/.openclaw/openclaw.json.bak*` |
| State database | `/home/minhmice/.openclaw/state/openclaw.sqlite` |
| State WAL/SHM | `/home/minhmice/.openclaw/state/openclaw.sqlite-wal` and `.sqlite-shm` |
| Workspace | `/home/minhmice/.openclaw/workspace` |
| Session store | `/home/minhmice/.openclaw/agents/main/sessions/sessions.json` |
| User systemd unit | `/home/minhmice/.config/systemd/user/openclaw-gateway.service` |
| Runtime log from CLI status | `/tmp/openclaw-1000/openclaw-YYYY-MM-DD.log` |
| Persistent OpenClaw logs | `/home/minhmice/.openclaw/logs` |

## Read-only health checks

Run these after connecting and before diagnosing or modifying anything:

```bash
id
hostname
uptime
df -hT / /home
free -h
systemctl --user status openclaw-gateway.service --no-pager
systemctl --user show openclaw-gateway.service --no-pager -p ActiveState -p SubState -p MainPID -p ExecMainStatus -p Restart
openclaw gateway status
openclaw health
openclaw channels status --channel discord
openclaw channels status --channel discord --probe
openclaw update status
```

Read-only security/diagnostic checks:

```bash
openclaw security audit --deep --json
openclaw doctor --lint --json
ss -lntup
```

Do not add `--fix` to `security audit` or `--repair` to `doctor` during an inspection. A deep probe may report `missing scope: operator.read`; that means the CLI lacks the operator probe scope, not necessarily that the gateway or Discord channel is down.

Useful service commands, all read-only:

```bash
systemctl --user is-enabled openclaw-gateway.service
systemctl --user is-active openclaw-gateway.service
systemctl --user cat openclaw-gateway.service
loginctl show-user minhmice -p State -p Sessions -p Linger
```

## Safe modification workflow

Before a config change:

```bash
cp -p ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.bak.$(date +%Y%m%d-%H%M%S)
```

Only run that backup after the user approves the planned change. Do not print the file contents. After editing, verify permissions and health:

```bash
stat -c '%a %U:%G %n' ~/.openclaw/openclaw.json
openclaw health
openclaw channels status --channel discord --probe
systemctl --user status openclaw-gateway.service --no-pager
```

For a service change, record the previous unit content and expected rollback before applying it. For a firewall or package change, first list the ports/packages that will be affected and explain the possibility of losing SSH access or restarting dependent services.

## Last-known baseline: audit on 2026-08-03

This section is historical. Re-run the live checks above before relying on it.

### Runtime

- Ubuntu host had been up for approximately 10 days.
- Root filesystem was 98 GB with about 41% used.
- Memory was 15 GiB total with about 10 GiB available.
- OpenClaw CLI and gateway were both `2026.7.1-2`.
- OpenClaw update status reported npm stable and up-to-date.
- The user-level service was enabled, active, and configured with `Restart=always` and a 5-second restart delay.
- `Linger=yes` was enabled for `minhmice`, allowing the user service to persist outside an interactive login.

### Gateway and Discord

- Gateway was loopback-only at `127.0.0.1:18789`.
- Gateway connectivity and event loop checks were OK.
- Discord was enabled, configured, running, and connected.
- Discord credential probe succeeded.
- The configured bot was connected to the approved guild/channel mapping.
- Provider requests returned HTTP 200 but took roughly 4.7–6.4 seconds in sampled logs.
- At least one Discord reconnect was observed and later recovered.

### Security findings

OpenClaw's deep security audit reported zero critical findings and five warnings:

1. `gateway.trustedProxies` was empty. This matters if Control UI is later exposed through a reverse proxy; it is less relevant while the gateway remains local-only.
2. `gateway.auth.password` was stored in the config file. The config file permission was `600`, but environment/secret-provider storage is preferred.
3. Effective agent defaults exposed runtime/process and filesystem capabilities without full sandboxing and with `workspaceOnly=false`. This is high risk if untrusted or mutually untrusted Discord users can reach the agent.
4. The Discord plugin install record used an unpinned npm spec.
5. The deep live probe lacked the `operator.read` scope.

The effective Discord policy observed was `dmPolicy=pairing` and `groupPolicy=allowlist`, which reduces exposure but does not by itself prove that every reachable channel member is trusted.

### Host network and backup

- UFW was enabled, but the configured default input policy was `ACCEPT`.
- Public listeners included Coolify/Traefik ports `80`, `443`, and `8080`; additional host rules included `7860`, `8000`, and `30000`.
- OpenClaw's `18789` listener remained loopback-only.
- Local config snapshots existed, but no tested off-host encrypted backup or scheduled OpenClaw restore procedure was found.
- Pending OS package updates included a Node.js patch, Tailscale, Docker Buildx, timezone data, and distro metadata.

## Recommended remediation order

Discuss and approve each item separately:

1. Confirm the actual Discord users/channels that may invoke the agent; then reduce runtime/filesystem scope or enable sandboxing if the boundary is not strictly one trusted operator.
2. Create an encrypted off-host backup for config, state, workspace, and session data, then test a restore.
3. Review public ports and tighten firewall policy with a tested SSH rollback path.
4. Move gateway password material to an environment/secret provider and rotate credentials exposed during setup.
5. Pin the Discord plugin dependency.
6. Apply OS/Node patch updates during a maintenance window and rerun smoke tests.
7. Add periodic monitoring for gateway state, Discord connectivity, provider latency, disk, and memory.

Do not expose port `18789` publicly just to make the CLI convenient. Keep the gateway local-only unless there is a specific, reviewed remote-control requirement and a trusted-proxy/auth design.

## Troubleshooting

### Environment variables missing

Load them through the agent runner or approved secret provider. `.env.example` is only a template. Do not create or commit a real `.env.local` in this repository.

### SSH fails

Check the host, port, user, key path, SSH agent, and network route. Use the native SSH diagnostic output. Do not retry by putting the password in the command line.

### Gateway appears down

Check the user service, not only a system-level service:

```bash
systemctl --user status openclaw-gateway.service --no-pager
openclaw gateway status
openclaw health
```

### Discord is not connected

Run:

```bash
openclaw channels status --channel discord --probe
journalctl --user -u openclaw-gateway.service --since '1 hour ago' --no-pager
```

Do not rotate the Discord token or restart the gateway without user approval.

### Deep audit reports `operator.read`

Treat it as a diagnostic-scope issue. The normal health and Discord credential probe can still be healthy. Report the missing scope and ask whether the user wants to configure a read-only operator credential.

## Credential and data handling

- Never put the SSH password, gateway password, Discord token, provider key, private key, cookie, or session contents in README, AGENTS, issues, commits, or chat output.
- Never run `ssh user@host password` or equivalent.
- Treat config backups, SQLite state, session stores, and logs as sensitive.
- Rotate credentials exposed in chat or shell history after the setup is complete.
- Prefer SSH keys with restricted permissions and a dedicated agent/secret provider.
- Keep the gateway loopback-only unless a reviewed architecture explicitly requires otherwise.
