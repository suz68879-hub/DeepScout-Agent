// @vitest-environment jsdom
import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ChartPanel from './index';

const setOption = vi.fn();
const resize = vi.fn();
const dispose = vi.fn();

vi.mock('./echarts', () => ({
  default: {
    init: () => ({ setOption, resize, dispose }),
  },
}));

describe('ChartPanel', () => {
  it('初始化并设置 option，卸载时销毁实例', () => {
    const { unmount } = render(<ChartPanel option={{}} />);
    expect(setOption).toHaveBeenCalled();
    unmount();
    expect(dispose).toHaveBeenCalled();
  });
});
