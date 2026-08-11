# Discuss Intent Contract

This contract defines the natural-language trigger for starting a fresh Curie discovery run from Discord `discuss`.

## Incoming event boundary

The trigger is valid only when all conditions match:

- Channel: `discuss` (`1533645084229369996`).
- Actor: Minh (`620891893659598850`).
- Message intent: Minh is affirming a request to find another business/lead.

## Vietnamese trigger examples

Treat these as the same intent when context is clear:

```text
oke thử cho tìm một con khác đi
ok thử tìm một con khác đi
thử tìm một con khác
tìm cho ta một con khác
kiếm một doanh nghiệp khác đi
find một lead khác đi
```

Matching is case-insensitive and should tolerate normal punctuation/spacing. Do not require an exact string match, but do require both:

1. an affirmative/request cue such as `ok`, `oke`, `thử`, `tìm`, `kiếm`, or `find`; and
2. a fresh-target cue such as `con khác`, `lead khác`, `doanh nghiệp khác`, `business khác`, or equivalent Vietnamese wording.

## Required action

When matched, `main` must:

1. Reply in `discuss` that Curie has been assigned a fresh discovery run.
2. Start exactly one isolated `curie` run.
3. Tell Curie to avoid existing active/review project IDs and return exactly one evidence-backed Vietnamese dossier.
4. Create the candidate in `review` state and post the concise dossier to review channel `1536658476288450630`.
5. Append a concise `curie-discovery-requested` event to the workflow worklog.

## Hard stops

- Do not run Website Brief or Project PM from this trigger.
- Do not approve the candidate automatically.
- Do not use this trigger for Wien, other actors, or messages in `task`, `review`, or `offer-ready`.
- If the wording is ambiguous, ask Minh to confirm instead of starting a run.
- If Curie finds no defensible candidate, report that outcome in Vietnamese and do not create a fake project.
