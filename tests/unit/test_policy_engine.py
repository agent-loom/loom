"""Tests for PolicyEngine – safety rules, routing, and output config."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agent_platform.policy.engine import (
    PolicyEngine,
    PolicySet,
    RoutingRule,
    SafetyRule,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _policy_set(
    safety_rules: list[SafetyRule] | None = None,
    routing_rules: list[RoutingRule] | None = None,
) -> PolicySet:
    return PolicySet(
        safety_rules=safety_rules or [],
        routing_rules=routing_rules or [],
    )


def _pii_rule(pattern: str = r"\b\d{3}-\d{2}-\d{4}\b", description: str = "SSN") -> SafetyRule:
    return SafetyRule(id="pii_ssn", type="pii_guard", pattern=pattern, description=description)


def _deny_pattern_rule(
    pattern: str = r"(?i)drop\s+table",
    description: str = "SQL injection",
) -> SafetyRule:
    return SafetyRule(
        id="deny_sql", type="deny_pattern",
        pattern=pattern, description=description,
    )


def _deny_output_rule(
    pattern: str = r"(?i)password\s*[:=]",
    description: str = "password leak",
) -> SafetyRule:
    return SafetyRule(
        id="deny_out_pw", type="deny_output_pattern",
        pattern=pattern, description=description,
    )


def _deny_tools_rule(
    tools: list[str] | None = None,
    description: str = "banned tools",
) -> SafetyRule:
    return SafetyRule(
        id="deny_tools_1", type="deny_tools",
        tools=tools or ["exec_sql"], description=description,
    )


# ---------------------------------------------------------------------------
# check_input – PII patterns
# ---------------------------------------------------------------------------

class TestCheckInputPII:
    def test_detects_ssn(self) -> None:
        engine = PolicyEngine()
        ps = _policy_set(safety_rules=[_pii_rule()])
        violations = engine.check_input("My SSN is 123-45-6789", ps)

        assert len(violations) == 1
        assert violations[0].policy_type == "safety"
        assert violations[0].rule_id == "pii_ssn"
        assert "PII" in violations[0].message

    def test_no_pii_returns_empty(self) -> None:
        engine = PolicyEngine()
        ps = _policy_set(safety_rules=[_pii_rule()])
        assert engine.check_input("hello world", ps) == []

    def test_multiple_pii_patterns(self) -> None:
        email_rule = SafetyRule(
            id="pii_email",
            type="pii_guard",
            pattern=r"[\w.+-]+@[\w-]+\.[\w.]+",
            description="email",
        )
        engine = PolicyEngine()
        ps = _policy_set(safety_rules=[_pii_rule(), email_rule])
        violations = engine.check_input("SSN 123-45-6789 email a@b.com", ps)
        assert len(violations) == 2
        ids = {v.rule_id for v in violations}
        assert ids == {"pii_ssn", "pii_email"}


# ---------------------------------------------------------------------------
# check_input – deny patterns
# ---------------------------------------------------------------------------

class TestCheckInputDeny:
    def test_deny_pattern_triggers(self) -> None:
        engine = PolicyEngine()
        ps = _policy_set(safety_rules=[_deny_pattern_rule()])
        violations = engine.check_input("please DROP TABLE users;", ps)

        assert len(violations) == 1
        assert violations[0].rule_id == "deny_sql"
        assert "denied pattern" in violations[0].message

    def test_deny_pattern_no_match(self) -> None:
        engine = PolicyEngine()
        ps = _policy_set(safety_rules=[_deny_pattern_rule()])
        assert engine.check_input("SELECT * FROM users", ps) == []

    def test_both_pii_and_deny_at_once(self) -> None:
        engine = PolicyEngine()
        ps = _policy_set(safety_rules=[_pii_rule(), _deny_pattern_rule()])
        violations = engine.check_input("SSN 123-45-6789 DROP TABLE x", ps)
        assert len(violations) == 2


# ---------------------------------------------------------------------------
# check_output
# ---------------------------------------------------------------------------

class TestCheckOutput:
    def test_deny_output_pattern_triggers(self) -> None:
        engine = PolicyEngine()
        ps = _policy_set(safety_rules=[_deny_output_rule()])
        violations = engine.check_output("password: hunter2", ps)

        assert len(violations) == 1
        assert violations[0].rule_id == "deny_out_pw"
        assert "denied content" in violations[0].message

    def test_pii_in_output_has_warning_severity(self) -> None:
        engine = PolicyEngine()
        ps = _policy_set(safety_rules=[_pii_rule()])
        violations = engine.check_output("SSN is 123-45-6789", ps)

        assert len(violations) == 1
        assert violations[0].severity == "warning"
        assert "PII" in violations[0].message

    def test_clean_output_no_violations(self) -> None:
        engine = PolicyEngine()
        ps = _policy_set(safety_rules=[_deny_output_rule(), _pii_rule()])
        assert engine.check_output("Everything looks fine!", ps) == []


# ---------------------------------------------------------------------------
# check_commands
# ---------------------------------------------------------------------------

class TestCheckCommands:
    def test_allowed_command_passes(self) -> None:
        engine = PolicyEngine()
        cmds: list[dict[str, Any]] = [{"name": "navigate"}, {"name": "search"}]
        violations = engine.check_commands(cmds, ["navigate", "search"])
        assert violations == []

    def test_disallowed_command_rejected(self) -> None:
        engine = PolicyEngine()
        cmds: list[dict[str, Any]] = [{"name": "navigate"}, {"name": "delete_all"}]
        violations = engine.check_commands(cmds, ["navigate"])
        assert len(violations) == 1
        assert "delete_all" in violations[0].message

    def test_empty_allowlist_permits_all(self) -> None:
        engine = PolicyEngine()
        cmds: list[dict[str, Any]] = [{"name": "anything"}]
        assert engine.check_commands(cmds, []) == []

    def test_empty_commands_no_violations(self) -> None:
        engine = PolicyEngine()
        assert engine.check_commands([], ["navigate"]) == []


# ---------------------------------------------------------------------------
# check_tool_allowed
# ---------------------------------------------------------------------------

class TestCheckToolAllowed:
    def test_denied_tool(self) -> None:
        engine = PolicyEngine()
        ps = _policy_set(safety_rules=[_deny_tools_rule()])
        violations = engine.check_tool_allowed("exec_sql", ps)
        assert len(violations) == 1
        assert "denied" in violations[0].message

    def test_allowed_tool(self) -> None:
        engine = PolicyEngine()
        ps = _policy_set(safety_rules=[_deny_tools_rule()])
        assert engine.check_tool_allowed("search", ps) == []

    def test_no_deny_rules(self) -> None:
        engine = PolicyEngine()
        ps = _policy_set()
        assert engine.check_tool_allowed("any_tool", ps) == []


# ---------------------------------------------------------------------------
# route_intent
# ---------------------------------------------------------------------------

class TestRouteIntent:
    def _routing_rule(
        self,
        intent: str = "faq",
        keywords: list[str] | None = None,
        threshold: float = 0.5,
    ) -> RoutingRule:
        return RoutingRule(
            intent=intent,
            keywords=keywords or ["help", "faq", "question"],
            worker="faq_worker",
            confidence_threshold=threshold,
        )

    def test_full_keyword_match(self) -> None:
        engine = PolicyEngine()
        rule = self._routing_rule()
        ps = _policy_set(routing_rules=[rule])
        result = engine.route_intent("help faq question", ps)
        assert result is not None
        assert result.intent == "faq"

    def test_partial_match_above_threshold(self) -> None:
        engine = PolicyEngine()
        rule = self._routing_rule(threshold=0.3)
        ps = _policy_set(routing_rules=[rule])
        result = engine.route_intent("I have a question", ps)
        assert result is not None

    def test_no_match_below_threshold(self) -> None:
        engine = PolicyEngine()
        rule = self._routing_rule(threshold=0.9)
        ps = _policy_set(routing_rules=[rule])
        result = engine.route_intent("I have a question", ps)
        assert result is None

    def test_best_match_wins(self) -> None:
        engine = PolicyEngine()
        rule_a = RoutingRule(
            intent="orders", keywords=["order", "status"],
            confidence_threshold=0.5,
        )
        rule_b = RoutingRule(
            intent="returns", keywords=["return", "refund"],
            confidence_threshold=0.5,
        )
        ps = _policy_set(routing_rules=[rule_a, rule_b])

        result = engine.route_intent("return refund", ps)
        assert result is not None
        assert result.intent == "returns"

    def test_no_rules_returns_none(self) -> None:
        engine = PolicyEngine()
        ps = _policy_set()
        assert engine.route_intent("anything", ps) is None


# ---------------------------------------------------------------------------
# Loading policies from YAML files (via _load_safety / _load_routing)
# ---------------------------------------------------------------------------

class TestLoadYAML:
    def test_load_safety_from_yaml(self, tmp_path: Path) -> None:
        safety_yaml = tmp_path / "safety.yaml"
        safety_yaml.write_text(
            yaml.dump({
                "rules": [
                    {"id": "r1", "type": "pii_guard",
                     "pattern": r"\bSSN\b", "description": "SSN mention"},
                ]
            }),
            encoding="utf-8",
        )
        engine = PolicyEngine()
        rules = engine._load_safety(safety_yaml)
        assert len(rules) == 1
        assert rules[0].id == "r1"

    def test_load_routing_from_yaml(self, tmp_path: Path) -> None:
        routing_yaml = tmp_path / "routing.yaml"
        routing_yaml.write_text(
            yaml.dump({
                "rules": [
                    {"intent": "faq", "keywords": ["help"], "worker": "faq_w"},
                ]
            }),
            encoding="utf-8",
        )
        engine = PolicyEngine()
        rules = engine._load_routing(routing_yaml)
        assert len(rules) == 1
        assert rules[0].intent == "faq"

    def test_missing_safety_file_returns_empty(self, tmp_path: Path) -> None:
        engine = PolicyEngine()
        rules = engine._load_safety(tmp_path / "nonexistent.yaml")
        assert rules == []

    def test_missing_routing_file_returns_empty(self, tmp_path: Path) -> None:
        engine = PolicyEngine()
        rules = engine._load_routing(tmp_path / "nonexistent.yaml")
        assert rules == []

    def test_malformed_yaml_returns_empty(self, tmp_path: Path) -> None:
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("not: [valid: yaml: content", encoding="utf-8")
        engine = PolicyEngine()
        assert engine._load_safety(bad_yaml) == []

    def test_yaml_without_rules_key_returns_empty(self, tmp_path: Path) -> None:
        no_rules = tmp_path / "empty.yaml"
        no_rules.write_text(yaml.dump({"something_else": 1}), encoding="utf-8")
        engine = PolicyEngine()
        assert engine._load_safety(no_rules) == []

    def test_yaml_with_non_dict_root_returns_empty(self, tmp_path: Path) -> None:
        list_yaml = tmp_path / "list.yaml"
        list_yaml.write_text(yaml.dump([1, 2, 3]), encoding="utf-8")
        engine = PolicyEngine()
        assert engine._load_safety(list_yaml) == []
