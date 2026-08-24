"""P6 T2：TOS 存储实现、工厂切换、报告导出存量兼容（不触真实 TOS）。"""
import pytest

from config import settings
from services.storage.file_storage import LocalFileStorage
from services.storage.tos_storage import TosFileStorage


class FakeTosClient:
    def __init__(self):
        self.puts: list[tuple] = []
        self.objects: dict[str, bytes] = {}
        self.presigned: list[tuple] = []

    def put_object(self, bucket, key, content=None):
        self.puts.append((bucket, key, content))
        self.objects[key] = content

    def get_object(self, bucket, key):
        from tos.exceptions import TosServerError

        if key not in self.objects:
            class _ErrResp:
                request_id = "fake-req"
                headers = {}
                status = 404

            raise TosServerError(_ErrResp(), "NoSuchKey", "404", "", "reports/nope/report.md")

        class _Resp:
            def __init__(self, data):
                self._data = data

            def read(self):
                return self._data

        return _Resp(self.objects[key])

    def pre_signed_url(self, http_method="GET", bucket=None, key=None, expires=3600, **kwargs):
        # 真实验收修复：真实 SDK 返回 PreSignedURLOutput 对象（URL 在 signed_url 字段）
        import types

        self.presigned.append((http_method, bucket, key, expires))
        return types.SimpleNamespace(signed_url=f"https://fake.tos/{key}")


def _make_tos(monkeypatch, client):
    for k, v in (
        ("TOS_ACCESS_KEY", "ak"), ("TOS_SECRET_KEY", "sk"),
        ("TOS_ENDPOINT", "https://tos-cn-beijing.volces.com"),
        ("TOS_REGION", "cn-beijing"), ("TOS_BUCKET", "b1"),
    ):
        monkeypatch.setattr(settings, k, v)
    from services.storage import tos_storage

    monkeypatch.setattr(tos_storage, "TosClientV2", lambda *a, **kw: client)
    return TosFileStorage()


async def test_tos_storage_missing_config_raises(monkeypatch):
    for k in ("TOS_ACCESS_KEY", "TOS_SECRET_KEY", "TOS_ENDPOINT", "TOS_REGION", "TOS_BUCKET"):
        monkeypatch.setattr(settings, k, None)
    with pytest.raises(ValueError):
        TosFileStorage()


async def test_save_bytes_puts_object_with_full_key(monkeypatch):
    client = FakeTosClient()
    store = _make_tos(monkeypatch, client)
    key = await store.save_bytes("recordings/r1.mp3", b"x")
    assert key == "recordings/r1.mp3"
    assert client.puts == [("b1", "recordings/r1.mp3", b"x")]


async def test_save_text_adds_reports_prefix_and_returns_full_key(monkeypatch):
    client = FakeTosClient()
    store = _make_tos(monkeypatch, client)
    key = await store.save_text("s1/report.md", "# 报告")
    assert key == "reports/s1/report.md"
    assert client.objects["reports/s1/report.md"] == "# 报告".encode("utf-8")


async def test_read_text_reads_stored_key_verbatim(monkeypatch):
    client = FakeTosClient()
    client.objects["reports/s1/report.md"] = "# 报告".encode("utf-8")
    store = _make_tos(monkeypatch, client)
    assert await store.read_text("reports/s1/report.md") == "# 报告"


async def test_read_missing_key_raises_oserror(monkeypatch):
    store = _make_tos(monkeypatch, FakeTosClient())
    with pytest.raises(OSError):
        await store.read_text("reports/nope/report.md")


async def test_put_failure_wraps_tos_exception(monkeypatch):
    from tos.exceptions import TosClientError

    class _Boom:
        def put_object(self, bucket, key, content=None):
            raise TosClientError("模拟网络故障")

    store = _make_tos(monkeypatch, _Boom())
    with pytest.raises(OSError):
        await store.save_bytes("recordings/r1.mp3", b"x")


async def test_presigned_url_get_with_expiry(monkeypatch):
    from tos import HttpMethodType

    client = FakeTosClient()
    store = _make_tos(monkeypatch, client)
    url = store.presigned_url("recordings/r1.mp3", expires=3600)
    # 真实验收修复：必须返回字符串 URL，且 http_method 传枚举（传字符串会 AttributeError）
    assert isinstance(url, str) and url == "https://fake.tos/recordings/r1.mp3"
    assert client.presigned == [(HttpMethodType.Http_Method_Get, "b1", "recordings/r1.mp3", 3600)]


async def test_local_file_storage_save_bytes_roundtrip(tmp_path):
    fs = LocalFileStorage(str(tmp_path / "data"))
    path = await fs.save_bytes("recordings/r1.mp3", b"x")
    with open(path, "rb") as f:
        assert f.read() == b"x"


async def test_get_file_store_returns_local_when_tos_unconfigured(monkeypatch):
    from services import storage as storage_pkg

    monkeypatch.setattr(storage_pkg, "_file_store", None)
    monkeypatch.setattr(settings, "TOS_ACCESS_KEY", None)
    store = storage_pkg.get_file_store()
    assert isinstance(store, LocalFileStorage)
    assert storage_pkg.get_file_store() is store  # 单例缓存


async def test_get_file_store_returns_tos_when_configured(monkeypatch):
    from services import storage as storage_pkg
    from services.storage import tos_storage

    monkeypatch.setattr(storage_pkg, "_file_store", None)
    for k, v in (
        ("TOS_ACCESS_KEY", "ak"), ("TOS_SECRET_KEY", "sk"),
        ("TOS_ENDPOINT", "https://tos-cn-beijing.volces.com"),
        ("TOS_REGION", "cn-beijing"), ("TOS_BUCKET", "b1"),
    ):
        monkeypatch.setattr(settings, k, v)
    monkeypatch.setattr(tos_storage, "TosClientV2", lambda *a, **kw: FakeTosClient())
    assert isinstance(storage_pkg.get_file_store(), TosFileStorage)


async def test_get_tos_store_returns_none_when_unconfigured(monkeypatch):
    from services import storage as storage_pkg

    monkeypatch.setattr(storage_pkg, "_file_store", None)
    monkeypatch.setattr(settings, "TOS_ACCESS_KEY", None)
    assert storage_pkg.get_tos_store() is None


async def test_export_md_legacy_local_path_falls_back(tmp_path, monkeypatch):
    # TOS 切换前的存量报告 md_path 为本地绝对路径：TOS 读失败时回落本地实现
    from fastapi import HTTPException

    from api import reports as reports_api

    class _FakeStorage:
        def __init__(self, reports):
            self._reports = reports

        async def report_get(self, user_id, rid):
            return self._reports.get(rid)

    class _TosFailing:
        async def read_text(self, relative_path):
            raise OSError("TOS 读取失败")

    class _Local(LocalFileStorage):
        def __init__(self, base_dir=None):
            super().__init__(str(tmp_path))

    md_path = str(tmp_path / "r1-report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# 报告")
    monkeypatch.setattr(reports_api, "storage", _FakeStorage({"r1": {"id": "r1", "md_path": md_path}}))
    monkeypatch.setattr(reports_api, "file_store", _TosFailing())
    monkeypatch.setattr(reports_api, "LocalFileStorage", _Local)
    resp = await reports_api.export_report_md("r1", {"id": "u1"})
    assert resp.body == "# 报告".encode("utf-8")


async def test_export_md_missing_file_returns_404(monkeypatch):
    from fastapi import HTTPException

    from api import reports as reports_api

    class _FakeStorage:
        async def report_get(self, user_id, rid):
            return {"id": "r1", "md_path": "reports/r1/report.md"}

    class _TosFailing:
        async def read_text(self, relative_path):
            raise OSError("TOS 读取失败")

    monkeypatch.setattr(reports_api, "storage", _FakeStorage())
    monkeypatch.setattr(reports_api, "file_store", _TosFailing())
    with pytest.raises(HTTPException) as ei:
        await reports_api.export_report_md("r1", {"id": "u1"})
    assert ei.value.status_code == 404
