# Curie Handoff Protocol

## Spawn

- Use `sessions_spawn` with `agentId: "curie"` and `cleanup: "keep"`, or omit `cleanup`.
- Do not use `cleanup: "delete"` for this workflow. Archive cleanup is secondary to delivery.
- Keep the task isolated and tell Curie to return one detailed Vietnamese dossier plus image inventory.
- The requester must be explicitly allowed to target Curie through `agents.list[0].subagents.allowAgents: ["curie"]`; a forbidden spawn is a configuration failure, not permission to make `main` do the discovery itself.
- Keep discovery bounded: at most three search attempts, three candidate fetches, three crawl pages, and one dossier. If `web_search` is unavailable, Curie may run one deterministic public-HTTP search batch of at most three queries (five results per query, 15 seconds per request); do not repeat it or start an ad-hoc `exec` loop. If that batch cannot produce defensible evidence, return `no_candidate_defensible` or `partial` with the evidence gap.
- Read project state only from `/home/minhmice/.openclaw/workflow/projects`; `/home/minhmice/.openclaw/workspace/workflow` is the shared context tree.

## Completion

- `sessions_spawn` is non-blocking; use `sessions_yield` and wait for the completion event in `main`.
- A cleanup error does not invalidate a completion payload. Log it and continue if the payload contains a defensible dossier.
- Curie does not own Discord delivery. `main` owns the review post and acknowledgment.

## Delivery

1. Write/verify the project record in `review`.
2. Send the compact dossier to `shit-that-could-cooking` (`1536658476288450630`). Attach up to five public first-party image URLs when available. Record every message ID returned by Discord, including split-message parts.
3. After the review send succeeds, send a Vietnamese acknowledgment to `discuss` (`1533645084229369996`) with the project ID and review location.
4. Retry one failed send; if it still fails, report the exact failure in `discuss` and keep the project in `review`.

Never auto-approve or start Website Brief/Project PM from discovery completion.
