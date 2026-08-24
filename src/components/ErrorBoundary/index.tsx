import React from 'react';
import styles from './index.module.less';

interface Props {
  children: React.ReactNode;
}

interface State {
  error: Error | null;
}

// spec §7：前端渲染异常全局边界（官方 demo 没有）；不依赖 Message 弹窗
export default class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div className={styles.fallback}>
          <div className={styles.code}>ERR_UNCAUGHT</div>
          <div className={styles.message}>{this.state.error.message}</div>
          <button type="button" className={styles.reload} onClick={() => window.location.reload()}>
            重新加载
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
