# Lead Intelligence topology

topology.yaml is the logical source of truth for the bounded Lead Intelligence
flow. Files under roles/ define role-scoped inputs, outputs, tools, skills,
retry limits and evidence policy.

This manifest is intentionally not an OpenClaw physical-agent configuration.
The live OpenClaw version must be inspected before rendering any supported
subset. Secret values never belong in this directory; secret_refs contain
provider/environment names only.

The canonical flow uses independent gates:

- BusinessStrength >= 60
- AgencyFit >= 65
- DigitalGap >= 55

A role with execution_kind: deterministic runs locally whenever possible.
Only bounded LLM roles are dispatched, and the ranker receives structured
specialist outputs without crawl or search access.

## Friendly display names

The machine-facing `role_id` values are stable contract identifiers. The
human-facing names below are display labels only and may be changed without
renaming a role, stage, workspace, or API field.

| role_id | display_name |
| --- | --- |
| orchestrator | An Minh |
| market-intelligence | Ngọc Hân |
| discovery-scout | Gia Linh |
| entity-resolver | Khánh An |
| business-strength | Đức Minh |
| agency-fit | Thanh Vy |
| fast-web-screener | Yến Nhi |
| technical-auditor | Hoàng Nam |
| ux-conversion-auditor | Mai Anh |
| commercial-opportunity | Tuệ Lâm |
| dealability | Bảo Ngọc |
| evidence-verifier | Nhật Minh |
| red-team | Hải Yến |
| lead-ranker | Minh Châu |
| portfolio-selector | Quỳnh Anh |
| dossier-writer | Thảo My |
| redesign-intelligence | Yến My |
