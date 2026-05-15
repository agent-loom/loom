"""Tests for SemanticRouter – keyword/pattern matching, scoring, threshold."""

from __future__ import annotations

from agent_platform.router_semantic import SemanticRouter, SemanticRule

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rule(
    agent_id: str = "agent_a",
    keywords: list[str] | None = None,
    patterns: list[str] | None = None,
    description: str = "",
) -> SemanticRule:
    return SemanticRule(
        agent_id=agent_id,
        keywords=keywords or [],
        patterns=patterns or [],
        description=description,
    )


# ---------------------------------------------------------------------------
# add_rule / basic wiring
# ---------------------------------------------------------------------------

class TestAddRule:
    def test_add_single_rule(self) -> None:
        router = SemanticRouter()
        router.add_rule(_rule())
        assert len(router._rules) == 1

    def test_add_multiple_rules(self) -> None:
        router = SemanticRouter()
        router.add_rule(_rule("a"))
        router.add_rule(_rule("b"))
        assert len(router._rules) == 2


# ---------------------------------------------------------------------------
# Keyword matching
# ---------------------------------------------------------------------------

class TestKeywordMatching:
    def test_all_keywords_match(self) -> None:
        router = SemanticRouter(confidence_threshold=0.85)
        router.add_rule(_rule(keywords=["order", "status", "check"]))

        result = router.match("check order status")
        assert result is not None
        assert result.agent_id == "agent_a"
        assert result.confidence == 1.0

    def test_partial_match_above_threshold(self) -> None:
        router = SemanticRouter(confidence_threshold=0.5)
        router.add_rule(_rule(keywords=["a", "b"]))

        result = router.match("a")
        assert result is not None
        assert result.confidence == 0.5

    def test_partial_match_below_threshold(self) -> None:
        router = SemanticRouter(confidence_threshold=0.85)
        router.add_rule(_rule(keywords=["a", "b", "c"]))

        # 1/3 ≈ 0.33 < 0.85
        result = router.match("a")
        assert result is None

    def test_no_keywords_match(self) -> None:
        router = SemanticRouter()
        router.add_rule(_rule(keywords=["x", "y"]))
        assert router.match("completely unrelated query") is None


# ---------------------------------------------------------------------------
# Pattern matching
# ---------------------------------------------------------------------------

class TestPatternMatching:
    def test_regex_pattern_match(self) -> None:
        router = SemanticRouter(confidence_threshold=0.85)
        router.add_rule(_rule(patterns=[r"order\s+#\d+"]))

        result = router.match("What about order #12345?")
        assert result is not None
        assert result.confidence == 1.0

    def test_pattern_no_match(self) -> None:
        router = SemanticRouter(confidence_threshold=0.85)
        router.add_rule(_rule(patterns=[r"order\s+#\d+"]))

        result = router.match("hello world")
        assert result is None

    def test_multiple_patterns_first_match_wins(self) -> None:
        router = SemanticRouter(confidence_threshold=0.85)
        router.add_rule(_rule(patterns=[r"^nope$", r"hello"]))

        result = router.match("hello there")
        assert result is not None
        assert result.confidence == 1.0


# ---------------------------------------------------------------------------
# Confidence threshold
# ---------------------------------------------------------------------------

class TestConfidenceThreshold:
    def test_default_threshold_is_085(self) -> None:
        router = SemanticRouter()
        assert router.confidence_threshold == 0.85

    def test_custom_threshold(self) -> None:
        router = SemanticRouter(confidence_threshold=0.5)
        assert router.confidence_threshold == 0.5

    def test_exact_threshold_passes(self) -> None:
        router = SemanticRouter(confidence_threshold=0.5)
        router.add_rule(_rule(keywords=["a", "b"]))
        result = router.match("a")
        assert result is not None
        assert result.confidence == 0.5

    def test_just_below_threshold_returns_none(self) -> None:
        # 1/3 < 0.34
        router = SemanticRouter(confidence_threshold=0.34)
        router.add_rule(_rule(keywords=["a", "b", "c"]))
        result = router.match("a")
        assert result is None


# ---------------------------------------------------------------------------
# No match returns None
# ---------------------------------------------------------------------------

class TestNoMatch:
    def test_no_rules(self) -> None:
        router = SemanticRouter()
        assert router.match("anything") is None

    def test_all_below_threshold(self) -> None:
        router = SemanticRouter(confidence_threshold=0.99)
        router.add_rule(_rule(keywords=["a", "b", "c", "d"]))
        # 1/4 = 0.25 < 0.99
        assert router.match("a") is None


# ---------------------------------------------------------------------------
# Multiple rules with scoring – best match
# ---------------------------------------------------------------------------

class TestMultipleRules:
    def test_best_keyword_match_wins(self) -> None:
        router = SemanticRouter(confidence_threshold=0.5)
        router.add_rule(_rule(agent_id="low", keywords=["alpha", "beta", "gamma", "delta"]))
        router.add_rule(_rule(agent_id="high", keywords=["alpha", "beta"]))

        # "alpha beta" -> low = 2/4 = 0.5, high = 2/2 = 1.0
        result = router.match("alpha beta")
        assert result is not None
        assert result.agent_id == "high"
        assert result.confidence == 1.0

    def test_pattern_beats_partial_keyword(self) -> None:
        router = SemanticRouter(confidence_threshold=0.5)
        router.add_rule(_rule(agent_id="kw", keywords=["order", "status", "info", "help"]))
        router.add_rule(_rule(agent_id="pat", patterns=[r"order\s+#\d+"]))

        # keywords: 1/4 = 0.25 < threshold -> kw won't match
        # pattern: 1.0 >= threshold -> pat wins
        result = router.match("order #999")
        assert result is not None
        assert result.agent_id == "pat"

    def test_keyword_and_pattern_on_same_rule_takes_max(self) -> None:
        router = SemanticRouter(confidence_threshold=0.85)
        router.add_rule(
            _rule(
                agent_id="combo",
                keywords=["a", "b", "c", "d", "e"],  # 1/5 = 0.2
                patterns=[r"hello"],  # 1.0
            )
        )
        result = router.match("a hello")
        assert result is not None
        assert result.confidence == 1.0


# ---------------------------------------------------------------------------
# SemanticMatch fields
# ---------------------------------------------------------------------------

class TestSemanticMatch:
    def test_reason_with_description(self) -> None:
        router = SemanticRouter(confidence_threshold=0.85)
        router.add_rule(_rule(keywords=["x"], patterns=[r"x"], description="test rule"))
        result = router.match("x")
        assert result is not None
        assert result.reason == "semantic:test rule"

    def test_reason_without_description(self) -> None:
        router = SemanticRouter(confidence_threshold=0.85)
        router.add_rule(_rule(keywords=["x"], patterns=[r"x"]))
        result = router.match("x")
        assert result is not None
        assert result.reason == "semantic"

    def test_matched_keywords_populated(self) -> None:
        router = SemanticRouter(confidence_threshold=0.5)
        router.add_rule(_rule(keywords=["alpha", "beta", "gamma"]))
        result = router.match("alpha gamma")
        assert result is not None
        assert set(result.matched_keywords) == {"alpha", "gamma"}


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------

class TestClear:
    def test_clear_removes_all_rules(self) -> None:
        router = SemanticRouter()
        router.add_rule(_rule())
        router.add_rule(_rule())
        router.clear()
        assert router._rules == []
        assert router.match("anything") is None
