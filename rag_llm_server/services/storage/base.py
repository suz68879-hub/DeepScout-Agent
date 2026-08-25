"""存储层接口（Repository 模式，spec §6）。

所有数据访问经此接口；当前实现 sqlite.py（aiosqlite），未来 MySQL 同接口替换。
"""
from abc import ABC, abstractmethod


class StorageConflictError(Exception):
    """持久化唯一性约束冲突，不暴露具体数据库异常。"""


class StorageVersionConflictError(Exception):
    """资源版本已变化，调用方必须重新读取后再更新。"""


class BaseStorage(ABC):
    @abstractmethod
    async def init(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    # auth
    @abstractmethod
    async def user_create(self, username: str, password_hash: str, role: str = "user") -> dict: ...
    @abstractmethod
    async def user_get_by_username(self, username: str) -> dict | None: ...
    @abstractmethod
    async def auth_session_create(self, user_id: str, token_hash: str, expires_at: str) -> None: ...
    @abstractmethod
    async def auth_session_get_user(self, token_hash: str) -> dict | None: ...
    @abstractmethod
    async def auth_session_revoke(self, token_hash: str) -> None: ...

    # resume
    @abstractmethod
    async def resume_create(self, user_id: str, resume: dict) -> dict: ...
    @abstractmethod
    async def resume_get(self, user_id: str, resume_id: str) -> dict | None: ...
    @abstractmethod
    async def resume_update(self, user_id: str, resume_id: str, patch: dict) -> dict | None: ...
    @abstractmethod
    async def resume_list(self, user_id: str) -> list[dict]: ...
    @abstractmethod
    async def resume_latest(self, user_id: str) -> dict | None: ...

    # interview_session
    @abstractmethod
    async def session_create(self, user_id: str, session: dict) -> dict: ...
    @abstractmethod
    async def session_get(self, user_id: str, session_id: str) -> dict | None: ...
    @abstractmethod
    async def session_get_by_callback(self, callback_id: str) -> dict | None: ...
    @abstractmethod
    async def session_update(
        self,
        user_id: str,
        session_id: str,
        patch: dict,
        expected_version: int | None = None,
    ) -> dict | None: ...
    @abstractmethod
    async def session_list_running(self, user_id: str) -> list[dict]: ...

    # message
    @abstractmethod
    async def message_append(self, user_id: str, session_id: str, role: str, content: str) -> dict: ...
    @abstractmethod
    async def message_list(self, user_id: str, session_id: str, limit: int = 100) -> list[dict]: ...

    # interview_report
    @abstractmethod
    async def report_create(self, user_id: str, report: dict) -> dict: ...
    @abstractmethod
    async def report_get(self, user_id: str, report_id: str) -> dict | None: ...
    @abstractmethod
    async def report_list(self, user_id: str) -> list[dict]: ...

    # recording
    @abstractmethod
    async def recording_create(self, user_id: str, recording: dict) -> dict: ...
    @abstractmethod
    async def recording_get(self, user_id: str, recording_id: str) -> dict | None: ...
    @abstractmethod
    async def recording_update(
        self, user_id: str, recording_id: str, patch: dict
    ) -> dict | None: ...
    @abstractmethod
    async def recording_list_processing(self) -> list[dict]: ...
