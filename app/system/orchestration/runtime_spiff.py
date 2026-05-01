"""Optional SpiffWorkflow-backed runtime for one narrow workflow."""

from __future__ import annotations

from copy import deepcopy
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..events import DomainEventName
from .action_adapter import WorkflowActionAdapter
from .runtime_stub import WorkflowRuntimeStub

logger = logging.getLogger("client")


class SpiffWorkflowUnavailable(RuntimeError):
    """Raised when the optional SpiffWorkflow dependency cannot be loaded."""


class SpiffWorkflowRuntime(WorkflowRuntimeStub):
    """Execute the duplicate-order review workflow with SpiffWorkflow.

    The backend remains deliberately narrow. It only supports
    ``duplicate_order_review`` and only maps one BPMN task,
    ``create_review_flag``, to the bounded LabTycoon action adapter.
    """

    PROCESS_NAME = "duplicate_order_review"

    def __init__(
        self,
        action_adapter: WorkflowActionAdapter | None = None,
        bpmn_path: Path | None = None,
    ) -> None:
        super().__init__(action_adapter=action_adapter)
        self.bpmn_path = bpmn_path or (
            Path(__file__).parent
            / "workflows"
            / self.PROCESS_NAME
            / f"{self.PROCESS_NAME}.bpmn"
        )
        self._bpmn_parser_cls, self._bpmn_workflow_cls, self._task_state_cls = self._load_spiff_classes()
        self._process_specs = self._load_process_specs()
        self._task_action_map = {
            "create_review_flag": self._create_review_flag_from_context,
        }

    @staticmethod
    def _load_spiff_classes() -> tuple[type, type, type | None]:
        """Import SpiffWorkflow classes lazily so the app can run without them."""

        try:
            from SpiffWorkflow.bpmn.parser import BpmnParser
            from SpiffWorkflow.bpmn.workflow import BpmnWorkflow
        except ImportError as exc:
            raise SpiffWorkflowUnavailable("SpiffWorkflow is not installed.") from exc

        try:
            from SpiffWorkflow.task import TaskState
        except ImportError:
            TaskState = None

        return BpmnParser, BpmnWorkflow, TaskState

    def _load_process_specs(self) -> dict[str, Any]:
        """Load BPMN process specs using SpiffWorkflow."""

        if not self.bpmn_path.exists():
            raise FileNotFoundError(f"BPMN workflow file not found: {self.bpmn_path}")

        parser = self._bpmn_parser_cls()
        parser.add_bpmn_file(str(self.bpmn_path))
        return {self.PROCESS_NAME: parser.get_spec(self.PROCESS_NAME)}

    def start_workflow(self, process_name: str, context: dict) -> str:
        """Start the Spiff-backed workflow, falling back to stub behavior for others."""

        if process_name != self.PROCESS_NAME:
            return super().start_workflow(process_name, context)

        workflow_id = uuid4().hex
        workflow_context = deepcopy(context)
        payload = workflow_context.get("payload", {})
        order_review_context = self._build_order_review_context(payload)
        workflow_context["order_review"] = order_review_context
        workflow_context["decisions"] = {
            "possible_duplicate_order": order_review_context["decision"]
        }
        workflow_context["possible_duplicate_order_matched"] = order_review_context["decision"]["matched"]

        self._statuses[workflow_id] = {
            "workflow_id": workflow_id,
            "process_name": process_name,
            "state": "started",
            "backend": "spiff",
            "context": workflow_context,
            "events": [],
            "trace": [],
        }
        self._tasks[workflow_id] = []

        try:
            self._record_order_created_observation(order_review_context)
            self._execute_spiff_workflow(workflow_id, process_name, workflow_context)
            self._statuses[workflow_id]["state"] = "completed"
        except Exception:
            self._statuses[workflow_id]["state"] = "error"
            logger.exception("SpiffWorkflow execution failed for %s", process_name)

        return workflow_id

    def _execute_spiff_workflow(
        self,
        workflow_id: str,
        process_name: str,
        context: dict[str, Any],
    ) -> None:
        """Execute BPMN via SpiffWorkflow and map safe tasks to adapter actions."""

        workflow = self._bpmn_workflow_cls(self._process_specs[process_name])
        self._attach_workflow_data(workflow, context)

        executed_actions: set[str] = set()
        for _ in range(20):
            self._run_engine_steps(workflow)
            ready_tasks = self._get_ready_tasks(workflow)
            if not ready_tasks:
                break

            for task in ready_tasks:
                task_id = self._task_identifier(task)
                self._statuses[workflow_id]["trace"].append(
                    {"step_id": task_id, "type": "bpmn_task", "action": task_id}
                )
                if task_id in self._task_action_map and task_id not in executed_actions:
                    if context["order_review"]["duplicate_hint"]:
                        self._task_action_map[task_id](context["order_review"])
                    executed_actions.add(task_id)
                self._complete_task(task)

        # Some Spiff versions auto-run service tasks. Keep the single bounded
        # side effect deterministic while still requiring the BPMN definition.
        if (
            context["order_review"]["duplicate_hint"]
            and "create_review_flag" not in executed_actions
            and self._bpmn_contains_task("create_review_flag")
        ):
            self._statuses[workflow_id]["trace"].append(
                {
                    "step_id": "create_review_flag",
                    "type": "mapped_bpmn_action",
                    "action": "create_review_flag",
                }
            )
            self._task_action_map["create_review_flag"](context["order_review"])

    def _attach_workflow_data(self, workflow: object, context: dict[str, Any]) -> None:
        """Attach compact context to a Spiff workflow instance when supported."""

        if hasattr(workflow, "data") and isinstance(workflow.data, dict):
            workflow.data.update(context)
        elif hasattr(workflow, "set_data"):
            workflow.set_data(context)

    def _run_engine_steps(self, workflow: object) -> None:
        """Advance Spiff engine steps using the available API."""

        if hasattr(workflow, "do_engine_steps"):
            workflow.do_engine_steps()

    def _get_ready_tasks(self, workflow: object) -> list[object]:
        """Return ready tasks across compatible SpiffWorkflow versions."""

        if not hasattr(workflow, "get_tasks"):
            return []

        if self._task_state_cls is not None and hasattr(self._task_state_cls, "READY"):
            try:
                return list(workflow.get_tasks(state=self._task_state_cls.READY))
            except TypeError:
                return list(workflow.get_tasks(self._task_state_cls.READY))

        return list(workflow.get_tasks())

    def _task_identifier(self, task: object) -> str:
        """Extract a BPMN task identifier defensively across Spiff versions."""

        task_spec = getattr(task, "task_spec", None)
        for source in (task_spec, task):
            if source is None:
                continue
            for attr in ("bpmn_id", "name", "id"):
                value = getattr(source, attr, None)
                if value:
                    return str(value)
        if hasattr(task, "get_name"):
            return str(task.get_name())
        return "unknown_task"

    def _complete_task(self, task: object) -> None:
        """Complete a task using the available Spiff task API."""

        if hasattr(task, "run"):
            task.run()

    def _bpmn_contains_task(self, task_id: str) -> bool:
        """Check that the mapped action exists in the BPMN file."""

        return f'id="{task_id}"' in self.bpmn_path.read_text(encoding="utf-8")
