"""Loopback-only live dashboard API and projection layer."""

from .coordinator import WorkflowDashboardCoordinator
from .models import ActionRequest, ActionResponse, DashboardPage, DashboardSnapshot

__all__ = [
    "ActionRequest",
    "ActionResponse",
    "DashboardPage",
    "DashboardSnapshot",
    "WorkflowDashboardCoordinator",
]
