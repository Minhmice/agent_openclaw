from __future__ import annotations

from openclaw_web import runtime
from openclaw_web.runtime_components import (
    CoordinatorActions,
    component_result_message,
    run_component_action,
    run_component_callback,
)
from openclaw_web.runtime_discovery import (
    ProductionDiscoveryComposition,
    drain_delivery_outbox,
    run_daily_discovery,
)
from openclaw_web.runtime_legacy import run_legacy_review


def test_runtime_facade_reexports_capability_boundaries() -> None:
    assert runtime._CoordinatorActions is CoordinatorActions
    assert runtime._component_result_message is component_result_message
    assert runtime.run_component_action is run_component_action
    assert runtime.run_component_callback is run_component_callback
    assert runtime.ProductionDiscoveryComposition is ProductionDiscoveryComposition
    assert runtime.drain_delivery_outbox is drain_delivery_outbox
    assert runtime.run_daily_discovery is run_daily_discovery
    assert runtime._run_legacy_review_impl is run_legacy_review
