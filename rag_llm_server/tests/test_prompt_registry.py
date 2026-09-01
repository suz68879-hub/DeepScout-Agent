"""提示词中心：全部模板可加载、可渲染、few-shot 可解析、版本化取用。"""
import pytest

from agents.prompts.registry import registry, render_structured, schema_json


class _DummyModel:
    @staticmethod
    def model_json_schema():
        return {"type": "object", "properties": {"question_text": {"type": "string"}}}


def test_schema_json_serializes_model_schema():
    assert '"question_text"' in schema_json(_DummyModel)


def test_render_structured_injects_output_schema():
    t = registry.get("planner", "system")
    rendered = render_structured(t, _DummyModel, {"position": "x", "stage": "x",
                                                  "resume_json": "{}", "asked_questions": "[]",
                                                  "rag_context": "x", "examples": "x"})
    assert '"question_text"' in rendered  # schema 注入

MD_NAMES = {"interviewer:system", "planner:system", "evaluator:system",
            "reporter:system", "resume_parser:system", "text2sql:system",
            "recording_analyzer:system", "recording_analyzer:role_judge"}


def test_all_templates_load_without_errors():
    assert registry.errors == [], f"registry 加载失败: {registry.errors}"


def test_template_count():
    # 8 个 md 模板 name（interviewer/planner/evaluator/reporter/resume_parser/text2sql + recording_analyzer 的 system/role_judge）
    assert set(registry._templates) == MD_NAMES
    for name in MD_NAMES:
        versions = set(registry._templates[name])
        assert {"1.0.0", "2.0.0"} <= versions, f"{name} 版本集合异常"
        if name == "interviewer:system":
            assert versions == {"1.0.0", "2.0.0", "3.0.0"}
        else:
            assert versions == {"1.0.0", "2.0.0"}


def test_yaml_examples_are_versioned():
    for name in ("planner:examples", "text2sql:examples", "evaluator:rubric", "evaluator:anchors"):
        assert set(registry._examples[name]) == {"1.0.0"}, f"{name} 版本集合异常"


def test_get_with_version_returns_specific_template():
    t = registry.get("interviewer", "system", "1.0.0")
    assert t.version == "1.0.0"
    assert registry.get("interviewer", "system").version == "3.0.0"  # 缺省取最新
    assert registry.get("interviewer", "system", "2.0.0").body != t.body


def test_get_with_missing_version_raises():
    with pytest.raises(KeyError):
        registry.get("interviewer", "system", "9.9.9")


def test_get_examples_with_version():
    ex = registry.get_examples("planner", "examples", "1.0.0")
    assert len(ex["examples"]) == 2


def test_snapshot_versions_returns_latest_per_name():
    # 含 md 模板与 yaml few-shot/rubric（同为提示词一部分，一并固化）
    snap = registry.snapshot_versions()
    assert set(snap) == MD_NAMES | {"planner:examples", "text2sql:examples",
                                    "evaluator:rubric", "evaluator:anchors"}
    assert snap["interviewer:system"] == "3.0.0"
    assert all(v == "2.0.0" for n, v in snap.items() if n in MD_NAMES and n != "interviewer:system")
    assert all(v == "1.0.0" for n, v in snap.items() if n not in MD_NAMES)  # yaml 未升


def test_get_pinned_uses_state_version():
    # 带 prompt_versions 时按 state 取版本；无 state 时取最新
    t1 = registry.get_pinned("interviewer", "system", {"prompt_versions": {"interviewer:system": "1.0.0"}})
    assert t1.version == "1.0.0"
    assert registry.get_pinned("interviewer", "system", None) is registry.get("interviewer", "system")


def test_interviewer_v2_versions_diverge():
    # P7：v2 已建后，最新版与 1.0.0 内容必须不同（版本分流生效）
    v1 = registry.get("interviewer", "system", "1.0.0")
    latest = registry.get("interviewer", "system")
    if latest.version != "1.0.0":
        assert v1.body != latest.body


def test_intro_never_advances_technical_question_in_latest_prompt():
    body = registry.get("interviewer", "system").body
    assert "不要开始技术提问" in body or "不要提问技术题" in body
    assert "开始简历技术基础提问" not in body


def test_interviewer_v2_has_resume_anchor_rule():
    body = registry.get("interviewer", "system").body
    assert "提问约束" in body
    assert "简历" in body and "引用" in body


def test_interviewer_v2_has_deepdive_strategy():
    body = registry.get("interviewer", "system").body
    assert "深挖策略" in body
    assert "至少 2 轮" in body or "连续追问" in body
    assert "权衡" in body


def test_interviewer_v2_has_scenario_design():
    body = registry.get("interviewer", "system").body
    assert "场景设计题" in body and "重新设计" in body


def test_interviewer_v2_has_example_dialogues():
    body = registry.get("interviewer", "system").body
    assert "示例对话" in body
    assert "示例 1" in body and "示例 3" in body


def test_interviewer_v2_has_negative_instructions():
    body = registry.get("interviewer", "system").body
    assert "负面指令" in body and "不要评价" in body


def test_interviewer_v2_keeps_speech_rules():
    # 6 条 TTS 说话规则不可丢
    body = registry.get("interviewer", "system").body
    assert "说话规则" in body and "2 句话" in body


def test_all_v2_have_changelog_and_selfcheck():
    # P7 横切断言：每个 v2 模板必须有 changelog 且含自检或负面指令
    for name in MD_NAMES:
        t = registry.get(name.split(":")[0], name.split(":")[1], "2.0.0")
        assert t.changelog, f"{name} v2 缺 changelog"
        assert "自检" in t.body or "不要" in t.body, f"{name} v2 缺自检或负面指令"


def test_planner_v2_declares_examples_variable_and_anchors_resume():
    t = registry.get("planner", "system", "2.0.0")
    assert "examples" in t.variables  # 变量化注入 few-shot
    assert "resume_json" in t.body  # 出题约束锚定简历
    assert "至少 2 条" in t.body  # follow_up_hints 要求


def test_evaluator_v2_has_anti_bias_section():
    body = registry.get("evaluator", "system", "2.0.0").body
    assert "防偏倚" in body and "不编造" in body or "不要编造" in body


def test_all_templates_render_with_dummy_values():
    registry.render_all()  # 抛异常即失败


def test_missing_variable_raises():
    t = registry.get("planner", "system")
    with pytest.raises(ValueError, match="缺少变量"):
        t.render()  # 一个变量都不给


def test_interviewer_persona_spot_check():
    # 防誊写漂移抽查：人设关键词必须在（agent-designs §1.5）
    body = registry.get("interviewer", "system").body
    assert "懂小智" in body and "说话规则" in body and "阶段推进规则" in body


def test_evaluator_rubric_has_four_dimensions():
    data = registry.get_examples("evaluator", "rubric")
    dims = data["dimensions"]
    assert set(dims) == {"技术深度", "项目理解", "表达沟通", "临场表现"}
    for d in dims:
        assert len(dims[d]) == 5  # 每维度 5 档


def test_evaluator_anchors_have_four_entries():
    assert len(registry.get_examples("evaluator", "anchors")["anchors"]) == 4


def test_planner_examples_parse():
    ex = registry.get_examples("planner", "examples")["examples"]
    assert len(ex) == 2 and ex[0]["output"]["stage"] == "deepdive"


def test_text2sql_examples_parse():
    ex = registry.get_examples("text2sql", "examples")["examples"]
    assert len(ex) == 3 and all("sql" in e for e in ex)
