# Website Redesign Agent — Research, Extraction & Design Generation Specification

> Purpose: Give an autonomous agent enough structured information to crawl an existing business website, understand what must be preserved, diagnose what should change, and generate a coherent new visual design system and redesign direction.

---

## 0. Core Principle

A redesign is **not** a prettier copy of the current website.

The agent must separate:

1. **Product / Business Truth** — what the company is, offers, proves, says, and needs users to do.
2. **Brand Truth** — recognizable brand assets and identity signals worth preserving.
3. **Existing Visual Truth** — how the current website expresses the brand.
4. **Design Problems** — patterns that reduce clarity, hierarchy, trust, usability, or distinctiveness.
5. **New Visual World** — a deliberate replacement system derived from the business, audience, brand, and market context.

Treat the current site as both:
- a **source of truth** for business/content/brand evidence;
- an **anti-reference** for visual decisions that are weak, generic, inconsistent, or outdated.

---

# 1. Agent Goal

Given one business website URL, produce:

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

Optional:

```text
screenshots/
assets/
reference-board/
generated-concepts/
```

---

# 2. Input Contract

Minimum required input:

```yaml
website_url: https://example.com
```

Optional:

```yaml
business_name:
industry:
target_market:
preferred_language:
competitor_urls: []
reference_urls: []
known_brand_guidelines:
known_constraints:
```

If optional information is missing, infer it from the website and mark confidence.

---

# 3. Crawl Strategy

The crawler must inspect:

## 3.1 Page discovery

Capture:

- homepage
- primary navigation pages
- product/service pages
- about/company page
- pricing page
- case studies/projects/portfolio
- testimonials/reviews
- blog/resources
- contact
- careers
- legal pages
- high-value landing pages
- footer-only pages

Build:

```json
{
  "url": "",
  "title": "",
  "page_type": "",
  "depth": 0,
  "nav_location": "primary|secondary|footer|internal",
  "priority": 1
}
```

## 3.2 For every page collect

- raw text
- semantic headings H1–H6
- links
- CTA labels
- forms
- media assets
- visible navigation
- page title/meta description
- structured data if present
- major sections
- screenshots at desktop/tablet/mobile
- computed CSS where possible

Viewport targets:

```text
Desktop: 1440 px
Tablet: 768–1024 px
Mobile: 375–430 px
```

---

# 4. Product / Business Truth Extraction

Do not redesign before this is known.

Extract:

```yaml
business:
  name:
  category:
  subcategory:
  geography:
  b2b_or_b2c:
  business_model:

audience:
  primary:
  secondary:
  likely_jobs_to_be_done:
  pain_points:
  buying_motivations:
  trust_requirements:

offer:
  products:
  services:
  pricing_model:
  differentiators:
  guarantees:
  delivery_model:

positioning:
  value_proposition:
  unique_selling_points:
  category_language:
  tone_of_voice:
  emotional_promise:

proof:
  testimonials:
  ratings:
  awards:
  certifications:
  clients:
  case_studies:
  statistics:
  years_in_business:
  team_credentials:

conversion:
  primary_cta:
  secondary_cta:
  conversion_paths:
  contact_methods:
```

Every inferred field should include:

```yaml
value:
confidence: 0-1
evidence:
  - source_url:
    extracted_text:
```

---

# 5. Content Inventory

For each reusable content entity capture:

```yaml
content_id:
type:
source_url:
raw_copy:
purpose:
audience:
importance:
proof_strength:
reuse_status:
```

Types:

```text
headline
subheadline
paragraph
CTA
feature
benefit
service
product
testimonial
statistic
case-study
logo
award
FAQ
team-member
contact
location
legal
```

Classify:

```text
KEEP
REWRITE
MERGE
MOVE
REMOVE
UNKNOWN
```

Do not remove business-critical information only because it looks visually outdated.

---

# 6. Existing Visual DNA Extraction

Extract the site's visual system even if it is inconsistent.

## 6.1 Color

Capture:

- hex/RGB values
- frequency
- semantic use
- background/text/border/CTA state
- approximate contrast pairings

Output:

```yaml
colors:
  - value: "#000000"
    usage:
    frequency:
    semantic_role:
    confidence:
```

## 6.2 Typography

Capture:

- font families
- fallback stacks
- weights
- sizes
- line heights
- letter spacing
- text transforms
- paragraph width
- heading hierarchy

Build approximate type scale.

## 6.3 Spacing

Measure recurring:

```text
4 / 8 / 12 / 16 / 24 / 32 / 48 / 64 / 96...
```

Infer:
- base spacing unit
- section spacing
- component padding
- grid gutters

## 6.4 Shape

Capture:

- border radius
- pill usage
- border thickness
- dividers
- card geometry
- image masking
- button geometry

## 6.5 Depth

Capture:

- box shadows
- overlays
- gradients
- glass effects
- blur
- elevation hierarchy

## 6.6 Grid / Layout

Measure:

- max content width
- side margins
- grid column count
- gutter width
- common section widths
- full-bleed usage
- alignment logic
- asymmetric patterns
- whitespace density

## 6.7 Imagery

Capture:

- photography vs illustration
- people/product/environment
- crop ratios
- color grading
- lighting
- background style
- image density
- subject placement
- stock-photo feel vs original photography
- icon family
- illustration language

## 6.8 Motion

Capture:

- hover states
- scroll reveals
- parallax
- marquees
- carousels
- page transitions
- menu transitions
- duration
- easing
- trigger

---

# 7. Section-Level Semantic Screenshot Analysis

Every major visual section should become an analyzable record.

```yaml
section_id:
page_url:
section_order:
section_type:
purpose:
primary_message:
secondary_message:
primary_cta:
secondary_cta:
layout_family:
alignment:
visual_weight:
density:
whitespace:
type_hierarchy:
image_treatment:
background_treatment:
component_patterns:
mobile_behavior:
problems:
strengths:
```

Suggested `section_type` vocabulary:

```text
hero
logo-wall
intro
feature-grid
service-grid
split-content
timeline
process
stats
gallery
case-study
testimonial
pricing
FAQ
CTA-banner
contact
footer
```

Suggested `layout_family` vocabulary:

```text
centered
left-editorial
split-50-50
split-asymmetric
grid
masonry
stacked
full-bleed
bento
carousel
horizontal-scroll
```

---

# 8. Design Diagnosis

Score every meaningful pattern from `1–10`.

```yaml
brand_specificity:
visual_hierarchy:
clarity:
consistency:
readability:
trust:
emotional_fit:
content_fit:
responsive_quality:
accessibility:
distinctiveness:
```

Then classify:

```text
KEEP
EVOLVE
RETIRE
```

## KEEP

Use when a pattern:
- strongly represents the brand;
- is recognizable;
- supports hierarchy;
- is coherent across pages;
- has high business value.

## EVOLVE

Use when:
- concept is correct;
- execution is weak;
- spacing/type/layout needs modernization;
- component can be preserved in spirit.

## RETIRE

Use when:
- generic template pattern;
- weak hierarchy;
- visual clutter;
- inconsistent;
- inaccessible;
- outdated;
- redundant;
- conflicts with desired positioning.

Every classification requires a reason.

---

# 9. Detect Generic / Cliché Design Patterns

Flag patterns such as:

- generic gradient hero
- random floating blobs
- excessive glassmorphism
- meaningless 3-card feature grids
- alternating image/text sections repeated endlessly
- arbitrary rounded cards everywhere
- stock SaaS illustrations unrelated to product
- giant heading without meaningful hierarchy
- decorative motion without communication value
- overuse of badges/pills
- icon soup
- fake dashboard mockups
- too many competing CTA styles

Output:

```yaml
pattern:
severity:
why_generic:
business_cost:
replacement_direction:
```

---

# 10. Competitive / Category Analysis

If competitor URLs are available, compare them.

If not, infer likely category conventions from the business.

For each competitor/reference capture:

```yaml
positioning:
visual_style:
palette:
typography:
hero_pattern:
content_density:
CTA_strategy:
image_strategy:
layout_patterns:
brand_distinctiveness:
strengths:
weaknesses:
opportunities_to_differentiate:
```

The goal is not imitation.

Goal:

```text
Understand category conventions
→ know what users expect
→ identify visual sameness
→ deliberately differentiate where safe
```

---

# 11. Redesign Mode

Choose one:

```text
PRESERVE
REFRESH
EVOLVE
OVERHAUL
```

## PRESERVE
Strong brand/system. Improve execution only.

## REFRESH
Keep brand language; modernize spacing, typography, components, imagery.

## EVOLVE
Keep recognizable brand DNA; create substantially new visual grammar.

## OVERHAUL
Current visual system conflicts with positioning or has little value. Rebuild direction from product truth.

Output reason and confidence.

---

# 12. Design Thesis

Before generating UI, write one concise design thesis.

Format:

```text
For [audience], the website should feel [3–5 attributes],
using [visual mechanisms],
so that [business/brand outcome].
```

Example:

```text
For engineering buyers, the website should feel precise, capable,
industrial, and premium, using disciplined typography, technical grid
structures, restrained color, and close-up material photography,
so the company appears like a high-trust specialist rather than
a generic supplier.
```

---

# 13. Creative North Star

Define the site's visual world.

Avoid vague words alone such as:

```text
modern
clean
premium
minimal
professional
```

Translate each adjective into observable design behavior.

Example:

```yaml
premium:
  means:
    - restrained palette
    - fewer but larger images
    - high whitespace
    - precise typography
    - minimal borders
    - controlled motion
  does_not_mean:
    - black background everywhere
    - gold gradient
    - serif font by default
```

---

# 14. Design Genome

Generate:

```yaml
brand_core:
  personality:
  emotional_promise:
  recognizable_assets:

visual_invariants:
  - ...

anti_references:
  - ...

design_thesis:

creative_north_star:

variance: 1-10
motion: 1-10
density: 1-10

palette_strategy:
type_character:
spatial_grammar:
shape_language:
materiality:
image_direction:
icon_direction:
motion_grammar:
signature_element:

composition_rules:
responsive_rules:

dos:
  - ...

donts:
  - ...
```

## Variance

```text
1 = conservative/category-safe
5 = visibly differentiated
10 = highly unconventional
```

## Motion

```text
1 = almost static
5 = purposeful interaction
10 = motion-led experience
```

## Density

```text
1 = spacious/editorial
5 = balanced
10 = dense/information-heavy
```

---

# 15. Generate Multiple Visual Worlds

Never jump directly to one design.

Generate `5–7` directions.

Each direction:

```yaml
name:
concept:
business_rationale:
audience_fit:
palette:
typography:
layout:
image_style:
shape_language:
motion:
signature_element:
risk:
distinctiveness_score:
```

Example direction families:

```text
Editorial Authority
Technical Precision
Human Craft
Bold Utility
Quiet Luxury
Industrial Modernism
Playful Expertise
Institutional Trust
```

Do not use these names mechanically; derive them from the business.

---

# 16. Direction Selection

Score each direction:

```yaml
brand_fit: /10
audience_fit: /10
business_fit: /10
distinctiveness: /10
scalability: /10
content_fit: /10
implementation_feasibility: /10
```

Weighted score suggestion:

```text
brand_fit          20%
audience_fit       20%
business_fit       20%
distinctiveness    15%
scalability        10%
content_fit        10%
feasibility         5%
```

Select one primary direction.

Optionally keep one alternate.

---

# 17. Design Token Generation

Generate semantic tokens, not isolated values.

## 17.1 Color

```yaml
color:
  brand:
    primary:
    secondary:
    accent:
  text:
    primary:
    secondary:
    muted:
    inverse:
  bg:
    base:
    subtle:
    elevated:
    inverse:
  border:
    default:
    strong:
  action:
    primary:
    primary_hover:
    secondary:
  state:
    success:
    warning:
    error:
```

## 17.2 Typography

```yaml
font:
  display:
  heading:
  body:
  mono:

type:
  display-xl:
  display-lg:
  h1:
  h2:
  h3:
  h4:
  body-lg:
  body:
  body-sm:
  label:
  caption:
```

Each includes:

```yaml
font-size:
line-height:
font-weight:
letter-spacing:
```

## 17.3 Spacing

```yaml
space:
  1:
  2:
  3:
  4:
  5:
  6:
  8:
  10:
  12:
  16:
  20:
  24:
```

## 17.4 Other tokens

```yaml
radius:
shadow:
border:
container:
grid:
breakpoint:
motion-duration:
motion-easing:
z-index:
```

---

# 18. Typography Requirements

Typography must define a hierarchy, not just fonts.

Check:

- H1 clearly dominates
- H2/H3 distinguishable
- paragraph line length controlled
- line-height supports readability
- display typography used selectively
- weights not overused
- responsive scaling behaves intentionally
- labels/buttons remain legible

Avoid:

- too many font families
- every heading bold
- tiny body text
- extreme tracking
- giant display type everywhere

---

# 19. Color Requirements

Color must communicate semantic roles.

Check:

- brand recognition
- CTA priority
- sufficient text/background contrast
- state colors
- hover/focus states
- light/dark surface relationships
- no accidental rainbow palette
- accent color used deliberately

Color should support hierarchy, not decorate arbitrarily.

---

# 20. Spatial Grammar

Define how space behaves.

Example:

```yaml
page_container:
  max_width: 1280px

section_spacing:
  desktop: 120px
  tablet: 88px
  mobile: 64px

grid:
  desktop_columns: 12
  tablet_columns: 8
  mobile_columns: 4
```

But numbers should be generated from chosen direction.

Define:

- section rhythm
- content width
- narrow reading width
- grid behavior
- full-bleed conditions
- asymmetry rules
- overlap rules
- vertical cadence

---

# 21. Shape Language

Define visual geometry globally.

Examples:

```text
sharp / architectural
soft / rounded
pill-heavy
mixed radius
editorial rectangles
technical lines
organic masks
```

Avoid random geometry.

All components should feel like they belong to the same visual world.

---

# 22. Materiality

Define surface character:

```text
flat
paper
metallic
glass
soft-shadow
ink/editorial
grain
technical grid
photographic
monochrome
```

Use materiality sparingly and consistently.

---

# 23. Image Art Direction

Define:

```yaml
subject:
environment:
camera_distance:
composition:
lighting:
color_grade:
background:
crop_rules:
aspect_ratios:
people_style:
product_style:
illustration_style:
forbidden_styles:
```

Example:

```text
Real workshop photography.
Close-up material details.
Directional natural light.
Neutral grade.
Avoid generic handshake/business stock photos.
```

Images often determine whether a site feels custom or templated.

---

# 24. Motion Grammar

Motion must have purpose.

Define:

```yaml
entry_motion:
hover_motion:
scroll_motion:
navigation_motion:
page_transition:
duration_range:
easing:
reduced_motion_behavior:
```

Use motion for:

- hierarchy
- causality
- state change
- spatial orientation
- product explanation

Avoid motion purely to impress.

---

# 25. Signature Element

Every new direction should identify one memorable visual mechanism.

Examples:

- a distinctive typographic treatment
- unusual grid rhythm
- characteristic image crop
- technical data lines
- branded border system
- editorial numbering
- material texture
- signature motion behavior

The signature must be repeatable across the site.

Do not invent several competing signatures.

---

# 26. Component System

Generate component rules for:

```text
header
navigation
mobile-nav
announcement-bar
buttons
text-links
hero
section-heading
cards
service-card
product-card
feature-list
stat-block
logo-wall
testimonial
quote
case-study
gallery
tabs
accordion
FAQ
form
input
select
checkbox
CTA-banner
footer
```

Each component:

```yaml
purpose:
anatomy:
variants:
states:
spacing:
typography:
color:
responsive_behavior:
motion:
usage_rules:
anti_patterns:
```

---

# 27. Section Composition Rules

The redesign agent must avoid building every page from identical cards.

Prefer compositional diversity:

```text
editorial text blocks
split layouts
full-width imagery
asymmetric grids
data/stat moments
narrow reading sections
immersive visual sections
structured utility sections
```

But preserve one coherent system.

---

# 28. Responsive Design

Do not simply shrink desktop.

For each component define:

```yaml
desktop:
tablet:
mobile:
```

Consider:

- hierarchy changes
- navigation transformation
- image crop changes
- column collapse
- text alignment
- CTA stacking
- content reordering
- reduced motion
- touch target sizes
- section spacing

---

# 29. Accessibility Design Checks

At minimum verify:

- readable color contrast
- visible keyboard focus
- non-color-only state communication
- clear active states
- sufficiently large interactive targets
- text remains readable when zoomed
- content does not rely on hover only
- motion can be reduced
- form errors are visually explicit

Accessibility must influence the design system before implementation.

---

# 30. Page Blueprint Generation

For each major page generate:

```yaml
page:
goal:
primary_user:
primary_conversion:
message_sequence:
sections:
```

Each section:

```yaml
order:
type:
purpose:
content:
layout:
visual_emphasis:
CTA:
proof:
component:
image_direction:
responsive_notes:
```

Example sequence:

```text
Hero
→ credibility proof
→ core offer
→ differentiation
→ process
→ case study
→ proof/statistics
→ FAQ
→ conversion CTA
```

Do not force this sequence on every business.

---

# 31. Homepage Redesign Validation

Before scaling to the whole site, validate homepage concept.

Ask:

1. Can user understand business in 5 seconds?
2. Is primary CTA obvious?
3. Is visual hierarchy deliberate?
4. Does design look specific to this business?
5. Does it avoid category clichés?
6. Is there one coherent art direction?
7. Is content credible?
8. Does the system scale to inner pages?
9. Does mobile remain intentional?
10. Are important brand assets preserved?

If not, revise direction before producing all pages.

---

# 32. Critique Loop

After each major design generation:

```text
GENERATE
→ CRITIQUE
→ PRIORITIZE
→ REVISE
→ VALIDATE
```

Critique categories:

```yaml
hierarchy:
spacing:
typography:
color:
composition:
brand_fit:
distinctiveness:
content_clarity:
image_fit:
responsive:
accessibility:
```

Do not fix everything equally.

Prioritize:

```text
P0 = blocks understanding/conversion
P1 = breaks visual system
P2 = polish
```

---

# 33. Anti-Drift Rules

The agent must not:

- introduce new colors without token update
- invent unrelated component styles
- switch art direction mid-page
- add random gradients
- add arbitrary rounded cards
- add icons without a defined icon family
- overuse shadows
- use stock imagery inconsistent with image direction
- create motion outside motion grammar
- violate type scale
- ignore spacing system
- make every section visually loud

Every new design decision must map back to `design-genome.json`.

---

# 34. Required Final Files

## website-analysis.json

Business truth + IA + page inventory.

## content-inventory.json

Reusable content and classification.

## visual-inventory.json

Raw visual DNA from existing site.

## design-audit.json

Scores + KEEP/EVOLVE/RETIRE.

## competitive-positioning.json

Category/competitor findings.

## redesign-brief.json

Problem, goals, mode, thesis, opportunities.

## design-genome.json

Canonical creative direction.

## design-tokens.json

Implementation-ready token system.

## component-system.json

Reusable component grammar.

## page-blueprints.json

Section-level redesigned IA/layout plan.

## DESIGN.md

Human-readable source of truth.

---

# 35. DESIGN.md Template

```md
# Design System

## 1. Design Thesis
...

## 2. Creative North Star
...

## 3. Brand Invariants
...

## 4. Things We Are Retiring
...

## 5. Visual Personality
...

## 6. Palette
...

## 7. Typography
...

## 8. Spatial Grammar
...

## 9. Grid
...

## 10. Shape Language
...

## 11. Materiality
...

## 12. Imagery
...

## 13. Iconography
...

## 14. Motion
...

## 15. Signature Element
...

## 16. Components
...

## 17. Page Composition Rules
...

## 18. Responsive Rules
...

## 19. Accessibility
...

## 20. Do / Don't
...
```

---

# 36. Agent Decision Order

The agent must reason in this sequence:

```text
1. What business is this?
2. Who is the user?
3. What must user understand?
4. What must user trust?
5. What must user do?
6. What brand signals must survive?
7. What currently works?
8. What currently fails?
9. What category conventions exist?
10. Where should this brand differentiate?
11. What visual world best expresses that?
12. What design system makes it repeatable?
13. How should pages use that system?
14. Does the result remain coherent and accessible?
```

Never start with:

```text
"What colors should I use?"
"What trendy style fits this?"
"What component library looks nice?"
```

---

# 37. Compact Machine Workflow

```text
INPUT URL
↓
DISCOVER PAGES
↓
CRAWL CONTENT + CSS + ASSETS
↓
CAPTURE RESPONSIVE SCREENSHOTS
↓
EXTRACT PRODUCT TRUTH
↓
EXTRACT BRAND TRUTH
↓
EXTRACT VISUAL DNA
↓
ANALYZE SECTIONS
↓
AUDIT KEEP / EVOLVE / RETIRE
↓
ANALYZE CATEGORY / COMPETITION
↓
SELECT REDESIGN MODE
↓
WRITE DESIGN THESIS
↓
GENERATE 5–7 VISUAL WORLDS
↓
SCORE DIRECTIONS
↓
SELECT NORTH STAR
↓
GENERATE DESIGN GENOME
↓
GENERATE TOKENS
↓
GENERATE COMPONENT GRAMMAR
↓
GENERATE PAGE BLUEPRINTS
↓
RENDER HOMEPAGE CONCEPT
↓
CRITIQUE
↓
REVISE
↓
FREEZE DESIGN.md
↓
SCALE TO FULL SITE
```

---

# 38. Canonical Redesign Output Schema

```yaml
redesign:
  business_truth:
  audience:
  conversion_goal:

  preserve:
  evolve:
  retire:

  redesign_mode:

  design_thesis:
  creative_north_star:

  controls:
    variance:
    motion:
    density:

  visual_system:
    palette:
    typography:
    spacing:
    grid:
    shape:
    depth:
    imagery:
    iconography:
    motion:
    signature_element:

  components:
  page_blueprints:

  accessibility:
  responsive:

  dos:
  donts:

  confidence:
  unresolved_questions:
```

---

# 39. Definition of Done

A redesign is ready only when:

- business identity is understandable;
- content hierarchy is explicit;
- primary conversion path is visible;
- preserved brand signals are documented;
- retired patterns have reasons;
- design thesis exists;
- one creative north star is selected;
- tokens are defined;
- component grammar is defined;
- image direction is defined;
- motion grammar is defined;
- responsive behavior is specified;
- accessibility checks exist;
- page blueprints exist;
- homepage direction passes critique;
- every major visual decision can be traced to the Design Genome.

---

# 40. Final Rule

**Do not redesign the screenshot. Redesign the system that produced the screenshot.**

The crawler extracts evidence.

The audit determines what deserves to survive.

The Design Genome defines the replacement visual world.

The token/component system makes that world repeatable.

The page blueprints apply it to the business.
