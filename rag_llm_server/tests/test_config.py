"""config.py 回落与解析逻辑测试。"""
import pytest

from rag_llm_server.config import Config, _bool_env, _csv_env, _float_env  # noqa: F401


def test_float_env_default_when_unset():
    assert _float_env("ARK_NO_SUCH_KEY_12345", 0.35) == 0.35


def test_float_env_parses(monkeypatch):
    monkeypatch.setenv("RAG_SIMILARITY_THRESHOLD", "0.5")
    assert _float_env("RAG_SIMILARITY_THRESHOLD", 0.35) == 0.5


def test_float_env_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("RAG_SIMILARITY_THRESHOLD", "abc")
    assert _float_env("RAG_SIMILARITY_THRESHOLD", 0.35) == 0.35


def test_bool_env_defaults_and_parses(monkeypatch):
    monkeypatch.delenv("ENABLE_DEBUG_ROUTES", raising=False)
    assert _bool_env("ENABLE_DEBUG_ROUTES", False) is False
    monkeypatch.setenv("ENABLE_DEBUG_ROUTES", "true")
    assert _bool_env("ENABLE_DEBUG_ROUTES", False) is True


def test_csv_env_defaults_and_ignores_blank_items(monkeypatch):
    default = ("http://localhost:3000", "http://127.0.0.1:3000")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert _csv_env("CORS_ORIGINS", default) == default
    monkeypatch.setenv("CORS_ORIGINS", "https://one.example, ,https://two.example")
    assert _csv_env("CORS_ORIGINS", default) == (
        "https://one.example", "https://two.example",
    )


def test_agent_endpoint_fallback_to_default(monkeypatch):
    # R-T1-1：ARK_ENDPOINT_ID 在 Config 类定义时绑定（启动读 .env 语义），用 setattr 注入
    monkeypatch.setattr(Config, "ARK_ENDPOINT_ID", "ep-default")
    monkeypatch.delenv("ARK_INTERVIEWER_ENDPOINT_ID", raising=False)
    assert Config().agent_endpoint_id("interviewer") == "ep-default"


def test_agent_endpoint_empty_string_falls_back_to_default(monkeypatch):
    # 空字符串视为未配置（.env.example 复制出的状态），回落默认端点
    monkeypatch.setattr(Config, "ARK_ENDPOINT_ID", "ep-default")
    monkeypatch.setenv("ARK_INTERVIEWER_ENDPOINT_ID", "")
    assert Config().agent_endpoint_id("interviewer") == "ep-default"


def test_agent_endpoint_override(monkeypatch):
    monkeypatch.setattr(Config, "ARK_ENDPOINT_ID", "ep-default")
    monkeypatch.setenv("ARK_INTERVIEWER_ENDPOINT_ID", "ep-interviewer")
    assert Config().agent_endpoint_id("interviewer") == "ep-interviewer"


def test_agent_endpoint_unknown_agent_falls_back(monkeypatch):
    monkeypatch.setattr(Config, "ARK_ENDPOINT_ID", "ep-default")
    assert Config().agent_endpoint_id("no_such_agent") == "ep-default"


def _clear_database_env(monkeypatch):
    for key in (
        "APP_ENV",
        "STORAGE_BACKEND",
        "DATABASE_URL",
        "ANALYTICS_DATABASE_URL",
        "DATABASE_POOL_SIZE",
        "DATABASE_MAX_OVERFLOW",
        "DATABASE_POOL_TIMEOUT",
        "DATABASE_POOL_RECYCLE",
        "ENABLE_LEGACY_SYNC_FINISH",
    ):
        monkeypatch.delenv(key, raising=False)


def _clear_redis_env(monkeypatch):
    for key in (
        "REDIS_URL",
        "REDIS_MAX_CONNECTIONS",
        "REDIS_SOCKET_TIMEOUT",
        "REDIS_CONNECT_TIMEOUT",
        "AUTH_SESSION_CACHE_ENABLED",
    ):
        monkeypatch.delenv(key, raising=False)


def test_database_config_defaults_to_sqlite(monkeypatch):
    _clear_database_env(monkeypatch)
    monkeypatch.setenv("DATABASE_PATH", "data/test.db")
    monkeypatch.setenv("REDIS_URL", "redis://cache.internal/0")

    config = Config()

    assert config.APP_ENV == "development"
    assert config.STORAGE_BACKEND == "sqlite"
    assert config.DATABASE_PATH == "data/test.db"
    assert config.DATABASE_URL is None
    assert config.ENABLE_LEGACY_SYNC_FINISH is False


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("APP_ENV", "staging", "APP_ENV must be"),
        ("STORAGE_BACKEND", "unknown", "STORAGE_BACKEND must be"),
    ],
)
def test_database_config_rejects_unknown_environment_values(
    monkeypatch, key, value, message
):
    _clear_database_env(monkeypatch)
    monkeypatch.setenv(key, value)

    with pytest.raises(ValueError, match=message):
        Config()


def test_postgres_config_parses_pool_and_hides_credentials(monkeypatch):
    _clear_database_env(monkeypatch)
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://deepscout_app:super-secret@db.internal:5433/deepscout_test",
    )
    monkeypatch.setenv("DATABASE_POOL_SIZE", "7")
    monkeypatch.setenv("DATABASE_MAX_OVERFLOW", "4")
    monkeypatch.setenv("DATABASE_POOL_TIMEOUT", "12")
    monkeypatch.setenv("DATABASE_POOL_RECYCLE", "900")
    monkeypatch.setenv("DATABASE_PATH", "must-not-be-read.db")
    monkeypatch.setenv("REDIS_URL", "redis://cache.internal/0")

    config = Config()

    assert config.DATABASE_PATH is None
    assert config.DATABASE_POOL_SIZE == 7
    assert config.DATABASE_MAX_OVERFLOW == 4
    assert config.DATABASE_POOL_TIMEOUT == 12
    assert config.DATABASE_POOL_RECYCLE == 900
    assert config.database_log_target() == "db.internal:5433/deepscout_test"
    assert "super-secret" not in config.database_log_target()


def test_production_requires_database_url(monkeypatch):
    _clear_database_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")

    with pytest.raises(ValueError, match="DATABASE_URL is required"):
        Config()


@pytest.mark.parametrize("url", ["sqlite:///data.db", "mysql://db/app", "not-a-url"])
def test_postgres_backend_rejects_non_postgres_url(monkeypatch, url):
    _clear_database_env(monkeypatch)
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", url)

    with pytest.raises(ValueError, match="DATABASE_URL must be a PostgreSQL URL"):
        Config()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("DATABASE_POOL_SIZE", "0"),
        ("DATABASE_MAX_OVERFLOW", "-1"),
        ("DATABASE_POOL_TIMEOUT", "0"),
        ("DATABASE_POOL_RECYCLE", "-2"),
        ("DATABASE_POOL_SIZE", "invalid"),
    ],
)
def test_database_pool_parameters_fail_fast(monkeypatch, key, value):
    _clear_database_env(monkeypatch)
    monkeypatch.setenv(key, value)

    with pytest.raises(ValueError, match=key):
        Config()


def test_production_rejects_default_password_without_leaking_it(monkeypatch):
    _clear_database_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://deepscout_app:password@db.internal/deepscout",
    )

    with pytest.raises(ValueError) as exc_info:
        Config()

    message = str(exc_info.value)
    assert "default database password" in message
    assert "password@" not in message


@pytest.mark.parametrize("key", ["DATABASE_URL", "ANALYTICS_DATABASE_URL"])
def test_config_rejects_invalid_postgres_dsn_without_leaking_secret(monkeypatch, key):
    _clear_database_env(monkeypatch)
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:safe@db.internal/app")
    monkeypatch.setenv(key, "mysql://user:do-not-leak@db.internal/app")

    with pytest.raises(ValueError) as exc_info:
        Config()

    assert "do-not-leak" not in str(exc_info.value)


def test_test_env_allows_missing_redis_url(monkeypatch):
    _clear_database_env(monkeypatch)
    _clear_redis_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "test")

    config = Config()

    assert config.REDIS_URL is None
    assert config.REDIS_MAX_CONNECTIONS == 20
    assert config.REDIS_SOCKET_TIMEOUT == 2.0
    assert config.REDIS_CONNECT_TIMEOUT == 2.0
    assert config.REDIS_TLS is False
    assert config.redis_log_target() is None


@pytest.mark.parametrize("app_env", ["development", "production"])
def test_runtime_environments_require_redis_url(monkeypatch, app_env):
    _clear_database_env(monkeypatch)
    _clear_redis_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", app_env)
    if app_env == "production":
        monkeypatch.setenv("STORAGE_BACKEND", "postgres")
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+psycopg://deepscout_app:safe-secret@db.internal/deepscout",
        )

    with pytest.raises(ValueError, match="REDIS_URL is required"):
        Config()


def test_redis_config_parses_tls_url_and_hides_credentials(monkeypatch):
    _clear_database_env(monkeypatch)
    _clear_redis_env(monkeypatch)
    monkeypatch.setenv("REDIS_URL", "rediss://app:super-secret@cache.internal:6380/2")
    monkeypatch.setenv("REDIS_MAX_CONNECTIONS", "32")
    monkeypatch.setenv("REDIS_SOCKET_TIMEOUT", "1.5")
    monkeypatch.setenv("REDIS_CONNECT_TIMEOUT", "0.75")

    config = Config()

    assert config.REDIS_MAX_CONNECTIONS == 32
    assert config.REDIS_SOCKET_TIMEOUT == 1.5
    assert config.REDIS_CONNECT_TIMEOUT == 0.75
    assert config.REDIS_TLS is True
    assert config.redis_log_target() == "cache.internal:6380/2"
    assert "super-secret" not in config.redis_log_target()


@pytest.mark.parametrize(
    "url",
    ["http://cache.internal", "redis://", "not-a-url", "redis://cache/not-a-db"],
)
def test_redis_config_rejects_invalid_url_without_leaking_secret(monkeypatch, url):
    _clear_database_env(monkeypatch)
    _clear_redis_env(monkeypatch)
    monkeypatch.setenv("REDIS_URL", url)

    with pytest.raises(ValueError, match="REDIS_URL must be a Redis URL") as exc_info:
        Config()

    assert url not in str(exc_info.value)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("REDIS_MAX_CONNECTIONS", "0"),
        ("REDIS_SOCKET_TIMEOUT", "0"),
        ("REDIS_CONNECT_TIMEOUT", "invalid"),
        ("REDIS_CONNECT_TIMEOUT", "nan"),
        ("REDIS_SOCKET_TIMEOUT", "inf"),
    ],
)
def test_redis_pool_parameters_fail_fast(monkeypatch, key, value):
    _clear_database_env(monkeypatch)
    _clear_redis_env(monkeypatch)
    monkeypatch.setenv(key, value)

    with pytest.raises(ValueError, match=key):
        Config()


def test_session_cache_flag_is_opt_in_and_requires_redis(monkeypatch):
    _clear_database_env(monkeypatch)
    _clear_redis_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "test")
    assert Config().AUTH_SESSION_CACHE_ENABLED is False

    monkeypatch.setenv("REDIS_URL", "redis://cache.internal/0")
    assert Config().AUTH_SESSION_CACHE_ENABLED is False
    monkeypatch.setenv("AUTH_SESSION_CACHE_ENABLED", "true")
    assert Config().AUTH_SESSION_CACHE_ENABLED is True


def test_session_cache_cannot_be_enabled_without_redis(monkeypatch):
    _clear_database_env(monkeypatch)
    _clear_redis_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AUTH_SESSION_CACHE_ENABLED", "true")

    with pytest.raises(ValueError, match="AUTH_SESSION_CACHE_ENABLED requires REDIS_URL"):
        Config()


def test_production_forces_secure_auth_cookie_even_when_disabled(monkeypatch):
    _clear_database_env(monkeypatch)
    _clear_redis_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://app:safe-secret@db.internal/deepscout",
    )
    monkeypatch.setenv("REDIS_URL", "rediss://cache.internal/0")
    monkeypatch.setenv("CELERY_BROKER_URL", "amqps://mq.internal/app")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")

    assert Config().AUTH_COOKIE_SECURE is True


def test_register_invite_code_is_read_from_environment(monkeypatch):
    monkeypatch.setenv("REGISTER_INVITE_CODE", " office-pass ")
    assert Config().REGISTER_INVITE_CODE == "office-pass"
