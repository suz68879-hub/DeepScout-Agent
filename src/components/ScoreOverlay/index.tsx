import type { RoundScore } from '@/domain/interview/types';
import styles from './index.module.less';

// 回合评分浮层（spec §2.2）：常驻右侧展示最近一次评分（Plan 3 Ruling R5）
export default function ScoreOverlay({ score }: { score: RoundScore | null }) {
  if (!score) return null;
  return (
    <div className={styles.overlay}>
      <div className={styles.title}>本轮表现</div>
      <div className={styles.overall}>
        {score.overall_score.toFixed(1)}
        <span className={styles.unit}>/10</span>
      </div>
      {Object.entries(score.dimensions ?? {}).map(([dim, d]) => (
        <div key={dim} className={styles.dimRow}>
          <div className={styles.dimHead}>
            <span className={styles.dimName}>{dim}</span>
            <span className={styles.dimScore}>{d.score}</span>
          </div>
          <div className={styles.dimReason}>{d.reason}</div>
        </div>
      ))}
      {score.comment ? <div className={styles.comment}>{score.comment}</div> : null}
    </div>
  );
}
