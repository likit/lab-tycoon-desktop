"""Small decision/rule layer for workflow orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import logging
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None

logger = logging.getLogger("client")


@dataclass(frozen=True, slots=True)
class DecisionResult:
    """Structured result returned by a named workflow decision."""

    decision_name: str
    matched: bool
    reason: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Serialize the decision result for workflow context and tests."""

        return asdict(self)


@dataclass(frozen=True, slots=True)
class RuleCondition:
    """One simple condition row loaded from an editable rule artifact."""

    field: str
    operator: str
    expected: object = None


@dataclass(frozen=True, slots=True)
class RuleConditionGroup:
    """A nested boolean condition group loaded from a rule artifact."""

    operator: str
    children: list["RuleCondition | RuleConditionGroup"]


@dataclass(frozen=True, slots=True)
class RuleDefinition:
    """A minimal rule definition loaded from a small YAML file."""

    decision_name: str
    description: str
    conditions: RuleConditionGroup
    true_reason: str
    false_reason: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuleDefinition":
        """Build a rule definition from decoded YAML data."""

        conditions = data.get("conditions", {})
        outcomes = data.get("outcomes", {})
        matched_outcome = outcomes.get("matched", {})
        unmatched_outcome = outcomes.get("unmatched", {})

        return cls(
            decision_name=str(data["decision_name"]),
            description=str(data.get("description", "")),
            conditions=_condition_group_from_conditions(conditions),
            true_reason=str(matched_outcome.get("reason", "Decision matched.")),
            false_reason=str(unmatched_outcome.get("reason", "Decision did not match.")),
        )


class RuleEngine:
    """Evaluate named decisions from compact workflow context.

    This intentionally tiny layer separates decision logic from workflow
    routing. It can later be replaced or backed by DMN/decision tables without
    changing the facade, bridge, or action-adapter boundaries.
    """

    def __init__(self, rules_dir: Path | None = None) -> None:
        self.rules_dir = rules_dir or Path(__file__).parent / "workflows" / "rules"
        self._rules = self._load_rules()

    def evaluate(self, decision_name: str, context: dict[str, Any]) -> DecisionResult:
        """Evaluate a named decision against compact context."""

        rule = self._rules.get(decision_name)
        if rule is None:
            return DecisionResult(
                decision_name=decision_name,
                matched=False,
                reason="Decision rule unavailable.",
                metadata={"matched_fields": [], "error": "missing_rule"},
            )

        matched, matched_fields = _evaluate_expression(rule.conditions, context)

        return DecisionResult(
            decision_name=decision_name,
            matched=matched,
            reason=rule.true_reason if matched else rule.false_reason,
            metadata={
                "matched_fields": matched_fields,
                "description": rule.description,
            },
        )

    def _load_rules(self) -> dict[str, RuleDefinition]:
        """Load all YAML rule definitions from the rules directory."""

        rules = {}
        for path in sorted([*self.rules_dir.glob("*.yaml"), *self.rules_dir.glob("*.yml")]):
            try:
                data = _load_yaml_rule(path)
                rule = RuleDefinition.from_dict(data)
            except (OSError, TypeError, KeyError, ValueError):
                logger.exception("Failed to load workflow rule artifact: %s", path)
                continue
            rules[rule.decision_name] = rule
        return rules


def _condition_group_from_conditions(data: dict[str, Any]) -> RuleConditionGroup:
    """Convert top-level YAML conditions into a root boolean group.

    Top-level ``match_all`` and ``match_any`` keep their previous meaning:
    all top-level groups must pass. Inside those groups, conditions can now be
    nested recursively.
    """

    children: list[RuleCondition | RuleConditionGroup] = []
    if "match_all" in data:
        children.append(_condition_group_from_list("match_all", data["match_all"]))
    if "match_any" in data:
        children.append(_condition_group_from_list("match_any", data["match_any"]))
    return RuleConditionGroup(operator="match_all", children=children)


def _condition_group_from_list(
    operator: str,
    items: list[dict[str, Any]],
) -> RuleConditionGroup:
    """Convert a list of condition or group dictionaries into a condition group."""

    if operator not in {"match_all", "match_any"}:
        raise KeyError("condition group must be match_all or match_any")
    return RuleConditionGroup(
        operator=operator,
        children=[_expression_from_dict(item) for item in items],
    )


def _expression_from_dict(data: dict[str, Any]) -> RuleCondition | RuleConditionGroup:
    """Convert one YAML condition expression into a condition or nested group."""

    if "field" in data:
        return _condition_from_dict(data)
    if "match_all" in data:
        return _condition_group_from_list("match_all", data["match_all"])
    if "match_any" in data:
        return _condition_group_from_list("match_any", data["match_any"])
    raise KeyError("condition expression must define field, match_all, or match_any")


def _condition_from_dict(data: dict[str, Any]) -> RuleCondition:
    """Convert a YAML condition row into a small condition object."""

    field = str(data["field"])
    if "equals" in data:
        return RuleCondition(field=field, operator="equals", expected=data["equals"])
    if "not_equals" in data:
        return RuleCondition(field=field, operator="not_equals", expected=data["not_equals"])
    if "exists" in data:
        return RuleCondition(field=field, operator="exists", expected=bool(data["exists"]))
    if "in" in data:
        return RuleCondition(field=field, operator="in", expected=list(data["in"]))
    raise KeyError("condition must define one of: equals, not_equals, exists, in")


def _evaluate_expression(
    expression: RuleCondition | RuleConditionGroup,
    context: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Evaluate one condition or boolean group and return matched leaf fields."""

    if isinstance(expression, RuleCondition):
        matched = _evaluate_condition(expression, context)
        return matched, [expression.field] if matched else []

    child_results = [
        _evaluate_expression(child, context)
        for child in expression.children
    ]
    if expression.operator == "match_all":
        matched = all(result for result, _fields in child_results) if child_results else True
    elif expression.operator == "match_any":
        matched = any(result for result, _fields in child_results) if child_results else True
    else:
        matched = False

    matched_fields: list[str] = []
    for child_matched, child_fields in child_results:
        if child_matched:
            matched_fields.extend(child_fields)
    return matched, matched_fields


def _evaluate_condition(condition: RuleCondition, context: dict[str, Any]) -> bool:
    """Evaluate one supported condition operator."""

    actual = _lookup_context_value(context, condition.field)
    if condition.operator == "equals":
        return actual == condition.expected
    if condition.operator == "not_equals":
        return actual != condition.expected
    if condition.operator == "exists":
        return (actual is not None) == condition.expected
    if condition.operator == "in":
        return actual in condition.expected
    return False


def _lookup_context_value(context: dict[str, Any], field_path: str) -> object:
    """Read a dotted-path value from nested dictionaries."""

    value: object = context
    for part in field_path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _load_yaml_rule(path: Path) -> dict[str, Any]:
    """Load a YAML rule file, using a tiny fallback parser if PyYAML is absent."""

    if yaml is not None:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    return _load_simple_rule_yaml(path.read_text(encoding="utf-8"))


def _load_simple_rule_yaml(text: str) -> dict[str, Any]:
    """Parse the small rule-artifact YAML subset used by LabTycoon.

    This is not a general YAML parser. It supports the mappings, sequences, and
    scalar values used by editable rule artifacts, including nested
    ``match_all`` / ``match_any`` condition groups.
    """

    lines = _prepare_simple_yaml_lines(text)
    data, index = _parse_simple_mapping(lines, 0, 0)
    if index != len(lines):
        raise ValueError("Invalid YAML rule structure.")
    return data


def _prepare_simple_yaml_lines(text: str) -> list[tuple[int, str]]:
    """Return significant YAML lines as indentation/content tuples."""

    prepared = []
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        prepared.append((indent, raw_line.strip()))
    return prepared


def _parse_simple_mapping(
    lines: list[tuple[int, str]],
    index: int,
    indent: int,
) -> tuple[dict[str, Any], int]:
    """Parse a simple YAML mapping block."""

    data: dict[str, Any] = {}
    while index < len(lines):
        line_indent, line = lines[index]
        if line_indent < indent:
            break
        if line_indent != indent or line.startswith("- "):
            raise ValueError("Invalid YAML mapping indentation.")

        key, value = _split_yaml_pair(line)
        index += 1
        if value:
            data[key] = _parse_scalar(value)
            continue
        if index >= len(lines) or lines[index][0] <= line_indent:
            data[key] = {}
            continue

        child_indent, child_line = lines[index]
        if child_line.startswith("- "):
            data[key], index = _parse_simple_sequence(lines, index, child_indent)
        else:
            data[key], index = _parse_simple_mapping(lines, index, child_indent)
    return data, index


def _parse_simple_sequence(
    lines: list[tuple[int, str]],
    index: int,
    indent: int,
) -> tuple[list[Any], int]:
    """Parse a simple YAML sequence block."""

    items = []
    while index < len(lines):
        line_indent, line = lines[index]
        if line_indent < indent:
            break
        if line_indent != indent:
            raise ValueError("Invalid YAML sequence indentation.")
        if not line.startswith("- "):
            break

        item_text = line[2:].strip()
        index += 1
        if not item_text:
            if index >= len(lines) or lines[index][0] <= line_indent:
                items.append({})
                continue
            item, index = _parse_simple_mapping(lines, index, lines[index][0])
            items.append(item)
            continue

        key, value = _split_yaml_pair(item_text)
        item = {key: _parse_scalar(value)} if value else {}
        if not value:
            if index >= len(lines) or lines[index][0] <= line_indent:
                item[key] = {}
            elif lines[index][1].startswith("- "):
                item[key], index = _parse_simple_sequence(lines, index, lines[index][0])
            else:
                item[key], index = _parse_simple_mapping(lines, index, lines[index][0])

        while index < len(lines) and lines[index][0] > line_indent:
            extra, index = _parse_simple_mapping(lines, index, lines[index][0])
            item.update(extra)
        items.append(item)
    return items, index


def _split_yaml_pair(line: str) -> tuple[str, str]:
    """Split one simple YAML key/value line."""

    if ":" not in line:
        raise ValueError("Invalid YAML rule line.")
    key, value = line.split(":", 1)
    return key.strip(), value.strip()


def _parse_scalar(value: str) -> object:
    """Parse the scalar values used by rule artifacts."""

    if (
        len(value) >= 2
        and value[0] == value[-1]
        and value[0] in {"'", '"'}
    ):
        return value[1:-1]
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.lower() in {"null", "none"}:
        return None
    return value
