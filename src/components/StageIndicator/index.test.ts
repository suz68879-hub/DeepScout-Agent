import { describe, expect, it } from 'vitest';
import { STAGE_ORDER } from './index';

describe('StageIndicator', () => {
  it('按自我介绍、技术面、项目深挖、反问、结束的流程展示', () => {
    expect(STAGE_ORDER).toEqual(['intro', 'technical', 'deepdive', 'qa', 'finish']);
  });
});
