"""Text2SQL：LLM 成功/失败/空结果/非法 SQL 各条路径。"""
import asyncio

from agents.text2sql import CHART_TYPES, SqlQuery, generate_query


class FakeStructured:
    def __init__(self, result):
        self.result = result
        self.messages_seen = []

    async def ainvoke(self, msgs):
        self.messages_seen.append(msgs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeLLM:
    def __init__(self, result):
        self.structured = FakeStructured(result)

    def with_structured_output(self, schema):
        return self.structured


def test_generate_query_success_passes_through():
    llm = FakeLLM(SqlQuery(
        sql="SELECT id FROM interview_session LIMIT 5",
        explanation="查询会话", chart_type="table",
    ))
    r = asyncio.run(generate_query("查查面试次数", llm))
    assert r.sql == "SELECT id FROM interview_session LIMIT 5"
    assert "interview_session" in llm.structured.messages_seen[0][0].content  # schema 注入


def test_generate_query_illegal_sql_falls_back_to_template():
    llm = FakeLLM(SqlQuery(
        sql="DROP TABLE resume", explanation="x", chart_type="line",
    ))
    r = asyncio.run(generate_query("看看我的薄弱点", llm))
    assert "interview_report" in r.sql  # 高频薄弱点模板
    assert r.chart_type == "bar"


def test_generate_query_llm_failure_falls_back_to_default_template():
    llm = FakeLLM(RuntimeError("boom"))
    r = asyncio.run(generate_query("随便问问", llm))
    assert r.chart_type == "line"  # 默认「近 N 次总评分趋势」
    assert "scores_json" in r.sql


def test_generate_query_none_result_falls_back_to_default_template():
    """R-T14-1：部分 langchain 版本空输出返回 None 不抛异常，须降级模板查询。"""
    llm = FakeLLM(None)
    r = asyncio.run(generate_query("随便问问", llm))
    assert r.chart_type == "line"
    assert "scores_json" in r.sql


def test_generate_query_unknown_chart_type_defaults_to_table():
    llm = FakeLLM(SqlQuery(
        sql="SELECT id FROM resume LIMIT 1", explanation="x", chart_type="pie3d",
    ))
    r = asyncio.run(generate_query("查简历", llm))
    assert r.chart_type == "table"


def test_keyword_matching_covers_all_five_templates():
    from agents.text2sql import _match_template
    assert _match_template("最近面试平均分趋势").get("name").startswith("近 N 次")
    assert _match_template("四维雷达对比").get("name").startswith("各维度")
    assert _match_template("题目类型分布").get("name").startswith("题目")
    assert _match_template("我的薄弱点").get("name").startswith("高频")
    assert _match_template("面试了多少次").get("name").startswith("面试频次")
    assert len(CHART_TYPES) == 5
