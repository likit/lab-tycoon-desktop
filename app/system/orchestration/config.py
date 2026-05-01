"""Configuration for optional workflow orchestration support."""

from __future__ import annotations

from dataclasses import dataclass
import os


WORKFLOW_ENABLED_ENV = "LABTYCOON_WORKFLOW_ENABLED"
WORKFLOW_BACKEND_ENV = "LABTYCOON_WORKFLOW_BACKEND"
_TRUE_VALUES = {"1", "true", "yes", "on"}
_SUPPORTED_BACKENDS = {"stub", "spiff"}


@dataclass(frozen=True, slots=True)
class WorkflowConfig:
    """Feature flag configuration for workflow orchestration."""

    enabled: bool = False
    backend: str = "stub"

    @classmethod
    def from_environment(cls) -> "WorkflowConfig":
        """Build config from environment variables.

        Workflow support is disabled by default. Set
        ``LABTYCOON_WORKFLOW_ENABLED`` to ``1``, ``true``, ``yes``, or ``on`` to
        enable the facade and stub runtime.
        """

        raw_enabled = os.environ.get(WORKFLOW_ENABLED_ENV, "")
        raw_backend = os.environ.get(WORKFLOW_BACKEND_ENV, "stub").strip().lower()
        backend = raw_backend if raw_backend in _SUPPORTED_BACKENDS else "stub"
        return cls(enabled=raw_enabled.strip().lower() in _TRUE_VALUES, backend=backend)
