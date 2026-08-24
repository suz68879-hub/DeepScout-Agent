"""chat_callback 热路径：SSE 格式、会话自动创建与图状态恢复。"""
import json

from services.interview_service import get_active_session, session_config, sse_chunk


def test_sse_chunk_shape_is_openai_compatible():
    line = sse_chunk("你好")
    assert line.startswith("data: ") and line.endswith("\n\n")
    chunk = json.loads(line[len("data: "):-2])
    assert chunk["object"] == "chat.completion.chunk"
    assert chunk["choices"][0]["delta"]["content"] == "你好"
    assert chunk["choices"][0]["finish_reason"] is None
    assert chunk["choices"][0]["index"] == 0


def test_session_config_thread_id_is_session_id():
    assert session_config("s-123") == {"configurable": {"thread_id": "s-123"}}


async def test_get_active_session_autocreates_when_none(tmp_path, monkeypatch):
    # R-T7-1：interview_service 经 from-import 绑定 storage，patch 消费方绑定
    import services.interview_service as isv
    from services.storage.sqlite import SqliteStorage

    s = SqliteStorage(str(tmp_path / "auto.db"))
    await s.init()
    user_id = (await s.user_create("callback-user", "hash"))["id"]
    monkeypatch.setattr(isv, "storage", s)

    session = await get_active_session(user_id)
    assert session["status"] == "running" and session["stage"] == "intro"
    assert session["position"] == "Java后端"
    # 再次调用命中同一 running 会话（单用户 MVP）
    again = await get_active_session(user_id)
    assert again["id"] == session["id"]
    await s.close()


async def test_get_active_session_uses_resume_position(tmp_path, monkeypatch):
    # R-T7-1：同上，patch 消费方绑定 services.interview_service.storage
    import services.interview_service as isv
    from services.storage.sqlite import SqliteStorage

    s = SqliteStorage(str(tmp_path / "pos.db"))
    await s.init()
    user_id = (await s.user_create("position-user", "hash"))["id"]
    await s.resume_create(user_id, {
        "content": "x",
        "structured_json": json.dumps({"position_target": "AI Agent 开发"}),
        "source": "md", "status": "ready",
    })
    monkeypatch.setattr(isv, "storage", s)

    session = await get_active_session(user_id)
    assert session["position"] == "AI Agent 开发"
    assert session["resume_id"] is not None
    await s.close()
