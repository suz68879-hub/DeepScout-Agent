"""阶段推进纯函数（spec §2.3 状态机 + Ruling R1 兜底轮数）。

intro(1 轮) → technical(6 题) → deepdive(≤8 轮) → qa(≤2 轮) → finish。
纯函数、无 IO，planner_node（Task 13）与测试共用。
"""
DEEPDIVE_ROUND_CAP = 8   # R1：min(项目数×3, 8) 的封顶
TECHNICAL_ROUND_CAP = 6  # 技术题至少 6 道
QA_ROUND_CAP = 2         # 反问阶段兜底 2 轮

NEXT_STAGE = {"intro": "technical", "technical": "deepdive", "deepdive": "qa", "qa": "finish"}

# 需要 Planner 出题的阶段（intro 开场白、qa 反问、finish 收尾均不出题）
PLANNING_STAGES = ("deepdive", "technical")

# intro 自我介绍、qa 反问、finish 收尾均不评分（M4：intro 对着空题打分）
UNSCORED_STAGES = ("intro", "qa", "finish")


def maybe_advance_stage(stage: str, round_no: int, project_count: int = 1) -> str | None:
    """判断当前阶段是否推进；返回下一阶段名，不推进返回 None。

    intro：1 轮后必进 technical。
    deepdive：按项目数×3 与 R1 封顶 8 取小。
    technical：6 题后进 deepdive。
    qa：2 轮后进 finish。
    """
    cap = {
        "intro": 1,
        "deepdive": min(max(project_count, 1) * 3, DEEPDIVE_ROUND_CAP),
        "technical": TECHNICAL_ROUND_CAP,
        "qa": QA_ROUND_CAP,
    }.get(stage)
    if cap is None:
        return None  # finish 或未知阶段不推进
    if round_no >= cap:
        return NEXT_STAGE[stage]
    return None


def after_planner_route(stage: str) -> str:
    """planner 节点之后的条件边：finish → reporter；其余 → 等待下一轮回调。"""
    return "reporter" if stage == "finish" else "wait"
