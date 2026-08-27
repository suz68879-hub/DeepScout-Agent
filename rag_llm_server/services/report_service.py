"""报告落库与 MD 渲染（spec §2.4 报告结构）。"""
import json

from jinja2 import Template

from services.storage import get_file_store, storage
from services.storage.base import StorageConflictError
from services.clock import utc_now

file_store = get_file_store()

REPORT_MD_TEMPLATE = Template("""# 面试报告

- 生成时间：{{ created }}
- 目标岗位：{{ position }}
- 总评：{{ overall }}/10

## 分维度评分

| 维度 | 得分 |
|------|------|
| 技术深度 | {{ dims['技术深度'] }} |
| 项目理解 | {{ dims['项目理解'] }} |
| 表达沟通 | {{ dims['表达沟通'] }} |
| 临场表现 | {{ dims['临场表现'] }} |

## 总体评价

{{ summary }}

## 逐题记录

{% for r in round_details %}
### 第 {{ r.round_no }} 题：{{ r.question }}

- 回答要点：{{ r.answer_summary }}
- 点评：{{ r.comment }}
{% endfor %}

## 改进建议（按优先级）

{% for s in improvements %}
{{ loop.index }}. {{ s }}
{% endfor %}

## 下次练习建议

{% for s in suggestions %}
- {{ s }}
{% endfor %}
""")


async def save_report(session: dict, report: dict, state: dict) -> str:
    """渲染 MD → 落文件 → 落库，返回 report_id。"""
    created = utc_now()[:19].replace("T", " ")
    md = REPORT_MD_TEMPLATE.render(
        created=created,
        position=state.get("position") or session.get("position") or "Java后端",
        overall=report["overall_score"],
        dims=report["dimension_scores"],
        summary=report["summary"],
        round_details=report["round_details"],
        improvements=report["improvements"],
        suggestions=report["suggestions"],
    )
    rel = f"users/{session['user_id']}/reports/{session['id']}/report.md"
    full_path = await file_store.save_text(rel, md)
    row = await storage.report_create(session["user_id"], {
        "session_id": session["id"],
        "position": state.get("position") or session.get("position") or "Java后端",
        "scores_json": json.dumps(report["dimension_scores"], ensure_ascii=False),
        "feedback_json": json.dumps({
            "summary": report["summary"],
            "round_details": report["round_details"],
            "round_scores": [
                s.get("overall_score") for s in state.get("scores", [])
                if s.get("overall_score") is not None
            ],
            "strengths": report["strengths"],
            "improvements": report["improvements"],
        }, ensure_ascii=False),
        "suggestions_json": json.dumps(report["suggestions"], ensure_ascii=False),
        "md_path": full_path,
        "created_at": utc_now(),
    })
    return row["id"]


async def save_recording_report(recording_id: str, report: dict, position: str,
                                transcript: list[dict], assignment: dict) -> str:
    """录音报告落库（spec §12.2/12.7）：source=recording、session_id 为空、
    feedback_json 附转写全文与角色判定。与 save_report 同模板同结构——
    Report 页 / History / Analytics 渲染零改动复用。"""
    created = utc_now()[:19].replace("T", " ")
    md = REPORT_MD_TEMPLATE.render(
        created=created,
        position=position,
        overall=report["overall_score"],
        dims=report["dimension_scores"],
        summary=report["summary"],
        round_details=report["round_details"],
        improvements=report["improvements"],
        suggestions=report["suggestions"],
    )
    recording = await storage.recording_get_internal(recording_id)
    if not recording:
        raise ValueError("recording not found")
    rel = f"users/{recording['user_id']}/reports/{recording_id}/report.md"
    md_path = await file_store.save_text(rel, md)
    try:
        row = await storage.report_create(recording["user_id"], {
            "id": recording_id,
            "session_id": None,
            "source": "recording",
            "position": position,
            "scores_json": json.dumps(report["dimension_scores"], ensure_ascii=False),
            "feedback_json": json.dumps({
                "summary": report["summary"],
                "round_details": report["round_details"],
                "round_scores": [],
                "strengths": report["strengths"],
                "improvements": report["improvements"],
                "transcript": transcript,
                "speaker_assignment": assignment,
            }, ensure_ascii=False),
            "suggestions_json": json.dumps(report["suggestions"], ensure_ascii=False),
            "md_path": md_path,
            "created_at": utc_now(),
        })
    except StorageConflictError:
        row = await storage.report_get(recording["user_id"], recording_id)
        if row is None or row.get("source") != "recording":
            raise
    return row["id"]
