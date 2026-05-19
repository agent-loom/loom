"""基于关键词和正则的语义路由器。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_platform.domain.models import AgentSpec
    from agent_platform.registry.registry import AgentRegistry

logger = logging.getLogger(__name__)


@dataclass
class SemanticMatch:
    """语义路由的匹配结果。"""

    agent_id: str
    confidence: float
    matched_keywords: list[str] = field(default_factory=list)
    reason: str = "semantic"


@dataclass
class SemanticRule:
    """语义路由规则，包含关键词和正则模式。"""

    agent_id: str
    keywords: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    description: str = ""


class SemanticRouter:
    """Keyword and pattern-based semantic routing for agent selection.

    Used as a fallback when explicit agent_id/app_id/retailer_id routing fails.
    Only activates when confidence >= threshold (default 0.85 per design doc §17).

    Rules can be loaded:
    - Programmatically via :meth:`add_rule`.
    - Automatically from Agent manifests via :meth:`load_from_registry` —
      each manifest's ``routing.rules`` section is translated into
      :class:`SemanticRule` objects and merged into the active rule set.
    """

    def __init__(self, confidence_threshold: float = 0.85) -> None:
        """初始化语义路由器。"""
        self.confidence_threshold = confidence_threshold
        self._rules: list[SemanticRule] = []
        # Track which agent IDs were loaded from registry to support incremental refresh
        self._registry_agent_ids: set[str] = set()

    # ------------------------------------------------------------------ #
    # Rule management
    # ------------------------------------------------------------------ #

    def add_rule(self, rule: SemanticRule) -> None:
        """添加一条语义路由规则。"""
        self._rules.append(rule)

    def remove_rules_for_agent(self, agent_id: str) -> int:
        """移除指定 Agent 的所有规则，返回被移除的条数。"""
        before = len(self._rules)
        self._rules = [r for r in self._rules if r.agent_id != agent_id]
        removed = before - len(self._rules)
        if removed:
            logger.debug("SemanticRouter: removed %d rule(s) for agent=%s", removed, agent_id)
        return removed

    # ------------------------------------------------------------------ #
    # Registry-driven bulk load
    # ------------------------------------------------------------------ #

    def load_from_manifest(self, agent_id: str, manifest: dict[str, Any]) -> int:
        """从单个 Manifest 字典加载路由规则。

        Manifest 示例格式::

            routing:
              rules:
                - keywords: ["退款", "订单取消"]
                  patterns: [".*退款.*"]
                  description: "退款相关查询"
                - keywords: ["库存", "缺货"]

        返回成功加载的规则条数。
        """
        routing_cfg: dict[str, Any] = manifest.get("routing", {}) or {}
        raw_rules: list[dict] = routing_cfg.get("rules", []) or []
        if not raw_rules:
            return 0

        # Drop existing rules for this agent before re-loading
        self.remove_rules_for_agent(agent_id)

        loaded = 0
        for raw in raw_rules:
            if not isinstance(raw, dict):
                continue
            keywords: list[str] = raw.get("keywords", []) or []
            patterns: list[str] = raw.get("patterns", []) or []
            description: str = raw.get("description", "")
            if not keywords and not patterns:
                continue
            self._rules.append(
                SemanticRule(
                    agent_id=agent_id,
                    keywords=keywords,
                    patterns=patterns,
                    description=description,
                )
            )
            loaded += 1

        if loaded:
            logger.info(
                "SemanticRouter: loaded %d rule(s) from manifest for agent=%s",
                loaded,
                agent_id,
            )
        return loaded

    async def load_from_registry(self, registry: "AgentRegistry") -> int:
        """从 AgentRegistry 批量加载所有已注册 Agent 的语义路由规则。

        遍历 registry 中每个 Agent 的 Manifest（通过 :meth:`AgentRegistry.list_agents`
        以及 ``AgentSpec.manifest.routing.routing_rules``），将规则增量合并到当前
        规则集（先清除该 Agent 旧规则再重加载）。

        该方法也可在部署事件后调用，以刷新新 Agent 的路由规则。

        返回总共加载的规则条数。
        """
        total = 0
        try:
            specs: list[AgentSpec] = await registry.list_agents()
        except Exception:
            logger.warning("SemanticRouter: 无法获取 Agent 列表", exc_info=True)
            return 0

        for spec in specs:
            agent_id = spec.agent_id
            try:
                count = self._load_from_spec(spec)
                total += count
                self._registry_agent_ids.add(agent_id)
            except Exception:
                logger.debug(
                    "SemanticRouter: 跳过 agent=%s（处理 manifest 失败）",
                    agent_id,
                    exc_info=True,
                )

        if total:
            logger.info(
                "SemanticRouter: 从 registry 共加载 %d 条路由规则（涉及 %d 个 Agent）",
                total,
                len(self._registry_agent_ids),
            )
        else:
            logger.debug("SemanticRouter: registry 中无任何 routing.rules 配置")
        return total

    def _load_from_spec(self, spec: "AgentSpec") -> int:
        """从 AgentSpec 的结构化 manifest 对象加载路由规则，返回加载条数。"""
        agent_id = spec.agent_id
        routing_rules = []
        try:
            routing_rules = spec.manifest.routing.routing_rules or []
        except AttributeError:
            pass

        if not routing_rules:
            return 0

        self.remove_rules_for_agent(agent_id)
        loaded = 0
        for rule_obj in routing_rules:
            try:
                keywords: list[str] = getattr(rule_obj, "keywords", []) or []
                patterns: list[str] = getattr(rule_obj, "patterns", []) or []
                description: str = getattr(rule_obj, "description", "") or ""
            except Exception:
                continue
            if not keywords and not patterns:
                continue
            self._rules.append(
                SemanticRule(
                    agent_id=agent_id,
                    keywords=keywords,
                    patterns=patterns,
                    description=description,
                )
            )
            loaded += 1
        if loaded:
            logger.info(
                "SemanticRouter: 从 AgentSpec 加载 %d 条规则 agent=%s",
                loaded,
                agent_id,
            )
        return loaded

    # ------------------------------------------------------------------ #
    # Matching
    # ------------------------------------------------------------------ #

    def match(self, query: str) -> SemanticMatch | None:
        """匹配查询文本，返回置信度最高且超过阈值的结果。"""
        best: SemanticMatch | None = None
        best_score = 0.0

        for rule in self._rules:
            matched_keywords = [kw for kw in rule.keywords if kw in query]
            keyword_score = len(matched_keywords) / max(len(rule.keywords), 1)

            pattern_score = 0.0
            for pattern in rule.patterns:
                if re.search(pattern, query):
                    pattern_score = 1.0
                    break

            score = max(keyword_score, pattern_score)
            if score > best_score:
                best_score = score
                best = SemanticMatch(
                    agent_id=rule.agent_id,
                    confidence=score,
                    matched_keywords=matched_keywords,
                    reason=f"semantic:{rule.description}" if rule.description else "semantic",
                )

        if best and best.confidence >= self.confidence_threshold:
            return best
        return None

    # ------------------------------------------------------------------ #
    # Utilities
    # ------------------------------------------------------------------ #

    def clear(self) -> None:
        """清空所有路由规则（包括 registry 来源的规则）。"""
        self._rules.clear()
        self._registry_agent_ids.clear()

    @property
    def rule_count(self) -> int:
        """当前已注册的规则总数。"""
        return len(self._rules)
