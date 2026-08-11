# Current State

## Repository

- Project: `agent_openclaw`
- Repository path: `C:\Users\minhmice\Documents\projects\agent_openclaw`
- Git branch at handoff: `main`
- Remote branch at handoff: `origin/main`
- Repository nature: documentation-first runbook; no lead-mining engine exists yet.
- Root runbook: [README.md](../../README.md)
- Root agent rules: [AGENTS.md](../../AGENTS.md)

## Existing runbook capability

The root repository already documents how to access the remote OpenClaw system. It includes native OpenSSH wrappers for PowerShell and Bash. Credential values are intentionally absent from the repo.

Expected environment names:

```text
OPENCLAW_SSH_HOST
OPENCLAW_SSH_PORT
OPENCLAW_SSH_USER
OPENCLAW_SSH_KEY
OPENCLAW_SSH_PASSWORD
```

The previous agent checked that none of these variables were present in its own environment. Do not infer that the remote host is inaccessible; use the approved agent/secret-provider setup when the user authorizes remote work.

## Remote OpenClaw last-known baseline

The last read-only audit was performed on 2026-08-03. It is historical and must be rechecked before relying on it.

- Host: `100.121.246.38`
- SSH user: `minhmice`
- OS: Ubuntu 24.04.4 LTS
- OpenClaw: `2026.7.1-2`
- Node.js: `22.23.1`
- Install: global npm package under `/usr/lib/node_modules/openclaw`
- User service: `openclaw-gateway.service`
- Gateway: loopback `127.0.0.1:18789`
- Discord: configured, connected, and credential probe succeeded during audit
- Gateway event loop: healthy during audit
- OpenClaw update status: stable/latest during audit
- `Linger=yes` for user `minhmice`
- UFW enabled, but default input policy observed as `ACCEPT`
- Public listeners included Coolify/Traefik ports `80`, `443`, and `8080`; other firewall rules included `7860`, `8000`, and `30000`
- No tested off-host encrypted OpenClaw backup was found

Security audit warnings included:

1. Empty `gateway.trustedProxies` if Control UI is later put behind a reverse proxy.
2. Gateway password stored in config.
3. Effective runtime/process and filesystem capabilities without full sandboxing and with `workspaceOnly=false`.
4. Unpinned Discord plugin npm spec.
5. Deep probe missing `operator.read` scope.

No remote changes were authorized or made as part of this handoff.
