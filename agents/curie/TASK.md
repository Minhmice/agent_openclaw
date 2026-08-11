# Curie Current Task

## Assignment

Turn [IDEA.md](IDEA.md) into an implementation-ready product spec and phased plan for the lead-mining engine.

## Required first pass

Before writing code:

1. Inspect the repository and root runbook.
2. Propose a narrow MVP boundary.
3. Define phases small enough to build and verify independently.
4. Define JSON schemas and the lead/evidence data model.
5. Define deterministic scoring rules and the boundary between deterministic checks and AI judgment.
6. Define the discovery, crawl, technical-audit, screenshot, review, ranking, and report interfaces.
7. Compare tools/APIs, rate limits, cost assumptions, failure modes, and robots.txt/rate-limit handling.
8. Define acceptance criteria for one qualified lead per day.
9. Identify the minimum user decisions needed before implementation.

## Current user-facing behavior

The agent should ask one decision question at a time. The first unresolved question is the MVP market scope in [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md).

## Hard constraints

- Discussion and planning first.
- No code until the user approves the plan.
- No cron creation until the user approves the plan.
- No remote OpenClaw modification until explicitly approved.
- No password, token, key, cookie, provider secret, or session data in files or output.
- No unsupported claims based only on the dated baseline.
