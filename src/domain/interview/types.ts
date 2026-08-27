// 面试会话类型（对齐 api/interview.py 与 LangGraph 图状态）
export type StageId = 'intro' | 'deepdive' | 'technical' | 'qa' | 'finish';

export interface DimensionScore {
  score: number;
  reason: string;
}

// 图状态 scores 条目（对齐 agents/evaluator.py RoundScore.model_dump）
export interface RoundScore {
  dimensions: Record<string, DimensionScore>;
  overall_score: number;
  strengths: string[];
  improvements: string[];
  comment: string;
  status?: string;
}

// GET /api/interview/state（Plan 3 T1 端点）
export interface InterviewStateResponse {
  session_id: string;
  stage: StageId;
  round_no: number;
  current_question: Record<string, unknown> | null;
  scores: RoundScore[];
}

// POST /api/interview/start
export interface StartResponse {
  session_id: string;
  position: string;
  stage: StageId;
}

// POST /api/interview/finish
export interface FinishResponse {
  job_id: string;
  session_id: string;
  status: 'pending';
}
