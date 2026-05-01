"""In-memory workflow runtime used until SpiffWorkflow is integrated."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

from ..events import DomainEventName
from .action_adapter import WorkflowActionAdapter
from .definition import WorkflowDefinition, WorkflowDefinitionLoader, WorkflowInstance, WorkflowStep
from .rules import RuleEngine
from .types import PendingTask, WorkflowObservation, WorkflowStatus


class WorkflowRuntimeStub:
    """Minimal runtime that stores placeholder workflow state in memory."""

    def __init__(
        self,
        action_adapter: WorkflowActionAdapter | None = None,
        definition_loader: WorkflowDefinitionLoader | None = None,
        rule_engine: RuleEngine | None = None,
    ) -> None:
        self._statuses: dict[str, WorkflowStatus] = {}
        self._tasks: dict[str, list[PendingTask]] = {}
        self._observations: list[WorkflowObservation] = []
        self._action_adapter = action_adapter or WorkflowActionAdapter()
        self._definitions = (definition_loader or WorkflowDefinitionLoader()).load_definitions()
        self._rule_engine = rule_engine or RuleEngine()

    def start_workflow(self, process_name: str, context: dict) -> str:
        """Start and execute a minimal workflow definition when available."""

        workflow_id = uuid4().hex
        workflow_context = deepcopy(context)
        self._statuses[workflow_id] = {
            "workflow_id": workflow_id,
            "process_name": process_name,
            "state": "started",
            "context": workflow_context,
            "events": [],
            "trace": [],
        }
        self._tasks[workflow_id] = []

        definition = self._definitions.get(process_name)
        if definition is not None:
            instance = WorkflowInstance(
                workflow_id=workflow_id,
                process_name=process_name,
                current_step=definition.start_step,
            )
            self._execute_definition(definition, instance, workflow_context)

        return workflow_id

    def publish_event(
        self,
        workflow_id: str,
        event_name: str,
        payload: dict | None = None,
    ) -> None:
        """Record an event against an in-memory workflow instance."""

        status = self._statuses.get(workflow_id)
        if status is None:
            return

        status["events"].append(
            {
                "name": event_name,
                "payload": deepcopy(payload or {}),
            }
        )

    def get_pending_tasks(self, entity_type: str, entity_id: str) -> list[PendingTask]:
        """Return placeholder pending tasks matching an entity."""

        tasks = []
        for workflow_tasks in self._tasks.values():
            tasks.extend(
                task
                for task in workflow_tasks
                if task.entity_type == entity_type and task.entity_id == entity_id
            )
        return tasks

    def get_status(self, workflow_id: str) -> WorkflowStatus | None:
        """Return a copy of placeholder workflow status."""

        status = self._statuses.get(workflow_id)
        return deepcopy(status) if status is not None else None

    def observe_domain_event(self, event_name: str, payload: dict | None = None) -> str:
        """Run passive proof-of-concept observation logic for a domain event."""

        event_payload = deepcopy(payload or {})
        if event_name == DomainEventName.ORDER_CREATED:
            return self.start_workflow(
                process_name="duplicate_order_review",
                context={"event_name": event_name, "payload": event_payload},
            )

        return self.start_workflow(
            process_name=f"observe_{event_name}",
            context={"event_name": event_name, "payload": event_payload},
        )

    def get_observations(
        self,
        entity_type: str | None = None,
        entity_id: str | None = None,
    ) -> list[dict]:
        """Return passive workflow observations, optionally filtered by entity."""

        observations = self._observations
        if entity_type is not None:
            observations = [
                observation
                for observation in observations
                if observation.entity_type == entity_type
            ]
        if entity_id is not None:
            observations = [
                observation
                for observation in observations
                if observation.entity_id == entity_id
            ]
        return [asdict(observation) for observation in observations]

    def get_entity_flags(self, entity_type: str, entity_id: str) -> list[dict]:
        """Return passive workflow-created flags for an entity."""

        return self._action_adapter.get_entity_flags(entity_type, entity_id)

    def _execute_definition(
        self,
        definition: WorkflowDefinition,
        instance: WorkflowInstance,
        context: dict,
    ) -> None:
        """Execute the small externalized workflow definition synchronously."""

        current_step_id = instance.current_step
        visited_steps = 0
        while current_step_id:
            visited_steps += 1
            if visited_steps > len(definition.steps) + 1:
                raise RuntimeError(f"Workflow {definition.process_name} contains a cycle.")

            step = definition.steps[current_step_id]
            self._record_trace(instance.workflow_id, step)

            if step.step_type == "start":
                current_step_id = step.next_step
            elif step.step_type == "task":
                self._execute_task(step, context)
                current_step_id = step.next_step
            elif step.step_type == "decision":
                current_step_id = step.true_next if self._evaluate_condition(step, context) else step.false_next
            elif step.step_type == "end":
                self._statuses[instance.workflow_id]["state"] = "completed"
                return
            else:
                raise ValueError(f"Unsupported workflow step type: {step.step_type}")

    def _execute_task(self, step: WorkflowStep, context: dict) -> None:
        """Execute a supported task action from the external definition."""

        if step.action == "record_order_created_observation":
            context["order_review"] = self._build_order_review_context(context.get("payload", {}))
            context["decisions"] = {
                "possible_duplicate_order": context["order_review"]["decision"]
            }
            self._record_order_created_observation(context["order_review"])
            return

        if step.action == "create_review_flag":
            self._create_review_flag_from_context(context["order_review"])
            return

        raise ValueError(f"Unsupported workflow action: {step.action}")

    def _evaluate_condition(self, step: WorkflowStep, context: dict) -> bool:
        """Evaluate a tiny condition object from the workflow definition."""

        condition = step.condition or {}
        expected_value = condition.get("equals")
        actual_value = self._lookup_context_value(context, str(condition.get("field", "")))
        return actual_value == expected_value

    def _lookup_context_value(self, context: dict, field_name: str) -> object:
        """Look up a value from workflow context or derived order-review context."""

        if "." in field_name:
            return _lookup_dotted_value(context, field_name)
        if field_name in context:
            return context[field_name]
        order_review = context.get("order_review", {})
        if isinstance(order_review, dict):
            return order_review.get(field_name)
        return None

    def _record_trace(self, workflow_id: str, step: WorkflowStep) -> None:
        """Append a minimal execution trace to workflow status."""

        self._statuses[workflow_id]["trace"].append(
            {
                "step_id": step.step_id,
                "type": step.step_type,
                "action": step.action,
            }
        )

    def _build_order_review_context(self, payload: dict) -> dict:
        """Build the duplicate-order review context from event payload."""

        order_id = _string_or_none(payload.get("order_id"))
        patient_id = _string_or_none(payload.get("patient_id") or payload.get("customer_id"))
        test_code = _string_or_none(payload.get("test_code"))
        duplicate_decision = self._rule_engine.evaluate(
            "possible_duplicate_order",
            {"payload": payload},
        )
        duplicate_hint = duplicate_decision.matched

        if duplicate_hint:
            recommendation = "Possible duplicate order review recommended"
        else:
            recommendation = (
                "Observed order_created event for workflow candidate: "
                "duplicate_order_review"
            )

        return {
            "order_id": order_id,
            "patient_id": patient_id,
            "test_code": test_code,
            "duplicate_hint": duplicate_hint,
            "recommendation": recommendation,
            "decision": duplicate_decision.to_dict(),
        }

    def _record_order_created_observation(self, order_review_context: dict) -> None:
        """Create a read-only recommendation/trace for an order event."""

        self._observations.append(
            WorkflowObservation(
                observation_id=uuid4().hex,
                observed_event=DomainEventName.ORDER_CREATED,
                process_name="duplicate_order_review",
                entity_type="order" if order_review_context["order_id"] is not None else None,
                entity_id=order_review_context["order_id"],
                recommendation=order_review_context["recommendation"],
                context_summary={
                    "order_id": order_review_context["order_id"],
                    "patient_id": order_review_context["patient_id"],
                    "test_code": order_review_context["test_code"],
                    "duplicate_hint": order_review_context["duplicate_hint"],
                    "decision": order_review_context["decision"],
                },
                timestamp=datetime.now(UTC).isoformat(),
            )
        )

    def _create_review_flag_from_context(self, order_review_context: dict) -> None:
        """Create a bounded passive flag through the action adapter."""

        order_id = order_review_context["order_id"]
        if order_id is None:
            return

        self._action_adapter.create_review_flag(
            entity_type="order",
            entity_id=order_id,
            reason=order_review_context["recommendation"],
            metadata={
                "process_name": "duplicate_order_review",
                "observed_event": DomainEventName.ORDER_CREATED,
                "context_summary": {
                    "order_id": order_review_context["order_id"],
                    "patient_id": order_review_context["patient_id"],
                    "test_code": order_review_context["test_code"],
                    "duplicate_hint": order_review_context["duplicate_hint"],
                    "decision": order_review_context["decision"],
                },
            },
        )


def _string_or_none(value: object) -> str | None:
    """Convert a payload value to string while preserving missing values."""

    if value is None:
        return None
    return str(value)


def _lookup_dotted_value(context: dict, field_path: str) -> object:
    """Read a dotted-path value from nested dictionaries."""

    value: object = context
    for part in field_path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value
