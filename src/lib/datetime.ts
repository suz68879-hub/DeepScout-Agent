function pad2(n: number): string {
  return String(n).padStart(2, '0');
}

/** 将后端 ISO 时间转为本地墙钟的 `YYYY-MM-DD HH:mm`，避免截取 UTC 字符串。 */
export function formatLocalDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) {
    return iso;
  }
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}
