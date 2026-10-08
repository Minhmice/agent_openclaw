# Website Redesign Agent — Compatibility Entry Point

The former monolithic contract is archived at `docs/archive/website-redesign-agent-spec-legacy.md`. This compatibility entry point is intentionally small so an agent can route to only the contract needed for its role.

Read `contracts/website-redesign-policy.md` first, then exactly one role contract:

| Role | Contract |
|---|---|
| Curie / research | `contracts/website-redesign-research.md` |
| Website Brief / design | `contracts/website-redesign-design.md` |
| Project PM / delivery | `contracts/website-redesign-pm.md` |

Use `contracts/website-redesign-output-schema.md` when validating a cross-agent package. Use the existing focused contracts for detailed machine fields and transitions: `curie-handoff.md`, `curie-report.md`, `website-to-pm.yaml`, `workflow-commands.md`, `QUALITY-GATES.md`, and `VIETNAMESE-LANGUAGE-POLICY.md`.

## Compatibility sequence

```text
public evidence → Curie dossier → approved lead → Website Brief package
  → approved redesign brief → PM page matrix → page approvals → offer-ready
```

The role contract is authoritative for stage-specific behavior. The archive is reference material only and must not be loaded by default.
