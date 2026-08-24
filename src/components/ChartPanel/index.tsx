import { useEffect, useRef } from 'react';
import type { EChartsOption } from 'echarts';
import echarts from './echarts';
import styles from './index.module.less';

// 自封装图表容器（Plan 3 Ruling R8）：init/setOption/resize/dispose 自理
export default function ChartPanel({
  option,
  className,
}: {
  option: EChartsOption;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chart.setOption(option);
    const onResize = () => chart.resize();
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      chart.dispose();
    };
  }, [option]);

  return <div ref={ref} className={`${styles.panel} ${className ?? ''}`} />;
}
