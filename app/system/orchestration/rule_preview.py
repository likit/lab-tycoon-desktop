"""Read-only preview service for editable workflow rules."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import logging
from pathlib import Path
from typing import Any

from .rules import DecisionResult, RuleDefinition, RuleEngine, _load_yaml_rule

logger = logging.getLogger("client")


@dataclass(frozen=True, slots=True)
class RulePreviewOutcome:
    """One side of a rule-preview comparison."""

    matched: bool
    reason: str
    metadata: dict[str, Any]
    would_create_review_flag: bool
    error: str | None = None

    @classmethod
    def from_decision(cls, decision: DecisionResult) -> "RulePreviewOutcome":
        """Build an outcome from a decision result without creating side effects."""

        return cls(
            matched=decision.matched,
            reason=decision.reason,
            metadata=decision.metadata,
            would_create_review_flag=decision.matched,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the preview outcome for tests or future UI display."""

        return asdict(self)


@dataclass(frozen=True, slots=True)
class RulePreviewResult:
    """Baseline-vs-candidate comparison for one rule and context."""

    rule_name: str
    context_summary: dict[str, Any]
    baseline_result: RulePreviewOutcome
    candidate_result: RulePreviewOutcome
    changed: bool
    change_summary: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize the preview result for tests or future UI display."""

        return asdict(self)


class RulePreviewService:
    """Compare current and candidate rule behavior without live side effects."""

    def __init__(self, baseline_rules_dir: Path | None = None) -> None:
        self.baseline_rules_dir = baseline_rules_dir

    def preview_rule_change(
        self,
        rule_name: str,
        context: dict[str, Any],
        candidate_rule_path: str | Path | None = None,
    ) -> RulePreviewResult:
        """Evaluate baseline and candidate rules against the same context."""

        baseline_result = RulePreviewOutcome.from_decision(
            RuleEngine(rules_dir=self.baseline_rules_dir).evaluate(rule_name, context)
        )
        candidate_result = self._evaluate_candidate(rule_name, context, candidate_rule_path)
        changed = _outcomes_changed(baseline_result, candidate_result)

        return RulePreviewResult(
            rule_name=rule_name,
            context_summary=_summarize_context(context),
            baseline_result=baseline_result,
            candidate_result=candidate_result,
            changed=changed,
            change_summary=_build_change_summary(baseline_result, candidate_result),
        )

    def _evaluate_candidate(
        self,
        rule_name: str,
        context: dict[str, Any],
        candidate_rule_path: str | Path | None,
    ) -> RulePreviewOutcome:
        """Evaluate the candidate rule, failing safely into an error outcome."""

        if candidate_rule_path is None:
            return RulePreviewOutcome.from_decision(
                RuleEngine(rules_dir=self.baseline_rules_dir).evaluate(rule_name, context)
            )

        try:
            candidate_definition = RuleDefinition.from_dict(
                _load_yaml_rule(Path(candidate_rule_path))
            )
            candidate_engine = RuleEngine(rules_dir=self.baseline_rules_dir)
            candidate_engine._rules = {rule_name: candidate_definition}
            return RulePreviewOutcome.from_decision(
                candidate_engine.evaluate(rule_name, context)
            )
        except (OSError, TypeError, KeyError, ValueError):
            logger.exception("Failed to preview candidate rule: %s", candidate_rule_path)
            return RulePreviewOutcome(
                matched=False,
                reason="Candidate rule unavailable.",
                metadata={"error": "invalid_candidate_rule"},
                would_create_review_flag=False,
                error="invalid_candidate_rule",
            )


def _summarize_context(context: dict[str, Any]) -> dict[str, Any]:
    """Return the compact context values useful for preview display."""

    payload = context.get("payload", {})
    return {
        "order_id": payload.get("order_id"),
        "patient_id": payload.get("patient_id") or payload.get("customer_id"),
        "test_code": payload.get("test_code"),
    }


def _outcomes_changed(
    baseline: RulePreviewOutcome,
    candidate: RulePreviewOutcome,
) -> bool:
    """Return whether the previewed behavior changes."""

    return (
        baseline.matched != candidate.matched
        or baseline.reason != candidate.reason
        or baseline.would_create_review_flag != candidate.would_create_review_flag
        or baseline.error != candidate.error
    )


def _build_change_summary(
    baseline: RulePreviewOutcome,
    candidate: RulePreviewOutcome,
) -> str:
    """Build a short human-readable preview summary."""

    if not _outcomes_changed(baseline, candidate):
        return "No rule behavior change for this context."

    return (
        "Rule behavior changed: "
        f"matched {baseline.matched} -> {candidate.matched}; "
        f"would_create_review_flag {baseline.would_create_review_flag} -> "
        f"{candidate.would_create_review_flag}."
    )
