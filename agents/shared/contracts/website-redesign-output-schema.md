# Website Redesign — Output and Definition of Done

This file is the compact cross-role output index. Machine-readable field requirements remain in the JSON/YAML schemas and contracts; this file defines when an artifact is safe to hand off.

## Artifact groups

Research artifacts:

```text
website-analysis.json
content-inventory.json
visual-inventory.json
design-audit.json
competitive-positioning.json
image-inventory.json
curie-to-website.json
```

Design artifacts:

```text
redesign-brief.json
design-genome.json
design-tokens.json
component-system.json
page-blueprints.json
DESIGN.md
website-to-pm.json
```

## Traceability minimum

Every artifact that contains a claim or decision records the project ID, source/evidence URL or artifact path, capture/creation time, and `observed`/`inferred`/`estimated` status where applicable. Cross-agent files preserve the exact schema keys and state names used by the runtime.

## Definition of done

- Business identity, audience, offer, geography, and primary conversion path are understandable.
- Evidence gaps, unavailable checks, asset ownership, and confidence are explicit.
- Brand invariants, `KEEP`/`EVOLVE`/`RETIRE`, redesign mode, thesis, north star, genome, tokens, components, and page blueprints exist.
- Responsive, accessibility, motion, SEO, CTA, and QA rules exist for the relevant pages.
- Homepage direction has passed critique and no required Gate 1/2/3 item is silently omitted.
- PM has an executable page matrix with owner, dependency, status, next action, and approval state.
