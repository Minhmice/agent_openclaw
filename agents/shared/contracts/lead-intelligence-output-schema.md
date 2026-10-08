# Lead Intelligence output schema

Canonical JSON Schemas are generated in `schemas/generated/`:

- `agent_topology.json`
- `stage_outcome.json`
- `lead_assessment.json`
- `gate_result.json`
- `portfolio_entry.json`
- `portfolio_decision.json`

The Pydantic contracts in `src/openclaw_web/lead_contracts.py` are the source
of truth. Generated files are deterministic build artifacts and contain no
secret values.
