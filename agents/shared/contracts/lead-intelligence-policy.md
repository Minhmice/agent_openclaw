# Lead Intelligence policy

Lead Intelligence qualifies a lead only when all four parts are present:

```text
strong business + weak website + meaningful online opportunity + evidence
```

The deterministic gate is an AND gate, never an average:

- `BusinessStrength >= 60`
- `AgencyFit >= 65`
- `DigitalGap >= 55`

Before outreach, the workflow may report observable fit, opportunity,
dealability proxies and evidence confidence. It must not create
`buyer_intent`, `engagement`, or equivalent intent signals. A provider failure
is represented as `partial`, `failed_retryable`, `failed_terminal`, or
`no_candidate_defensible`; the workflow never invents a candidate to fill a
quota.

Red Team returns only `survive`, `downgrade`, or `reject`. `reject` cannot enter
the portfolio. Selection creates a human-action boundary and never dispatches
redesign automatically.
