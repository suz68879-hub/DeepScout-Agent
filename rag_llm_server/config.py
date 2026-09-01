import os
import ssl
from math import isfinite
from pathlib import Path
from urllib.parse import unquote, urlsplit

from dotenv import load_dotenv

load_dotenv()

# 方舟 OpenAI 兼容端点（LangChain ChatOpenAI / LlamaIndex OpenAIEmbedding 共用）
ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"


def _float_env(key: str, default: float) -> float:
    """读取浮点环境变量；未配置或非法时回落默认值。"""
    raw = os.getenv(key)
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _bool_env(key: str, default: bool) -> bool:
    """读取布尔环境变量；无法识别时回落默认值。"""
    raw = os.getenv(key)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _csv_env(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """读取逗号分隔环境变量，忽略空项。"""
    raw = os.getenv(key)
    if raw is None:
        return default
    values = tuple(value.strip() for value in raw.split(",") if value.strip())
    return values or default


def _int_env(key: str, default: int, minimum: int) -> int:
    """读取有下界的整数环境变量，非法配置直接终止启动。"""
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{key} must be at least {minimum}")
    return value


def _positive_float_env(key: str, default: float) -> float:
    """读取正浮点环境变量，非法配置直接终止启动。"""
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be a number") from exc
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{key} must be greater than 0")
    return value


def _postgres_target(key: str, value: str, app_env: str) -> str:
    """校验 PostgreSQL DSN，并返回不含凭据的日志目标。"""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{key} must be a valid PostgreSQL URL") from exc

    if (
        parsed.scheme not in {"postgresql", "postgresql+psycopg"}
        or not parsed.hostname
        or not parsed.path.lstrip("/")
    ):
        raise ValueError(f"{key} must be a PostgreSQL URL")

    password = unquote(parsed.password or "")
    username = unquote(parsed.username or "")
    if app_env == "production" and password.lower() in {
        "changeme",
        "password",
        "postgres",
    }:
        raise ValueError(f"{key} uses a default database password")
    if app_env == "production" and password and password == username:
        raise ValueError(f"{key} uses a default database password")

    host = parsed.hostname
    if port is not None:
        host = f"{host}:{port}"
    return f"{host}/{parsed.path.lstrip('/')}"


def _redis_target(value: str) -> tuple[str, bool]:
    """校验 Redis URL，并返回不含凭据的日志目标及 TLS 状态。"""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("REDIS_URL must be a Redis URL") from exc
    if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
        raise ValueError("REDIS_URL must be a Redis URL")

    host = parsed.hostname
    if port is not None:
        host = f"{host}:{port}"
    database = parsed.path.lstrip("/") or "0"
    if not database.isdigit():
        raise ValueError("REDIS_URL must be a Redis URL")
    return f"{host}/{database}", parsed.scheme == "rediss"


def _celery_broker_target(value: str, app_env: str) -> tuple[str, bool]:
    """校验 RabbitMQ URL，并返回不含凭据的日志目标及 TLS 状态。"""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("CELERY_BROKER_URL must be an AMQP URL") from exc
    if parsed.scheme not in {"amqp", "amqps"} or not parsed.hostname:
        raise ValueError("CELERY_BROKER_URL must be an AMQP URL")
    if app_env == "production" and parsed.scheme != "amqps":
        raise ValueError("CELERY_BROKER_URL must use amqps in production")

    host = parsed.hostname
    if port is not None:
        host = f"{host}:{port}"
    virtual_host = parsed.path.lstrip("/") or "/"
    return f"{host}/{virtual_host}", parsed.scheme == "amqps"


def _optional_existing_file(key: str) -> str | None:
    """读取可选证书路径；配置后必须指向现有文件。"""
    value = os.getenv(key)
    if not value:
        return None
    if not Path(value).is_file():
        raise ValueError(f"{key} does not exist or is not a file")
    return value


def _validate_celery_broker_certificates(
    ca_cert: str | None,
    client_cert: str | None,
    client_key: str | None,
) -> None:
    """在启动阶段解析自定义 TLS 证书，错误信息不包含本地路径。"""
    if not (ca_cert or client_cert or client_key):
        return
    try:
        context = ssl.create_default_context(cafile=ca_cert)
        if client_cert and client_key:
            context.load_cert_chain(client_cert, client_key)
    except (OSError, ssl.SSLError):
        raise ValueError("Celery broker certificate configuration is invalid") from None


class Config:
    APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
    LOG_FORMAT = os.getenv("LOG_FORMAT", "json").strip().lower()
    CORS_ORIGINS = _csv_env(
        "CORS_ORIGINS",
        ("http://localhost:3000", "http://127.0.0.1:3000"),
    )
    ENABLE_DEBUG_ROUTES = _bool_env("ENABLE_DEBUG_ROUTES", False)
    ENABLE_LEGACY_SYNC_FINISH = _bool_env("ENABLE_LEGACY_SYNC_FINISH", False)
    AUTH_COOKIE_SECURE = _bool_env("AUTH_COOKIE_SECURE", False)
    REGISTER_INVITE_CODE = os.getenv("REGISTER_INVITE_CODE", "").strip()
    BOOTSTRAP_ADMIN_USERNAME = os.getenv("BOOTSTRAP_ADMIN_USERNAME")
    BOOTSTRAP_ADMIN_PASSWORD = os.getenv("BOOTSTRAP_ADMIN_PASSWORD")

    # 火山引擎 OpenAPI 凭证（RTC/知识库 OpenAPI）
    VOLC_AK = os.getenv("VOLC_ACCESS_KEY")
    VOLC_SK = os.getenv("VOLC_SECRET_KEY")
    ARK_ENDPOINT_ID = os.getenv("ARK_ENDPOINT_ID")
    ARK_API_KEY = os.getenv("ARK_API_KEY")

    RTC_APP_ID = os.getenv("RTC_APP_ID")
    RTC_APP_KEY = os.getenv("RTC_APP_KEY")
    ASR_APP_ID = os.getenv("ASR_APP_ID")
    TTS_APP_ID = os.getenv("TTS_APP_ID")

    SERVER_URL = os.getenv("SERVER_URL")

    # P3 新增：各 Agent 独立端点（未配置时回落到 ARK_ENDPOINT_ID）
    AGENT_ENDPOINT_KEYS = {
        "interviewer": "ARK_INTERVIEWER_ENDPOINT_ID",
        "planner": "ARK_PLANNER_ENDPOINT_ID",
        "evaluator": "ARK_EVALUATOR_ENDPOINT_ID",
        "reporter": "ARK_REPORTER_ENDPOINT_ID",
        "resume_parser": "ARK_RESUME_ENDPOINT_ID",
        "text2sql": "ARK_TEXTSQL_ENDPOINT_ID",
        "recording_analyzer": "ARK_RECORDING_ANALYZER_ENDPOINT_ID",
    }

    # P6 新增：TOS 对象存储（缺失时录音接口 fail fast；报告文件回落本地）
    TOS_ACCESS_KEY = os.getenv("TOS_ACCESS_KEY")
    TOS_SECRET_KEY = os.getenv("TOS_SECRET_KEY")
    TOS_ENDPOINT = os.getenv("TOS_ENDPOINT")
    TOS_REGION = os.getenv("TOS_REGION")
    TOS_BUCKET = os.getenv("TOS_BUCKET")

    # P6 新增：豆包语音「录音文件识别大模型版」API Key（新控制台，非 AK/SK）
    ASR_FILE_API_KEY = os.getenv("ASR_FILE_API_KEY")

    # P3 新增：视觉模型（简历扫描件 OCR）与 embedding
    ARK_VISION_ENDPOINT_ID = os.getenv("ARK_VISION_ENDPOINT_ID")
    ARK_EMBEDDING_ENDPOINT_ID = os.getenv("ARK_EMBEDDING_ENDPOINT_ID")

    # P3 新增：RAG 与存储
    RAG_PROVIDER = os.getenv("RAG_PROVIDER", "llamaindex")  # llamaindex / volc_kb
    RAG_SIMILARITY_THRESHOLD = _float_env("RAG_SIMILARITY_THRESHOLD", 0.35)
    DATABASE_PATH = os.getenv("DATABASE_PATH", "data/interview.db")
    FILE_STORAGE_DIR = os.getenv("FILE_STORAGE_DIR", "data/reports")

    # P3 新增：回调验签密钥（不配置则跳过签名校验，Ruling R2）
    RTC_CALLBACK_SECRET = os.getenv("RTC_CALLBACK_SECRET", "")

    def __init__(self) -> None:
        self.APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
        self.ENABLE_LEGACY_SYNC_FINISH = _bool_env(
            "ENABLE_LEGACY_SYNC_FINISH", False
        )
        if self.APP_ENV not in {"development", "test", "production"}:
            raise ValueError("APP_ENV must be development, test, or production")
        self.AUTH_COOKIE_SECURE = (
            True if self.APP_ENV == "production" else _bool_env("AUTH_COOKIE_SECURE", False)
        )
        self.REGISTER_INVITE_CODE = os.getenv("REGISTER_INVITE_CODE", "").strip()

        self.STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "sqlite").strip().lower()
        if self.STORAGE_BACKEND not in {"sqlite", "postgres"}:
            raise ValueError("STORAGE_BACKEND must be sqlite or postgres")
        if self.APP_ENV == "production" and self.STORAGE_BACKEND != "postgres":
            raise ValueError("production requires PostgreSQL storage")

        self.DATABASE_URL = os.getenv("DATABASE_URL") or None
        self.ANALYTICS_DATABASE_URL = os.getenv("ANALYTICS_DATABASE_URL") or None
        self.DATABASE_POOL_SIZE = _int_env("DATABASE_POOL_SIZE", 5, 1)
        self.DATABASE_MAX_OVERFLOW = _int_env("DATABASE_MAX_OVERFLOW", 10, 0)
        self.DATABASE_POOL_TIMEOUT = _int_env("DATABASE_POOL_TIMEOUT", 30, 1)
        self.DATABASE_POOL_RECYCLE = _int_env("DATABASE_POOL_RECYCLE", 1800, -1)
        self.REDIS_URL = os.getenv("REDIS_URL") or None
        self.REDIS_MAX_CONNECTIONS = _int_env("REDIS_MAX_CONNECTIONS", 20, 1)
        self.REDIS_SOCKET_TIMEOUT = _positive_float_env("REDIS_SOCKET_TIMEOUT", 2.0)
        self.REDIS_CONNECT_TIMEOUT = _positive_float_env("REDIS_CONNECT_TIMEOUT", 2.0)
        self.SESSION_MAX_SECONDS = _int_env("SESSION_MAX_SECONDS", 3600, 60)
        self.AUTH_SESSION_CACHE_ENABLED = _bool_env(
            "AUTH_SESSION_CACHE_ENABLED", False
        )
        self.CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL") or None
        self.CELERY_BROKER_CONNECTION_TIMEOUT = _positive_float_env(
            "CELERY_BROKER_CONNECTION_TIMEOUT", 5.0
        )
        self.CELERY_BROKER_MAX_RETRIES = _int_env(
            "CELERY_BROKER_MAX_RETRIES", 3, 0
        )
        self.CELERY_COLD_WORKER_CONCURRENCY = _int_env(
            "CELERY_COLD_WORKER_CONCURRENCY", 2, 1
        )
        self.CELERY_RECORDING_WORKER_CONCURRENCY = _int_env(
            "CELERY_RECORDING_WORKER_CONCURRENCY", 2, 1
        )
        self.CELERY_OUTBOX_WORKER_CONCURRENCY = _int_env(
            "CELERY_OUTBOX_WORKER_CONCURRENCY", 1, 1
        )

        requires_postgres = self.STORAGE_BACKEND == "postgres" or self.APP_ENV == "production"
        if requires_postgres and not self.DATABASE_URL:
            raise ValueError("DATABASE_URL is required for PostgreSQL storage")

        self._database_log_target = None
        if self.DATABASE_URL:
            self._database_log_target = _postgres_target(
                "DATABASE_URL", self.DATABASE_URL, self.APP_ENV
            )
        if self.ANALYTICS_DATABASE_URL:
            _postgres_target(
                "ANALYTICS_DATABASE_URL",
                self.ANALYTICS_DATABASE_URL,
                self.APP_ENV,
            )

        if self.APP_ENV == "production" and not self.REDIS_URL:
            raise ValueError("REDIS_URL is required in production")
        self._redis_log_target = None
        self.REDIS_TLS = False
        if self.REDIS_URL:
            self._redis_log_target, self.REDIS_TLS = _redis_target(self.REDIS_URL)
        if self.AUTH_SESSION_CACHE_ENABLED and not self.REDIS_URL:
            raise ValueError("AUTH_SESSION_CACHE_ENABLED requires REDIS_URL")

        if self.APP_ENV == "production" and not self.CELERY_BROKER_URL:
            raise ValueError("CELERY_BROKER_URL is required in production")
        self._celery_broker_log_target = None
        self.CELERY_BROKER_TLS = False
        if self.CELERY_BROKER_URL:
            (
                self._celery_broker_log_target,
                self.CELERY_BROKER_TLS,
            ) = _celery_broker_target(self.CELERY_BROKER_URL, self.APP_ENV)

        self.CELERY_BROKER_CA_CERT = _optional_existing_file(
            "CELERY_BROKER_CA_CERT"
        )
        self.CELERY_BROKER_CLIENT_CERT = os.getenv("CELERY_BROKER_CLIENT_CERT") or None
        self.CELERY_BROKER_CLIENT_KEY = os.getenv("CELERY_BROKER_CLIENT_KEY") or None
        if bool(self.CELERY_BROKER_CLIENT_CERT) != bool(self.CELERY_BROKER_CLIENT_KEY):
            raise ValueError(
                "CELERY_BROKER_CLIENT_CERT and CELERY_BROKER_CLIENT_KEY "
                "must be configured together"
            )
        if self.CELERY_BROKER_CLIENT_CERT:
            self.CELERY_BROKER_CLIENT_CERT = _optional_existing_file(
                "CELERY_BROKER_CLIENT_CERT"
            )
            self.CELERY_BROKER_CLIENT_KEY = _optional_existing_file(
                "CELERY_BROKER_CLIENT_KEY"
            )
        if (
            self.CELERY_BROKER_CA_CERT
            or self.CELERY_BROKER_CLIENT_CERT
            or self.CELERY_BROKER_CLIENT_KEY
        ) and not self.CELERY_BROKER_TLS:
            raise ValueError("Celery broker certificates require an amqps URL")
        _validate_celery_broker_certificates(
            self.CELERY_BROKER_CA_CERT,
            self.CELERY_BROKER_CLIENT_CERT,
            self.CELERY_BROKER_CLIENT_KEY,
        )

        self.DATABASE_PATH = (
            os.getenv("DATABASE_PATH", "data/interview.db")
            if self.STORAGE_BACKEND == "sqlite"
            else None
        )

    def database_log_target(self) -> str | None:
        """返回可安全写入日志的数据库主机与库名。"""
        return self._database_log_target

    def redis_log_target(self) -> str | None:
        """返回可安全写入日志的 Redis 主机与数据库编号。"""
        return self._redis_log_target

    def celery_broker_log_target(self) -> str | None:
        """返回可安全写入日志的 RabbitMQ 主机与 vhost。"""
        return self._celery_broker_log_target

    def agent_endpoint_id(self, agent: str) -> str:
        """按 Agent 名取端点 ID；未配置或未知 Agent 回落到默认端点。"""
        key = self.AGENT_ENDPOINT_KEYS.get(agent)
        if key:
            return os.getenv(key) or self.ARK_ENDPOINT_ID
        return self.ARK_ENDPOINT_ID

    def embedding_config(self) -> tuple[str, str, str]:
        """embedding 多厂商兼容（OpenAI 兼容协议：方舟/百炼等）；未配置时回落方舟。

        调用时读取环境变量（同 agent_endpoint_id），避免 import 时快照。
        返回 (api_base, api_key, model)。
        """
        api_base = os.getenv("EMBEDDING_API_BASE") or ARK_BASE_URL
        api_key = os.getenv("EMBEDDING_API_KEY") or os.getenv("ARK_API_KEY")
        model = os.getenv("EMBEDDING_MODEL") or os.getenv("ARK_EMBEDDING_ENDPOINT_ID")
        return api_base, api_key, model


settings = Config()
