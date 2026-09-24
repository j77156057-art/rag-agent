"""mcp_server_index 离线精选索引单测（纯函数，无 chromadb / 网络依赖，可在任意环境跑）。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_server_index import match_curated_server, curated_entry_to_config, list_curated_names


def test_exact_name_match():
    hits = match_curated_server("github")
    assert hits and hits[0]["name"] == "github"


def test_alias_token_match():
    assert match_curated_server("postgres")[0]["name"] == "postgres"
    assert match_curated_server("slack")[0]["name"] == "slack"
    assert match_curated_server("fetch")[0]["name"] == "fetch"


def test_package_id_match():
    hits = match_curated_server("@modelcontextprotocol/server-github")
    assert hits and hits[0]["name"] == "github"


def test_fuzzy_phrase_match():
    # 自然语言 query 也能命中关键词
    assert match_curated_server("我想连 github 仓库")[0]["name"] == "github"
    assert match_curated_server("postgresql 数据库连接")[0]["name"] == "postgres"


def test_no_match_for_gibberish():
    assert match_curated_server("asdfqwerty") == []
    assert match_curated_server("") == []


def test_limit_caps_results():
    # 极短通用词不应返回超过 limit 条无关噪声；这里验证上限生效
    hits = match_curated_server("server", limit=3)
    assert len(hits) <= 3


def test_config_shape_and_trusted_domain():
    entry = match_curated_server("github")[0]
    cfg = curated_entry_to_config(entry)
    assert cfg["transport"] == "stdio"
    assert cfg["command"] == "npx"
    assert cfg["args"] == ["-y", "@modelcontextprotocol/server-github"]
    # provenance domain 落在可信域 → 管线里会被标 trusted
    assert cfg["provenance"]["domain"] == "github.com"
    assert cfg["provenance"]["curated"] is True


def test_secret_refs_not_plaintext():
    # env 值必须是 @secret: 引用，绝不落明文密钥（R7 安全不变量）
    for name in ("github", "postgres", "slack", "brave-search", "notion"):
        entry = match_curated_server(name)[0]
        cfg = curated_entry_to_config(entry)
        for v in cfg["env"].values():
            assert v.startswith("@secret:"), f"{name} 的 env 值应走 @secret 引用，实际={v}"


def test_all_entries_build_valid_config():
    # 每条索引都能转成 config，且字段闭合（不依赖外部解析）
    for name in list_curated_names():
        entry = match_curated_server(name)[0]
        cfg = curated_entry_to_config(entry)
        assert cfg["command"] in ("npx", "uvx", "docker"), cfg
        assert isinstance(cfg["args"], list)
