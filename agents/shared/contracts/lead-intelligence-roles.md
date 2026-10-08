# Lead Intelligence role boundaries

Logical roles are declared in `config/agents/topology.yaml` and the files below
`config/agents/roles/`. They are not automatically physical OpenClaw agents.
The local orchestrator owns ordering, validation, bounded retry, deduplication,
gates and persistence.

Boundary rules:

- deterministic checks run locally where possible;
- the ranker accepts structured specialist outputs only and has no crawler,
  search or provider access;
- UX/conversion audit receives no `BusinessStrengthScore`;
- business-strength receives no screenshot or design payload;
- technical audit receives no money thesis;
- Red Team receives the complete evidence package;
- no role fabricates buyer intent before an outreach event.
