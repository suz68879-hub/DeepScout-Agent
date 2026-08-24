import { describe, expect, it } from 'vitest';
import { STAGE_META, stageMeta } from './stageDisplay';

describe('stageDisplay', () => {
  it('五个阶段均有展示文案', () => {
    for (const id of ['intro', 'deepdive', 'technical', 'qa', 'finish'] as const) {
      expect(STAGE_META[id].label).toBeTruthy();
      expect(STAGE_META[id].hint).toBeTruthy();
    }
  });

  it('未知阶段回退为原始字符串', () => {
    expect(stageMeta('mystery').label).toBe('mystery');
    expect(stageMeta('mystery').hint).toBe('');
  });
});
