"""interview_service 热路径与持久冷任务调度测试。"""
import os
import inspect
import uuid

import pytest
from dotenv import load_dotenv
from sqlalchemy import delete, func, select

import services.interview_service as isv
from config import Config
from db.engine import build_database_runtime
from db.models import AppUser, BackgroundJob, InterviewSession, OutboxEvent
from services.jobs.types import JobStatus


def test_welcome_message_asks_for_readiness_before_self_introduction():
    message = isv.build_welcome_message({"position": "Java 后端开发工程师"})
    assert "Java 后端开发工程师" in message
    assert "准备好了吗" in message
    assert "自我介绍" not in message
    assert "课程顾问" not in message


@pytest.mark.parametrize("user_text", ["我准备好了", "准备好了，开始吧", "可以开始", "ready"])
def test_readiness_gate_invites_confirmed_candidate_to_self_introduction(user_text):
    reply = isv.readiness_gate_reply(
        {"stage": "intro", "round_no": 0, "messages": []}, user_text,
    )
    assert reply is not None
    assert "一分钟" in reply
    assert "自我介绍" in reply


@pytest.mark.parametrize("user_text", ["还没准备好", "等一下", "稍等一下"])
def test_readiness_gate_waits_for_unready_candidate(user_text):
    reply = isv.readiness_gate_reply(
        {"stage": "intro", "round_no": 0, "messages": []}, user_text,
    )
    assert reply is not None
    assert "准备好" in reply
    assert "自我介绍" not in reply


def test_readiness_gate_releases_after_self_introduction_prompt():
    reply = isv.readiness_gate_reply(
        {
            "stage": "intro",
            "round_no": 0,
            "messages": [{"role": "assistant", "content": isv.SELF_INTRO_PROMPT}],
        },
        "我叫小王，有三年后端开发经验",
    )
    assert reply is None


@pytest.fixture
async def interview_job_runtime(monkeypatch):
    load_dotenv()
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL is required for interview job tests")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    runtime = build_database_runtime(Config())
    await runtime.start()
    owner_id = uuid.uuid4()
    session_id = uuid.uuid4()
    async with runtime.session_scope() as session:
        session.add(
            AppUser(
                id=owner_id,
                username=f"interview_job_{owner_id.hex}",
                password_hash="test-only",
            )
        )
        session.add(
            InterviewSession(
                id=session_id,
                user_id=owner_id,
                position="Java后端",
                stage="technical",
                status="running",
                rtc_room_id=f"room-{session_id}",
                rtc_user_id=f"user-{session_id}",
                rtc_task_id=f"task-{session_id}",
                rtc_callback_id=f"callback-{session_id}",
            )
        )
    try:
        yield runtime, owner_id, session_id
    finally:
        async with runtime.session_scope() as session:
            await session.execute(delete(AppUser).where(AppUser.id == owner_id))
        await runtime.close()


async def test_enqueue_cold_path_is_idempotent_per_checkpoint_trigger(
    interview_job_runtime,
):
    runtime, owner_id, session_id = interview_job_runtime
    async with runtime.session_scope() as session:
        first = await isv.enqueue_cold_path(
            session,
            owner_id=owner_id,
            session_id=session_id,
            trigger_id=2,
        )
        repeated = await isv.enqueue_cold_path(
            session,
            owner_id=owner_id,
            session_id=session_id,
            trigger_id=2,
        )
        next_round = await isv.enqueue_cold_path(
            session,
            owner_id=owner_id,
            session_id=session_id,
            trigger_id=4,
        )

    assert repeated.id == first.id
    assert next_round.id != first.id
    assert first.payload_ref == {
        "schema_version": 1,
        "session_id": str(session_id),
        "step": "round",
    }
    async with runtime.session_scope() as session:
        assert await session.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(BackgroundJob.owner_id == owner_id)
        ) == 2
        assert await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .join(BackgroundJob, BackgroundJob.id == OutboxEvent.aggregate_id)
            .where(BackgroundJob.owner_id == owner_id)
        ) == 2


@pytest.mark.parametrize("trigger_id", [0, -1, "messages"])
async def test_enqueue_cold_path_rejects_invalid_trigger_before_database(trigger_id):
    with pytest.raises(isv.ColdPathStateError, match="INVALID_COLD_PATH_TRIGGER"):
        await isv.enqueue_cold_path(
            None,
            owner_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            trigger_id=trigger_id,
        )


async def test_enqueue_cold_path_rejects_cross_owner_session(interview_job_runtime):
    runtime, _, session_id = interview_job_runtime
    async with runtime.session_scope() as session:
        with pytest.raises(isv.ColdPathStateError, match="INTERVIEW_SESSION_NOT_FOUND"):
            await isv.enqueue_cold_path(
                session,
                owner_id=uuid.uuid4(),
                session_id=session_id,
                trigger_id=2,
            )


async def test_enqueue_finish_job_returns_same_persisted_job_for_session(
    interview_job_runtime,
):
    runtime, owner_id, session_id = interview_job_runtime
    async with runtime.session_scope() as session:
        first = await isv.enqueue_finish_job(
            session, owner_id=owner_id, session_id=session_id
        )
        repeated = await isv.enqueue_finish_job(
            session, owner_id=owner_id, session_id=session_id
        )

    assert repeated.id == first.id
    assert first.payload_ref == {
        "schema_version": 1,
        "session_id": str(session_id),
        "step": "finish",
    }
    async with runtime.session_scope() as session:
        assert await session.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(BackgroundJob.id == first.id)
        ) == 1
        assert await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.aggregate_id == first.id)
        ) == 1


def test_interview_service_has_no_process_local_cold_task_registry():
    assert not hasattr(isv, "_cold_tasks")
    assert not hasattr(isv, "shutdown_cold_tasks")
    assert "asyncio.create_task" not in inspect.getsource(isv)


async def test_await_pending_cold_observes_persisted_terminal_state(monkeypatch):
    states = iter([JobStatus.PENDING, JobStatus.RUNNING, JobStatus.SUCCEEDED])
    sleeps = []

    async def latest(_session_id):
        return type("Job", (), {"status": next(states)})()

    async def no_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(isv, "_latest_cold_job", latest, raising=False)
    monkeypatch.setattr(isv.asyncio, "sleep", no_sleep)

    await isv.await_pending_cold("session-1", timeout=1, poll_interval=0.01)

    assert sleeps == [0.01, 0.01]


@pytest.mark.parametrize("status", [JobStatus.FAILED, JobStatus.CANCELLED])
async def test_await_pending_cold_rejects_failed_predecessor(monkeypatch, status):
    async def latest(_session_id):
        return type("Job", (), {"status": status})()

    monkeypatch.setattr(isv, "_latest_cold_job", latest, raising=False)

    with pytest.raises(isv.ColdPathStateError, match="PREVIOUS_COLD_PATH_FAILED"):
        await isv.await_pending_cold("session-1", timeout=0)


async def test_await_pending_cold_times_out_while_predecessor_still_running(monkeypatch):
    async def latest(_session_id):
        return type("Job", (), {"status": JobStatus.RUNNING})()

    monkeypatch.setattr(isv, "_latest_cold_job", latest, raising=False)

    with pytest.raises(isv.ColdPathStateError, match="PREVIOUS_COLD_PATH_PENDING"):
        await isv.await_pending_cold("session-1", timeout=0)


def test_hot_path_cold_wait_is_bounded_to_five_seconds():
    assert isv.HOT_PATH_COLD_WAIT_SECONDS == 5.0


async def test_run_cold_path_rereads_session_and_checkpoint_before_each_step(
    monkeypatch,
):
    session_reads = []
    graph_reads = []
    invokes = []
    versions = iter([1, 2, 3])

    class FakeStorage:
        async def session_get_internal(self, session_id):
            session_reads.append(session_id)
            return {
                "id": session_id,
                "user_id": "11111111-1111-1111-1111-111111111111",
                "stage": "technical",
                "version": next(versions),
            }

        async def session_update(
            self, user_id, session_id, patch, expected_version=None
        ):
            return {
                "id": session_id,
                "user_id": user_id,
                "stage": patch["stage"],
                "version": expected_version + 1,
            }

    class FakeGraph:
        nodes = [("evaluator",), ("planner",), ()]
        current = 0

        async def aget_state(self, _config):
            graph_reads.append(True)
            return type("Snapshot", (), {
                "values": {
                    "session_id": "22222222-2222-2222-2222-222222222222",
                    "stage": "technical",
                    "report": None,
                },
                "next": self.nodes[self.current],
            })()

        async def ainvoke(self, _input, _config):
            invokes.append(True)
            self.current += 1

    job = type("Job", (), {
        "id": uuid.uuid4(),
        "owner_id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
        "payload_ref": {
            "session_id": "22222222-2222-2222-2222-222222222222"
        },
    })()
    monkeypatch.setattr(isv, "storage", FakeStorage())
    monkeypatch.setattr(isv, "get_graph", lambda: FakeGraph())

    result = await isv.run_cold_path(job)

    assert result == {
        "schema_version": 1,
        "session_id": "22222222-2222-2222-2222-222222222222",
    }
    assert len(session_reads) == 3
    assert len(invokes) == 2
    assert len(graph_reads) == 5


async def test_run_cold_path_reconciles_stage_after_checkpoint_only_crash(
    monkeypatch,
):
    updates = []
    session_id = "22222222-2222-2222-2222-222222222222"
    owner_id = uuid.UUID("11111111-1111-1111-1111-111111111111")

    class FakeStorage:
        async def session_get_internal(self, _session_id):
            return {
                "id": session_id,
                "user_id": str(owner_id),
                "stage": "technical",
                "version": 7,
            }

        async def session_update(
            self, user_id, _session_id, patch, expected_version=None
        ):
            updates.append((user_id, patch, expected_version))
            return {
                "id": session_id,
                "user_id": user_id,
                "stage": patch["stage"],
                "version": expected_version + 1,
            }

    class FakeGraph:
        async def aget_state(self, _config):
            return type("Snapshot", (), {
                "values": {
                    "session_id": session_id,
                    "stage": "deepdive",
                    "report": None,
                },
                "next": (),
            })()

    job = type("Job", (), {
        "id": uuid.uuid4(),
        "owner_id": owner_id,
        "payload_ref": {"session_id": session_id},
    })()
    monkeypatch.setattr(isv, "storage", FakeStorage())
    monkeypatch.setattr(isv, "get_graph", lambda: FakeGraph())

    await isv.run_cold_path(job)

    assert updates == [(str(owner_id), {"stage": "deepdive"}, 7)]


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
