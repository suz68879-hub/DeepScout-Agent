// @vitest-environment jsdom
import { render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import ErrorBoundary from './index';

function Bomb(): React.ReactElement {
  throw new Error('boom');
}

test('子组件抛错时渲染兜底界面', () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  render(
    <ErrorBoundary>
      <Bomb />
    </ErrorBoundary>
  );
  expect(screen.getByText('ERR_UNCAUGHT')).toBeTruthy();
  expect(screen.getByText(/boom/)).toBeTruthy();
});
