import { describe, expect, it } from 'vitest';

import { resultHandler } from './base';

describe('resultHandler', () => {
  it('将 FastAPI 错误详情转换为可展示错误', () => {
    expect(() => resultHandler({ detail: 'shared state unavailable' } as never))
      .toThrow('shared state unavailable');
  });
});
