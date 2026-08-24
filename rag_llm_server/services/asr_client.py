"""豆包语音「录音文件识别大模型版」REST 客户端（spec §12.11）。

接口事实唯一权威：docs/superpowers/notes/2026-08-18-seed-asr-bigmodel-api.md
无回调模式：submit 后主动 query 轮询；task_id 为客户端生成的 UUID（X-Api-Request-Id），
提交与查询共用同一值，作为重启恢复的幂等锚点（T7 recording.asr_task_id）。
"""
import uuid

import httpx

from config import settings

ASR_BASE_URL = "https://openspeech.bytedance.com"
SUBMIT_PATH = "/api/v3/auc/bigmodel/submit"
QUERY_PATH = "/api/v3/auc/bigmodel/query"
RESOURCE_ID = "volc.seedasr.auc"  # 豆包录音文件识别模型 2.0

STATUS_SUCCESS = "20000000"  # 成功
STATUS_PROCESSING = "20000001"  # 正在处理中
STATUS_QUEUED = "20000002"  # 任务在队列中

FORMAT_BY_EXT = {"mp3": "mp3", "wav": "wav", "ogg": "ogg"}


class AsrError(Exception):
    """识别服务返回失败状态（携带服务端状态码与信息）。"""

    def __init__(self, status_code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _headers(task_id: str) -> dict:
    return {
        "X-Api-Key": settings.ASR_FILE_API_KEY,
        "X-Api-Resource-Id": RESOURCE_ID,
        "X-Api-Request-Id": task_id,
        "X-Api-Sequence": "-1",
    }


async def submit_asr(audio_url: str, ext: str) -> str:
    """提交识别任务，返回 task_id（客户端生成的 UUID，query 复用）。"""
    if not settings.ASR_FILE_API_KEY:
        raise ValueError("未配置 ASR_FILE_API_KEY，无法提交语音识别任务")
    task_id = str(uuid.uuid4())
    body = {
        "user": {"uid": task_id},
        "audio": {"format": FORMAT_BY_EXT[ext], "url": audio_url, "language": "zh-CN"},
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,
            "enable_punc": True,
            "enable_ddc": True,
            "enable_speaker_info": True,
            "ssd_version": "200",
            "show_utterances": True,
        },
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(ASR_BASE_URL + SUBMIT_PATH, headers=_headers(task_id), json=body)
    code = resp.headers.get("X-Api-Status-Code", "")
    if code != STATUS_SUCCESS:
        raise AsrError(code, resp.headers.get("X-Api-Message", "识别服务错误"))
    return task_id


async def query_asr(task_id: str) -> dict | None:
    """查询识别任务；完成返回应答 JSON，处理中返回 None，失败抛 AsrError。"""
    if not settings.ASR_FILE_API_KEY:
        raise ValueError("未配置 ASR_FILE_API_KEY，无法查询语音识别任务")
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(ASR_BASE_URL + QUERY_PATH, headers=_headers(task_id), json={})
    code = resp.headers.get("X-Api-Status-Code", "")
    if code == STATUS_SUCCESS:
        return resp.json()
    if code in (STATUS_PROCESSING, STATUS_QUEUED):
        return None
    raise AsrError(code, resp.headers.get("X-Api-Message", "识别服务错误"))


def parse_transcript(payload: dict) -> list[dict]:
    """应答 JSON → 带说话人标签的分句列表（speaker/start_ms/end_ms/text）。

    说话人字段双键防御读取（笔记「Query 响应」）：additions.speaker 优先，
    回退 utterance 顶层 speaker，再缺省 "0"。
    """
    utterances = (payload.get("result") or {}).get("utterances") or []
    segments = []
    for u in utterances:
        text = (u.get("text") or "").strip()
        if not text:
            continue
        speaker = (u.get("additions") or {}).get("speaker") or u.get("speaker") or "0"
        segments.append({
            "speaker": str(speaker),
            "start_ms": int(u.get("start_time") or 0),
            "end_ms": int(u.get("end_time") or 0),
            "text": text,
        })
    return segments
