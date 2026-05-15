from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from agent_platform.domain.models import AgentSpec

logger = logging.getLogger(__name__)


class PolicyViolation(BaseModel):
    policy_type: str
    rule_id: str
    message: str
    severity: str = "error"


class SafetyRule(BaseModel):
    id: str
    type: str
    description: str = ""
    pattern: str | None = None
    tools: list[str] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)


class RoutingRule(BaseModel):
    intent: str
    keywords: list[str] = Field(default_factory=list)
    worker: str = ""
    tools: list[str] = Field(default_factory=list)
    confidence_threshold: float = 0.5


class PolicySet(BaseModel):
    safety_rules: list[SafetyRule] = Field(default_factory=list)
    routing_rules: list[RoutingRule] = Field(default_factory=list)
    output_config: dict[str, Any] = Field(default_factory=dict)


class PolicyEngine:
    """Loads and enforces agent policies (safety, routing, output) at runtime."""

    def __init__(self) -> None:
        self._cache: dict[str, PolicySet] = {}

    def load_policies(self, spec: AgentSpec) -> PolicySet:
        cache_key = f"{spec.agent_id}@{spec.version}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        policy_set = PolicySet()
        base = spec.package_path

        safety_path = spec.manifest.safety.policy
        if safety_path:
            policy_set.safety_rules = self._load_safety(base / safety_path)

        routing_path = spec.manifest.routing.rules
        if routing_path:
            policy_set.routing_rules = self._load_routing(base / routing_path)

        output_config = self._load_output_config(spec)
        policy_set.output_config = output_config

        self._cache[cache_key] = policy_set
        return policy_set

    def check_input(self, text: str, policy_set: PolicySet) -> list[PolicyViolation]:
        violations: list[PolicyViolation] = []
        for rule in policy_set.safety_rules:
            if rule.type == "pii_guard" and rule.pattern:
                if re.search(rule.pattern, text):
                    violations.append(PolicyViolation(
                        policy_type="safety",
                        rule_id=rule.id,
                        message=f"Input contains PII matching pattern: {rule.description}",
                    ))
            if rule.type == "deny_pattern" and rule.pattern:
                if re.search(rule.pattern, text):
                    violations.append(PolicyViolation(
                        policy_type="safety",
                        rule_id=rule.id,
                        message=f"Input matches denied pattern: {rule.description}",
                    ))
        return violations

    def check_output(self, text: str, policy_set: PolicySet) -> list[PolicyViolation]:
        violations: list[PolicyViolation] = []
        for rule in policy_set.safety_rules:
            if rule.type == "deny_output_pattern" and rule.pattern:
                if re.search(rule.pattern, text):
                    violations.append(PolicyViolation(
                        policy_type="safety",
                        rule_id=rule.id,
                        message=f"Output contains denied content: {rule.description}",
                    ))
            if rule.type == "pii_guard" and rule.pattern:
                if re.search(rule.pattern, text):
                    violations.append(PolicyViolation(
                        policy_type="safety",
                        rule_id=rule.id,
                        message=f"Output contains PII: {rule.description}",
                        severity="warning",
                    ))
        return violations

    def check_commands(
        self, commands: list[dict[str, Any]], allowlist: list[str],
    ) -> list[PolicyViolation]:
        if not allowlist:
            return []
        violations: list[PolicyViolation] = []
        for cmd in commands:
            name = cmd.get("name", "")
            if name not in allowlist:
                violations.append(PolicyViolation(
                    policy_type="output",
                    rule_id="command_allowlist",
                    message=f"Command '{name}' not in allowlist",
                ))
        return violations

    def check_tool_allowed(self, tool_name: str, policy_set: PolicySet) -> list[PolicyViolation]:
        violations: list[PolicyViolation] = []
        for rule in policy_set.safety_rules:
            if rule.type == "deny_tools" and tool_name in rule.tools:
                violations.append(PolicyViolation(
                    policy_type="safety",
                    rule_id=rule.id,
                    message=f"Tool '{tool_name}' is denied by safety policy: {rule.description}",
                ))
        return violations

    def route_intent(self, query: str, policy_set: PolicySet) -> RoutingRule | None:
        best: RoutingRule | None = None
        best_score = 0.0
        for rule in policy_set.routing_rules:
            score = sum(1 for kw in rule.keywords if kw in query)
            if rule.keywords:
                score = score / len(rule.keywords)
            if score >= rule.confidence_threshold and score > best_score:
                best = rule
                best_score = score
        return best

    def _load_safety(self, path: Path) -> list[SafetyRule]:
        if not path.exists():
            logger.warning("safety policy not found: %s", path)
            return []
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return []
            rules = data.get("rules", [])
            return [SafetyRule(**r) for r in rules if isinstance(r, dict)]
        except Exception:
            logger.exception("failed to load safety policy: %s", path)
            return []

    def _load_routing(self, path: Path) -> list[RoutingRule]:
        if not path.exists():
            logger.warning("routing policy not found: %s", path)
            return []
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return []
            rules = data.get("rules", [])
            return [RoutingRule(**r) for r in rules if isinstance(r, dict)]
        except Exception:
            logger.exception("failed to load routing policy: %s", path)
            return []

    def _load_output_config(self, spec: AgentSpec) -> dict[str, Any]:
        return {
            "protocol": spec.manifest.output.protocol,
            "supports": spec.manifest.output.supports,
            "command_allowlist": spec.manifest.output.command_allowlist,
        }
