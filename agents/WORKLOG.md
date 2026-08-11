# Agent Workflow Worklog

This file records every implementation/review session for the OpenClaw multi-agent workflow. Never put passwords, tokens, private keys, cookies, provider secrets, or session content here.

## 2026-08-11 — Remote sync attempt after credential handoff

### Request

- Minh requested live sync of the Discord owner/exec-approval parity fix and the model timeout fix.

### Verification

- Checked the runner without revealing secret values: `OPENCLAW_SSH_KEY`, `OPENCLAW_SSH_PASSWORD`, `OPENCLAW_SSH_HOST`, and `OPENCLAW_SSH_USER` are unset.
- Checked the current app terminal: no interactive terminal session is attached.
- Non-interactive SSH reached `100.121.246.38` but returned `Permission denied (publickey,password)`.

### Status

- No remote file, config, service, or Discord state was mutated.
- Waiting for an SSH key or approved secret-provider integration in the agent runner. Passwords must not be embedded in commands, logs, or repository files.

## 2026-08-11 — Diagnose model idle timeout

### Evidence

- OpenClaw reported: `LLM request timed out. The model did not produce a response before the model idle timeout.`
- No provider ID, primary model, current timeout, or run-specific timeout was available in this local documentation-only repository.

### Root-cause boundary

- The failure can occur at the provider request timeout, `agents.defaults.timeoutSeconds`, or a lower cron/run-specific ceiling.
- The correct provider-specific timeout cannot be selected safely until the live effective model/provider is inspected.

### Planned narrow fix

- Read the primary model, provider ID, current provider timeout, agent default timeout, subagent timeout, and cron timeout using redacted diagnostics.
- Raise `models.providers.<providerId>.timeoutSeconds` first for the affected slow provider, then raise only the lower outer ceiling if necessary.
- Keep fallback routing and security policy unchanged.

## 2026-08-11 — Diagnose Discord exec approval denial for Wien

### Evidence

- Discord showed `You are not authorized to approve exec requests on Discord.` when Wien pressed the native `approve` control.
- This is OpenClaw's host-exec approval layer, not the workflow coordinator's `/approve <project_id>` handler.

### Root cause

- The previous workflow change only allowed Wien in the project state coordinator. It did not change OpenClaw's owner/exec-approval configuration.
- OpenClaw requires Wien to be present in both `commands.ownerAllowFrom` and `channels.discord.execApprovals.approvers` for parity with Minh.

### Planned narrow fix

- Add only Minh (`620891893659598850`) and Wien (`859783610625556480`) to those two allowlists.
- Preserve all unrelated config and do not enable wildcard access or `security: "full"`.

### Remote status

- Not applied yet: this session still has no SSH key or secret-provider credential. The host is reachable, but non-interactive SSH returns `Permission denied (publickey,password)`.

## 2026-08-11 — Allow Wien to approve Curie review leads

### Request

- Minh requested that Wien (`859783610625556480`) also be allowed to approve a Curie lead from `review`.

### Local changes

- Updated the deterministic coordinator so `/approve <project_id>` accepts Minh (`620891893659598850`) or Wien and records the actual actor in `approved_by`.
- Kept `/reject` and `/request-change` Minh-only.
- Updated the command contract, coordinator rules, PM role documentation, quality gate, and Curie-to-Website handoff description.
- Extended the workflow test to cover Wien approval, actual `approved_by` recording, and rejection of an unknown actor.

### Verification

- `10/10` workflow tests passed.
- Python compilation passed.
- `git diff --check` passed.

### Remote status

- Remote sync is pending because this agent session has no configured SSH key or secret-provider credential. The host was reachable, but `BatchMode` SSH returned `Permission denied (publickey,password)`.

## 2026-08-11 — Workflow implementation approved

### User request

Implement the complete workflow:

```text
Curie discovery/scraping
→ Minh review
→ Website Brief Agent
→ Checklist/Project-PM Agent
→ Minh + Wien completion confirmation
→ offer-ready handoff
```

Discord routing:

- Review: `1536658476288450630`
- Task: `1533643473486348458`
- Offer-ready: `1536659097649422356`
- Minh: `620891893659598850`
- Wien: `859783610625556480`

### Read-only findings before write

- Remote OpenClaw version: `2026.7.1-2`.
- Only configured agent: `main`.
- Existing cron jobs: none.
- Discord capability includes send/read/edit/thread/poll/reaction actions but no native buttons/components.
- Existing Discord guild config contains only `task` and `discuss` channel entries.
- Gateway is user-level systemd, loopback-bound at `127.0.0.1:18789`.

### Decision

- Use isolated OpenClaw agents for `curie`, `website-brief`, and `project-pm`.
- Use explicit typed commands as the approval control; optionally accept Minh's ✅ reaction if message identity and actor checks are reliable.
- Use a 30-minute active-task reminder with event-driven updates, suppressing reminders when nothing changed.
- Create local and remote worklogs; append an entry after every implementation/verification session.

### Work performed in this session

- Read current remote CLI capabilities and config shape.
- Created the implementation plan and contracts locally.
- Created remote backup checkpoint:
  `/home/minhmice/.openclaw/backups/workflow-20260811T094000Z`
- Created remote workflow directories and `/home/minhmice/.openclaw/workflow/WORKLOG.md`.
- No agents, config, cron, or Discord message has been changed yet.

### Rollback

- Local: revert the workflow commit(s).
- Remote: restore the timestamped OpenClaw config/workspace backup and remove newly added cron jobs/agents by recorded IDs.

### Next step

Create the remote backup, then add isolated agents and upload their instructions/contracts.

### Plan

Detailed plan: [2026-08-11-openclaw-multi-agent-workflow.md](../docs/superpowers/plans/2026-08-11-openclaw-multi-agent-workflow.md).

The plan is being executed inline after Minh explicitly approved the complete workflow.

## 2026-08-11 — Coordinator completion, reminder validation, and remote smoke test

### Work performed

- Extended `agents/shared/workflow-coordinator.py` with the remaining typed-command handlers: `reject`, `request-change`, `page-status`, `page-approve`, and `block`.
- Added page assignment checks, checklist-complete gating, unresolved P0/P1 gating, and the missing `stakeholder-review → approved` transition.
- Updated the command contract and coordinator instructions so Minh and Wien can each issue `final-confirm`; `offer-ready` still requires both confirmations.
- Added `tests/test_workflow_coordinator.py` covering authorization, page checklist gating, pending-page reminders, and the complete synthetic happy path. Local result: `3/3` tests passed; Python compile and `git diff --check` passed.
- Created reminder cron `project-pm-active-reminders` with ID `9b083499-7412-47fb-bdaa-39083da68e84`, every 30 minutes, isolated `project-pm` session, explicit delivery to task channel `1533643473486348458`. It is enabled.
- The first manual cron run timed out and was recorded as an execution error caused by an inline heredoc attempt inside the PM agent. The prompt was tightened to use the existing coordinator executable directly and forbid heredocs/inline-generated scripts.
- The controlled retry completed with `status: ok`, `summary: HEARTBEAT_OK`, and `delivered: false` because there were no active projects requiring a reminder.
- Uploaded the completed coordinator, command contract, and coordinator instructions to the remote main workspace.
- Ran a synthetic remote state-machine test and cleaned up its temporary project. Verified unauthorized Minh-only approval rejection, unauthorized page update rejection, page approval, first final confirmation holding at `stakeholder-review`, and second confirmation reaching `offer-ready` with channel `1536659097649422356`.

### Verified remote state

- Gateway service: active.
- OpenClaw health: OK; Discord connected; event-loop health recovered to non-degraded.
- Agents: `main`, `curie`, `website-brief`, `project-pm` present with isolated workspaces.
- Backup checkpoint remains `/home/minhmice/.openclaw/backups/workflow-20260811T094000Z`.
- No real lead, project, website brief, Discord review message, or offer-ready message was created by the synthetic test.

### Limitations and next action

- Current Discord capability still has no native button/component support; typed commands remain canonical, with reactions only as a verified optional fallback.
- The workflow is ready to receive a real Curie dossier. The next real action is to place a lead handoff in the review channel and wait for Minh's `/approve <project_id>` before Website Brief and PM processing.
- Every future implementation or remote-change session must append a new dated section here and to the remote worklog.

## 2026-08-11 — Curie discovery dry-run and review-channel test

### Work performed

- Ran Curie against public web sources for exactly one discovery candidate. Curie used `web_search` and `web_fetch` only; tool trace reported 28 calls with no tool failures.
- Candidate: `vn-zamilsteel-20260811`, Zamil Steel Buildings Vietnam Co., Ltd, `https://zamilsteel.com.vn/`.
- Curie produced an evidence-backed discovery dossier with public URLs, five website opportunity hypotheses, an inferred-only money opportunity, and explicit confidence gaps. No unsupported revenue number was used.
- Created the remote project record in `review` state and stored the dossier under the project directory. No Website Brief or Project PM stage was started.
- Posted the concise review card to channel `1536658476288450630`. Discord send succeeded with message ID `1536680224882950238`.
- Removed the temporary local seed helper after execution; it contained no credential values and was not committed.

### Review status

- Awaiting Minh's decision. Valid next commands:

  ```text
  /approve vn-zamilsteel-20260811
  /reject vn-zamilsteel-20260811 <reason>
  /request-change vn-zamilsteel-20260811 <note>
  ```

- The candidate remains blocked at review until Minh explicitly approves it.

## 2026-08-11 — Vietnamese language policy for all workflow agents

### Design decision

- `main`, `curie`, `website-brief`, and `project-pm` now use Vietnamese for all human-facing replies, Discord messages, reminders, reviews, handoffs, checklist text, and narrative Markdown artifacts.
- Machine-facing interfaces remain stable: project IDs, commands, state names, channel IDs, actor IDs, URLs, file paths, artifact filenames, JSON/YAML keys, and evidence URLs are not translated.
- Facts, inferences, estimates, and confidence gaps must remain explicitly separated in Vietnamese. Technical terms may stay in English in parentheses where that prevents ambiguity.

### Work performed

- Added [VIETNAMESE-LANGUAGE-POLICY.md](shared/VIETNAMESE-LANGUAGE-POLICY.md).
- Updated the local root instructions, coordinator instructions, and the Curie, Website Brief, and Project PM dossiers.
- Uploaded the policy to the remote main workflow workspace and each isolated agent context.
- Appended direct language guards to the remote `AGENTS.md` and each isolated agent `ROLE.md`.

### Verification

- Smoke-tested all four remote agents with a no-write, no-Discord prompt.
- `main`: replied `Tôi sẵn sàng giao tiếp bằng tiếng Việt.`
- `curie`: replied `Tôi sẵn sàng giao tiếp bằng tiếng Việt.`
- `website-brief`: replied `Đã sẵn sàng giao tiếp bằng tiếng Việt.`
- `project-pm`: replied `Mình vừa khởi động và sẵn sàng giao tiếp bằng tiếng Việt.`
- No downstream project state or Discord message was changed by the smoke test.
- Evidence limitations are recorded: no visual screenshot audit, no analytics/performance data, no conversion data, and some public fetches were truncated by tool/rate limits.

## 2026-08-11 — Natural-language Curie trigger from `discuss`

### User request

When Minh writes a Vietnamese message such as `oke thử cho tìm một con khác đi` in `discuss`, Curie should find one fresh business candidate.

### Design

- Scope the trigger to `discuss` channel `1533645084229369996` and Minh actor `620891893659598850`.
- Match a clear affirmative/request cue plus a fresh-target cue; tolerate case, punctuation, and ordinary spacing differences.
- Start exactly one isolated Curie run, exclude active/review project duplicates, create the result in `review`, and post it to review channel `1536658476288450630`.
- Never auto-approve and never invoke Website Brief or Project PM from this natural-language trigger.
- If the message is ambiguous, ask Minh to clarify. If Curie cannot produce a defensible lead, report no result rather than creating a fake project.

### Contract and verification

- Added [discuss-intents.md](shared/contracts/discuss-intents.md) and updated coordinator instructions in local and remote main workspaces.
- Smoke-tested the simulated event with `main`: it classified `new-curie-discovery`, routed exactly one isolated Curie run, kept the candidate unapproved, skipped Website Brief/PM, and targeted review channel `1536658476288450630`.
- The smoke test did not call Curie, write project state, or send Discord.

## 2026-08-11 — Curie handoff reliability and detailed image-aware reporting

### Finding

- A real `discuss` trigger spawned Curie but surfaced `cleanup delete failed`. OpenClaw documentation confirms sub-agent completion returns to the requester session, while `cleanup: "delete"` is best-effort archive cleanup after announce; cleanup failure must not be treated as Curie failure.
- The previous routing did not explicitly tell `main` to post Curie output to review or acknowledge completion in `discuss`.

### Design change

- Use `cleanup: "keep"` or omit cleanup for Curie discovery.
- Wait for completion with `sessions_yield`, then let `main` explicitly send the review post to `shit-that-could-cooking` (`1536658476288450630`).
- After successful review delivery, send a Vietnamese acknowledgment to `discuss` (`1533645084229369996`) with project ID and review location.
- Keep Website Brief and Project PM blocked until Minh approves.
- Add a detailed Curie report contract with page-level audit, issue severity, evidence matrix, confidence gaps, and first-party image evidence.
- Attach up to five defensible public images when available; otherwise record `image_evidence: []` and explain why.

### Research basis

- Official OpenClaw sub-agent documentation: `sessions_spawn` is non-blocking, completion returns to the requester, cleanup delete archives after announce, and channel delivery parameters are not part of `sessions_spawn`.
- Official Discord documentation: `openclaw message send --channel discord --target channel:<id>` is the explicit delivery path, and media attachments can be sent separately from the text post.

## 2026-08-11 — Deterministic PM reminder dispatch and task-channel formatting

### Root cause

- The reminder cron was an `agentTurn`. Even with a prompt telling Project PM to run the existing coordinator file, the model could still attempt an inline Python heredoc. Run history confirmed intermittent success and the reported `python inline script (heredoc)` failure.

### Fix

- Added `reminder-dispatch` to `workflow-coordinator.py`; it reads the canonical state, formats Vietnamese reminders, and sends them directly with `openclaw message send`.
- Switched cron `project-pm-active-reminders` (`9b083499-7412-47fb-bdaa-39083da68e84`) from `agentTurn` to `command`, with `agentId: null`, `delivery.mode: none`, and a fixed command invoking `reminder-dispatch`.
- Kept Project PM available for project/page checklist work; only periodic reminder delivery is deterministic and model-free.
- Added a Discord-friendly visual format: header, business name, project/status line, grouped action details, next step, exact commands, and a quiet footer. Review-only projects without pages get approve/request-change actions instead of a confusing empty page checklist.

### Verification

- Local formatter/state tests: `5/5` passed.
- Remote dry-run produced the expected Vietnamese message for `vn-zamilsteel-20260811`.
- Manual cron execution returned `status: ok`, `summary: REMINDERS_SENT=1`, `exitCode: 0`, with no model/agent execution.
- Task channel received formatted message ID `1536691948587327592`.
- Old heredoc failure remains only as historical message/run history; future cron runs use the command payload.

## 2026-08-11 — Reminder links and Discord checklist research

### Reminder link change

- `reminder-dispatch` now renders `message_tracking.review_message_url` as a clickable `🔗 Bài review` line beside `/approve` and `/request-change` for review-stage projects.
- Existing verified review IDs for ATAD, Zamil, and Pebsteel were backfilled; Pebsteel has two tracked review parts because Discord split the dossier.

### Checklist research

- Verified live Discord permissions and OpenClaw version `2026.7.1-2`.
- Official docs describe Components v2 buttons/selects/modals with `allowedUsers` and callback TTL, while the current capability probe does not advertise components separately.
- Wrote [Discord checklist research](../docs/research/2026-08-11-discord-checklist-openclaw.md). Recommended rollout: deterministic check-task state first, native button test second, permanent typed-command fallback.
- Recommended semantics: each tick completes one task; all ticks produce `checklist-complete`; `offer-ready` still requires page approvals and final confirmations unless Minh explicitly overrides.

## 2026-08-11 — End-to-end workflow audit and shared quality gates

### Live audit

- Gateway/Discord healthy: `openclaw health` OK, Discord connected, event loop non-degraded.
- Reminder cron healthy: fixed `command` payload, `agentId: null`, enabled, last run OK.
- Durable task audit: zero stale/failed/lost background tasks.
- Removed one leftover synthetic project from remote workflow state; real projects were preserved.
- Real projects currently remain in `review` until Minh acts; no coding agent was created.

### Improvements

- Added 120-minute reminder cooldown keyed by stable project/status/checklist/link signature; unchanged reminders are skipped, changed state sends immediately.
- Added shared [QUALITY-GATES.md](shared/QUALITY-GATES.md) for Curie, Website Brief, PM, and main coordinator.
- Uploaded the coordinator/script/contracts to remote after each change.

### Research conclusion

- Current agent workflow is ready for another hardening pass and later coding-agent integration, but native Discord checklist buttons should be rolled out as a separate controlled phase with typed-command fallback retained.

## 2026-08-11 — Track discovery messages and support safe discard

### User request

- On a new-lead request in `discuss`, show an immediate “đang tìm” message, then return “đã tìm được” with a direct link to the review message in `shit-that-could-cooking`.
- When Minh says “bỏ thằng này đi” or equivalent, delete the tracked bot messages for that candidate.

### Design

- Added guild-aware Discord message links using guild `1446612692910739637`.
- Track only bot-owned IDs in `project.json`: search-started message in `discuss`, completion acknowledgment in `discuss`, and review dossier message in `shit-that-could-cooking`.
- Added `record-messages` and `discard` coordinator commands. `discard` is Minh-only, marks the project `rejected`, deletes only tracked bot messages, records failures, and never deletes the user's original command.
- Expanded the natural-language discuss contract with progress, completion-link, and discard behavior.

### Verification

- Added message URL/tracking tests; local suite now passes `7/7`.
- No real candidate messages were deleted during this implementation session.

### Follow-up detail

- Review posts may be split into multiple Discord messages. Tracking now stores `review_message_ids` and discard deletes all tracked review parts, while the acknowledgment link points to the first part.
- The Pebsteel message ID supplied by Minh was verified as a review post; its evidence continuation is a second message, so future tracking must preserve both IDs.

## 2026-08-11 — Discovery progress, direct-link acknowledgment, and safe discard

### Work performed

- Added immediate progress-message tracking for new Curie searches in `discuss`.
- Added direct Discord message-link generation using guild `1446612692910739637`.
- Added completion acknowledgment requirements: post to `shit-that-could-cooking`, then reply in `discuss` with the first review message link and project ID.
- Added safe discard lifecycle: Minh-only `discard` resolves tracked bot messages, deletes review/progress/ack parts, marks the project `rejected`, and never deletes the original user message.
- Backfilled Pebsteel review parts `1536692489602338917` and `1536692493138264084` into tracking without deleting them.

### Verification

- Synthetic remote `record-messages` + `discard --dry-run` passed; project state stayed unchanged during dry-run.
- Main lifecycle smoke-test confirmed progress reply, first-part review link, split-message tracking, and original-user-message protection.
