"""Resume upload and current-user lookup API."""
import os
import tempfile
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from agents.resume_parser import parse_pdf, parse_text
from api.auth import get_current_user
from services.storage import storage

router = APIRouter(prefix="/api/resume", tags=["resume"])
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


async def _create_and_parse(
    user_id: str,
    content: str,
    source: str,
    parser,
    resume_id: str | None = None,
) -> dict:
    resume = await storage.resume_create(user_id, {
        "id": resume_id or str(uuid.uuid4()),
        "content": content,
        "structured_json": None,
        "source": source,
        "status": "parsing",
    })
    try:
        structured = await parser(content)
        await storage.resume_update(user_id, resume["id"], {
            "structured_json": structured.model_dump_json(),
            "status": "ready",
        })
    except ValueError as exc:
        await storage.resume_update(user_id, resume["id"], {"status": "failed"})
        raise HTTPException(status_code=422, detail="resume format is not supported") from exc
    except Exception as exc:
        await storage.resume_update(user_id, resume["id"], {"status": "failed"})
        raise HTTPException(status_code=400, detail="resume parsing failed") from exc
    return {"id": resume["id"], "status": "ready"}


@router.post("/upload")
async def upload_resume(
    content: str = Form(...),
    source: str = Form("md"),
    user: dict = Depends(get_current_user),
):
    from services.agent_llm import get_agent_llm

    llm = get_agent_llm("resume_parser")
    return await _create_and_parse(
        user["id"], content, source, lambda text: parse_text(text, llm),
    )


@router.post("/upload_pdf")
async def upload_resume_pdf(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    from services.agent_llm import get_agent_llm
    from services.storage import get_tos_store

    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file exceeds the 5MB limit")
    if not raw.lstrip().startswith(b"%PDF"):
        raise HTTPException(status_code=422, detail="only PDF files are supported")
    resume_id = str(uuid.uuid4())
    tos = get_tos_store()
    if tos is not None:
        try:
            await tos.save_bytes(f"users/{user['id']}/resumes/{resume_id}.pdf", raw)
        except OSError as exc:
            raise HTTPException(status_code=503, detail="resume cloud upload failed") from exc
    llm = get_agent_llm("resume_parser")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(raw)
        tmp_path = tmp.name
    try:
        return await _create_and_parse(
            user["id"],
            raw[:4096].decode("utf-8", errors="ignore"),
            "pdf",
            lambda _text: parse_pdf(tmp_path, llm),
            resume_id=resume_id,
        )
    finally:
        os.unlink(tmp_path)


@router.get("")
async def get_latest_resume(user: dict = Depends(get_current_user)):
    resume = await storage.resume_latest(user["id"])
    if not resume:
        raise HTTPException(status_code=404, detail="no resume found")
    return {key: value for key, value in resume.items() if key != "user_id"}
