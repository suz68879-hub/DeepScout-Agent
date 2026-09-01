import { describe, expect, it } from 'vitest';
import { formatLocalDateTime } from './datetime';

describe('formatLocalDateTime', () => {
  it('用本地时区的年月日时分展示，而不是截取 UTC ISO 字符串', () => {
    const iso = '2026-08-26T10:00:00+00:00';
    const d = new Date(iso);
    const pad = (n: number) => String(n).padStart(2, '0');
    const expected = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;

    expect(formatLocalDateTime(iso)).toBe(expected);
    if (d.getTimezoneOffset() !== 0) {
      expect(formatLocalDateTime(iso)).not.toBe(iso.slice(0, 16).replace('T', ' '));
    }
  });

  it('无法解析时原样返回', () => {
    expect(formatLocalDateTime('not-a-date')).toBe('not-a-date');
  });
});
