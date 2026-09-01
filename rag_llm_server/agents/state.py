"""InterviewState：对话图共享状态（agent-designs §0.3 逐字段）。

messages 用 Annotated[list, operator.add] 追加合并——热/冷路径分步
aupdate_state（先写用户消息、后写助手消息）必须保留历史，覆盖语义会
导致 evaluator 取不到当轮用户消息。其余字段覆盖语义（节点返回完整列表）。
"""
import operator
from typing import Annotated, TypedDict


class InterviewState(TypedDict, total=False):
    session_id: str
    position: str              # 目标岗位
    resume: dict               # 结构化简历（ResumeParser 产物）
    stage: str                 # intro / deepdive / technical / qa / finish
    round_no: int              # 当前阶段已作答轮数（evaluator 累加，阶段切换归零）
    stage_questions: list      # 本阶段题目队列（保留字段，当前实现由 current_question 单题驱动）
    questions_asked: list      # 已出题目（question_text + topic）
    current_question: dict     # 当前题（Planner 产物，Question schema）
    messages: Annotated[list, operator.add]  # 对话记录（追加合并：热/冷路径分步写入）
    scores: list               # 逐轮评分（Evaluator 产物，失败轮 {"status": "failed"}；节点返回完整列表，覆盖语义）
    rag_context: str           # 上一轮检索上下文（本轮出题复用，避免二次检索）
    pending_user_text: str     # 热路径写入的当轮用户文本
    report: dict | None        # Reporter 产物（finish 后非空）
    prompt_versions: dict      # 会话创建时固化的提示词版本快照（agent-designs §0.5：会话中途改提示词不影响进行中会话）
    pending_ask: bool          # planner 刚出题、尚未向候选人宣读；下一冷路径不评分、不再出题
