from pathlib import Path

from agent_platform.registry.loader import ManifestLoader


def test_myj_manifest_has_entry_field():
    spec = ManifestLoader().load_file(Path("agents/myj/manifest.yaml"))
    assert spec.manifest.entry.mode == "orchestrator_workers"
    assert spec.manifest.entry.default_worker == "direct_reply"


def test_myj_manifest_all_tools_registered():
    spec = ManifestLoader().load_file(Path("agents/myj/manifest.yaml"))
    allowed = spec.manifest.tools.allow
    assert "myj.goods_search" in allowed
    assert "myj.goods_location" in allowed
    assert "myj.promotion_lookup" in allowed
    assert "myj.store_consult" in allowed


def test_myj_routing_strategy():
    spec = ManifestLoader().load_file(Path("agents/myj/manifest.yaml"))
    assert spec.manifest.routing.strategy == "hybrid"
    assert spec.manifest.routing.fallback_worker == "direct_reply"
    assert "转人工" in spec.manifest.routing.human_handoff_intents


def test_myj_safety_moderation():
    spec = ManifestLoader().load_file(Path("agents/myj/manifest.yaml"))
    assert spec.manifest.safety.moderation["input"] is True
    assert spec.manifest.safety.moderation["output"] is True
