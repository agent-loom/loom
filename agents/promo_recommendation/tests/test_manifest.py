
from agent_platform.registry.loader import ManifestLoader


def test_promo_manifest_loads():
    from pathlib import Path
    spec = ManifestLoader().load_file(Path("agents/promo_recommendation/manifest.yaml"))
    assert spec.agent_id == "promo_recommendation"
    assert "promo.promotion_search" in spec.manifest.tools.allow
    assert spec.manifest.entry.mode == "orchestrator_workers"
