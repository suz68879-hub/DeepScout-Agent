"""进程级异步 Redis client 生命周期与稳定错误边界。"""
import asyncio
from contextlib import asynccontextmanager

from redis.asyncio import Redis
from redis.exceptions import RedisError

from config import Config, settings


class SharedStateUnavailable(RuntimeError):
    """Redis 共享状态当前不可用。"""


_client: Redis | None = None


def _unavailable(exc: BaseException) -> SharedStateUnavailable:
    return SharedStateUnavailable("Redis shared state is unavailable")


async def init_redis(config: Config = settings) -> Redis | None:
    """创建单个共享 client（由其内部连接池服务全部请求）并执行 PING。"""
    global _client
    await close_redis()
    if not config.REDIS_URL:
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
    if client is not None:
        await client.aclose()
