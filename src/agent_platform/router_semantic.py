"""基于关键词和正则的语义路由器。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

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
    """

    def __init__(self, confidence_threshold: float = 0.85) -> None:
        """初始化语义路由器。"""
        self.confidence_threshold = confidence_threshold
        self._rules: list[SemanticRule] = []

    def add_rule(self, rule: SemanticRule) -> None:
        """添加一条语义路由规则。"""
        self._rules.append(rule)

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

    def clear(self) -> None:
        """清空所有路由规则。"""
        self._rules.clear()
