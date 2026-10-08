# Agent Instructions

This repository is an operational runbook for a remote OpenClaw instance. Before taking any action:

1. Read [README.md](README.md) completely.
2. Check whether `OPENCLAW_SSH_HOST`, `OPENCLAW_SSH_USER`, and `OPENCLAW_SSH_PORT` are available in the agent environment or configured secret provider.
3. Prefer `OPENCLAW_SSH_KEY` or the host's SSH agent. If a password prompt is required, use the interactive SSH prompt; never print, commit, or place the password in a command argument.
4. Run the documented read-only health checks before diagnosing or changing anything.

## Workflow language

The OpenClaw workflow agents communicate with Minh and Wien in Vietnamese. Read [agents/shared/VIETNAMESE-LANGUAGE-POLICY.md](agents/shared/VIETNAMESE-LANGUAGE-POLICY.md) when working on `main`, `curie`, `website-brief`, or `project-pm`. Preserve command names, IDs, URLs, paths, state names, and JSON/YAML keys exactly.

## Workflow state and discovery guardrails

- The canonical runtime state root is `/home/minhmice/.openclaw/workflow`; project JSON, message tracking, and `WORKLOG.md` live there.
- `/home/minhmice/.openclaw/workspace/workflow` is the shared instruction/schema tree. It does not contain runtime project state; do not read or create `/home/minhmice/.openclaw/workspace/workflow/projects`.
- Discord channel sends must use `--channel discord --target channel:<id>` (or the equivalent message-tool target `channel="discord"`, `target="channel:<id>"`). Never retry a bare numeric target after an `Ambiguous Discord recipient` error.
- If `sessions_spawn` is forbidden or a discovery provider is unavailable, stop the discovery attempt and report a bounded `no_candidate_defensible`/`partial` result. Do not let `main` run an unbounded ad-hoc search or `exec` loop.

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

## High-priority Codex quota routing

When Minh asks `check quota`, `quota`, `Codex quota`, or `9Router quota` in
discuss channel `1533645084229369996`, run the deterministic quota checker before
answering. Never substitute OpenClaw agent context/token usage for the 9Router
Codex quota. Only the runner output is the quota answer. Individual Codex
accounts that are unavailable or malformed must be skipped; report a collector
error only when no valid Codex account remains.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
