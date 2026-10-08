# Shared Agent Context

The website-redesign contract is modular. Read [website-redesign-policy.md](contracts/website-redesign-policy.md) plus the one role contract required by the current stage:

- Curie: [website-redesign-research.md](contracts/website-redesign-research.md)
- Website Brief: [website-redesign-design.md](contracts/website-redesign-design.md)
- Project PM: [website-redesign-pm.md](contracts/website-redesign-pm.md)

Use [website-redesign-output-schema.md](contracts/website-redesign-output-schema.md) for cross-agent artifact validation. [website-redesign-agent-spec.md](website-redesign-agent-spec.md) remains as a small compatibility entry point; the old monolithic detail is archived and must not be loaded by default.

The shared policy and role contracts are the source of truth for required output files and the decision order:

```text
business truth → brand truth → visual truth → design diagnosis
→ competitive/category context → new visual world → tokens/components/pages
```

The current site is both a source of business/brand evidence and an anti-reference for weak visual patterns. Do not simply imitate or beautify screenshots.
