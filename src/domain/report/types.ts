// 报告类型（对齐 interview_report 行与 save_report 的 JSON 结构）
export interface RoundDetail {
  round_no: number;
  question: string;
  answer_summary: string;
  comment: string;
}

export interface TranscriptSegment {
  speaker: string;
  role: string;
  start_ms: number;
  end_ms: number;
  text: string;
}

export interface SpeakerAssignment {
  candidate_speaker: string;
  confidence: string;
  reason: string;
}

export interface FeedbackJson {
  summary: string;
  round_details: RoundDetail[];
  round_scores: number[];
  strengths: string[];
  improvements: string[];
  transcript?: TranscriptSegment[]; // 录音报告才有（T6 写入）
  speaker_assignment?: SpeakerAssignment;
}

export interface ReportRow {
  id: string;
  session_id: string;
  scores_json: string;
  feedback_json: string;
  suggestions_json: string;
  md_path: string;
  created_at: string;
  position: string | null;
  source?: string; // 'session' | 'recording'（T3 新增列，旧行无此字段）
}

export interface ReportPage {
  items: ReportRow[];
  next_cursor: string | null;
}

// spec §2.4 分维度（与后端 DIMENSIONS 一致）
export const DIMENSIONS = ['技术深度', '项目理解', '表达沟通', '临场表现'] as const;

export type DimensionScores = Record<string, number>;

export function parseScores(json: string | null): DimensionScores {
  if (!json) return {};
  try {
    return JSON.parse(json) as DimensionScores;
  } catch {
    return {};
  }
}

const EMPTY_FEEDBACK: FeedbackJson = {
  summary: '',
  round_details: [],
  round_scores: [],
  strengths: [],
  improvements: [],
};

export function parseFeedback(json: string | null): FeedbackJson {
  if (!json) return EMPTY_FEEDBACK;
  try {
    return JSON.parse(json) as FeedbackJson;
  } catch {
    return EMPTY_FEEDBACK;
  }
}

// 后端 overall = mean(维度均值)（agents/reporter.py compute_average_scores），前端重算等价（Ruling R4）
export function overallFromDims(dims: DimensionScores): number {
  const values = Object.values(dims);
  if (!values.length) return 0;
  return Math.round((values.reduce((a, b) => a + b, 0) / values.length) * 10) / 10;
}

export function overallFromReport(row: Pick<ReportRow, 'scores_json'>): number {
  return overallFromDims(parseScores(row.scores_json));
}
