# Website Redesign — Curie Research Contract

This contract is for the lead-mining and evidence-collection stage. Read it with `website-redesign-policy.md`, `QUALITY-GATES.md`, `VIETNAMESE-LANGUAGE-POLICY.md`, `curie-handoff.md`, and `curie-report.md`.

## Mission

Find one defensible website-redesign prospect per day: a real business with a weak website and enough public evidence to explain why a redesign conversation may be useful. The output is an evidence-backed lead dossier, not a claim that revenue is being lost.

## Input

```yaml
website_url: https://example.com
business_name:
industry:
target_market:
preferred_language:
competitor_urls: []
reference_urls: []
known_brand_guidelines:
known_constraints:
```

`website_url` is required. Missing optional fields may be inferred only from public evidence and must carry confidence and source URLs.

## Bounded research sequence

1. Validate the URL, public reachability, robots policy, redirect target, and registrable domain.
2. Discover a bounded set of relevant public pages. Prioritize home, product/service, about, proof/case, contact, FAQ, and legal/trust pages.
3. Crawl each permitted page with rate limiting. Preserve status, canonical URL, title, description, headings, links, visible copy, structured data, public asset URLs, and capture metadata.
4. Capture technical evidence needed by the quality gates: broken links, mobile/accessibility clues, performance observations, screenshot paths, and unavailable checks.
5. Build the business truth: identity, offer, audience, geography, conversion path, proof, differentiators, trust signals, and explicit evidence gaps.
6. Build a content and image inventory. Prefer public first-party images; record `page_url`, `image_url`, alt text, relevance reason, and usage-rights status. If no image is defensible, keep the list empty and explain why.
7. Compare the site against the approved rubric and write an opportunity hypothesis with confidence; do not manufacture a competitor claim.
8. Run the applicable Curie gates before creating the review candidate or handoff.

## Required observation fields

For each page, record at minimum:

- `page_url`, `final_url`, `status_code`, `captured_at`, and source/provider.
- title, meta description, H1/headings, visible copy sections, CTA labels/targets, forms, navigation, and internal/external links.
- public image/media URLs, dimensions when available, alt text, first-party/third-party origin, and rights uncertainty.
- crawl/link errors, robots decisions, blocked or unavailable checks, and exact evidence paths.

For the business dossier, distinguish observed facts, inferred positioning, estimated opportunity, and unresolved questions. An absent signal is evidence of absence only when the bounded check covered the relevant page/source.

## Curie outputs

Create the required artifacts under the project root expected by the runtime and use the schemas/contracts already deployed:

```text
website-analysis.json
content-inventory.json
visual-inventory.json
design-audit.json
competitive-positioning.json
image-inventory.json
curie-to-website.json
```

The final review message and `curie-to-website.json` must include the project ID, website URL, lead status, evidence matrix, confidence gaps, rubric result, and the next action. Follow `curie-report.md` for the detailed dossier shape and `curie-handoff.md` for the state transition.

## Failure behavior

If discovery, crawl, screenshot, or provider access fails, retain the successful evidence, mark the result `partial`, and state the exact bounded failure. If the evidence gate cannot be met, return `no_candidate_defensible` and do not create a qualified review candidate.
