"""报告 API（spec §5.3）：列表 / 详情 / MD 导出。"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from api.auth import get_current_user
from services.storage import get_file_store, storage
from services.storage.file_storage import LocalFileStorage

router = APIRouter(prefix="/api/reports", tags=["reports"])
file_store = get_file_store()


async def _read_report_md(md_path: str) -> str:
    """TOS 切换前的存量报告 md_path 为本地绝对路径：当前 store 读失败时回落本地实现。"""
    try:
        return await file_store.read_text(md_path)
    except (OSError, ValueError):
        pass
    if not isinstance(file_store, LocalFileStorage):
        try:
            return await LocalFileStorage().read_text(md_path)
        except (OSError, ValueError):
            pass
    raise HTTPException(status_code=404, detail="报告文件缺失")


@router.get("")
async def list_reports(user: dict = Depends(get_current_user)):
    rows = await storage.report_list(user["id"])
    return [{key: value for key, value in row.items() if key != "user_id"} for row in rows]


@router.get("/{report_id}")
async def get_report(report_id: str, user: dict = Depends(get_current_user)):
    report = await storage.report_get(user["id"], report_id)
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    return {key: value for key, value in report.items() if key != "user_id"}


@router.get("/{report_id}/export.md")
async def export_report_md(report_id: str, user: dict = Depends(get_current_user)):
    report = await storage.report_get(user["id"], report_id)
    if not report or not report.get("md_path"):
        raise HTTPException(status_code=404, detail="报告不存在")
    md = await _read_report_md(report["md_path"])
    return PlainTextResponse(md, media_type="text/markdown")
