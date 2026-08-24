"""报告文件存储接口。本地实现，预留 TOS 云存储（spec §5.4）。"""
import os
from abc import ABC, abstractmethod

from config import settings


class FileStorage(ABC):
    @abstractmethod
    async def save_text(self, relative_path: str, content: str) -> str: ...

    @abstractmethod
    async def read_text(self, relative_path: str) -> str: ...

    @abstractmethod
    async def save_bytes(self, relative_path: str, content: bytes) -> str: ...


class LocalFileStorage(FileStorage):
    def __init__(self, base_dir: str | None = None):
        self.base_dir = base_dir or settings.FILE_STORAGE_DIR

    def _full(self, relative_path: str) -> str:
        base = os.path.realpath(self.base_dir)
        full = os.path.realpath(os.path.join(base, relative_path))
        try:
            within = os.path.commonpath([base, full]) == base
        except ValueError:
            within = False  # 跨盘符等无法比较，一律视为越界
        if not within:
            raise ValueError(f"路径越界: {relative_path}")
        return full

    async def save_text(self, relative_path: str, content: str) -> str:
        full = self._full(relative_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return full

    async def read_text(self, relative_path: str) -> str:
        with open(self._full(relative_path), encoding="utf-8") as f:
            return f.read()

    async def save_bytes(self, relative_path: str, content: bytes) -> str:
        full = self._full(relative_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(content)
        return full
