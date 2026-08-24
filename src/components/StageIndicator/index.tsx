import type { StageId } from '@/domain/interview/types';
import { stageMeta } from '@/domain/interview/stageDisplay';
import styles from './index.module.less';

export const STAGE_ORDER: StageId[] = ['intro', 'technical', 'deepdive', 'qa', 'finish'];

// 五段进度指示（纯展示；阶段归属后端，spec §2.3）
export default function StageIndicator({ stage }: { stage: StageId | null }) {
  const current = stage ?? 'intro';
  const curIdx = STAGE_ORDER.indexOf(current);
  return (
    <div className={styles.indicator}>
      {STAGE_ORDER.map((id, idx) => {
        const meta = stageMeta(id);
        const cls = idx < curIdx ? styles.done : idx === curIdx ? styles.current : styles.upcoming;
        return (
          <div key={id} className={`${styles.step} ${cls}`}>
            <span className={styles.dot} />
            <span className={styles.label}>{meta.label}</span>
          </div>
        );
      })}
    </div>
  );
}
