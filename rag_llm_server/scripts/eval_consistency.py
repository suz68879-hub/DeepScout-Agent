"""提示词质量验收脚本（spec §1.3 成功标准 / §8 / P7 提示词工程）。

三种评测（用法，rag_llm_server 目录下）：
    uv run python scripts/eval_consistency.py                    # 默认 evaluator：评分一致性 ±1
    uv run python scripts/eval_consistency.py --eval interviewer # interviewer 规则（简历锚定/单问/短句/无评价）
    uv run python scripts/eval_consistency.py --eval planner     # planner 简历锚定（题目须含简历项目/技能 token）
"""
import argparse
import asyncio
import sys
from pathlib import Path

# 环境适配（偏离 brief，报告注明）：python scripts/xxx.py 执行时 sys.path[0]
# 为 scripts/ 目录，不含仓库根，agents/services 导入失败；项目无包安装配置，
# 故在此将仓库根插入 sys.path。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.evaluator import DIMENSIONS, evaluate_round
from services.agent_llm import get_agent_llm

N = 10
ACCEPT_SPREAD = 1  # 波动 ≤ ±1（spec §8）

SAMPLE = {
    "position": "Java后端",
    "question": "谈谈你对 JVM 内存模型与垃圾回收的理解。",
    "reference_points": ["运行时数据区划分", "分代收集", "G1 收集器"],
    "answer": (
        "JVM 运行时数据区分为堆、栈、方法区、程序计数器等。对象主要分配在堆上，"
        "垃圾回收采用分代收集，年轻代用标记复制，老年代用标记整理。"
        "我在项目中通过调整 G1 的停顿时间目标解决了 Full GC 频繁的问题。"
    ),
}

# interviewer/planner 评测共用简历 fixture（含项目细节，供锚定判定）
RESUME_FIXTURE = {
    "skills": [{"name": "Java", "level": "熟练"}],
    "projects": [{
        "name": "秒杀系统", "tech_stack": ["Spring", "Redis"],
        "responsibilities": "库存扣减", "challenges": "超卖问题",
        "results": "压测 QPS 1 万",
    }],
}

MAX_REPLY_LEN = 60  # interviewer 说话规则"2 句话以内（约 40 字）"的执行区间（40±20，容忍边界波动）
MARKDOWN_CHARS = ("#", "*", "`", "|", ">")
BANNED_EVALUATIVE = ("回答正确", "不错", "很好", "答得", "很棒", "优秀")


def check_interviewer_reply(text: str, project_names: list[str]) -> list[str]:
    """校验 interviewer 回复是否符合 v2 说话规则；返回违规列表（空 = 通过）。

    规则（P7 v2 模板）：短句 ≤40 字 / 无 Markdown / 以问号结尾（单问）/
    引用简历项目名 / 无评价语。
    """
    violations = []
    if not text:
        return ["空回复"]
    if len(text) > MAX_REPLY_LEN:
        violations.append(f"超长（{len(text)} 字，要求 ≤{MAX_REPLY_LEN}）")
    if any(c in text for c in MARKDOWN_CHARS):
        violations.append("含 Markdown 符号")
    if not text.rstrip().endswith(("？", "?")):
        violations.append("未以问号结尾（单问）")
    if project_names and not any(p and p in text for p in project_names):
        violations.append("未引用简历项目名")
    for w in BANNED_EVALUATIVE:
        if w in text:
            violations.append(f"含评价语「{w}」")
    return violations


async def run(n: int = N, llm=None) -> list[dict]:
    """跑 n 次评分，返回成功评分列表。"""
    llm = llm or get_agent_llm("evaluator")
    results = []
    for i in range(n):
        r = await evaluate_round(
            SAMPLE["position"], SAMPLE["question"], SAMPLE["reference_points"], SAMPLE["answer"], llm=llm
        )
        results.append(r)
        print(f"[EVAL] 第 {i + 1}/{n} 次: {r.get('overall_score', 'FAILED')}")
    return [r for r in results if r.get("status") != "failed"]


def summarize(ok: list[dict], accept_spread: int = ACCEPT_SPREAD) -> bool:
    """打印波动统计并返回是否通过；成功数不足 2 直接失败。"""
    print(f"[EVAL] 成功评分 {len(ok)}")
    if len(ok) < 2:
        print("[EVAL] FAIL: 成功样本不足，无法验收")
        return False
    worst = 0
    for d in DIMENSIONS:
        vals = [r["dimensions"][d]["score"] for r in ok]
        spread = max(vals) - min(vals)
        worst = max(worst, spread)
        print(f"[EVAL] {d}: min={min(vals)} max={max(vals)} 波动={spread}"
              f" [{'PASS' if spread <= accept_spread else 'FAIL'}]")
    overall = [r["overall_score"] for r in ok]
    o_spread = max(overall) - min(overall)
    print(f"[EVAL] 总分: min={min(overall)} max={max(overall)} 波动={o_spread}"
          f" [{'PASS' if o_spread <= accept_spread else 'FAIL'}]")
    return worst <= accept_spread and o_spread <= accept_spread


async def eval_interviewer_rules(n: int = 5, llm=None) -> bool:
    """interviewer 规则评测（真实 LLM N 轮）：deepdive 阶段回复须满足 v2 说话规则。"""
    from agents.interviewer import build_system_messages

    llm = llm or get_agent_llm("interviewer")
    state = {
        "position": "Java后端",
        "stage": "deepdive",
        "resume": RESUME_FIXTURE,
        "current_question": {
            "question_text": "你简历里的秒杀系统，库存是怎么扣的？",
            "follow_up_hints": ["讲讲扣减一致性"],
        },
        "messages": [],
    }
    msgs = build_system_messages(state)
    project_names = [p["name"] for p in RESUME_FIXTURE["projects"]]
    failed = 0
    for i in range(n):
        resp = await llm.ainvoke(msgs)
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        violations = check_interviewer_reply(text, project_names)
        if violations:
            failed += 1
            print(f"[EVAL] interviewer 第 {i + 1}/{n}: FAIL: {'; '.join(violations)}")
            print(f"      原文：{text}")
        else:
            print(f"[EVAL] interviewer 第 {i + 1}/{n}: PASS: {text}")
    print(f"[EVAL] interviewer 通过 {n - failed}/{n}")
    return failed == 0


async def eval_planner_resume_anchor(n: int = 5, llm=None) -> bool:
    """planner 简历锚定评测（真实 LLM 连出 N 题）：每题 question_text 须含简历项目/技能 token。"""
    from agents.planner import generate_question

    llm = llm or get_agent_llm("planner")
    tokens = [p["name"] for p in RESUME_FIXTURE["projects"]]
    tokens += [s["name"] for s in RESUME_FIXTURE["skills"]]
    tokens = [t for t in tokens if t]
    state = {
        "position": "Java后端",
        "stage": "deepdive",
        "resume": RESUME_FIXTURE,
        "questions_asked": [],
        "messages": [],
    }
    failed = 0
    for i in range(n):
        q = await generate_question(state, "", llm)
        anchored = any(t and t in q.question_text for t in tokens)
        if not anchored:
            failed += 1
            print(f"[EVAL] planner 第 {i + 1}/{n}: FAIL（未锚定简历）: {q.question_text}")
        else:
            print(f"[EVAL] planner 第 {i + 1}/{n}: PASS: {q.question_text}")
        state["questions_asked"] = state["questions_asked"] + [{"question_text": q.question_text}]
    print(f"[EVAL] planner 锚定通过 {n - failed}/{n}")
    return failed == 0


async def main() -> None:
    parser = argparse.ArgumentParser(description="提示词质量验收")
    parser.add_argument("--eval", choices=("evaluator", "interviewer", "planner"),
                        default="evaluator", help="评测对象（默认 evaluator 评分一致性）")
    args = parser.parse_args()
    if args.eval == "interviewer":
        passed = await eval_interviewer_rules()
    elif args.eval == "planner":
        passed = await eval_planner_resume_anchor()
    else:
        ok = await run()
        passed = summarize(ok)
    if not passed:
        raise SystemExit("[EVAL] 验收未通过")


if __name__ == "__main__":
    asyncio.run(main())
