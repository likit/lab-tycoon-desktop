"""Tests for the optional workflow orchestration skeleton."""

from __future__ import annotations

import os
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.system.engine import Simulator
from app.system.model import BaseWidget, Specimen, Workflow
from app.system.orchestration import (
    RuleDraftManager,
    RuleEngine,
    RuleDraftLifecycleService,
    RulePromotionService,
    RulePreviewService,
    RulePreviewSnapshotService,
    SpiffWorkflowRuntime,
    WorkflowActionAdapter,
    WorkflowConfig,
    WorkflowDefinitionLoader,
    WorkflowFacade,
)
from app.system.orchestration.rule_preview_ui import (
    DEFAULT_CONTEXT_JSON,
    create_preview_snapshot,
    load_default_candidate_rule_text,
    load_preview_snapshot,
    list_rule_drafts,
    list_preview_snapshots,
    load_rule_draft_text,
    promote_rule_draft,
    preview_rule_draft,
    preview_rule_text,
    save_rule_draft_text,
    transition_rule_draft,
)
from app.system.orchestration.runtime_spiff import SpiffWorkflowUnavailable


class WorkflowOrchestrationTests(unittest.TestCase):
    def test_workflow_support_is_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = WorkflowConfig.from_environment()

        self.assertFalse(config.enabled)

    def test_facade_methods_are_safe_when_disabled(self) -> None:
        facade = WorkflowFacade(config=WorkflowConfig(enabled=False))

        self.assertFalse(facade.enabled)
        self.assertIsNone(facade.start_workflow("receive_order", {"order_id": "1"}))
        facade.publish_event("missing", "order_received", {"order_id": "1"})
        self.assertEqual(facade.get_pending_tasks("order", "1"), [])
        self.assertIsNone(facade.get_status("missing"))
        self.assertEqual(facade.get_entity_flags("order", "1"), [])

    def test_environment_flag_can_enable_workflow_support(self) -> None:
        with patch.dict(os.environ, {"LABTYCOON_WORKFLOW_ENABLED": "true"}, clear=True):
            facade = WorkflowFacade()

        self.assertTrue(facade.enabled)

    def test_environment_can_select_spiff_backend(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LABTYCOON_WORKFLOW_ENABLED": "true",
                "LABTYCOON_WORKFLOW_BACKEND": "spiff",
            },
            clear=True,
        ):
            config = WorkflowConfig.from_environment()

        self.assertTrue(config.enabled)
        self.assertEqual(config.backend, "spiff")

    def test_invalid_backend_defaults_to_stub(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LABTYCOON_WORKFLOW_ENABLED": "true",
                "LABTYCOON_WORKFLOW_BACKEND": "unknown",
            },
            clear=True,
        ):
            config = WorkflowConfig.from_environment()

        self.assertEqual(config.backend, "stub")

    def test_enabled_facade_uses_stub_runtime(self) -> None:
        facade = WorkflowFacade(config=WorkflowConfig(enabled=True))

        workflow_id = facade.start_workflow("receive_order", {"order_id": "1"})
        self.assertIsNotNone(workflow_id)

        facade.publish_event(workflow_id, "order_received", {"source": "test"})
        status = facade.get_status(workflow_id)

        self.assertIsNotNone(status)
        self.assertEqual(status["workflow_id"], workflow_id)
        self.assertEqual(status["process_name"], "receive_order")
        self.assertEqual(status["state"], "started")
        self.assertEqual(status["events"][0]["name"], "order_received")
        self.assertEqual(facade.get_pending_tasks("order", "1"), [])

    def test_duplicate_order_review_definition_loads(self) -> None:
        definitions = WorkflowDefinitionLoader().load_definitions()

        definition = definitions["duplicate_order_review"]
        self.assertEqual(definition.process_name, "duplicate_order_review")
        self.assertEqual(definition.start_step, "start")
        self.assertEqual(definition.steps["is_possible_duplicate"].step_type, "decision")
        self.assertEqual(
            definition.steps["is_possible_duplicate"].condition["field"],
            "decisions.possible_duplicate_order.matched",
        )
        self.assertEqual(definition.steps["create_review_flag"].action, "create_review_flag")

    def test_noop_behavior_does_not_raise(self) -> None:
        facade = WorkflowFacade(config=WorkflowConfig(enabled=False))

        for _ in range(3):
            facade.publish_event("unknown", "anything")
            facade.get_pending_tasks("specimen", "tube-1")
            facade.get_status("unknown")

    def test_disabled_facade_creates_no_observations(self) -> None:
        facade = WorkflowFacade(config=WorkflowConfig(enabled=False))

        workflow_id = facade.observe_domain_event("order_created", {"order_id": "1"})

        self.assertIsNone(workflow_id)
        self.assertEqual(facade.get_observations(), [])
        self.assertEqual(facade.get_entity_flags("order", "1"), [])

    def test_enabled_facade_creates_order_created_observation(self) -> None:
        facade = WorkflowFacade(config=WorkflowConfig(enabled=True))

        workflow_id = facade.start_workflow(
            "duplicate_order_review",
            {
                "event_name": "order_created",
                "payload": {
                    "order_id": "1",
                    "patient_id": "P001",
                    "test_code": "CBC",
                    "duplicate_candidate": True,
                },
            },
        )
        observations = facade.get_observations(entity_type="order", entity_id="1")
        status = facade.get_status(workflow_id)

        self.assertIsNotNone(workflow_id)
        self.assertEqual(status["state"], "completed")
        self.assertEqual(
            [step["step_id"] for step in status["trace"]],
            ["start", "record_observation", "is_possible_duplicate", "create_review_flag", "end"],
        )
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["observed_event"], "order_created")
        self.assertEqual(observations[0]["process_name"], "duplicate_order_review")
        self.assertEqual(observations[0]["entity_type"], "order")
        self.assertEqual(observations[0]["entity_id"], "1")
        self.assertEqual(
            observations[0]["recommendation"],
            "Possible duplicate order review recommended",
        )
        self.assertEqual(observations[0]["context_summary"]["test_code"], "CBC")
        flags = facade.get_entity_flags("order", "1")
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["kind"], "review_flag")
        self.assertEqual(flags[0]["source"], "workflow")
        self.assertEqual(
            flags[0]["message"],
            "Possible duplicate order review recommended",
        )
        self.assertTrue(
            flags[0]["metadata"]["context_summary"]["decision"]["matched"]
        )

    def test_enabled_facade_records_neutral_order_trace_without_duplicate_hint(self) -> None:
        facade = WorkflowFacade(config=WorkflowConfig(enabled=True))

        workflow_id = facade.start_workflow(
            "duplicate_order_review",
            {"event_name": "order_created", "payload": {"order_id": "2"}},
        )
        observations = facade.get_observations()
        status = facade.get_status(workflow_id)

        self.assertEqual(len(observations), 1)
        self.assertEqual(
            [step["step_id"] for step in status["trace"]],
            ["start", "record_observation", "is_possible_duplicate", "end"],
        )
        self.assertEqual(
            observations[0]["recommendation"],
            "Observed order_created event for workflow candidate: duplicate_order_review",
        )
        self.assertEqual(facade.get_entity_flags("order", "2"), [])

    def test_non_order_event_does_not_create_recommendation_observation(self) -> None:
        facade = WorkflowFacade(config=WorkflowConfig(enabled=True))

        workflow_id = facade.observe_domain_event("sample_received", {"specimen_id": "tube-1"})

        self.assertIsNotNone(workflow_id)
        self.assertEqual(facade.get_observations(), [])


class WorkflowActionAdapterTests(unittest.TestCase):
    def test_action_adapter_creates_and_retrieves_review_flag(self) -> None:
        adapter = WorkflowActionAdapter()

        flag_id = adapter.create_review_flag(
            entity_type="order",
            entity_id="1",
            reason="Manual review recommended",
            metadata={"source_event": "order_created"},
        )
        flags = adapter.get_entity_flags("order", "1")

        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["record_id"], flag_id)
        self.assertEqual(flags[0]["entity_type"], "order")
        self.assertEqual(flags[0]["entity_id"], "1")
        self.assertEqual(flags[0]["source"], "workflow")
        self.assertEqual(flags[0]["kind"], "review_flag")
        self.assertEqual(flags[0]["message"], "Manual review recommended")

    def test_action_adapter_validates_inputs(self) -> None:
        adapter = WorkflowActionAdapter()

        with self.assertRaises(ValueError):
            adapter.create_review_flag("", "1", "reason")
        with self.assertRaises(ValueError):
            adapter.create_review_flag("order", "", "reason")
        with self.assertRaises(ValueError):
            adapter.create_review_flag("order", "1", "")
        with self.assertRaises(ValueError):
            adapter.create_review_flag("order", "1", "reason", metadata="invalid")

    def test_workflow_flag_does_not_change_simulation_outcome(self) -> None:
        def run_simple_simulation() -> tuple[float, int, str]:
            workflow = Workflow()
            intake = BaseWidget("Intake", service_time=1.0)
            analyzer = BaseWidget("Analyzer", service_time=2.0)
            workflow.connect(intake, analyzer)
            simulator = Simulator(workflow)
            simulator.send_to(intake, Specimen("S-001"))
            simulator.run()
            return (
                simulator.clock.current_time,
                len(simulator.completed_specimens),
                simulator.completed_specimens[0].specimen_id,
            )

        baseline = run_simple_simulation()
        facade = WorkflowFacade(config=WorkflowConfig(enabled=True))
        facade.start_workflow(
            "duplicate_order_review",
            {
                "event_name": "order_created",
                "payload": {"order_id": "1", "duplicate_candidate": True},
            },
        )
        after_workflow_flag = run_simple_simulation()

        self.assertEqual(after_workflow_flag, baseline)


class WorkflowRuleEngineTests(unittest.TestCase):
    def test_possible_duplicate_order_yaml_rule_artifact_loads(self) -> None:
        engine = RuleEngine()

        self.assertIn("possible_duplicate_order", engine._rules)

    def test_possible_duplicate_order_rule_matches_duplicate_indicator(self) -> None:
        engine = RuleEngine()

        result = engine.evaluate(
            "possible_duplicate_order",
            {"payload": {"duplicate_candidate": True, "priority": "routine"}},
        )

        self.assertEqual(result.decision_name, "possible_duplicate_order")
        self.assertTrue(result.matched)
        self.assertEqual(
            result.reason,
            "Order payload contains a duplicate-order indicator.",
        )
        self.assertEqual(
            result.metadata["matched_fields"],
            ["payload.duplicate_candidate"],
        )

    def test_possible_duplicate_order_rule_returns_neutral_result(self) -> None:
        engine = RuleEngine()

        result = engine.evaluate("possible_duplicate_order", {"payload": {"order_id": "1"}})

        self.assertFalse(result.matched)
        self.assertEqual(result.reason, "No duplicate-order indicators were present.")
        self.assertEqual(result.metadata["matched_fields"], [])

    def test_missing_rule_fails_safely(self) -> None:
        engine = RuleEngine()

        result = engine.evaluate("missing_rule", {"payload": {"duplicate_candidate": True}})

        self.assertFalse(result.matched)
        self.assertEqual(result.reason, "Decision rule unavailable.")
        self.assertEqual(result.metadata["error"], "missing_rule")

    def test_malformed_rule_file_fails_safely(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rules_dir = Path(tmpdir)
            (rules_dir / "broken.yaml").write_text("not: [valid", encoding="utf-8")

            with patch("app.system.orchestration.rules.logger.exception") as log_exception:
                engine = RuleEngine(rules_dir=rules_dir)

            result = engine.evaluate("possible_duplicate_order", {"payload": {"duplicate_candidate": True}})

        log_exception.assert_called_once()
        self.assertFalse(result.matched)
        self.assertEqual(result.metadata["error"], "missing_rule")

    def test_changing_yaml_rule_changes_decision_without_python_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rules_dir = Path(tmpdir)
            (rules_dir / "possible_duplicate_order.yaml").write_text(
                """
decision_name: possible_duplicate_order
description: Test editable rule.
conditions:
  match_any:
    - field: payload.manual_review
      equals: true
outcomes:
  matched:
    reason: Editable rule matched.
  unmatched:
    reason: Editable rule did not match.
""",
                encoding="utf-8",
            )
            engine = RuleEngine(rules_dir=rules_dir)

            old_indicator = engine.evaluate(
                "possible_duplicate_order",
                {"payload": {"duplicate_candidate": True}},
            )
            new_indicator = engine.evaluate(
                "possible_duplicate_order",
                {"payload": {"manual_review": True}},
            )

        self.assertFalse(old_indicator.matched)
        self.assertTrue(new_indicator.matched)
        self.assertEqual(new_indicator.reason, "Editable rule matched.")

    def test_not_equals_operator_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rules_dir = Path(tmpdir)
            (rules_dir / "possible_duplicate_order.yaml").write_text(
                """
decision_name: possible_duplicate_order
description: Test not equals rule.
conditions:
  match_all:
    - field: payload.status
      not_equals: cancelled
outcomes:
  matched:
    reason: Status is not cancelled.
  unmatched:
    reason: Status is cancelled.
""",
                encoding="utf-8",
            )
            engine = RuleEngine(rules_dir=rules_dir)

            active = engine.evaluate(
                "possible_duplicate_order",
                {"payload": {"status": "active"}},
            )
            cancelled = engine.evaluate(
                "possible_duplicate_order",
                {"payload": {"status": "cancelled"}},
            )

        self.assertTrue(active.matched)
        self.assertEqual(active.metadata["matched_fields"], ["payload.status"])
        self.assertFalse(cancelled.matched)

    def test_nested_match_all_and_match_any_groups_are_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rules_dir = Path(tmpdir)
            (rules_dir / "possible_duplicate_order.yaml").write_text(
                """
decision_name: possible_duplicate_order
description: Test nested groups.
conditions:
  match_all:
    - field: payload.patient_id
      not_equals: ""
    - match_any:
        - field: payload.duplicate_candidate
          equals: true
        - match_all:
            - field: payload.repeated_indicator
              equals: true
            - field: payload.status
              not_equals: cancelled
outcomes:
  matched:
    reason: Nested rule matched.
  unmatched:
    reason: Nested rule did not match.
""",
                encoding="utf-8",
            )
            engine = RuleEngine(rules_dir=rules_dir)

            direct_duplicate = engine.evaluate(
                "possible_duplicate_order",
                {
                    "payload": {
                        "patient_id": "P001",
                        "duplicate_candidate": True,
                        "status": "cancelled",
                    }
                },
            )
            repeated_active = engine.evaluate(
                "possible_duplicate_order",
                {
                    "payload": {
                        "patient_id": "P002",
                        "repeated_indicator": True,
                        "status": "active",
                    }
                },
            )
            repeated_cancelled = engine.evaluate(
                "possible_duplicate_order",
                {
                    "payload": {
                        "patient_id": "P003",
                        "repeated_indicator": True,
                        "status": "cancelled",
                    }
                },
            )
            missing_patient = engine.evaluate(
                "possible_duplicate_order",
                {
                    "payload": {
                        "patient_id": "",
                        "duplicate_candidate": True,
                        "status": "active",
                    }
                },
            )

        self.assertTrue(direct_duplicate.matched)
        self.assertTrue(repeated_active.matched)
        self.assertFalse(repeated_cancelled.matched)
        self.assertFalse(missing_patient.matched)

    def test_fallback_yaml_parser_supports_nested_groups_without_pyyaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rules_dir = Path(tmpdir)
            (rules_dir / "possible_duplicate_order.yaml").write_text(
                """
decision_name: possible_duplicate_order
description: Test fallback nested parser.
conditions:
  match_all:
    - field: payload.patient_id
      not_equals: ""
    - match_any:
        - field: payload.manual_review
          equals: true
        - field: payload.repeated_indicator
          equals: true
outcomes:
  matched:
    reason: Fallback nested rule matched.
  unmatched:
    reason: Fallback nested rule did not match.
""",
                encoding="utf-8",
            )

            with patch("app.system.orchestration.rules.yaml", None):
                engine = RuleEngine(rules_dir=rules_dir)
                result = engine.evaluate(
                    "possible_duplicate_order",
                    {
                        "payload": {
                            "patient_id": "P001",
                            "manual_review": True,
                        }
                    },
                )

        self.assertTrue(result.matched)
        self.assertEqual(result.reason, "Fallback nested rule matched.")


class WorkflowRuleGrammarTests(unittest.TestCase):
    def _engine_for_rule(self, rule_yaml: str) -> RuleEngine:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        rules_dir = Path(self._tmpdir.name)
        (rules_dir / "possible_duplicate_order.yaml").write_text(
            rule_yaml,
            encoding="utf-8",
        )
        return RuleEngine(rules_dir=rules_dir)

    def test_simple_existing_equals_rule_still_works(self) -> None:
        engine = self._engine_for_rule(
            """
decision_name: possible_duplicate_order
description: Existing equals rule.
conditions:
  match_any:
    - field: payload.duplicate_candidate
      equals: true
outcomes:
  matched:
    reason: Existing equals matched.
  unmatched:
    reason: Existing equals did not match.
"""
        )

        matched = engine.evaluate(
            "possible_duplicate_order",
            {"payload": {"duplicate_candidate": True}},
        )
        not_matched = engine.evaluate(
            "possible_duplicate_order",
            {"payload": {"duplicate_candidate": False}},
        )

        self.assertTrue(matched.matched)
        self.assertEqual(matched.reason, "Existing equals matched.")
        self.assertFalse(not_matched.matched)

    def test_not_equals_works(self) -> None:
        engine = self._engine_for_rule(
            """
decision_name: possible_duplicate_order
description: Not equals rule.
conditions:
  match_all:
    - field: payload.status
      not_equals: cancelled
outcomes:
  matched:
    reason: Status allowed.
  unmatched:
    reason: Status blocked.
"""
        )

        allowed = engine.evaluate(
            "possible_duplicate_order",
            {"payload": {"status": "active"}},
        )
        blocked = engine.evaluate(
            "possible_duplicate_order",
            {"payload": {"status": "cancelled"}},
        )

        self.assertTrue(allowed.matched)
        self.assertFalse(blocked.matched)

    def test_nested_match_any_works(self) -> None:
        engine = self._engine_for_rule(
            """
decision_name: possible_duplicate_order
description: Nested match_any rule.
conditions:
  match_all:
    - match_any:
        - field: payload.manual_review
          equals: true
        - field: payload.repeated_indicator
          equals: true
outcomes:
  matched:
    reason: Nested any matched.
  unmatched:
    reason: Nested any did not match.
"""
        )

        first_branch = engine.evaluate(
            "possible_duplicate_order",
            {"payload": {"manual_review": True}},
        )
        second_branch = engine.evaluate(
            "possible_duplicate_order",
            {"payload": {"repeated_indicator": True}},
        )
        no_branch = engine.evaluate(
            "possible_duplicate_order",
            {"payload": {"manual_review": False, "repeated_indicator": False}},
        )

        self.assertTrue(first_branch.matched)
        self.assertTrue(second_branch.matched)
        self.assertFalse(no_branch.matched)

    def test_nested_match_all_works(self) -> None:
        engine = self._engine_for_rule(
            """
decision_name: possible_duplicate_order
description: Nested match_all rule.
conditions:
  match_any:
    - match_all:
        - field: payload.repeated_indicator
          equals: true
        - field: payload.status
          not_equals: cancelled
outcomes:
  matched:
    reason: Nested all matched.
  unmatched:
    reason: Nested all did not match.
"""
        )

        matched = engine.evaluate(
            "possible_duplicate_order",
            {"payload": {"repeated_indicator": True, "status": "active"}},
        )
        missing_one = engine.evaluate(
            "possible_duplicate_order",
            {"payload": {"repeated_indicator": True, "status": "cancelled"}},
        )

        self.assertTrue(matched.matched)
        self.assertFalse(missing_one.matched)

    def test_combined_nested_rule_works(self) -> None:
        engine = self._engine_for_rule(
            """
decision_name: possible_duplicate_order
description: Combined nested rule.
conditions:
  match_all:
    - field: payload.patient_id
      not_equals: ""
    - match_any:
        - field: payload.duplicate_candidate
          equals: true
        - match_all:
            - field: payload.repeated_indicator
              equals: true
            - field: payload.status
              not_equals: cancelled
outcomes:
  matched:
    reason: Combined nested matched.
  unmatched:
    reason: Combined nested did not match.
"""
        )

        duplicate = engine.evaluate(
            "possible_duplicate_order",
            {"payload": {"patient_id": "P1", "duplicate_candidate": True}},
        )
        repeated_active = engine.evaluate(
            "possible_duplicate_order",
            {
                "payload": {
                    "patient_id": "P2",
                    "repeated_indicator": True,
                    "status": "active",
                }
            },
        )
        repeated_cancelled = engine.evaluate(
            "possible_duplicate_order",
            {
                "payload": {
                    "patient_id": "P3",
                    "repeated_indicator": True,
                    "status": "cancelled",
                }
            },
        )
        missing_patient = engine.evaluate(
            "possible_duplicate_order",
            {"payload": {"patient_id": "", "duplicate_candidate": True}},
        )

        self.assertTrue(duplicate.matched)
        self.assertTrue(repeated_active.matched)
        self.assertFalse(repeated_cancelled.matched)
        self.assertFalse(missing_patient.matched)

    def test_invalid_operator_fails_safely(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rules_dir = Path(tmpdir)
            (rules_dir / "possible_duplicate_order.yaml").write_text(
                """
decision_name: possible_duplicate_order
description: Invalid operator rule.
conditions:
  match_all:
    - field: payload.status
      greater_than: active
outcomes:
  matched:
    reason: Invalid matched.
  unmatched:
    reason: Invalid did not match.
""",
                encoding="utf-8",
            )

            with patch("app.system.orchestration.rules.logger.exception") as log_exception:
                engine = RuleEngine(rules_dir=rules_dir)
            result = engine.evaluate(
                "possible_duplicate_order",
                {"payload": {"status": "active"}},
            )

        log_exception.assert_called_once()
        self.assertFalse(result.matched)
        self.assertEqual(result.metadata["error"], "missing_rule")

    def test_malformed_nested_structure_fails_safely(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rules_dir = Path(tmpdir)
            (rules_dir / "possible_duplicate_order.yaml").write_text(
                """
decision_name: possible_duplicate_order
description: Malformed nested rule.
conditions:
  match_all:
    - match_any:
        field: payload.manual_review
        equals: true
outcomes:
  matched:
    reason: Malformed matched.
  unmatched:
    reason: Malformed did not match.
""",
                encoding="utf-8",
            )

            with patch("app.system.orchestration.rules.logger.exception") as log_exception:
                engine = RuleEngine(rules_dir=rules_dir)
            result = engine.evaluate(
                "possible_duplicate_order",
                {"payload": {"manual_review": True}},
            )

        log_exception.assert_called_once()
        self.assertFalse(result.matched)
        self.assertEqual(result.metadata["error"], "missing_rule")

    def test_preview_diff_works_with_richer_rule_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            candidate_path = Path(tmpdir) / "possible_duplicate_order.yaml"
            candidate_path.write_text(
                """
decision_name: possible_duplicate_order
description: Richer candidate rule.
conditions:
  match_all:
    - field: payload.patient_id
      not_equals: ""
    - match_any:
        - field: payload.manual_review
          equals: true
        - match_all:
            - field: payload.repeated_indicator
              equals: true
            - field: payload.status
              not_equals: cancelled
outcomes:
  matched:
    reason: Richer candidate matched.
  unmatched:
    reason: Richer candidate did not match.
""",
                encoding="utf-8",
            )

            preview = RulePreviewService().preview_rule_change(
                "possible_duplicate_order",
                {
                    "payload": {
                        "order_id": "RICH-1",
                        "patient_id": "P001",
                        "manual_review": True,
                    }
                },
                candidate_rule_path=candidate_path,
            )

        self.assertFalse(preview.baseline_result.matched)
        self.assertTrue(preview.candidate_result.matched)
        self.assertTrue(preview.changed)
        self.assertIsNone(preview.candidate_result.error)


class RulePreviewServiceTests(unittest.TestCase):
    def test_preview_baseline_rule_on_sample_context(self) -> None:
        preview = RulePreviewService().preview_rule_change(
            "possible_duplicate_order",
            {"payload": {"order_id": "1", "duplicate_candidate": True}},
        )

        self.assertFalse(preview.changed)
        self.assertTrue(preview.baseline_result.matched)
        self.assertTrue(preview.baseline_result.would_create_review_flag)
        self.assertTrue(preview.candidate_result.would_create_review_flag)
        self.assertEqual(preview.context_summary["order_id"], "1")

    def test_preview_reports_candidate_behavior_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            candidate_path = Path(tmpdir) / "possible_duplicate_order.yaml"
            candidate_path.write_text(
                """
decision_name: possible_duplicate_order
description: Candidate rule matches manual review.
conditions:
  match_any:
    - field: payload.manual_review
      equals: true
outcomes:
  matched:
    reason: Candidate manual-review rule matched.
  unmatched:
    reason: Candidate manual-review rule did not match.
""",
                encoding="utf-8",
            )

            preview = RulePreviewService().preview_rule_change(
                "possible_duplicate_order",
                {"payload": {"order_id": "2", "manual_review": True}},
                candidate_rule_path=candidate_path,
            )

        self.assertFalse(preview.baseline_result.matched)
        self.assertTrue(preview.candidate_result.matched)
        self.assertTrue(preview.changed)
        self.assertIn("matched False -> True", preview.change_summary)
        self.assertFalse(preview.baseline_result.would_create_review_flag)
        self.assertTrue(preview.candidate_result.would_create_review_flag)

    def test_preview_reports_unchanged_behavior(self) -> None:
        preview = RulePreviewService().preview_rule_change(
            "possible_duplicate_order",
            {"payload": {"order_id": "3"}},
        )

        self.assertFalse(preview.baseline_result.matched)
        self.assertFalse(preview.candidate_result.matched)
        self.assertFalse(preview.changed)
        self.assertEqual(preview.change_summary, "No rule behavior change for this context.")

    def test_invalid_candidate_rule_fails_safely(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            candidate_path = Path(tmpdir) / "broken.yaml"
            candidate_path.write_text("not: [valid", encoding="utf-8")

            with patch("app.system.orchestration.rule_preview.logger.exception") as log_exception:
                preview = RulePreviewService().preview_rule_change(
                    "possible_duplicate_order",
                    {"payload": {"order_id": "4", "duplicate_candidate": True}},
                    candidate_rule_path=candidate_path,
                )

        log_exception.assert_called_once()
        self.assertTrue(preview.baseline_result.matched)
        self.assertFalse(preview.candidate_result.matched)
        self.assertEqual(preview.candidate_result.error, "invalid_candidate_rule")
        self.assertTrue(preview.changed)

    def test_preview_accepts_candidate_with_not_equals_and_nested_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            candidate_path = Path(tmpdir) / "possible_duplicate_order.yaml"
            candidate_path.write_text(
                """
decision_name: possible_duplicate_order
description: Candidate rule uses nested groups.
conditions:
  match_all:
    - field: payload.patient_id
      not_equals: ""
    - match_any:
        - field: payload.manual_review
          equals: true
        - field: payload.repeated_indicator
          equals: true
outcomes:
  matched:
    reason: Candidate nested rule matched.
  unmatched:
    reason: Candidate nested rule did not match.
""",
                encoding="utf-8",
            )

            preview = RulePreviewService().preview_rule_change(
                "possible_duplicate_order",
                {
                    "payload": {
                        "order_id": "NEST-1",
                        "patient_id": "P001",
                        "manual_review": True,
                    }
                },
                candidate_rule_path=candidate_path,
            )

        self.assertFalse(preview.baseline_result.matched)
        self.assertTrue(preview.candidate_result.matched)
        self.assertIsNone(preview.candidate_result.error)

    def test_preview_does_not_create_real_action_adapter_side_effects(self) -> None:
        adapter = WorkflowActionAdapter()

        RulePreviewService().preview_rule_change(
            "possible_duplicate_order",
            {"payload": {"order_id": "5", "duplicate_candidate": True}},
        )

        self.assertEqual(adapter.get_entity_flags("order", "5"), [])


class RulePreviewUiHelperTests(unittest.TestCase):
    def test_preview_ui_helper_returns_structured_preview(self) -> None:
        result = preview_rule_text(
            "possible_duplicate_order",
            DEFAULT_CONTEXT_JSON,
            "",
        )

        self.assertTrue(result["ok"])
        self.assertIn("preview", result)
        self.assertTrue(result["preview"]["baseline_result"]["matched"])
        self.assertFalse(result["preview"]["changed"])

    def test_preview_ui_helper_handles_invalid_context_json(self) -> None:
        result = preview_rule_text(
            "possible_duplicate_order",
            "{not-json",
            "",
        )

        self.assertFalse(result["ok"])
        self.assertIn("Invalid context JSON", result["error"])

    def test_preview_ui_helper_accepts_candidate_rule_text(self) -> None:
        candidate_yaml = """
decision_name: possible_duplicate_order
description: Candidate rule matches manual review.
conditions:
  match_any:
    - field: payload.manual_review
      equals: true
outcomes:
  matched:
    reason: Candidate manual-review rule matched.
  unmatched:
    reason: Candidate manual-review rule did not match.
"""
        result = preview_rule_text(
            "possible_duplicate_order",
            '{"payload": {"order_id": "UI-1", "manual_review": true}}',
            candidate_yaml,
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["preview"]["changed"])
        self.assertFalse(result["preview"]["baseline_result"]["matched"])
        self.assertTrue(result["preview"]["candidate_result"]["matched"])

    def test_preview_ui_helper_does_not_create_live_side_effects(self) -> None:
        adapter = WorkflowActionAdapter()

        preview_rule_text(
            "possible_duplicate_order",
            '{"payload": {"order_id": "UI-2", "duplicate_candidate": true}}',
            "",
        )

        self.assertEqual(adapter.get_entity_flags("order", "UI-2"), [])


class RuleDraftManagerTests(unittest.TestCase):
    def test_candidate_draft_can_be_saved_listed_and_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RuleDraftManager(drafts_root=Path(tmpdir))
            draft = manager.save_rule_draft(
                "possible_duplicate_order",
                load_default_candidate_rule_text(),
                "Initial candidate",
            )

            drafts = manager.list_rule_drafts("possible_duplicate_order")
            loaded = manager.load_rule_draft("possible_duplicate_order", draft.draft_id)

        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].draft_id, draft.draft_id)
        self.assertEqual(drafts[0].state, "draft")
        self.assertEqual(drafts[0].history[0]["to_state"], "draft")
        self.assertIn("decision_name: possible_duplicate_order", loaded)

    def test_invalid_draft_content_fails_safely(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RuleDraftManager(drafts_root=Path(tmpdir))

            with self.assertRaises(Exception):
                manager.save_rule_draft("possible_duplicate_order", "not: [valid")

            self.assertEqual(manager.list_rule_drafts("possible_duplicate_order"), [])

    def test_baseline_rule_remains_unchanged_after_draft_operations(self) -> None:
        baseline_text = load_default_candidate_rule_text()
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RuleDraftManager(drafts_root=Path(tmpdir))
            manager.save_rule_draft(
                "possible_duplicate_order",
                baseline_text.replace("duplicate_candidate", "manual_review"),
                "Alternative candidate",
            )

        self.assertEqual(load_default_candidate_rule_text(), baseline_text)

    def test_preview_can_compare_baseline_vs_saved_draft(self) -> None:
        candidate_yaml = """
decision_name: possible_duplicate_order
description: Candidate rule matches manual review.
conditions:
  match_any:
    - field: payload.manual_review
      equals: true
outcomes:
  matched:
    reason: Candidate manual-review rule matched.
  unmatched:
    reason: Candidate manual-review rule did not match.
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RuleDraftManager(drafts_root=Path(tmpdir))
            draft = manager.save_rule_draft("possible_duplicate_order", candidate_yaml)
            preview = RulePreviewService().preview_rule_change(
                "possible_duplicate_order",
                {"payload": {"order_id": "DRAFT-1", "manual_review": True}},
                candidate_rule_path=manager.get_draft_path("possible_duplicate_order", draft.draft_id),
            )

        self.assertFalse(preview.baseline_result.matched)
        self.assertTrue(preview.candidate_result.matched)
        self.assertTrue(preview.changed)

    def test_delete_draft_works_safely(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RuleDraftManager(drafts_root=Path(tmpdir))
            draft = manager.save_rule_draft("possible_duplicate_order", load_default_candidate_rule_text())

            deleted = manager.delete_rule_draft("possible_duplicate_order", draft.draft_id)
            deleted_again = manager.delete_rule_draft("possible_duplicate_order", draft.draft_id)

        self.assertTrue(deleted)
        self.assertFalse(deleted_again)

    def test_invalid_rule_name_cannot_escape_draft_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RuleDraftManager(drafts_root=Path(tmpdir))

            with self.assertRaises(ValueError):
                manager.save_rule_draft("../possible_duplicate_order", load_default_candidate_rule_text())

    def test_invalid_draft_metadata_is_skipped_safely(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RuleDraftManager(drafts_root=Path(tmpdir))
            rule_dir = Path(tmpdir) / "possible_duplicate_order"
            rule_dir.mkdir()
            (rule_dir / "broken.json").write_text("{not-json", encoding="utf-8")

            with patch("app.system.orchestration.rule_drafts.logger.exception") as log_exception:
                drafts = manager.list_rule_drafts("possible_duplicate_order")

        log_exception.assert_called_once()
        self.assertEqual(drafts, [])

    def test_preview_ui_helpers_work_with_drafts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RuleDraftManager(drafts_root=Path(tmpdir))
            save_result = save_rule_draft_text(
                "possible_duplicate_order",
                load_default_candidate_rule_text(),
                "UI helper draft",
                draft_manager=manager,
            )
            drafts = list_rule_drafts("possible_duplicate_order", draft_manager=manager)
            load_result = load_rule_draft_text(
                "possible_duplicate_order",
                save_result["draft"]["draft_id"],
                draft_manager=manager,
            )
            preview_result = preview_rule_draft(
                "possible_duplicate_order",
                DEFAULT_CONTEXT_JSON,
                save_result["draft"]["draft_id"],
                draft_manager=manager,
            )

        self.assertTrue(save_result["ok"])
        self.assertEqual(len(drafts), 1)
        self.assertTrue(load_result["ok"])
        self.assertIn("possible_duplicate_order", load_result["content"])
        self.assertTrue(preview_result["ok"])
        self.assertIn("preview", preview_result)

    def test_preview_ui_save_draft_preserves_candidate_text(self) -> None:
        baseline_text = load_default_candidate_rule_text()
        candidate_text = baseline_text.replace(
            "No duplicate-order indicators were present.",
            "Candidate draft did not match.",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RuleDraftManager(drafts_root=Path(tmpdir))
            save_result = save_rule_draft_text(
                "possible_duplicate_order",
                candidate_text,
                "Modified candidate",
                draft_manager=manager,
            )
            load_result = load_rule_draft_text(
                "possible_duplicate_order",
                save_result["draft"]["draft_id"],
                draft_manager=manager,
            )

        self.assertTrue(save_result["ok"])
        self.assertTrue(load_result["ok"])
        self.assertEqual(load_result["content"], candidate_text.strip())
        self.assertNotEqual(load_result["content"], baseline_text.strip())


class RuleDraftLifecycleServiceTests(unittest.TestCase):
    def test_valid_lifecycle_transitions_succeed_and_record_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RuleDraftManager(drafts_root=Path(tmpdir))
            draft = manager.save_rule_draft(
                "possible_duplicate_order",
                load_default_candidate_rule_text(),
            )
            lifecycle = RuleDraftLifecycleService(draft_manager=manager)

            reviewed = lifecycle.mark_reviewed(
                "possible_duplicate_order",
                draft.draft_id,
                "Reviewed in preview.",
            )
            approved = lifecycle.approve_draft(
                "possible_duplicate_order",
                draft.draft_id,
                "Approved for promotion.",
            )
            info = manager.get_rule_draft_info("possible_duplicate_order", draft.draft_id)

        self.assertTrue(reviewed.ok)
        self.assertEqual(reviewed.old_state, "draft")
        self.assertEqual(reviewed.new_state, "reviewed")
        self.assertTrue(approved.ok)
        self.assertEqual(info.state, "approved")
        self.assertEqual([entry["to_state"] for entry in info.history], ["draft", "reviewed", "approved"])

    def test_invalid_lifecycle_transition_fails_safely(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RuleDraftManager(drafts_root=Path(tmpdir))
            draft = manager.save_rule_draft(
                "possible_duplicate_order",
                load_default_candidate_rule_text(),
            )
            lifecycle = RuleDraftLifecycleService(draft_manager=manager)

            result = lifecycle.approve_draft("possible_duplicate_order", draft.draft_id)
            info = manager.get_rule_draft_info("possible_duplicate_order", draft.draft_id)

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "invalid_transition")
        self.assertEqual(info.state, "draft")

    def test_preview_ui_helper_can_transition_draft_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RuleDraftManager(drafts_root=Path(tmpdir))
            draft = manager.save_rule_draft(
                "possible_duplicate_order",
                load_default_candidate_rule_text(),
            )
            lifecycle = RuleDraftLifecycleService(draft_manager=manager)

            result = transition_rule_draft(
                "possible_duplicate_order",
                draft.draft_id,
                "reviewed",
                lifecycle_service=lifecycle,
            )
            info = manager.get_rule_draft_info("possible_duplicate_order", draft.draft_id)

        self.assertTrue(result["ok"])
        self.assertEqual(info.state, "reviewed")


class RulePreviewSnapshotServiceTests(unittest.TestCase):
    def test_preview_result_can_be_saved_as_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RuleDraftManager(drafts_root=Path(tmpdir) / "drafts")
            draft = manager.save_rule_draft(
                "possible_duplicate_order",
                load_default_candidate_rule_text(),
            )
            service = RulePreviewSnapshotService(
                draft_manager=manager,
                snapshots_root=Path(tmpdir) / "snapshots",
            )

            snapshot = service.create_preview_snapshot(
                "possible_duplicate_order",
                draft.draft_id,
                {"payload": {"order_id": "SNAP-1", "duplicate_candidate": True}},
                note="Evidence for approval.",
            )

        self.assertEqual(snapshot.rule_name, "possible_duplicate_order")
        self.assertEqual(snapshot.draft_id, draft.draft_id)
        self.assertTrue(snapshot.preview["baseline_result"]["matched"])
        self.assertTrue(snapshot.preview["candidate_result"]["matched"])
        self.assertEqual(snapshot.context["payload"]["order_id"], "SNAP-1")

    def test_snapshots_are_retrievable_and_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RuleDraftManager(drafts_root=Path(tmpdir) / "drafts")
            draft = manager.save_rule_draft(
                "possible_duplicate_order",
                load_default_candidate_rule_text(),
            )
            service = RulePreviewSnapshotService(
                draft_manager=manager,
                snapshots_root=Path(tmpdir) / "snapshots",
            )
            snapshot = service.create_preview_snapshot(
                "possible_duplicate_order",
                draft.draft_id,
                {"payload": {"order_id": "SNAP-2", "duplicate_candidate": True}},
            )
            original_payload = Path(snapshot.path).read_text(encoding="utf-8")
            manager.get_draft_path(
                "possible_duplicate_order",
                draft.draft_id,
            ).write_text(
                load_default_candidate_rule_text().replace("duplicate_candidate", "manual_review"),
                encoding="utf-8",
            )

            listed = service.list_rule_snapshots("possible_duplicate_order", draft.draft_id)
            loaded = service.load_rule_snapshot(snapshot.snapshot_id)
            after_edit_payload = Path(snapshot.path).read_text(encoding="utf-8")

        self.assertEqual(len(listed), 1)
        self.assertEqual(loaded.snapshot_id, snapshot.snapshot_id)
        self.assertEqual(after_edit_payload, original_payload)

    def test_approval_can_link_to_snapshot_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RuleDraftManager(drafts_root=Path(tmpdir) / "drafts")
            draft = manager.save_rule_draft(
                "possible_duplicate_order",
                load_default_candidate_rule_text(),
            )
            snapshot_service = RulePreviewSnapshotService(
                draft_manager=manager,
                snapshots_root=Path(tmpdir) / "snapshots",
            )
            snapshot = snapshot_service.create_preview_snapshot(
                "possible_duplicate_order",
                draft.draft_id,
                {"payload": {"order_id": "SNAP-3", "duplicate_candidate": True}},
            )
            lifecycle = RuleDraftLifecycleService(
                draft_manager=manager,
                snapshot_service=snapshot_service,
            )

            lifecycle.mark_reviewed("possible_duplicate_order", draft.draft_id)
            approved = lifecycle.approve_draft(
                "possible_duplicate_order",
                draft.draft_id,
                snapshot_id=snapshot.snapshot_id,
            )
            info = manager.get_rule_draft_info("possible_duplicate_order", draft.draft_id)

        self.assertTrue(approved.ok)
        self.assertEqual(info.approved_snapshot_id, snapshot.snapshot_id)
        self.assertEqual(info.history[-1]["snapshot_id"], snapshot.snapshot_id)

    def test_missing_snapshot_reference_fails_safely(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RuleDraftManager(drafts_root=Path(tmpdir) / "drafts")
            draft = manager.save_rule_draft(
                "possible_duplicate_order",
                load_default_candidate_rule_text(),
            )
            lifecycle = RuleDraftLifecycleService(
                draft_manager=manager,
                snapshot_service=RulePreviewSnapshotService(
                    draft_manager=manager,
                    snapshots_root=Path(tmpdir) / "snapshots",
                ),
            )

            lifecycle.mark_reviewed("possible_duplicate_order", draft.draft_id)
            result = lifecycle.approve_draft(
                "possible_duplicate_order",
                draft.draft_id,
                snapshot_id="missing",
            )
            info = manager.get_rule_draft_info("possible_duplicate_order", draft.draft_id)

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "snapshot_missing")
        self.assertEqual(info.state, "reviewed")

    def test_snapshot_for_different_draft_fails_safely(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RuleDraftManager(drafts_root=Path(tmpdir) / "drafts")
            first_draft = manager.save_rule_draft(
                "possible_duplicate_order",
                load_default_candidate_rule_text(),
            )
            second_draft = manager.save_rule_draft(
                "possible_duplicate_order",
                load_default_candidate_rule_text(),
            )
            snapshot_service = RulePreviewSnapshotService(
                draft_manager=manager,
                snapshots_root=Path(tmpdir) / "snapshots",
            )
            snapshot = snapshot_service.create_preview_snapshot(
                "possible_duplicate_order",
                first_draft.draft_id,
                {"payload": {"order_id": "SNAP-4", "duplicate_candidate": True}},
            )
            lifecycle = RuleDraftLifecycleService(
                draft_manager=manager,
                snapshot_service=snapshot_service,
            )

            lifecycle.mark_reviewed("possible_duplicate_order", second_draft.draft_id)
            result = lifecycle.approve_draft(
                "possible_duplicate_order",
                second_draft.draft_id,
                snapshot_id=snapshot.snapshot_id,
            )
            info = manager.get_rule_draft_info(
                "possible_duplicate_order",
                second_draft.draft_id,
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "snapshot_mismatch")
        self.assertEqual(info.state, "reviewed")

    def test_preview_ui_helpers_create_and_load_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = RuleDraftManager(drafts_root=Path(tmpdir) / "drafts")
            draft = manager.save_rule_draft(
                "possible_duplicate_order",
                load_default_candidate_rule_text(),
            )
            snapshot_service = RulePreviewSnapshotService(
                draft_manager=manager,
                snapshots_root=Path(tmpdir) / "snapshots",
            )

            created = create_preview_snapshot(
                "possible_duplicate_order",
                draft.draft_id,
                DEFAULT_CONTEXT_JSON,
                snapshot_service=snapshot_service,
            )
            listed = list_preview_snapshots(
                "possible_duplicate_order",
                draft.draft_id,
                snapshot_service=snapshot_service,
            )
            loaded = load_preview_snapshot(
                created["snapshot"]["snapshot_id"],
                snapshot_service=snapshot_service,
            )

        self.assertTrue(created["ok"])
        self.assertEqual(len(listed), 1)
        self.assertTrue(loaded["ok"])
        self.assertEqual(
            loaded["snapshot"]["snapshot_id"],
            created["snapshot"]["snapshot_id"],
        )


class RulePromotionServiceTests(unittest.TestCase):
    def test_default_promotion_history_is_stored_next_to_app_drafts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            drafts_root = Path(tmpdir) / "workflow_rule_drafts"
            manager = RuleDraftManager(drafts_root=drafts_root)
            service = RulePromotionService(draft_manager=manager)

        self.assertEqual(
            service.history_dir,
            drafts_root.parent / "workflow_rule_history",
        )

    def test_promotion_without_confirmation_does_not_proceed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            baseline_dir = Path(tmpdir) / "rules"
            baseline_dir.mkdir()
            baseline_path = baseline_dir / "possible_duplicate_order.yaml"
            baseline_text = load_default_candidate_rule_text()
            baseline_path.write_text(baseline_text, encoding="utf-8")
            manager = RuleDraftManager(drafts_root=Path(tmpdir) / "drafts")
            draft = manager.save_rule_draft(
                "possible_duplicate_order",
                baseline_text.replace("duplicate_candidate", "manual_review"),
            )
            service = RulePromotionService(
                draft_manager=manager,
                baseline_rules_dir=baseline_dir,
            )

            result = service.promote_rule_draft(
                "possible_duplicate_order",
                draft.draft_id,
                confirmation=False,
            )
            baseline_after = baseline_path.read_text(encoding="utf-8")

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "confirmation_required")
        self.assertEqual(baseline_after, baseline_text)

    def test_valid_draft_can_be_promoted_with_backup_and_history(self) -> None:
        candidate_yaml = """
decision_name: possible_duplicate_order
description: Candidate rule matches manual review.
conditions:
  match_any:
    - field: payload.manual_review
      equals: true
outcomes:
  matched:
    reason: Candidate manual-review rule matched.
  unmatched:
    reason: Candidate manual-review rule did not match.
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            baseline_dir = Path(tmpdir) / "rules"
            baseline_dir.mkdir()
            baseline_path = baseline_dir / "possible_duplicate_order.yaml"
            baseline_text = load_default_candidate_rule_text()
            baseline_path.write_text(baseline_text, encoding="utf-8")
            manager = RuleDraftManager(drafts_root=Path(tmpdir) / "drafts")
            draft = manager.save_rule_draft("possible_duplicate_order", candidate_yaml)
            lifecycle = RuleDraftLifecycleService(draft_manager=manager)
            lifecycle.mark_reviewed("possible_duplicate_order", draft.draft_id)
            lifecycle.approve_draft("possible_duplicate_order", draft.draft_id)
            service = RulePromotionService(
                draft_manager=manager,
                lifecycle_service=lifecycle,
                baseline_rules_dir=baseline_dir,
            )

            result = service.promote_rule_draft(
                "possible_duplicate_order",
                draft.draft_id,
                confirmation=True,
                note="Promote tested candidate",
            )

            promoted_preview = RulePreviewService(
                baseline_rules_dir=baseline_dir
            ).preview_rule_change(
                "possible_duplicate_order",
                {"payload": {"order_id": "PROMO-1", "manual_review": True}},
            )
            promoted_baseline = baseline_path.read_text(encoding="utf-8")
            backup_content = Path(result.backup_path).read_text(encoding="utf-8")
            history_exists = Path(result.history_path).exists()
            draft_info = manager.get_rule_draft_info("possible_duplicate_order", draft.draft_id)

        self.assertTrue(result.ok)
        self.assertEqual(promoted_baseline, candidate_yaml.strip())
        self.assertEqual(backup_content, baseline_text)
        self.assertTrue(history_exists)
        self.assertEqual(draft_info.state, "promoted")
        self.assertTrue(promoted_preview.baseline_result.matched)
        self.assertFalse(promoted_preview.changed)

    def test_promotion_requires_approved_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            baseline_dir = Path(tmpdir) / "rules"
            baseline_dir.mkdir()
            baseline_path = baseline_dir / "possible_duplicate_order.yaml"
            baseline_text = load_default_candidate_rule_text()
            baseline_path.write_text(baseline_text, encoding="utf-8")
            manager = RuleDraftManager(drafts_root=Path(tmpdir) / "drafts")
            draft = manager.save_rule_draft(
                "possible_duplicate_order",
                baseline_text.replace("duplicate_candidate", "manual_review"),
            )
            service = RulePromotionService(
                draft_manager=manager,
                baseline_rules_dir=baseline_dir,
            )

            result = service.promote_rule_draft(
                "possible_duplicate_order",
                draft.draft_id,
                confirmation=True,
            )
            baseline_after = baseline_path.read_text(encoding="utf-8")

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "approval_required")
        self.assertEqual(baseline_after, baseline_text)

    def test_invalid_draft_cannot_be_promoted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            baseline_dir = Path(tmpdir) / "rules"
            baseline_dir.mkdir()
            baseline_path = baseline_dir / "possible_duplicate_order.yaml"
            baseline_text = load_default_candidate_rule_text()
            baseline_path.write_text(baseline_text, encoding="utf-8")
            manager = RuleDraftManager(drafts_root=Path(tmpdir) / "drafts")
            draft = manager.save_rule_draft("possible_duplicate_order", baseline_text)
            lifecycle = RuleDraftLifecycleService(draft_manager=manager)
            lifecycle.mark_reviewed("possible_duplicate_order", draft.draft_id)
            lifecycle.approve_draft("possible_duplicate_order", draft.draft_id)
            manager.get_draft_path(
                "possible_duplicate_order",
                draft.draft_id,
            ).write_text("not: [valid", encoding="utf-8")
            service = RulePromotionService(
                draft_manager=manager,
                lifecycle_service=lifecycle,
                baseline_rules_dir=baseline_dir,
            )

            result = service.promote_rule_draft(
                "possible_duplicate_order",
                draft.draft_id,
                confirmation=True,
            )
            baseline_after = baseline_path.read_text(encoding="utf-8")

        self.assertFalse(result.ok)
        self.assertEqual(baseline_after, baseline_text)

    def test_preview_ui_helper_can_promote_draft_safely(self) -> None:
        candidate_yaml = """
decision_name: possible_duplicate_order
description: Candidate rule matches manual review.
conditions:
  match_any:
    - field: payload.manual_review
      equals: true
outcomes:
  matched:
    reason: Candidate manual-review rule matched.
  unmatched:
    reason: Candidate manual-review rule did not match.
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            baseline_dir = Path(tmpdir) / "rules"
            baseline_dir.mkdir()
            baseline_path = baseline_dir / "possible_duplicate_order.yaml"
            baseline_path.write_text(load_default_candidate_rule_text(), encoding="utf-8")
            manager = RuleDraftManager(drafts_root=Path(tmpdir) / "drafts")
            draft = manager.save_rule_draft("possible_duplicate_order", candidate_yaml)
            lifecycle = RuleDraftLifecycleService(draft_manager=manager)
            lifecycle.mark_reviewed("possible_duplicate_order", draft.draft_id)
            lifecycle.approve_draft("possible_duplicate_order", draft.draft_id)
            service = RulePromotionService(
                draft_manager=manager,
                lifecycle_service=lifecycle,
                baseline_rules_dir=baseline_dir,
            )

            result = promote_rule_draft(
                "possible_duplicate_order",
                draft.draft_id,
                confirmation=True,
                note="UI promotion",
                promotion_service=service,
            )
            evaluation = RuleEngine(rules_dir=baseline_dir).evaluate(
                "possible_duplicate_order",
                {"payload": {"manual_review": True}},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["message"], "Draft promoted to baseline.")
        self.assertTrue(evaluation.matched)

    def test_promotion_does_not_change_simulation_outcome(self) -> None:
        def run_simple_simulation() -> tuple[float, int, str]:
            workflow = Workflow()
            intake = BaseWidget("Intake", service_time=1.0)
            analyzer = BaseWidget("Analyzer", service_time=2.0)
            workflow.connect(intake, analyzer)
            simulator = Simulator(workflow)
            simulator.send_to(intake, Specimen("S-001"))
            simulator.run()
            return (
                simulator.clock.current_time,
                len(simulator.completed_specimens),
                simulator.completed_specimens[0].specimen_id,
            )

        candidate_yaml = load_default_candidate_rule_text().replace(
            "duplicate_candidate",
            "manual_review",
        )
        baseline = run_simple_simulation()
        with tempfile.TemporaryDirectory() as tmpdir:
            baseline_dir = Path(tmpdir) / "rules"
            baseline_dir.mkdir()
            (baseline_dir / "possible_duplicate_order.yaml").write_text(
                load_default_candidate_rule_text(),
                encoding="utf-8",
            )
            manager = RuleDraftManager(drafts_root=Path(tmpdir) / "drafts")
            draft = manager.save_rule_draft("possible_duplicate_order", candidate_yaml)
            lifecycle = RuleDraftLifecycleService(draft_manager=manager)
            lifecycle.mark_reviewed("possible_duplicate_order", draft.draft_id)
            lifecycle.approve_draft("possible_duplicate_order", draft.draft_id)
            RulePromotionService(
                draft_manager=manager,
                lifecycle_service=lifecycle,
                baseline_rules_dir=baseline_dir,
            ).promote_rule_draft(
                "possible_duplicate_order",
                draft.draft_id,
                confirmation=True,
            )
        after_promotion = run_simple_simulation()

        self.assertEqual(after_promotion, baseline)

    def test_end_to_end_snapshot_approval_and_promotion(self) -> None:
        candidate_yaml = """
decision_name: possible_duplicate_order
description: Candidate rule matches manual review.
conditions:
  match_any:
    - field: payload.manual_review
      equals: true
outcomes:
  matched:
    reason: Candidate manual-review rule matched.
  unmatched:
    reason: Candidate manual-review rule did not match.
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            baseline_dir = Path(tmpdir) / "rules"
            baseline_dir.mkdir()
            baseline_path = baseline_dir / "possible_duplicate_order.yaml"
            baseline_path.write_text(load_default_candidate_rule_text(), encoding="utf-8")
            manager = RuleDraftManager(drafts_root=Path(tmpdir) / "drafts")
            draft = manager.save_rule_draft("possible_duplicate_order", candidate_yaml)
            snapshot_service = RulePreviewSnapshotService(
                draft_manager=manager,
                preview_service=RulePreviewService(baseline_rules_dir=baseline_dir),
                snapshots_root=Path(tmpdir) / "snapshots",
            )
            snapshot = snapshot_service.create_preview_snapshot(
                "possible_duplicate_order",
                draft.draft_id,
                {"payload": {"order_id": "E2E-1", "manual_review": True}},
                note="End-to-end approval evidence.",
            )
            lifecycle = RuleDraftLifecycleService(
                draft_manager=manager,
                snapshot_service=snapshot_service,
            )

            preview_before = snapshot.preview
            lifecycle.mark_reviewed("possible_duplicate_order", draft.draft_id)
            approved = lifecycle.approve_draft(
                "possible_duplicate_order",
                draft.draft_id,
                snapshot_id=snapshot.snapshot_id,
            )
            promoted = RulePromotionService(
                draft_manager=manager,
                lifecycle_service=lifecycle,
                baseline_rules_dir=baseline_dir,
            ).promote_rule_draft(
                "possible_duplicate_order",
                draft.draft_id,
                confirmation=True,
            )
            final_info = manager.get_rule_draft_info(
                "possible_duplicate_order",
                draft.draft_id,
            )
            promotion_record = json.loads(
                Path(promoted.history_path).read_text(encoding="utf-8")
            )
            promoted_baseline = baseline_path.read_text(encoding="utf-8")

        self.assertFalse(preview_before["baseline_result"]["matched"])
        self.assertTrue(preview_before["candidate_result"]["matched"])
        self.assertTrue(approved.ok)
        self.assertTrue(promoted.ok)
        self.assertEqual(final_info.state, "promoted")
        self.assertEqual(final_info.approved_snapshot_id, snapshot.snapshot_id)
        self.assertEqual(promotion_record["approved_snapshot_id"], snapshot.snapshot_id)
        self.assertEqual(promoted_baseline, candidate_yaml.strip())


class SpiffWorkflowRuntimeTests(unittest.TestCase):
    def test_spiff_backend_loads_bpmn_and_creates_bounded_flag(self) -> None:
        adapter = WorkflowActionAdapter()

        with patch.object(
            SpiffWorkflowRuntime,
            "_load_spiff_classes",
            return_value=(FakeBpmnParser, FakeBpmnWorkflow, FakeTaskState),
        ):
            runtime = SpiffWorkflowRuntime(action_adapter=adapter)

        workflow_id = runtime.start_workflow(
            "duplicate_order_review",
            {
                "event_name": "order_created",
                "payload": {"order_id": "10", "duplicate_candidate": True},
            },
        )

        status = runtime.get_status(workflow_id)
        flags = runtime.get_entity_flags("order", "10")
        self.assertEqual(status["backend"], "spiff")
        self.assertEqual(status["state"], "completed")
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["message"], "Possible duplicate order review recommended")

    def test_spiff_backend_false_path_does_not_create_flag(self) -> None:
        with patch.object(
            SpiffWorkflowRuntime,
            "_load_spiff_classes",
            return_value=(FakeBpmnParser, FakeBpmnWorkflow, FakeTaskState),
        ):
            runtime = SpiffWorkflowRuntime()

        workflow_id = runtime.start_workflow(
            "duplicate_order_review",
            {"event_name": "order_created", "payload": {"order_id": "11"}},
        )

        self.assertEqual(runtime.get_status(workflow_id)["state"], "completed")
        self.assertEqual(runtime.get_entity_flags("order", "11"), [])

    def test_spiff_backend_unavailable_falls_back_to_stub_runtime(self) -> None:
        with patch.object(
            SpiffWorkflowRuntime,
            "_load_spiff_classes",
            side_effect=SpiffWorkflowUnavailable("missing"),
        ), patch("app.system.orchestration.facade.logger.exception"):
            facade = WorkflowFacade(config=WorkflowConfig(enabled=True, backend="spiff"))

        workflow_id = facade.start_workflow(
            "duplicate_order_review",
            {
                "event_name": "order_created",
                "payload": {"order_id": "12", "duplicate_candidate": True},
            },
        )

        status = facade.get_status(workflow_id)
        self.assertNotEqual(status.get("backend"), "spiff")
        self.assertEqual(len(facade.get_entity_flags("order", "12")), 1)


class FakeTaskState:
    READY = "READY"


class FakeBpmnParser:
    loaded_path = None

    def add_bpmn_file(self, path: str) -> None:
        self.loaded_path = path

    def get_spec(self, process_name: str) -> dict[str, str]:
        return {"process_name": process_name, "path": self.loaded_path}


class FakeTaskSpec:
    def __init__(self, bpmn_id: str) -> None:
        self.bpmn_id = bpmn_id


class FakeTask:
    def __init__(self, bpmn_id: str) -> None:
        self.task_spec = FakeTaskSpec(bpmn_id)
        self.ran = False

    def run(self) -> None:
        self.ran = True


class FakeBpmnWorkflow:
    def __init__(self, spec: dict[str, str]) -> None:
        self.spec = spec
        self.data = {}
        self._ready_tasks = [FakeTask("create_review_flag")]
        self._engine_steps = 0

    def do_engine_steps(self) -> None:
        self._engine_steps += 1

    def get_tasks(self, state: str | None = None) -> list[FakeTask]:
        tasks = self._ready_tasks
        self._ready_tasks = []
        return tasks


if __name__ == "__main__":
    unittest.main()
