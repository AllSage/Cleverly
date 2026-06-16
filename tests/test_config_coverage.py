def test_app_config_data_base_dir_validator_uses_supplied_base(tmp_path):
    from src.config import AppConfig

    cfg = AppConfig(data={"base_dir": tmp_path, "chunk_size": 123})

    assert cfg.data.base_dir == tmp_path
    assert cfg.data.data_dir == tmp_path / "data"
    assert cfg.data.uploads_dir == tmp_path / "data" / "uploads"
    assert cfg.data.chunk_size == 123


def test_validate_config_accepts_local_network_host(monkeypatch):
    import src.config as config

    calls = []
    monkeypatch.setattr(config.config.llm, "default_host", "192.168.1.50")
    monkeypatch.setattr(config.config.llm, "openai_api_key", None)
    monkeypatch.setattr(config, "create_directories", lambda: calls.append("created"))

    config.validate_config()

    assert calls == ["created"]
