# Open Questions

## 1. MVP market scope — decided

The discovery market is intentionally broad by industry but bounded geographically:

- Market ID: hanoi-80km
- Industries: đa ngành
- Region: quanh Hà Nội (within the configured Hanoi radius)

The geofence remains fail-closed when a source cannot provide reliable coordinates or
administrative evidence. Discovery providers may cover multiple cohorts, but every
candidate still needs public evidence and defensible audit findings before review.

## Later decisions

The market scope is now fixed. Continue to calibrate:

- Discovery sources and API budget.
- Daily candidate volume.
- Whether the dashboard is local HTML or deployed.
- JSON/SQLite/database choice.
- Screenshot/browser runtime.
- Whether human review happens before outreach.
- Whether OpenClaw only schedules/orchestrates or also performs the complete pipeline.
