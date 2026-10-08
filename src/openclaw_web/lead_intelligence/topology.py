"""Strict, role-scoped topology contracts for Lead Intelligence."""

from openclaw_web.topology import (
    CANONICAL_STAGE_FLOW,
    REDESIGN_STAGE_FLOW,
    AgentTopology,
    ExecutionKind,
    RetryPolicy,
    RoleManifest,
    TopologyManifest,
    load_topology,
    validate_topology,
)

__all__ = [
    "CANONICAL_STAGE_FLOW",
    "REDESIGN_STAGE_FLOW",
    "AgentTopology",
    "ExecutionKind",
    "RetryPolicy",
    "RoleManifest",
    "TopologyManifest",
    "load_topology",
    "validate_topology",
]
