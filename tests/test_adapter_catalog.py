from agent_runtime.adapter_catalog import catalog, configure
from agent_runtime.project_profile import load_profile


def test_catalog_contains_domain_adapters_without_credentials(tmp_path):
    result = catalog(tmp_path, connectors=[
        {"key": "easyeda", "label": "EasyEDA", "enabled": True, "transport": "stdio",
         "env": {"API_KEY": "must-not-appear"}},
        {"key": "offline", "label": "Offline", "enabled": False},
    ])
    ids = {item["id"] for item in result["adapters"]}
    assert {"visual", "game", "eda", "native", "mcp:easyeda", "mcp:offline"} <= ids
    assert all("must-not-appear" not in str(item) for item in result["adapters"])
    easyeda = next(item for item in result["adapters"] if item["id"] == "mcp:easyeda")
    assert easyeda["available"] is True and easyeda["connector_key"] == "easyeda"


def test_configure_persists_only_known_adapter_ids(tmp_path):
    profile = configure(tmp_path, ["game", "eda", "game"])
    assert profile["preview_adapters"] == ["game", "eda"]
    assert load_profile(tmp_path)["preview_adapters"] == ["game", "eda"]
