from app.core.config import Settings, settings


def test_settings_defaults():
    assert settings.host == "0.0.0.0"
    assert settings.port == 8000
    assert settings.max_file_size == 104857600
    assert settings.chunk_size == 65536


def test_allowed_origins_parses_comma_separated():
    """Env vars carry origins as a comma list, not JSON."""
    parsed = Settings(allowed_origins="http://a.test, https://b.test ,").allowed_origin_list
    assert parsed == ["http://a.test", "https://b.test"]


def test_empty_allowed_origins_means_no_check():
    assert Settings(allowed_origins="").allowed_origin_list == []


def test_multi_replica_requires_redis():
    assert Settings(redis_url="").multi_replica_ready is False
    assert Settings(redis_url="redis://localhost:6379/0").multi_replica_ready is True
