// @vitest-environment jsdom
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { RoundScore } from '@/domain/interview/types';
import ScoreOverlay from './index';

const score: RoundScore = {
  dimensions: {
    技术深度: { score: 8, reason: '原理清晰' },
    项目理解: { score: 7, reason: '数据意识一般' },
    表达沟通: { score: 9, reason: '结构清晰' },
    临场表现: { score: 6, reason: '略有紧张' },
  },
  overall_score: 7.5,
  strengths: [],
  improvements: [],
  comment: '整体不错',
};

describe('ScoreOverlay', () => {
  it('渲染总评分、维度与点评', () => {
    render(<ScoreOverlay score={score} />);
    expect(screen.getByText('7.5')).toBeTruthy();
    expect(screen.getByText('技术深度')).toBeTruthy();
    expect(screen.getByText('整体不错')).toBeTruthy();
  });

  it('无评分时不渲染', () => {
    const { container } = render(<ScoreOverlay score={null} />);
    expect(container.firstChild).toBeNull();
  });
});
