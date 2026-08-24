import { useEffect, useRef, useState } from 'react';
import { Button, Message, Tag } from '@arco-design/web-react';
import { ApiError, getJson, postFile, postForm } from '@/api/rest';
import type { ResumeRow, StructuredResume } from '@/domain/resume/types';
import styles from './index.module.less';

const STATUS_TEXT: Record<string, string> = {
  parsing: '解析中',
  ready: '已就绪',
  failed: '解析失败',
};

function parseStructured(row: ResumeRow): StructuredResume | null {
  if (!row.structured_json) return null;
  try {
    return JSON.parse(row.structured_json) as StructuredResume;
  } catch {
    return null;
  }
}

function StructuredPreview({ data }: { data: StructuredResume | null }) {
  if (!data) return null;
  const { basic_info: basic, skills, projects, position_target: target } = data;
  return (
    <div className={styles.preview}>
      <div className={styles.previewRow}>
        <span className={styles.previewKey}>姓名</span>
        <span>{basic.name || '—'}</span>
        <span className={styles.previewKey}>学历</span>
        <span>{basic.education || '—'}</span>
        <span className={styles.previewKey}>经验</span>
        <span>{basic.years_of_experience ? `${basic.years_of_experience} 年` : '—'}</span>
        <span className={styles.previewKey}>意向岗位</span>
        <span>{target || '—'}</span>
      </div>
      <div className={styles.previewRow}>
        <span className={styles.previewKey}>技能</span>
        <span>{skills.length ? skills.map((s) => s.name).join(' / ') : '—'}</span>
      </div>
      <div className={styles.previewRow}>
        <span className={styles.previewKey}>项目</span>
        <span>{projects.length ? projects.map((p) => p.name).join(' / ') : '—'}</span>
      </div>
    </div>
  );
}

export default function ResumePanel({ onResume }: { onResume: (row: ResumeRow | null) => void }) {
  const [resume, setResume] = useState<ResumeRow | null>(null);
  const [uploading, setUploading] = useState(false);
  const [draft, setDraft] = useState('');
  const mdInput = useRef<HTMLInputElement>(null);
  const pdfInput = useRef<HTMLInputElement>(null);

  const refresh = async () => {
    try {
      const row = await getJson<ResumeRow>('/api/resume');
      setResume(row);
      onResume(row);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setResume(null);
        onResume(null);
      } else {
        Message.error(e instanceof Error ? e.message : '简历获取失败');
      }
    }
  };

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 后端 /upload 同步完成解析后返回，故上传成功后刷新一次即可
  const uploadText = async (content: string, source: string) => {
    if (!content.trim()) {
      Message.warning('简历内容不能为空');
      return;
    }
    setUploading(true);
    try {
      await postForm<{ id: string; status: string }>('/api/resume/upload', { content, source });
      Message.success('上传成功，结构化解析已完成');
      setDraft('');
      await refresh();
    } catch (e) {
      Message.error(e instanceof Error ? e.message : '上传失败');
    } finally {
      setUploading(false);
    }
  };

  const onMdFile = async (file: File) => {
    const text = await file.text();
    await uploadText(text, 'md');
    if (mdInput.current) mdInput.current.value = '';
  };

  const onPdfFile = async (file: File) => {
    setUploading(true);
    try {
      await postFile<{ id: string; status: string }>('/api/resume/upload_pdf', file);
      Message.success('上传成功，结构化解析已完成');
      await refresh();
    } catch (e) {
      Message.error(e instanceof Error ? e.message : 'PDF 解析失败');
    } finally {
      setUploading(false);
      if (pdfInput.current) pdfInput.current.value = '';
    }
  };

  const structured = resume ? parseStructured(resume) : null;

  return (
    <section className={styles.card}>
      <div className={styles.headerRow}>
        <div className={styles.cardTitle}>&gt; 简历管理</div>
        {resume ? (
          <Tag color={resume.status === 'ready' ? 'green' : resume.status === 'failed' ? 'red' : 'orange'}>
            {STATUS_TEXT[resume.status] ?? resume.status}
          </Tag>
        ) : null}
      </div>

      <div className={styles.channels}>
        <div className={styles.channel}>
          <span className={styles.channelName}>MD 文件</span>
          <input
            ref={mdInput}
            type="file"
            accept=".md,.markdown,.txt"
            onChange={(e) => e.target.files?.[0] && onMdFile(e.target.files[0])}
          />
        </div>
        <div className={styles.channel}>
          <span className={styles.channelName}>PDF 文件</span>
          <input
            ref={pdfInput}
            type="file"
            accept="application/pdf"
            onChange={(e) => e.target.files?.[0] && onPdfFile(e.target.files[0])}
          />
        </div>
        <div className={styles.channel}>
          <span className={styles.channelName}>表单录入</span>
          <textarea
            className={styles.textarea}
            rows={4}
            placeholder={'粘贴简历文本（Markdown 或纯文本）\n# 张三\n- Java 后端 5 年\n- 项目：xx 系统……'}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <Button size="small" loading={uploading} onClick={() => uploadText(draft, 'md')}>
            提交并解析
          </Button>
        </div>
      </div>

      {resume ? <StructuredPreview data={structured} /> : null}
      {resume?.status === 'failed' ? (
        <div className={styles.failedRow}>上次解析失败，可重新上传覆盖</div>
      ) : null}
    </section>
  );
}
