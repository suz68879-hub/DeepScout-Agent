"""阶段推进纯函数测试（spec §2.3 状态机 + Ruling R1 轮数）。"""
from agents.stage_flow import (
    DEEPDIVE_ROUND_CAP, QA_ROUND_CAP, TECHNICAL_ROUND_CAP, UNSCORED_STAGES,
    after_planner_route, maybe_advance_stage,
)


def test_intro_qa_finish_are_unscored():
    assert UNSCORED_STAGES == ("intro", "qa", "finish")


def test_intro_advances_after_one_round():
    assert maybe_advance_stage("intro", 0, 2) is None
    assert maybe_advance_stage("intro", 1, 2) == "technical"


def test_deepdive_cap_is_min_of_projects_times_3_and_8():
    # 1 个项目 → 3 轮即推进
    assert maybe_advance_stage("deepdive", 2, 1) is None
    assert maybe_advance_stage("deepdive", 3, 1) == "qa"
    # 5 个项目 → 15 封顶到 8
    assert maybe_advance_stage("deepdive", 7, 5) is None
    assert maybe_advance_stage("deepdive", 8, 5) == "qa"
    assert DEEPDIVE_ROUND_CAP == 8


def test_technical_advances_after_six_questions():
    assert maybe_advance_stage("technical", 5, 1) is None
    assert maybe_advance_stage("technical", TECHNICAL_ROUND_CAP, 1) == "deepdive"


def test_qa_advances_after_two_rounds():
    assert maybe_advance_stage("qa", 1, 1) is None
    assert maybe_advance_stage("qa", QA_ROUND_CAP, 1) == "finish"


def test_finish_never_advances():
    assert maybe_advance_stage("finish", 0, 1) is None
    assert maybe_advance_stage("finish", 99, 1) is None


def test_after_planner_route():
    assert after_planner_route("technical") == "wait"
    assert after_planner_route("finish") == "reporter"
