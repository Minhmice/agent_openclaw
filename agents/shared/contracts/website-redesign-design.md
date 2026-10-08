# Website Redesign — Website Brief Contract

This contract is for the approved-lead design brief stage. Read it with `website-redesign-policy.md`, `QUALITY-GATES.md`, `VIETNAMESE-LANGUAGE-POLICY.md`, and the Curie handoff/report artifacts.

## Mission and input

Given one approved business and its public evidence, produce a business-specific redesign direction that can be implemented page by page. The agent must demonstrate understanding of the company rather than apply a generic template.

Minimum input:

```yaml
website_url: https://example.com
```

Use the approved Curie artifacts as evidence. Preserve their confidence labels and do not silently upgrade an inference into a fact.

## Design sequence

1. Discover the approved page set and crawl text, CSS, metadata, assets, and public links within the bounded scope.
2. Capture desktop, tablet, and mobile evidence where the runtime permits; record unavailable captures instead of fabricating them.
3. Extract the current visual DNA: color, typography, spacing, shape, depth/materiality, grid/layout, imagery, and motion.
4. Diagnose the current experience as `KEEP`, `EVOLVE`, or `RETIRE`, preserving brand invariants and useful business signals.
5. Analyze category conventions and approved competitors/references. State what is observed versus inferred.
6. Select one redesign mode: `PRESERVE`, `REFRESH`, `EVOLVE`, or `OVERHAUL`, with evidence and tradeoffs.
7. Write a design thesis and creative north star grounded in the business truth.
8. Define a design genome: variance, motion, density, palette strategy, type character, spatial grammar, image direction, and signature element.
9. Generate 5–7 distinct visual worlds, score them against the thesis and evidence, and select one north star. Do not present seven cosmetic color swaps as seven worlds.
10. Generate semantic tokens, component grammar, responsive rules, motion grammar, image direction, and page blueprints.
11. Run a homepage critique loop, resolve or record issues, then freeze the package only after the applicable quality gate passes.

## Design quality bar

- The primary user, conversion path, message sequence, and proof are explicit for every major page.
- Every major visual decision traces to a business fact, evidence item, brand invariant, or stated hypothesis.
- The direction has deliberate contrast, hierarchy, density, and signature behavior; it is not a collection of decorative effects.
- Responsive behavior, accessibility basics, keyboard/focus states, image alternatives, and motion reduction are specified.
- Avoid category clichés, generic gradients, random blobs, endless repeated cards, and motion without meaning.
- Keep unresolved questions, confidence gaps, asset ownership, and content dependencies visible.

## Required output package

```text
website-analysis.json
content-inventory.json
visual-inventory.json
design-audit.json
competitive-positioning.json
redesign-brief.json
design-genome.json
design-tokens.json
component-system.json
page-blueprints.json
DESIGN.md
```

Optional evidence directories are `screenshots/`, `assets/`, `reference-board/`, and `generated-concepts/`. `DESIGN.md` must explain the thesis, north star, invariants, retired patterns, palette, typography, spatial grammar, imagery, components, page composition, responsive rules, accessibility, and do/don't rules.

## Handoff to PM

Send a compact Vietnamese handoff with project slug/ID, website URL, approved redesign mode, thesis, selected north star, `DESIGN.md` path, `page-blueprints.json` path, page list/dependencies, evidence gaps, and suggested page order/effort. Do not own scheduling or reminders.
