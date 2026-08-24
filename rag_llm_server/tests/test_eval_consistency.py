"""一致性脚本纯逻辑：summarize 波动判定 + interviewer 规则校验。"""
from scripts.eval_consistency import check_interviewer_reply, summarize


def _score(value):
    return {
        "status": "ok",
        "dimensions": {d: {"score": value, "reason": "r"} for d in
                       ["技术深度", "项目理解", "表达沟通", "临场表现"]},
        "overall_score": float(value),
    }


def test_summarize_passes_within_spread():
    assert summarize([_score(7), _score(8), _score(7)], accept_spread=1) is True


def test_summarize_fails_beyond_spread():
    assert summarize([_score(5), _score(8)], accept_spread=1) is False


def test_summarize_fails_with_too_few_samples():
    assert summarize([_score(7)], accept_spread=1) is False


def test_check_interviewer_reply_pass():
    text = "你简历里的秒杀系统，库存是怎么扣的？"
    assert check_interviewer_reply(text, ["秒杀系统"]) == []


def test_check_interviewer_reply_catches_violations():
    bad = "不错！这个问题问得很好，你讲的很详细。关于你简历里的秒杀系统，库存扣减用了行锁，性能上会有瓶颈，这里涉及到多线程并发控制的问题，以及分布式事务的一致性保证，还有 Redis 预扣方案可以对比，你觉得呢"
    v = check_interviewer_reply(bad, ["秒杀系统"])
    assert any("评价语" in x for x in v)       # 含「不错/很好」
    assert any("超长" in x for x in v)         # 超过 40 字
    assert any("问号" in x for x in v)         # 未以问号结尾


def test_check_interviewer_reply_missing_anchor():
    v = check_interviewer_reply("请谈谈你对分布式系统的理解？", ["秒杀系统"])
    assert any("简历" in x for x in v)


def test_check_interviewer_reply_empty():
    assert check_interviewer_reply("", ["秒杀系统"])
