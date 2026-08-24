"""ResumeParser Agent（冷路径简历结构化，agent-designs §5）。

分层解析：MD 直读 → PDF pypdf 文本层（任一页 < 50 字符视为扫描件）
→ PyMuPDF 渲染页面图片 → 方舟视觉模型 OCR（Ruling R3）。
注入防护三层：数据/指令分离 + 分隔符 + Schema 白名单。
"""
import base64

from pydantic import BaseModel

from .prompts.registry import registry, render_structured

TEXT_MIN_CHARS_PER_PAGE = 50  # 文本层判定阈值（agent-designs §5.1）


class BasicInfo(BaseModel):
    name: str = ""
    education: str = ""
    years_of_experience: int = 0


class Skill(BaseModel):
    name: str = ""
    level: str = ""
    years: int = 0


class Project(BaseModel):
    name: str = ""
    background: str = ""
    responsibilities: str = ""
    tech_stack: list[str] = []
    challenges: str = ""
    results: str = ""


class ResumeStructured(BaseModel):
    basic_info: BasicInfo = BasicInfo()
    skills: list[Skill] = []
    projects: list[Project] = []
    position_target: str = ""


async def parse_text(text: str, llm=None) -> ResumeStructured:
    """文本简历 → 结构化（结构化输出即 Schema 白名单，注入攻击只能落到字段内）。"""
    from langchain_core.messages import HumanMessage

    if llm is None:
        from services.agent_llm import get_agent_llm

        llm = get_agent_llm("resume_parser")
    template = registry.get("resume_parser", "system")
    content = render_structured(template, ResumeStructured, {"resume_text": text})
    structured = llm.with_structured_output(ResumeStructured)
    result = await structured.ainvoke([HumanMessage(content=content)])
    return result


def needs_ocr(pages_text: list[str]) -> bool:
    """任一页文本量低于阈值 → 视为扫描件，需 OCR（agent-designs §5.1）。"""
    return any(len(p.strip()) < TEXT_MIN_CHARS_PER_PAGE for p in pages_text)


def extract_pdf_text(path: str) -> list[str]:
    """pypdf 逐页提取文本层（扫描件返回近乎空串列表）。"""
    from pypdf import PdfReader

    reader = PdfReader(path)
    return [page.extract_text() or "" for page in reader.pages]


def _render_pages_to_images(path: str) -> list[bytes]:
    """PyMuPDF 把每页渲染为 PNG 字节（视觉模型输入）。"""
    import fitz  # PyMuPDF

    doc = fitz.open(path)
    try:
        images = []
        for page in doc:
            pix = page.get_pixmap(dpi=120)
            images.append(pix.tobytes("png"))
        return images
    finally:
        doc.close()


async def _ocr_pages(images: list[bytes], vision_llm) -> str:
    """视觉模型逐页 OCR 并拼接（data URL 内联图片）。"""
    from langchain_core.messages import HumanMessage

    parts = []
    for img in images:
        data_url = f"data:image/png;base64,{base64.b64encode(img).decode('ascii')}"
        msg = HumanMessage(content=[
            {"type": "text", "text": "请逐字提取这张简历图片中的全部文字，保持原有结构与顺序。"},
            {"type": "image_url", "image_url": {"url": data_url}},
        ])
        resp = await vision_llm.ainvoke([msg])
        parts.append(resp.content)
    return "\n".join(parts)


async def parse_pdf(path: str, llm=None, vision_llm=None) -> ResumeStructured:
    """PDF 简历 → 结构化：文本层直读；扫描件渲染 → OCR → 解析。"""
    if llm is None:
        from services.agent_llm import get_agent_llm

        llm = get_agent_llm("resume_parser")
    pages_text = extract_pdf_text(path)
    text = "\n".join(pages_text)
    if needs_ocr(pages_text):
        if vision_llm is None:
            from services.agent_llm import get_vision_llm

            vision_llm = get_vision_llm()
        text = await _ocr_pages(_render_pages_to_images(path), vision_llm)
    return await parse_text(text, llm)
