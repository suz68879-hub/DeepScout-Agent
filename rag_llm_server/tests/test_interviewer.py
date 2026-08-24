"""Interviewer：消息拼装、窗口裁剪与流式错误话术。"""
import pytest

from agents.interviewer import (
    ERROR_REPLY, WINDOW_SIZE, build_system_messages, generate_stream, trim_window,
)


class FakeLLM:
    """可编程假 LLM：astream 产出给定 chunks，或按 fail=True 抛异常。"""

    def __init__(self, chunks=("你好", "，请介绍自己"), fail=False):
        self.chunks = chunks
        self.fail = fail
        self.messages_seen = None

    async def astream(self, msgs):
        self.messages_seen = msgs
        if self.fail:
            raise RuntimeError("模拟 LLM 故障")
        for c in self.chunks:
            yield type("Chunk", (), {"content": c})()


def _state(**over):
    s = {
        "session_id": "s1", "position": "Java后端", "stage": "deepdive",
        "resume": {"skills": [{"name": "Java"}], "projects": [{"name": "秒杀系统"}]},
        "current_question": {"question_text": "讲讲你的项目难点"},
        "messages": [],
    }
    s.update(over)
    return s


def test_build_system_messages_contains_position_resume_question():
    msgs = build_system_messages(_state())
    content = msgs[0].content
    assert "Java后端" in content
    assert "秒杀系统" in content
    assert "讲讲你的项目难点" in content
    assert "项目深挖" in content  # 阶段指令注入


def test_build_system_messages_without_resume():
    content = build_system_messages(_state(resume=None))[0].content
    assert "尚未上传简历" in content


def test_intro_treats_user_text_as_self_intro_and_starts_resume_technical_question():
    content = build_system_messages(_state(stage="intro", current_question=None))[0].content
    assert "不要再次要求候选人自我介绍" in content
    assert "简历" in content and "技术基础" in content


def test_build_system_messages_pinned_v1_differs_from_latest():
    # P7：会话固化 1.0.0 时渲染 v1（无"提问约束"段）；默认取最新 v2（含）
    v1 = build_system_messages(
        _state(prompt_versions={"interviewer:system": "1.0.0"})
    )[0].content
    latest = build_system_messages(_state())[0].content
    assert "提问约束" not in v1
    assert "提问约束" in latest


def test_resume_brief_contains_project_details():
    # P7：简历要点含项目细节（难点/成果），供深挖锚定
    rich = {
        "skills": [{"name": "Java", "level": "熟练"}],
        "projects": [{
            "name": "秒杀系统", "tech_stack": ["Spring", "Redis"],
            "responsibilities": "库存扣减", "challenges": "超卖问题",
            "results": "压测 QPS 1 万",
        }],
    }
    content = build_system_messages(_state(resume=rich))[0].content
    assert "超卖问题" in content and "压测 QPS 1 万" in content


def test_current_question_joins_follow_up_hints():
    # P7：current_question 带 follow_up_hints 时拼入提示，供深挖承接
    q = {"question_text": "讲讲秒杀系统的库存方案", "follow_up_hints": ["对比 Redis 预扣", "讲讲扣减一致性"]}
    content = build_system_messages(_state(current_question=q))[0].content
    assert "对比 Redis 预扣" in content and "扣减一致性" in content


def test_trim_window_keeps_last_ten():
    msgs = [{"role": "user", "content": str(i)} for i in range(15)]
    kept = trim_window(msgs)
    assert len(kept) == WINDOW_SIZE
    assert kept[0]["content"] == "5" and kept[-1]["content"] == "14"


def test_generate_stream_yields_chunks_in_order():
    llm = FakeLLM(chunks=("你", "好"))

    async def run():
        return [t async for t in generate_stream(_state(), "我准备好了", llm)]

    import asyncio
    assert asyncio.run(run()) == ["你", "好"]
    # 校验发往 LLM 的消息：system 在最前，末尾是本轮用户输入
    assert llm.messages_seen[0].type == "system"
    assert llm.messages_seen[-1].content == "我准备好了"


def test_generate_stream_error_yields_error_reply():
    llm = FakeLLM(fail=True)

    async def run():
        return [t async for t in generate_stream(_state(), "你好", llm)]

    import asyncio
    result = asyncio.run(run())
    assert result == [ERROR_REPLY]
    assert "问题" in ERROR_REPLY


def test_generate_stream_assembly_error_yields_error_reply():
    llm = FakeLLM()
    bad_state = _state()
    bad_state["messages"] = [{"role": "user"}]  # 缺 content，组装时 KeyError

    async def run():
        return [t async for t in generate_stream(bad_state, "你好", llm)]

    import asyncio
    assert asyncio.run(run()) == [ERROR_REPLY]


def test_generate_stream_empty_stream_yields_error_reply():
    llm = FakeLLM(chunks=())

    async def run():
        return [t async for t in generate_stream(_state(), "你好", llm)]

    import asyncio
    assert asyncio.run(run()) == [ERROR_REPLY]
