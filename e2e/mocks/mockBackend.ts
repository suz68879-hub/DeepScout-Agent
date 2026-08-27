/**
 * E2E mock 后端（P7）：page.route 拦截 /api/**，让前端在无真实后端时
 * 跑通 首页 → 面试间 → 报告 主流程。仅测试代码引用，不进入生产构建。
 */
import type { Page } from '@playwright/test';

export const E2E_SESSION_ID = 'e2e-session';
export const E2E_REPORT_ID = 'e2e-report';
export const E2E_JOB_ID = 'e2e-job';

const E2E_REPORT = {
  id: E2E_REPORT_ID,
  session_id: E2E_SESSION_ID,
  scores_json: JSON.stringify({ 技术深度: 8, 项目理解: 8.5, 表达沟通: 7, 临场表现: 7.5 }),
  feedback_json: JSON.stringify({
    summary: 'E2E 测试报告：技术基础扎实，项目讲解清晰。',
    round_details: [
      {
        round_no: 1,
        question: '你简历里的秒杀系统，库存是怎么扣的？',
        answer_summary: '数据库行锁加 Redis 预扣。',
        comment: '方案合理。',
      },
    ],
    round_scores: [],
    strengths: ['技术基础扎实'],
    improvements: ['可以更深入'],
  }),
  suggestions_json: JSON.stringify(['多练习系统设计题']),
  md_path: 'data/reports/e2e.md',
  created_at: '2026-08-21 00:00:00',
  position: 'Java后端',
  source: 'session',
};

/** 拦截 /api/**：start/state/finish/jobs/reports 返回固定 fixture，其余 404。 */
export function installMockRoutes(
  page: Page,
  options: { authenticated?: boolean } = {}
): void {
  let authenticated = options.authenticated ?? true;
  page.route('**/api/auth/me', (route) => {
    void route.fulfill(
      authenticated
        ? { json: { id: 'e2e-user', username: 'e2e_user' } }
        : { status: 401, json: { detail: 'authentication required' } }
    );
  });
  page.route('**/api/auth/login', (route) => {
    authenticated = true;
    void route.fulfill({ json: { id: 'e2e-user', username: 'e2e_user' } });
  });
  page.route('**/api/auth/register', (route) => {
    authenticated = true;
    void route.fulfill({ status: 201, json: { id: 'e2e-user', username: 'e2e_user' } });
  });
  page.route('**/api/auth/logout', (route) => {
    authenticated = false;
    void route.fulfill({ status: 204 });
  });

  page.route('**/api/reports', (route) => {
    if (route.request().method() === 'GET') {
      void route.fulfill({ json: [] });
    } else {
      void route.fallback();
    }
  });

  page.route('**/api/interview/start', (route) => {
    void route.fulfill({ json: { session_id: E2E_SESSION_ID, position: 'Java后端' } });
  });

  page.route('**/api/interview/state**', (route) => {
    // 轮询返回固定 deepdive 状态（StageIndicator 文案随轮询推进）
    void route.fulfill({ json: { stage: 'deepdive', round_no: 1, scores: [], messages: [] } });
  });

  page.route('**/api/interview/finish', (route) => {
    void route.fulfill({
      status: 202,
      json: { job_id: E2E_JOB_ID, session_id: E2E_SESSION_ID, status: 'pending' },
    });
  });

  page.route(`**/api/jobs/${E2E_JOB_ID}`, (route) => {
    void route.fulfill({ json: {
      job_id: E2E_JOB_ID,
      type: 'interview.finish',
      status: 'succeeded',
      attempt: 1,
      created_at: '2026-08-26T10:00:00Z',
      started_at: '2026-08-26T10:00:01Z',
      finished_at: '2026-08-26T10:00:02Z',
      result_ref: { report_id: E2E_REPORT_ID },
      error_code: null,
    } });
  });

  page.route(`**/api/reports/${E2E_REPORT_ID}/export.md`, (route) => {
    void route.fulfill({ body: '# E2E 面试报告\n\n技术基础扎实。', contentType: 'text/plain' });
  });

  page.route(`**/api/reports/${E2E_REPORT_ID}`, (route) => {
    void route.fulfill({ json: E2E_REPORT });
  });
}
