"""Safe public facade for optional workflow orchestration."""

from __future__ import annotations

import logging

from .config import WorkflowConfig
from .runtime_stub import WorkflowRuntimeStub
from .runtime_spiff import SpiffWorkflowRuntime, SpiffWorkflowUnavailable
from .types import PendingTask, WorkflowStatus

logger = logging.getLogger("client")


class WorkflowFacade:
    """Narrow interface for current and future workflow orchestration.

    The facade is safe to instantiate anywhere. When workflow support is
    disabled, all methods no-op and do not affect existing LIS or simulation
    behavior.
    """

    def __init__(
        self,
        config: WorkflowConfig | None = None,
        runtime: WorkflowRuntimeStub | None = None,
    ) -> None:
        self.config = config or WorkflowConfig.from_environment()
        self._runtime = None
        if self.config.enabled:
            self._runtime = runtime or self._create_runtime()

    @property
    def enabled(self) -> bool:
        """Return whether workflow orchestration is active."""

        return self.config.enabled

    def _create_runtime(self) -> WorkflowRuntimeStub:
        """Create the selected runtime backend with safe fallback."""

        if self.config.backend == "spiff":
            try:
                return SpiffWorkflowRuntime()
            except (SpiffWorkflowUnavailable, FileNotFoundError, RuntimeError):
                logger.exception("Falling back to stub workflow runtime.")

        return WorkflowRuntimeStub()

    def start_workflow(self, process_name: str, context: dict) -> str | None:
        """Start a workflow instance when enabled; otherwise safely no-op."""

        if self._runtime is None:
            return None
        return self._runtime.start_workflow(process_name, context)

    def publish_event(
        self,
        workflow_id: str,
        event_name: str,
        payload: dict | None = None,
    ) -> None:
        """Publish an event when enabled; otherwise safely no-op."""

        if self._runtime is None:
            return
        self._runtime.publish_event(workflow_id, event_name, payload)

    def get_pending_tasks(self, entity_type: str, entity_id: str) -> list[PendingTask]:
        """Return pending tasks when enabled, or an empty list when disabled."""

        if self._runtime is None:
            return []
        return self._runtime.get_pending_tasks(entity_type, entity_id)

    def get_status(self, workflow_id: str) -> WorkflowStatus | None:
        """Return workflow status when enabled, or ``None`` when disabled."""

        if self._runtime is None:
            return None
        return self._runtime.get_status(workflow_id)

    def observe_domain_event(self, event_name: str, payload: dict | None = None) -> str | None:
        """Run read-only event observation logic when workflow support is enabled."""

        if self._runtime is None:
            return None
        return self._runtime.observe_domain_event(event_name, payload)

    def get_observations(
        self,
        entity_type: str | None = None,
        entity_id: str | None = None,
    ) -> list[dict]:
        """Return passive workflow observations, or an empty list when disabled."""

        if self._runtime is None:
            return []
        return self._runtime.get_observations(entity_type, entity_id)

    def get_entity_flags(self, entity_type: str, entity_id: str) -> list[dict]:
        """Return passive workflow-created flags, or an empty list when disabled."""

        if self._runtime is None:
            return []
        return self._runtime.get_entity_flags(entity_type, entity_id)
