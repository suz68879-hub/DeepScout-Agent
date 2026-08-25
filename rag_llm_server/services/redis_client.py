"""进程级异步 Redis client 生命周期与稳定错误边界。"""
import asyncio
from contextlib import asynccontextmanager
from enum import Enum

from redis.asyncio import Redis
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from config import Config, settings


class RedisFailureKind(str, Enum):
    CONNECTION = "connection"
    TIMEOUT = "timeout"
    DATA = "data"


class SharedStateUnavailable(RuntimeError):
    """Redis 共享状态当前不可用。"""

    def __init__(
        self,
        message: str = "Redis shared state is unavailable",
        *,
        kind: RedisFailureKind = RedisFailureKind.CONNECTION,
    ) -> None:
        super().__init__(message)
        self.kind = kind


class _RedisReadiness:
    def __init__(self) -> None:
        self.disable()

    def disable(self) -> None:
        self.enabled = False
        self.ready = True
        self.failures = 0
        self.successes = 0

    def enable(self) -> None:
        self.enabled = True
        self.ready = True
        self.failures = 0
        self.successes = 0

    def record_failure(self) -> bool:
        self.successes = 0
        self.failures += 1
        if self.failures >= 3:
            self.ready = False
        return self.ready

    def record_success(self) -> bool:
        self.failures = 0
        if self.ready:
            self.successes = 0
            return True
        self.successes += 1
        if self.successes >= 2:
            self.ready = True
            self.successes = 0
        return self.ready


_client: Redis | None = None
_readiness = _RedisReadiness()


def _unavailable(exc: BaseException) -> SharedStateUnavailable:
    kind = (
        RedisFailureKind.TIMEOUT
        if isinstance(exc, (RedisTimeoutError, asyncio.TimeoutError))
        else RedisFailureKind.CONNECTION
    )
    return SharedStateUnavailable(kind=kind)


def data_unavailable() -> SharedStateUnavailable:
    """将损坏 payload 映射为内部可分类、对外脱敏的错误。"""
    return SharedStateUnavailable(kind=RedisFailureKind.DATA)


async def init_redis(config: Config = settings) -> Redis | None:
    """创建单个共享 client（由其内部连接池服务全部请求）并执行 PING。"""
    global _client
    await close_redis()
    if not config.REDIS_URL:
        _readiness.disable()
        return None

    client = Redis.from_url(
        config.REDIS_URL,
        decode_responses=True,
        max_connections=config.REDIS_MAX_CONNECTIONS,
        socket_timeout=config.REDIS_SOCKET_TIMEOUT,
        socket_connect_timeout=config.REDIS_CONNECT_TIMEOUT,
    )
    try:
        await client.ping()
    except (RedisError, OSError, asyncio.TimeoutError) as exc:
        await client.aclose()
        raise _unavailable(exc) from exc
    _client = client
    _readiness.enable()
    return client


def get_redis() -> Redis:
    """取得共享 client；未配置或未初始化时禁止回退本地状态。"""
    if _client is None:
        raise SharedStateUnavailable("Redis shared state is unavailable")
    return _client


async def ping_redis() -> bool:
    """验证运行期连接并把底层异常映射为稳定错误。"""
    try:
        return bool(await get_redis().ping())
    except SharedStateUnavailable:
        raise
    except (RedisError, OSError, asyncio.TimeoutError) as exc:
        raise _unavailable(exc) from exc


async def check_redis_readiness() -> bool:
    """三次连续失败摘流，两次连续成功后自动恢复。"""
    if not _readiness.enabled:
        return True
    try:
        await ping_redis()
    except SharedStateUnavailable:
        return _readiness.record_failure()
    return _readiness.record_success()


@asynccontextmanager
async def redis_error_boundary(client: Redis | None = None):
    """把任意 Redis 命令的底层故障转换为共享状态稳定错误。"""
    try:
        yield client or get_redis()
    except SharedStateUnavailable:
        raise
    except (RedisError, OSError, asyncio.TimeoutError) as exc:
        raise _unavailable(exc) from exc


async def close_redis() -> None:
    """确定性关闭 client 持有的连接池。"""
    global _client
    client, _client = _client, None
    _readiness.disable()
    if client is not None:
        await client.aclose()
