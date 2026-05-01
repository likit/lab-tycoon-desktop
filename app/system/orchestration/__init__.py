"""Optional workflow orchestration boundary for LabTycoon.

This package is intentionally inactive by default. It provides a narrow facade
that can later be backed by SpiffWorkflow without requiring the LIS,
simulation, or UI layers to import SpiffWorkflow directly.
"""

from .action_adapter import WorkflowActionAdapter, WorkflowActionRecord
from .config import WorkflowConfig
from .definition import WorkflowDefinition, WorkflowDefinitionLoader, WorkflowInstance, WorkflowStep
from .event_bridge import ObservedWorkflowEvent, WorkflowEventBridge
from .facade import WorkflowFacade
from .rule_drafts import RuleDraftInfo, RuleDraftManager
from .rule_lifecycle import RuleDraftLifecycleService, RuleDraftTransitionResult
from .rule_promotion import RulePromotionResult, RulePromotionService
from .rule_preview import RulePreviewOutcome, RulePreviewResult, RulePreviewService
from .rule_snapshots import RulePreviewSnapshotInfo, RulePreviewSnapshotService
from .rules import DecisionResult, RuleDefinition, RuleEngine
from .runtime_spiff import SpiffWorkflowRuntime, SpiffWorkflowUnavailable
from .types import PendingTask, WorkflowObservation, WorkflowStatus

__all__ = [
    "ObservedWorkflowEvent",
    "PendingTask",
    "DecisionResult",
    "RuleDefinition",
    "RuleDraftInfo",
    "RuleDraftManager",
    "RuleDraftLifecycleService",
    "RuleDraftTransitionResult",
    "RulePromotionResult",
    "RulePromotionService",
    "RulePreviewOutcome",
    "RulePreviewResult",
    "RulePreviewService",
    "RulePreviewSnapshotInfo",
    "RulePreviewSnapshotService",
    "RuleEngine",
    "WorkflowActionAdapter",
    "WorkflowActionRecord",
    "WorkflowConfig",
    "WorkflowDefinition",
    "WorkflowDefinitionLoader",
    "WorkflowEventBridge",
    "WorkflowFacade",
    "WorkflowInstance",
    "WorkflowObservation",
    "WorkflowStep",
    "WorkflowStatus",
    "SpiffWorkflowRuntime",
    "SpiffWorkflowUnavailable",
]
