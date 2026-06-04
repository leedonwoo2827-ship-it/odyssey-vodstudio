"""PDF 병합 + 페이지별 PNG 렌더 + 텍스트 추출 (PyMuPDF / fitz).

3단계 렌더링이 20장 단위로 PDF를 여러 개 만들기 때문에 먼저 병합하고,
페이지별로 PNG(검수/번들 이미지)와 텍스트(검수 대조)를 뽑는다.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


@dataclass
class PageRender:
    index: int          # 1-based page index across the merged deck
    image_path: str     # rendered PNG
    text: str           # extracted text (for review vs. 화면 텍스트)


def merge_pdfs(pdf_paths: List[str], out_path: str) -> str:
    """Concatenate PDFs in order into out_path. Returns out_path."""
    paths = [p for p in pdf_paths if p and Path(p).exists()]
    if not paths:
        raise FileNotFoundError("No input PDFs to merge")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    merged = fitz.open()
    try:
        for p in paths:
            with fitz.open(p) as src:
                merged.insert_pdf(src)
        merged.save(out_path)
    finally:
        merged.close()
    logger.info("Merged %d PDF(s) -> %s", len(paths), out_path)
    return out_path


def extract_text(pdf_path: str, *, max_chars: int = 20000) -> str:
    """Concatenate the text of all pages (for feeding an LLM as source).

    Capped at max_chars to keep LLM token/quota use sane on big PDFs — the
    caller notes when truncation happened."""
    parts: List[str] = []
    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc, start=1):
            t = page.get_text("text").strip()
            if t:
                parts.append(f"[p{i}] {t}")
    text = "\n\n".join(parts).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n…(이하 생략)"
    return text


def render_pages(pdf_path: str, out_dir: str, *, dpi: int = 150, prefix: str = "page") -> List[PageRender]:
    """Render each PDF page to a PNG and extract its text.

    Returns one PageRender per page (1-based). PNG filenames are
    {prefix}_{index:02d}.png so callers can rename into the bundle scheme.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    results: List[PageRender] = []
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc, start=1):
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            img_path = out / f"{prefix}_{i:02d}.png"
            pix.save(str(img_path))
            text = page.get_text("text").strip()
            results.append(PageRender(index=i, image_path=str(img_path), text=text))
    logger.info("Rendered %d page(s) from %s -> %s", len(results), pdf_path, out_dir)
    return results
