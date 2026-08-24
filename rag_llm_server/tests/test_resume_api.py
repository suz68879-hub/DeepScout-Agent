"""简历上传 API 硬化测试（R-T11-2）：大小上限、魔数校验、损坏文件错误语义。"""
import io

import pytest
from fastapi import HTTPException, UploadFile

from api import resume as resume_api


TEST_USER = {"id": "u1", "username": "alice"}


def _upload(data: bytes) -> UploadFile:
    return UploadFile(filename="t.pdf", file=io.BytesIO(data))


class FakeStorage:
    """落库/回写桩（不触真实 SQLite）。"""

    def __init__(self):
        self.created = None
        self.updated = None

    async def resume_create(self, user_id, resume):
        self.created = dict(resume)
        return self.created

    async def resume_update(self, user_id, resume_id, patch):
        self.updated = dict(patch)
        return {**self.created, **patch}


async def test_upload_over_limit_returns_413(monkeypatch):
    monkeypatch.setattr(resume_api, "MAX_UPLOAD_BYTES", 100)
    with pytest.raises(HTTPException) as ei:
        await resume_api.upload_resume_pdf(_upload(b"%PDF-1.4" + b"x" * 200))
    assert ei.value.status_code == 413


async def test_upload_not_pdf_returns_422():
    with pytest.raises(HTTPException) as ei:
        await resume_api.upload_resume_pdf(_upload(b"hello world"))
    assert ei.value.status_code == 422


async def test_upload_corrupt_pdf_returns_400_without_leaking_details(monkeypatch):
    fs = FakeStorage()
    monkeypatch.setattr(resume_api, "storage", fs)
    # from-import 绑定陷阱：patch 消费方 api.resume 持有的 parse_pdf 绑定
    async def boom(_path, _llm):
        raise RuntimeError("secret-E:\\tmp\\xyz.pdf")

    monkeypatch.setattr(resume_api, "parse_pdf", boom)
    # get_agent_llm 为函数内局部 import，patch 源模块即可
    monkeypatch.setattr("services.agent_llm.get_agent_llm", lambda agent: object())
    monkeypatch.setattr("services.storage.get_tos_store", lambda: None)  # P6：隔离真实 TOS

    with pytest.raises(HTTPException) as ei:
        await resume_api.upload_resume_pdf(_upload(b"%PDF-1.4 garbage"), TEST_USER)
    assert ei.value.status_code == 400
    assert ei.value.detail == "resume parsing failed"
    assert fs.updated == {"status": "failed"}


async def test_upload_pdf_persists_raw_to_tos(monkeypatch):
    # P6（spec §12.1）：TOS 已配置时原文件持久化，key 与 resume 行 id 一致
    fs = FakeStorage()
    monkeypatch.setattr(resume_api, "storage", fs)

    class _FakeTos:
        def __init__(self):
            self.saved = {}

        async def save_bytes(self, key, content):
            self.saved[key] = content
            return key

    tos = _FakeTos()
    # get_tos_store 为函数内局部 import：patch 源模块
    monkeypatch.setattr("services.storage.get_tos_store", lambda: tos)

    async def fake_parse(path, llm):
        class _Structured:
            def model_dump_json(self):
                return '{"skills": []}'

        return _Structured()

    monkeypatch.setattr(resume_api, "parse_pdf", fake_parse)
    monkeypatch.setattr("services.agent_llm.get_agent_llm", lambda agent: object())
    res = await resume_api.upload_resume_pdf(_upload(b"%PDF-1.4 fake"), TEST_USER)
    assert res["status"] == "ready"
    assert len(tos.saved) == 1
    key = next(iter(tos.saved))
    assert key.startswith("users/u1/resumes/") and key.endswith(".pdf")
    assert tos.saved[key] == b"%PDF-1.4 fake"
    assert fs.updated == {"structured_json": '{"skills": []}', "status": "ready"}


async def test_upload_pdf_tos_failure_returns_503_without_row(monkeypatch):
    fs = FakeStorage()
    monkeypatch.setattr(resume_api, "storage", fs)

    class _FailingTos:
        async def save_bytes(self, key, content):
            raise OSError("TOS 上传失败")

    monkeypatch.setattr("services.storage.get_tos_store", lambda: _FailingTos())
    with pytest.raises(HTTPException) as ei:
        await resume_api.upload_resume_pdf(_upload(b"%PDF-1.4 fake"), TEST_USER)
    assert ei.value.status_code == 503
    assert fs.created is None
