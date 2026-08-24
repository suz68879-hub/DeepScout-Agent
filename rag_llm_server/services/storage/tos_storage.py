"""TOS 对象存储实现（spec §12.3）。

- 报告文本（save_text/read_text）统一落在 reports/ 前缀：save_text 返回完整 key，
  read_text 按落库值原样读取（不做二次前缀——md_path 语义见 services/storage/__init__.py）
- 录音/简历二进制走 save_bytes，relative_path 即完整对象 key（recordings//resumes/）
- SDK 为同步接口，经 asyncio.to_thread 包装；SDK 异常转 OSError（业务层统一语义）
"""
import asyncio

from config import settings
from tos import HttpMethodType, TosClientV2
from tos.exceptions import TosClientError, TosServerError

from .file_storage import FileStorage


class TosFileStorage(FileStorage):
    def __init__(self):
        missing = [
            k for k in ("TOS_ACCESS_KEY", "TOS_SECRET_KEY", "TOS_ENDPOINT", "TOS_REGION", "TOS_BUCKET")
            if not getattr(settings, k)
        ]
        if missing:
            raise ValueError(f"TOS 配置不完整: {', '.join(missing)}")
        self.bucket = settings.TOS_BUCKET
        self.client = TosClientV2(
            settings.TOS_ACCESS_KEY, settings.TOS_SECRET_KEY,
            settings.TOS_ENDPOINT, settings.TOS_REGION,
        )

    def _put_object(self, key: str, content: bytes) -> None:
        try:
            self.client.put_object(self.bucket, key, content=content)
        except (TosClientError, TosServerError) as e:
            raise OSError(f"TOS 上传失败: {key}") from e

    def _get_object(self, key: str) -> bytes:
        try:
            return self.client.get_object(self.bucket, key).read()
        except (TosClientError, TosServerError) as e:
            raise OSError(f"TOS 读取失败: {key}") from e

    async def save_bytes(self, relative_path: str, content: bytes) -> str:
        """二进制对象（录音/简历）：relative_path 即完整 key，返回该 key。"""
        key = relative_path.replace("\\", "/")
        await asyncio.to_thread(self._put_object, key, content)
        return key

    async def save_text(self, relative_path: str, content: str) -> str:
        """报告文本：统一加 reports/ 前缀，返回完整 key（作为 md_path 落库）。"""
        key = f"reports/{relative_path.replace(chr(92), '/')}"
        await asyncio.to_thread(self._put_object, key, content.encode("utf-8"))
        return key

    async def read_text(self, relative_path: str) -> str:
        """按落库的完整 key 原样读取（save_text 返回值），不做二次前缀。"""
        data = await asyncio.to_thread(self._get_object, relative_path.replace("\\", "/"))
        return data.decode("utf-8")

    def presigned_url(self, key: str, expires: int = 3600) -> str:
        """GET 预签名 URL（识别服务拉取音频用）；expires 为有效期秒数。

        真实验收修复：tos SDK pre_signed_url 的 http_method 需传 HttpMethodType 枚举
        （传字符串会在 clientv2 内部 http_method.value 处抛 AttributeError）。
        """
        return self.client.pre_signed_url(
            http_method=HttpMethodType.Http_Method_Get,
            bucket=self.bucket, key=key, expires=expires,
        ).signed_url  # SDK 返回 PreSignedURLOutput 对象，URL 在 signed_url 字段
