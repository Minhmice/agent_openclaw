# OpenClaw Multi-Agent Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Configure a durable OpenClaw workflow that discovers leads, waits for Minh approval, generates redesign information, creates page/day checklists, reminds Minh and Wien, and hands completed projects to the offer-ready channel.

**Architecture:** Keep the existing `main` Discord-facing agent as coordinator. Add isolated `curie`, `website-brief`, and `project-pm` agents with separate workspaces and explicit handoff contracts. Use typed Discord commands because the current Discord capability does not expose native buttons; use a 30-minute cron only for active-task reminders and persist project state/worklogs in the PM workspace.

**Tech Stack:** OpenClaw isolated agents, user-level systemd, OpenClaw cron, Discord message/reaction actions, YAML/JSON contracts, Markdown worklogs, Python/Node utilities already available on the host.

---

### Task 1: Create and record local contracts

**Files:**
- Create: `agents/WORKLOG.md`
- Create: `agents/shared/contracts/curie-to-website.yaml`
- Create: `agents/shared/contracts/website-to-pm.yaml`
- Create: `agents/shared/contracts/workflow-commands.md`

- [ ] **Step 1: Add the immutable handoff contracts**

Use the contract files as the only required fields for cross-agent handoff. Do not include credentials.

- [ ] **Step 2: Record the implementation decision**

Append the current user approval, remote findings, fallback from buttons to typed commands, and rollback plan to `agents/WORKLOG.md`.

- [ ] **Step 3: Verify local contracts**

Run a YAML/JSON parse check where available, `git diff --check`, and a secret-marker scan over `agents/`.

### Task 2: Snapshot remote state and create remote worklog

**Remote files:**
- Backup: `/home/minhmice/.openclaw/openclaw.json`
- Backup: `/home/minhmice/.config/systemd/user/openclaw-gateway.service`
- Create: `/home/minhmice/.openclaw/workspace/WORKLOG.md`
- Create: `/home/minhmice/.openclaw/workspace/workflow/`

- [ ] **Step 1: Create a timestamped backup**

Create a private backup directory under `/home/minhmice/.openclaw/backups/workflow-<UTC timestamp>/`, copy the active config and user service there with permissions preserved, and record the path locally. Do not print config contents.

- [ ] **Step 2: Create the remote worklog**

Write a Markdown worklog with the same channel IDs, user IDs, implementation decision, capability finding, backup path, and a blank “next step” section. Ensure it is user-readable but contains no secret values.

- [ ] **Step 3: Verify backup and worklog**

Check file existence, owner/mode, and that the worklog does not contain token/password/key markers.

### Task 3: Add isolated OpenClaw agents and upload role instructions

**Remote agent workspaces:**
- `/home/minhmice/.openclaw/workflow-agents/curie`
- `/home/minhmice/.openclaw/workflow-agents/website-brief`
- `/home/minhmice/.openclaw/workflow-agents/project-pm`

- [ ] **Step 1: Add `curie`**

Run `openclaw agents add curie --non-interactive --workspace /home/minhmice/.openclaw/workflow-agents/curie --json`, then set identity if needed. Curie owns discovery and business scraping only.

- [ ] **Step 2: Add `website-brief`**

Run the equivalent command for `website-brief`. Upload the canonical redesign spec, Website Brief instructions, Curie page playbook, and handoff contract.

- [ ] **Step 3: Add `project-pm`**

Run the equivalent command for `project-pm`. Upload the canonical redesign spec, PM instructions, page checklist, workflow commands, and handoff contract.

- [ ] **Step 4: Verify agent isolation**

Run `openclaw agents list --json` and confirm all three agents have distinct IDs/workspaces. Run each agent with a planning-only smoke message and confirm no remote mutation.

### Task 4: Add coordinator instructions and Discord channel routing

**Remote files:**
- Modify: `/home/minhmice/.openclaw/workspace/AGENTS.md` via timestamped backup + append-only workflow section.
- Create: `/home/minhmice/.openclaw/workspace/workflow/COORDINATOR.md`
- Modify: `/home/minhmice/.openclaw/openclaw.json` only through validated config patch.

- [ ] **Step 1: Add coordinator rules**

Tell `main` to validate actor IDs, enforce state transitions, call the appropriate isolated agent, use the typed command contract, and append every action to `WORKLOG.md`.

- [ ] **Step 2: Add the three guild channel entries**

Preserve existing `task` and `discuss` config. Add logical entries for `review` (`1536658476288450630`) and `offer` (`1536659097649422356`) under the existing guild. Keep gateway loopback-only and do not alter unrelated credentials or model/provider settings.

- [ ] **Step 3: Validate config before apply**

Use `openclaw config patch --dry-run` with the smallest JSON5 patch, inspect the result, then apply the same validated patch. Record the config backup path.

- [ ] **Step 4: Restart only if required and verify**

If the OpenClaw config command does not hot-reload, restart the user service once after the backup. Verify gateway, Discord probe, existing `task`/`discuss`, and new channel entries.

### Task 5: Create the active-task reminder cron

**Remote state:** OpenClaw cron scheduler.

- [ ] **Step 1: Create a disabled reminder job**

Create an idempotent job named `project-pm-active-reminders` with `--every 30m`, agent `project-pm`, and a message instructing it to read project state, send only due reminders to the task channel, mention the appropriate actor, and append the worklog. Keep delivery disabled until the job JSON is inspected.

- [ ] **Step 2: Inspect and enable the job**

Run `openclaw cron get/list/status`, confirm schedule, agent, payload, and delivery target, then enable it. Record the job ID in both worklogs.

- [ ] **Step 3: Run one controlled smoke execution**

Run the cron job once in debug mode. It must send no reminder when there are no active projects and must not create duplicate messages.

### Task 6: Verify end-to-end without a real project

- [ ] **Step 1: Verify commands and state**

Check `openclaw agents list --bindings`, `openclaw cron list`, `openclaw cron status`, `openclaw status`, `openclaw health`, and Discord probe.

- [ ] **Step 2: Verify authorization logic in coordinator instructions**

Confirm Minh-only approval/finalization, Wien task updates, typed-command fallback, and no transition to `offer-ready` without both final confirmations.

- [ ] **Step 3: Verify rollback references**

Confirm both local and remote worklogs identify config backup, service backup, added agent IDs, and cron job ID.

- [ ] **Step 4: Append final session note**

Record all commands/actions, verification results, remaining limitations, and next user action in both worklogs.

