// 结构化简历类型（对齐后端 agents/resume_parser.py 的 ResumeStructured）
export interface BasicInfo {
  name: string;
  education: string;
  years_of_experience: number;
}

export interface SkillItem {
  name: string;
  level: string;
  years: number;
}

export interface ProjectItem {
  name: string;
  background: string;
  responsibilities: string;
  tech_stack: string[];
  challenges: string;
  results: string;
}

export interface StructuredResume {
  basic_info: BasicInfo;
  skills: SkillItem[];
  projects: ProjectItem[];
  position_target: string;
}

export type ResumeStatus = 'parsing' | 'ready' | 'failed';

// GET /api/resume 行（storage.resume 行结构）
export interface ResumeRow {
  id: string;
  content: string;
  structured_json: string | null;
  source: string;
  status: ResumeStatus;
}
