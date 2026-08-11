# Discord Checklist Research — OpenClaw

## Request

Minh wants to tick completed tasks directly in Discord. When every required task is ticked, the project should be considered complete.

## Current capability findings

Remote OpenClaw:

```text
Version: 2026.7.1-2
Discord: connected
Permissions: SendMessages, EmbedLinks, ReadMessageHistory, AttachFiles, AddReactions, UseApplicationCommands
Missing required permissions: none
```

The current capability command advertises native commands, reactions, media, and message actions, but does not list `components` as a separate capability. The current OpenClaw documentation nevertheless describes Discord Components v2, buttons, select menus, modals, `allowedUsers`, and callback TTL. Treat native checklist buttons as feasible but requiring a controlled live send/interaction test before production rollout.

References:

- https://docs.openclaw.ai/channels/discord
- https://docs.openclaw.ai/plugins/message-presentation
- https://docs.openclaw.ai/cli/message

## Recommended v1 design

### User experience

Project PM posts a checklist card in `task`:

```text
📋 WEBSITE CHECKLIST
Pebsteel · vn-pebsteel-20260811

☐ Confirm scope and audience
☐ Finish homepage copy
☐ Check mobile layout
☐ Run CTA/link QA

Tiến độ: 0/4
```

Each item has a button. Clicking it changes the item to `✅`, records the actor and timestamp, and re-renders the card with the new progress count.

### Button semantics

- Button action: `/check <project_id> <task_id>` or an equivalent typed callback.
- Allowed users: Minh and Wien only for assigned tasks; use Discord `allowedUsers` when native components are enabled.
- Buttons are reusable until the checklist is complete or the callback TTL expires.
- Manual fallback remains available: `/check <project_id> <task_id>`.
- A stale/duplicate click is idempotent and returns the current state instead of toggling a completed item back to incomplete.

### State model

Each task stores:

```yaml
task_id: homepage-copy
label: Hoàn thiện homepage copy
status: todo|done|blocked
checked_by: 620891893659598850
checked_at: ISO-8601
owner_id: 620891893659598850
message_id: Discord message ID
```

Project stores:

```yaml
checklist:
  message_id: Discord checklist card ID
  tasks: []
  completed_count: 0
  total_count: 4
  completed_at: ISO-8601
```

### Completion gate

Recommended safe behavior:

```text
one tick
  → one task becomes done

all required tasks ticked
  → project becomes checklist-complete

all pages approved + both final confirmations
  → project becomes offer-ready
```

This keeps “all checklist tasks done” visible without silently bypassing stakeholder approval. If Minh explicitly wants tick-all to close the project regardless of approval, add a separate Minh-only override rather than weakening the default gate.

### Message tracking

Track the checklist card message ID in `project.json` so future reminders can link directly to it:

```yaml
message_tracking:
  review_message_url: https://discord.com/channels/...
  checklist_message_id: Discord message ID
  checklist_message_url: https://discord.com/channels/...
```

## Risks and mitigations

- Native component callback TTL defaults to 30 minutes in the current docs. Use a short-lived card or refresh the card from PM; never trust an old click without revalidating project/task state.
- If native components are unavailable at send time, render the same checklist as text with `/check` commands.
- Discord can split long checklist messages. Track every message part, but keep one canonical checklist card ID for interaction updates.
- A task click must verify actor, project status, assignment, task ID, and current state before mutation.
- “Project complete” must not equal “offer-ready” unless the existing page/approval gates have also passed.

## Rollout sequence

1. Implement deterministic `check-task` state transition and tests.
2. Implement text checklist fallback and message tracking.
3. Run one controlled native button send in `task` with Minh/Wien allowlist.
4. Verify click routing, idempotency, unauthorized click rejection, re-render, and callback expiry.
5. Enable native buttons for new PM cards; retain typed command fallback permanently.
