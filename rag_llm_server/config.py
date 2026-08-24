import os
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


class Config:
    CORS_ORIGINS = _csv_env(
        "CORS_ORIGINS",
        ("http://localhost:3000", "http://127.0.0.1:3000"),
    )
    ENABLE_DEBUG_ROUTES = _bool_env("ENABLE_DEBUG_ROUTES", False)
    AUTH_COOKIE_SECURE = _bool_env("AUTH_COOKIE_SECURE", False)
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
