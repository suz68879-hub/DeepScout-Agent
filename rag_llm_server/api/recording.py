"""录音分析 API（spec §12.5）。"""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from api.auth import get_current_user
from services.recording_service import (
    ALLOWED_EXTS, DEFAULT_POSITION, MAX_UPLOAD_BYTES, upload_recording,
)
from services.storage import storage

router = APIRouter(prefix="/api/recording", tags=["recording"])


@router.post("/upload")
async def upload_recording_file(
    file: UploadFile = File(...),
    position: str = Form(DEFAULT_POSITION),
    user: dict = Depends(get_current_user),
):
    """上传面试录音：边界校验 → TOS → 提交识别任务 → {recording_id, status}。"""
    filename = file.filename or "recording"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTS:
        raise HTTPException(status_code=422, detail="仅支持 mp3 / wav / ogg 格式的录音文件")
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if not raw:
        raise HTTPException(status_code=422, detail="录音文件为空")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="录音超过 200MB 上限")
    try:
        row = await upload_recording(user["id"], filename, ext, raw, position)
    except (RuntimeError, OSError) as e:
        raise HTTPException(status_code=503, detail=f"录音上传失败：{e}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"语音识别任务提交失败：{e}")
    return {"recording_id": row["id"], "status": row["status"]}


@router.get("/{recording_id}")
async def get_recording(recording_id: str, user: dict = Depends(get_current_user)):
    """任务状态：{status: processing/done/failed, report_id?, error?}（前端 3s 轮询）。"""
    row = await storage.recording_get(user["id"], recording_id)
    if not row:
        raise HTTPException(status_code=404, detail="录音任务不存在")
    return {
        "recording_id": row["id"],
        "status": row["status"],
        "report_id": row.get("report_id"),
        "error": row.get("error"),
    }
