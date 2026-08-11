# Agent Access Runbook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a secure, self-contained runbook that lets a new agent discover how to access and operate the remote OpenClaw host without storing credentials in Git.

**Architecture:** `AGENTS.md` bootstraps every new agent into the runbook and approval rules. `README.md` documents the remote system, read-only checks, modification workflow, and dated baseline. `.env.example` and `.gitignore` define the credential boundary, while PowerShell/Bash wrappers normalize SSH access without putting passwords in process arguments.

**Tech Stack:** Markdown, POSIX shell, PowerShell, native OpenSSH, Git.

---

### Task 1: Establish the local credential boundary

**Files:**
- Create: `.env.example`
- Create: `.gitignore`

- [ ] **Step 1: Add the environment template**

Create `.env.example` with empty secret values and documented non-secret defaults:

```dotenv
OPENCLAW_SSH_HOST=100.121.246.38
OPENCLAW_SSH_PORT=22
OPENCLAW_SSH_USER=minhmice
OPENCLAW_SSH_PASSWORD=
OPENCLAW_SSH_KEY=
```

- [ ] **Step 2: Add ignore rules**

Ignore `.env`, `.env.local`, private keys, certificates, and local secret directories. Do not ignore `README.md`, `AGENTS.md`, scripts, or `.env.example`.

- [ ] **Step 3: Verify the boundary**

Run `git check-ignore -v .env.local id_rsa secrets/example.txt` and confirm each path is ignored. Run `git check-ignore -v .env.example` and confirm it is not ignored.

### Task 2: Add cross-platform SSH helpers

**Files:**
- Create: `scripts/openclaw-ssh.ps1`
- Create: `scripts/openclaw-ssh.sh`

- [ ] **Step 1: Implement PowerShell wrapper**

Read `OPENCLAW_SSH_HOST`, `OPENCLAW_SSH_USER`, `OPENCLAW_SSH_PORT`, and optional `OPENCLAW_SSH_KEY`. Fail with a concise message if host or user is missing. Build an argument array for `ssh`, append the identity file only when present, append the destination, forward remaining arguments, invoke native `ssh`, and return its exit code. Never print or pass `OPENCLAW_SSH_PASSWORD`.

- [ ] **Step 2: Implement Bash wrapper**

Use `${OPENCLAW_SSH_HOST:?}`, `${OPENCLAW_SSH_USER:?}`, a default port of `22`, an optional `-i` identity file, and `exec ssh` with quoted arguments. Forward all script arguments. Do not read or echo the password variable.

- [ ] **Step 3: Verify helper behavior**

Run `bash -n scripts/openclaw-ssh.sh`. Run the PowerShell helper with `OPENCLAW_SSH_HOST` unset and confirm it exits non-zero with a missing-host message and no secret output. Run the Bash helper with the host unset and confirm it exits non-zero without invoking SSH.

### Task 3: Bootstrap new agents

**Files:**
- Create: `AGENTS.md`

- [ ] **Step 1: Document startup behavior**

Tell every agent to read `README.md` first, check environment/secret-provider availability, avoid printing secrets, run read-only checks before changes, and obtain explicit user approval for mutations.

- [ ] **Step 2: Document remote modification policy**

State that a user request to modify OpenClaw authorizes only the requested scope. Configuration, service, firewall, package, credential, backup, and restart operations must be named in the plan before execution. Require post-change health checks and rollback notes.

### Task 4: Write the complete operational README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Add access instructions**

Document environment loading, SSH-key use, interactive password fallback, the PowerShell/Bash helpers, and the fact that the password must never be committed or placed in an SSH command argument.

- [ ] **Step 2: Add OpenClaw runbook**

Document the user-level systemd unit, service commands, gateway loopback binding, config/state/workspace/session/log paths, Discord/provider checks, and safe read-only commands.

- [ ] **Step 3: Add baseline and risk register**

Record only non-secret observations from the 2026-08-03 audit: versions, service state, resource state, Discord connectivity, update state, firewall exposure, backup gap, and OpenClaw security warnings. Label every dated observation as stale until rechecked.

- [ ] **Step 4: Add agent workflow**

Define the sequence: understand request, inspect current state, propose exact mutation, wait for approval when needed, make the smallest change, verify, report files/commands/results, and never rotate or expose credentials silently.

### Task 5: Verify and commit

**Files:**
- Verify: `.env.example`, `.gitignore`, `AGENTS.md`, `README.md`, `scripts/openclaw-ssh.ps1`, `scripts/openclaw-ssh.sh`

- [ ] **Step 1: Check documentation and shell syntax**

Run `bash -n scripts/openclaw-ssh.sh`, `git diff --check`, and a repository-wide search for the credential value supplied out-of-band plus common secret markers. The credential value must produce zero matches.

- [ ] **Step 2: Check helper and instruction references**

Confirm every path and command referenced by `AGENTS.md` exists, and README references the actual service name `openclaw-gateway.service`, gateway port `18789`, and remote paths from the dated audit.

- [ ] **Step 3: Review the final diff**

Run `git status --short` and `git diff --stat`; verify no `.env.local`, key, or credential file is staged.

- [ ] **Step 4: Commit the runbook**

Run:

```bash
git add .env.example .gitignore AGENTS.md README.md scripts/openclaw-ssh.ps1 scripts/openclaw-ssh.sh docs/superpowers/plans/2026-08-11-agent-access-readme.md
git commit -m "docs: add OpenClaw agent access runbook"
```
