"""P6 T7：录音 API 边界校验与状态查询。"""
import io

import pytest
from fastapi import HTTPException, UploadFile

from api import recording as rec_api
from services.asr_client import AsrError


TEST_USER = {"id": "u1", "username": "alice"}


def _upload(filename: str, data: bytes) -> UploadFile:
    return UploadFile(filename=filename, file=io.BytesIO(data))


class _FakeStorage:
    def __init__(self, rows=None):
        self.rows = rows or {}

    async def recording_get(self, user_id, rid):
        return self.rows.get(rid)


async def test_upload_rejects_bad_extension():
    with pytest.raises(HTTPException) as ei:
        await rec_api.upload_recording_file(_upload("a.txt", b"x"))
    assert ei.value.status_code == 422
    assert "mp3" in ei.value.detail


async def test_upload_rejects_empty_file():
    with pytest.raises(HTTPException) as ei:
        await rec_api.upload_recording_file(_upload("a.mp3", b""))
    assert ei.value.status_code == 422


async def test_upload_rejects_oversize(monkeypatch):
    monkeypatch.setattr(rec_api, "MAX_UPLOAD_BYTES", 10)
    with pytest.raises(HTTPException) as ei:
        await rec_api.upload_recording_file(_upload("a.mp3", b"x" * 11))
    assert ei.value.status_code == 413


async def test_upload_happy_path(monkeypatch):
    captured = {}

    async def fake_upload(user_id, filename, ext, raw, position):
        captured.update({"filename": filename, "ext": ext, "raw": raw, "position": position})
        return {"id": "r1", "status": "processing"}

    monkeypatch.setattr(rec_api, "upload_recording", fake_upload)
    # 走真实请求管线（TestClient）而非直接调用：position 缺省经 FastAPI 依赖注入
    # 解析为 DEFAULT_POSITION 字符串——直接调用端点时默认值是 Form() 元数据对象，
    # 无法验证 multipart 缺省语义（brief 原文断言在 fastapi==0.115.0 下必然失败）
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(rec_api.router)
    app.dependency_overrides[rec_api.get_current_user] = lambda: TEST_USER
    resp = TestClient(app).post(
        "/api/recording/upload",
        files={"file": ("a.wav", b"RIFF", "audio/wav")},
    )
    assert resp.status_code == 200
    assert resp.json() == {"recording_id": "r1", "status": "processing"}
    assert captured["filename"] == "a.wav" and captured["ext"] == "wav"
    assert captured["raw"] == b"RIFF" and captured["position"] == "真实面试录音"


async def test_upload_tos_unavailable_returns_503(monkeypatch):
    async def fake_upload(user_id, filename, ext, raw, position):
        raise RuntimeError("录音分析未配置：缺少 TOS_* 环境变量")

    monkeypatch.setattr(rec_api, "upload_recording", fake_upload)
    with pytest.raises(HTTPException) as ei:
        await rec_api.upload_recording_file(_upload("a.mp3", b"x"), user=TEST_USER)
    assert ei.value.status_code == 503
    assert "TOS" in ei.value.detail


async def test_upload_submit_failure_returns_502(monkeypatch):
    async def fake_upload(user_id, filename, ext, raw, position):
        raise AsrError("45000151", "音频格式不正确")

    monkeypatch.setattr(rec_api, "upload_recording", fake_upload)
    with pytest.raises(HTTPException) as ei:
        await rec_api.upload_recording_file(_upload("a.mp3", b"x"), user=TEST_USER)
    assert ei.value.status_code == 502


async def test_get_recording_unknown_returns_404(monkeypatch):
    monkeypatch.setattr(rec_api, "storage", _FakeStorage())
    with pytest.raises(HTTPException) as ei:
        await rec_api.get_recording("nope", TEST_USER)
    assert ei.value.status_code == 404


async def test_get_recording_status_mapping(monkeypatch):
    monkeypatch.setattr(rec_api, "storage", _FakeStorage({
        "r1": {"id": "r1", "status": "done", "report_id": "rep-1", "error": None},
        "r2": {"id": "r2", "status": "failed", "report_id": None, "error": "转写超时，请重试"},
    }))
    done = await rec_api.get_recording("r1", TEST_USER)
    assert done == {"recording_id": "r1", "status": "done", "report_id": "rep-1", "error": None}
    failed = await rec_api.get_recording("r2", TEST_USER)
    assert failed["status"] == "failed" and failed["error"] == "转写超时，请重试"
