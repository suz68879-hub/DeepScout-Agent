"""存储层单例与生命周期（Repository 模式，spec §6）。

表结构唯一权威在 sqlite.py 的 _SCHEMA；未来 MySQL 实现同接口替换。
md_path 语义：落库值永远是可直接读取的路径/key（TOS 行存完整 key，本地行存绝对路径）。
"""
from .file_storage import LocalFileStorage
from .sqlite import SqliteStorage

storage = SqliteStorage()

_file_store = None


def get_file_store():
    """文件存储单例（spec §12.3）：TOS 五项配置齐全走 TosFileStorage，否则本地。

    录音/简历原文件仅走 TOS（缺失时对应接口 fail fast）；报告文件随本工厂自动切换。
    """
    global _file_store
    if _file_store is None:
        try:
            from .tos_storage import TosFileStorage

            _file_store = TosFileStorage()
        except (ImportError, ValueError):
            _file_store = LocalFileStorage()
    return _file_store


def get_tos_store():
    """TOS 存储单例；未配置/未安装 SDK 返回 None（本地模式，T7/T8 用）。"""
    try:
        from .tos_storage import TosFileStorage
    except ImportError:
        return None
    store = get_file_store()
    return store if isinstance(store, TosFileStorage) else None


async def init_storage() -> None:
    await storage.init()


async def close_storage() -> None:
    await storage.close()
