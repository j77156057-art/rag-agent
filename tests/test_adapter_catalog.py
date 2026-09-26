from agent_runtime.adapter_catalog import catalog


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
