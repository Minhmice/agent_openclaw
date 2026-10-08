# Lead Intelligence stage contract

The stable stage order is:

```text
define_market → discover → resolve_entities → cheap_filter → business_fit
→ agency_fit → digital_gap → deep_audit → commercial_opportunity → dealability
→ evidence_verification → red_team → score_survivors → rank
→ portfolio_selection → human_approval → redesign_intelligence
```

Every stage emits a versioned structured outcome with `stage_id`, `run_id`,
`producer_role`, `status`, `attempt_count`, timestamps, input/output refs,
evidence IDs, confidence, an error code and a resumable allowlisted
`checkpoint`. A checkpoint never contains credentials, cookies, session data,
private keys or raw provider payloads.

The redesign subflow is only available after human approval:

```text
business_truth → content_inventory → visual_dna → keep_evolve_retire
→ redesign_mode → design_direction → prototype → design_system
→ page_blueprints
```
