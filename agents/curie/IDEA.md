# Lead-Mining Engine — Product Idea

## Core thesis

Do not sell redesigns to every business with an ugly website. Find the intersection of:

```text
strong business
+ weak website
+ meaningful online conversion opportunity
+ evidence that the opportunity is worth money
```

The product is a lead-mining engine, not a generic website auditor. Its daily output is one qualified prospect that a human can understand and approach with a credible redesign offer.

## What makes a website economically valuable

A website is worth building or rebuilding when it can do at least one of these jobs:

- Increase leads or orders.
- Reduce sales or customer-support cost.
- Increase trust during buyer research.
- Retain search traffic.
- Automate quote requests, booking, or ordering.
- Reduce repetitive questions for sales/CSKH.
- Act as the company verification page when a buyer searches its name.

The key diagnostic question is:

> Which step of the company's money-making process should the website improve?

If there is no business objective, the result is likely to be a decorative brochure with no measurable value.

## ROI model

```text
ROI = (incremental profit + saved cost - website cost) / website cost
```

Example:

```text
Website cost: 50,000,000 VND/year
Leads: 100
Leads becoming customers: 20
Average profit/customer: 5,000,000 VND

Incremental profit = 20 × 5,000,000 = 100,000,000 VND
ROI = (100,000,000 - 50,000,000) / 50,000,000 = 100%
```

Funnel signals:

```text
visitor → important page → CTA → lead → customer → revenue/profit
```

Measure traffic, conversion rate, cost per lead, lead-to-customer rate, web revenue, web profit, and CAC.

Expected annual gain can be approximated as:

```text
traffic × conversion uplift × profit per order
```

Example:

```text
50,000 visits × 0.5% conversion uplift × 800,000 VND profit/order
= 200,000,000 VND/year
```

## Ideal business prospect

Look for businesses with:

- High AOV or high-value contracts.
- Strong gross margin.
- Repeat purchase or high LTV.
- Search demand for products/services.
- Buyers who research before purchasing.
- Many reviews, branches, showrooms, or locations.
- Active Facebook/TikTok/social presence.
- Google Maps reviews.
- Google or Meta advertising.
- Sales, hotline, or Zalo accepting leads.
- Recognizable brands, clients, certificates, factory, or capacity evidence.
- Recent hiring, news, or activity showing that the company is active.

Avoid treating a business as a good prospect only because it has traffic. The business must have enough economic value for a redesign to matter.

## Website weakness signals

Inspect the homepage, one product/service page, and the contact or conversion page when possible.

Signals include:

- Design/template more than 5–8 years old.
- Poor mobile responsiveness.
- Fixed-width or table-based layout.
- Tiny typography or visibly broken layout.
- Weak, vague, or duplicated CTAs.
- Poor navigation.
- Missing search, filter, or comparison where the catalog needs it.
- Unclear price, specification, catalog, case study, or service scope.
- Long quote-request, checkout, or booking flow.
- Broken images or links.
- Lorem Ipsum or stale content.
- Old copyright/news/activity.
- Missing trust proof.
- HTTP, mixed content, or obvious security hygiene problems.
- Weak product/category SEO.
- Poor PageSpeed/Core Web Vitals.

Website ugliness alone is not enough; it must combine with business strength and money potential.

## Daily pipeline

```text
discover 100–300 public domains
  → business quality filter: approximately 30
  → web weakness filter: approximately 10
  → technical audit + screenshots
  → deterministic scoring
  → AI visual/business review
  → rank
  → HTML report
  → output at most one qualified lead/day
```

### Discovery

Candidate sources:

- Search queries combining industry, location, and buyer-intent terms such as “nhà sản xuất”, “công ty”, “báo giá”, “showroom”, and “đại lý”.
- Public directories.
- Industry associations.
- Public business listings.
- Public map/search results.

Rules:

- Crawl only public pages.
- Obey `robots.txt`.
- Apply request rate limits and retries with backoff.
- Persist evidence URLs and timestamps.
- Do not repeatedly crawl rejected leads until a recheck window.

### Business quality fields

- Industry.
- Company age.
- Branch/showroom count.
- Product/service count.
- AOV or price estimate.
- B2B/B2C classification.
- Hotline/Zalo availability.
- Social activity.
- Review count and rating.
- Brands, clients, and certificates.
- Factory/capacity evidence.
- Hiring/news/activity recency.

Output: `business_score` from 0 to 100.

### Money potential fields

```text
search_demand          20 points
aov_ltv                20 points
trust_dependency       15 points
online_conversion_fit  15 points
business_strength      15 points
web_gap                15 points
```

Output: `money_score` from 0 to 100.

### Website and technical fields

Output:

```text
web_ugly_score       0–100
technical_pain_score 0–100
```

Keep deterministic checks separate from AI judgment:

- Deterministic: HTTP status, HTTPS, redirects, responsive signals, broken links, image failures, metadata, PageSpeed data, CWV, DOM/layout signals, stale dates, CTA presence, and technology detection.
- AI: visual quality, perceived trust, conversion friction, why the business is strong, redesign opportunity, and money-opportunity narrative.

## Scoring

```text
LeadScore =
  BusinessScore × 30%
+ MoneyScore × 35%
+ WebUglyScore × 25%
+ TechnicalPainScore × 10%
```

Only output a qualified lead when `LeadScore >= 75`. If no lead reaches the threshold, report that there is no qualified lead for the day instead of forcing a weak result.

## MVP tool stack

| Tool | Primary purpose | MVP status |
|---|---|---|
| Lighthouse local/CI | Lab scores, raw JSON, screenshots, audit detail | Include |
| PageSpeed Insights API | Performance, accessibility, SEO, best practices | Include |
| CrUX API | Real-user P75 LCP/INP/CLS when data exists | Include when available |
| Lychee | Broken links and URL checks | Include |
| Wappalyzer API | CMS/framework/analytics/ecommerce detection | Include if access/quota allows |
| Screenshot/AI visual review | Visual and conversion interpretation | Include |
| WebPageTest | Waterfall, TTFB, filmstrip, request/byte detail | Later phase |
| HTTP Observatory | HTTP security headers | Later phase |
| SSL Labs API | TLS/certificate/config grade | Later phase; rate-limited |

Metrics to persist:

```text
performance_score
accessibility_score
seo_score
best_practices_score

LCP
INP
CLS
FCP
TTFB
SpeedIndex
TBT

page_weight_mb
request_count
image_weight
js_weight

broken_links
http_errors
redirect_count

security_grade
ssl_grade
https
mixed_content

cms
framework
analytics
ecommerce
tech_age_signal
```

Reference CWV thresholds:

```text
LCP <= 2.5s
INP <= 200ms
CLS <= 0.1
```

CrUX data is a strong bonus signal when available. Absence of CrUX data must not be interpreted as proof that a website has no traffic.

## Dashboard output

Each lead card should contain:

- Screenshot.
- Company.
- URL.
- Overall LeadScore.
- Business, WebUgly, Money, and Technical sub-scores.
- Why the business is good.
- Why the website is weak.
- Money opportunity.
- Top five issues.
- Redesign idea.
- Contact information.
- Evidence URLs.

Actions:

```text
Reject | Watch | Qualified
```

Persist:

```text
first_seen
last_checked
status
reject_reason
screenshots
raw_evidence_json
scores
tool_results
```

## OpenClaw orchestration

The intended flow is:

```text
OpenClaw persistent cron
  → discovery job
  → crawler/audit pipeline
  → deterministic scoring
  → AI review
  → dashboard/report
```

The OpenClaw gateway must be alive for the cron job to fire. OpenClaw should orchestrate and summarize; deterministic scoring should remain reproducible and auditable.

## Deferred scope

Defer until the MVP proves that the daily lead is useful:

- WebPageTest integration.
- SSL Labs and advanced security checks.
- CRM integration.
- Automated email/LinkedIn outreach.
- Contact enrichment.
- Multi-country discovery.
- Full auto-redesign generation.
- Automatic changes to the remote OpenClaw host.
