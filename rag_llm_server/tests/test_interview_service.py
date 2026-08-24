"""interview_service 冷路径调度测试（Task 13 修复轮 R1 起；T15 将扩展本文件）。"""
import asyncio

import services.interview_service as isv


def test_welcome_message_proactively_starts_self_introduction_for_position():
    message = isv.build_welcome_message({"position": "Java 后端开发工程师"})
    assert "Java 后端开发工程师" in message
    assert "自我介绍" in message
    assert "课程顾问" not in message


async def test_serialized_cold_finally_keeps_newer_task(monkeypatch):
    """三方重叠竞态回归：旧任务的 finally 不得误删已被替换的 newer 条目（R-T13-4）。

    无条件 pop 时：task1 finally 会删掉 dict 中已被 task2 替换的条目，
    使后续回调的 await_pending_cold 落空 → 并发冷跑静默丢状态。
    """
    entered = asyncio.Event()
    release = asyncio.Event()

    async def fake_run_cold_path(session_id):
        entered.set()  # 包装任务已进入 _serialized_cold 函数体
        await release.wait()

    monkeypatch.setattr(isv, "run_cold_path", fake_run_cold_path)
    sid = "s-race"
    isv.schedule_cold_path(sid)
    wrapped = isv._cold_tasks.get(sid)
    await entered.wait()  # 确定性暂停在函数体内，打开重叠窗口
    newer = asyncio.current_task()  # 测试协程自身：模拟更晚到达的调度替换
    isv._cold_tasks[sid] = newer
    release.set()
    await wrapped  # 旧任务 finally 执行：身份守卫不得误删 newer
    assert isv._cold_tasks.get(sid) is newer
    isv._cold_tasks.pop(sid, None)  # 清理模块级字典，防跨测试污染


async def test_restore_state_initializes_all_14_fields(monkeypatch):
    """新会话 _restore_state 写入 InterviewState 全部 14 字段（R-T15-2 + P7 prompt_versions）。

    沿用 test_chat_callback 的消费方绑定 patch 模式：patch 使用方模块
    services.interview_service 的 graph / storage 绑定，不 patch 源模块属性。
    """
    import types

    import services.interview_service as isv

    class FakeGraph:
        """fake graph：aget_state 返回空 values 触发初始化，aupdate_state 落存。"""

        def __init__(self):
            self.saved: dict = {}

        async def aget_state(self, config):
            return types.SimpleNamespace(values=self.saved)

        async def aupdate_state(self, config, values):
            self.saved = values

    class FakeStorage:
        async def resume_get(self, user_id, resume_id):
            return None  # 简历缺失：structured 回落 {}

    fake_graph = FakeGraph()
    monkeypatch.setattr(isv, "get_graph", lambda: fake_graph)
    monkeypatch.setattr(isv, "storage", FakeStorage())

    state = await isv._restore_state(
        {"id": "s-restore", "user_id": "u1", "position": "Java后端", "resume_id": "r-1"}
    )

    expected = {
        "session_id", "position", "resume", "stage", "round_no", "stage_questions",
        "questions_asked", "current_question", "messages", "scores",
        "rag_context", "pending_user_text", "report", "prompt_versions",
    }
    assert set(state) == expected
    assert state["session_id"] == "s-restore"
    # P7：会话创建即固化提示词版本快照
    assert "interviewer:system" in state["prompt_versions"]
    assert state["stage"] == "intro" and state["round_no"] == 0
    assert state["report"] is None and state["stage_questions"] == []


def test_sse_chunk_format_is_openai_compatible():
    """SSE 增量行格式断言：RTC 兼容 chunk 结构与中文明文（R-T15-2）。"""
    import json
    import time

    from config import settings
    from services.interview_service import sse_chunk

    line = sse_chunk("面试官你好")
    assert line.startswith("data: ") and line.endswith("\n\n")
    chunk = json.loads(line[len("data: "):-2])
    assert chunk["object"] == "chat.completion.chunk"
    assert chunk["model"] == settings.ARK_ENDPOINT_ID
    assert chunk["id"].startswith("chatcmpl-") and len(chunk["id"]) == 17
    assert isinstance(chunk["created"], int)
    assert abs(chunk["created"] - int(time.time())) <= 5
    choices = chunk["choices"]
    assert len(choices) == 1
    assert choices[0]["index"] == 0
    assert choices[0]["finish_reason"] is None
    assert choices[0]["delta"] == {"content": "面试官你好"}  # 中文不被 \u 转义
