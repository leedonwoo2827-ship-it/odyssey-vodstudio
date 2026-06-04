"""통합 문서 텍스트 추출 — odysseus 파서 재사용.

- PDF        → PyMuPDF(fitz) (services.vodstudio.pdf_tools.extract_text)
- Office/EPUB(.docx/.pptx/.xlsx/.xls/.epub) → markitdown (src.markitdown_runtime)
- 텍스트류(.txt/.md/.json/.csv/.html) → 그대로 읽기

회사 자산이 '긁히는 PDF나 오피스 파일'이라는 전제에 맞춘 입력 어댑터.
(스캔 이미지 PDF는 텍스트가 안 나옴 — 그 경우 빈 문자열)
"""

from pathlib import Path

from src.markitdown_runtime import convert_to_markdown, is_markitdown_format
from services.vodstudio.pdf_tools import extract_text as _pdf_extract

TEXT_EXTS = {".txt", ".md", ".json", ".csv", ".html", ".htm"}
SUPPORTED_EXTS = {".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".epub"} | TEXT_EXTS


def supported(path: str) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_EXTS


def is_pdf(path: str) -> bool:
    return Path(path).suffix.lower() == ".pdf"


def extract(path: str, *, max_chars: int = 20000) -> str:
    """Extract source text from a PDF/Office/text file. Truncated to max_chars."""
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return _pdf_extract(path, max_chars=max_chars)
    if is_markitdown_format(path):
        text = convert_to_markdown(path) or ""
    elif ext in TEXT_EXTS:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            text = ""
    else:
        # Unknown extension: try markitdown, then plain read.
        text = convert_to_markdown(path) or ""
        if not text:
            try:
                text = Path(path).read_text(encoding="utf-8", errors="ignore")
            except Exception:
                text = ""
    text = (text or "").strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n…(이하 생략)"
    return text
