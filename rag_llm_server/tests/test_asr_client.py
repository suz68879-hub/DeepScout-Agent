"""P6 T4：识别客户端单测——请求构造 / 状态码语义 / 转写解析（不触真实服务）。"""
import pytest

from services import asr_client
from services.asr_client import AsrError, parse_transcript, query_asr, submit_asr


class _FakeResponse:
    def __init__(self, status_code: str, body: bytes = b"{}"):
        self.headers = {"X-Api-Status-Code": status_code, "X-Api-Message": "OK"}
        self._body = body

    def json(self):
        import json

        return json.loads(self._body)


class _FakeClient:
    def __init__(self, responder):
        self._responder = responder
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self._responder(self.calls[-1])


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setattr(asr_client.settings, "ASR_FILE_API_KEY", "test-key")


async def test_submit_builds_request_and_returns_task_id(monkeypatch, api_key):
    captured = {}

    def responder(call):
        captured.update(call)
        return _FakeResponse("20000000")

    monkeypatch.setattr(asr_client.httpx, "AsyncClient", lambda **kw: _FakeClient(responder))
    task_id = await submit_asr("https://fake.tos/recording.mp3", "mp3")
    assert task_id
    assert captured["url"] == "https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit"
    assert captured["headers"]["X-Api-Key"] == "test-key"
    assert captured["headers"]["X-Api-Resource-Id"] == "volc.seedasr.auc"
    assert captured["headers"]["X-Api-Request-Id"] == task_id
    assert captured["headers"]["X-Api-Sequence"] == "-1"
    body = captured["json"]
    assert body["audio"] == {"format": "mp3", "url": "https://fake.tos/recording.mp3", "language": "zh-CN"}
    assert body["request"]["model_name"] == "bigmodel"
    assert body["request"]["enable_speaker_info"] is True
    assert body["request"]["ssd_version"] == "200"
    assert body["request"]["show_utterances"] is True


async def test_submit_reuses_caller_supplied_idempotency_anchor(monkeypatch, api_key):
    captured = {}

    def responder(call):
        captured.update(call)
        return _FakeResponse("20000000")

    monkeypatch.setattr(asr_client.httpx, "AsyncClient", lambda **kw: _FakeClient(responder))
    task_id = await submit_asr(
        "https://fake.tos/recording.wav", "wav", task_id="fixed-task-id"
    )

    assert task_id == "fixed-task-id"
    assert captured["headers"]["X-Api-Request-Id"] == "fixed-task-id"
    assert captured["json"]["user"]["uid"] == "fixed-task-id"


async def test_submit_raises_asr_error_on_failure_status(monkeypatch, api_key):
    class _Resp:
        def __init__(self):
            self.headers = {"X-Api-Status-Code": "45000151", "X-Api-Message": "音频格式不正确"}

    monkeypatch.setattr(asr_client.httpx, "AsyncClient", lambda **kw: _FakeClient(lambda call: _Resp()))
    with pytest.raises(AsrError) as ei:
        await submit_asr("https://fake.tos/r.ogg", "ogg")
    assert ei.value.status_code == "45000151"
    assert "音频格式不正确" in ei.value.message


async def test_submit_requires_api_key(monkeypatch):
    monkeypatch.setattr(asr_client.settings, "ASR_FILE_API_KEY", None)
    with pytest.raises(ValueError):
        await submit_asr("https://fake.tos/r.mp3", "mp3")


async def test_query_returns_none_while_processing(monkeypatch, api_key):
    calls = {"n": 0}

    def responder(call):
        calls["n"] += 1
        status = "20000001" if calls["n"] == 1 else "20000002"
        return _FakeResponse(status)

    monkeypatch.setattr(asr_client.httpx, "AsyncClient", lambda **kw: _FakeClient(responder))
    assert await query_asr("t-1") is None
    assert await query_asr("t-1") is None


async def test_query_returns_payload_when_done(monkeypatch, api_key):
    payload = {"audio_info": {"duration": 10}, "result": {"text": "整体"}}
    monkeypatch.setattr(
        asr_client.httpx, "AsyncClient",
        lambda **kw: _FakeClient(lambda call: _FakeResponse("20000000", body=b'{"audio_info": {"duration": 10}, "result": {"text": "\xe6\x95\xb4\xe4\xbd\x93"}}')),
    )
    got = await query_asr("t-1")
    assert got == payload


async def test_query_raises_asr_error_on_quiet_audio(monkeypatch, api_key):
    monkeypatch.setattr(
        asr_client.httpx, "AsyncClient",
        lambda **kw: _FakeClient(lambda call: _FakeResponse("20000003")),
    )
    with pytest.raises(AsrError) as ei:
        await query_asr("t-1")
    assert ei.value.status_code == "20000003"


async def test_query_uses_same_task_id_as_submit(monkeypatch, api_key):
    captured = {}

    def responder(call):
        captured.update(call)
        return _FakeResponse("20000000")

    monkeypatch.setattr(asr_client.httpx, "AsyncClient", lambda **kw: _FakeClient(responder))
    await query_asr("task-42")
    assert captured["url"] == "https://openspeech.bytedance.com/api/v3/auc/bigmodel/query"
    assert captured["headers"]["X-Api-Request-Id"] == "task-42"


async def test_parse_transcript_extracts_speaker_and_times():
    payload = {
        "result": {
            "text": "整体",
            "utterances": [
                {"text": " 请做自我介绍 ", "start_time": 0, "end_time": 1000,
                 "additions": {"speaker": "0"}},
                {"text": "我是张三", "start_time": 1000, "end_time": 5000,
                 "additions": {"speaker": "1"}},
                {"text": "   "},
            ],
        }
    }
    segs = parse_transcript(payload)
    assert segs == [
        {"speaker": "0", "start_ms": 0, "end_ms": 1000, "text": "请做自我介绍"},
        {"speaker": "1", "start_ms": 1000, "end_ms": 5000, "text": "我是张三"},
    ]


async def test_parse_transcript_falls_back_when_no_speaker_info():
    payload = {"result": {"utterances": [{"text": "无标签", "start_time": 0, "end_time": 100}]}}
    assert parse_transcript(payload) == [
        {"speaker": "0", "start_ms": 0, "end_ms": 100, "text": "无标签"}
    ]
