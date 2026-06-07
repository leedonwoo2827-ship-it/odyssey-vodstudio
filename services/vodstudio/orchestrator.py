"""3단계 레시피 오케스트레이터.

run_pipeline(job): NotebookLM에서 대본(2단계) + 슬라이드(3단계, 20장 분할)를 받아
페이지별 이미지/텍스트까지 만들고 status=review 로 멈춘다(사용자 검수 대기).
finalize_bundle(job, ...): (검수/수정된) 대본 + 이미지로 mp4maker 번들을 쓴다.

NotebookLM 의존 단계는 services.notebooklm_service 를 통해 호출하므로, 이 모듈 자체는
실계정 없이도 import/구조 검증이 가능하다.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services import notebooklm_service as nlm
from services.vodstudio import bundle_builder, pdf_tools, prompts
from services.vodstudio.jobs import Job, STATUS_REVIEW, STATUS_DONE
from services.vodstudio.master_script import Slide, parse_master_script, parse_or_split

logger = logging.getLogger(__name__)

DATA_ROOT = Path("data") / "vodstudio"


def chunk_ranges(total: int, size: int = 20) -> List[Tuple[int, int]]:
    """[(1,20),(21,40),...] covering 1..total in `size`-slide chunks."""
    total = max(1, int(total))
    size = max(1, int(size))
    out: List[Tuple[int, int]] = []
    start = 1
    while start <= total:
        out.append((start, min(start + size - 1, total)))
        start += size
    return out


def _work_dir(job: Job) -> Path:
    d = DATA_ROOT / job.id
    (d / "pdf").mkdir(parents=True, exist_ok=True)
    (d / "imgs").mkdir(parents=True, exist_ok=True)
    return d


async def run_pipeline(job: Job) -> None:
    p = job.params
    notebook_id: str = p["notebook_id"]
    profile: Optional[str] = p.get("profile")
    total_pages: int = int(p.get("total_pages", 40))
    chunk_size: int = int(p.get("chunk_size", 20))
    language: str = p.get("language", "ko")
    fmt: str = p.get("fmt", "detailed_deck")
    length: str = p.get("length", "default")
    target_audience: str = p.get("target_audience", "일반 청중")
    objective: str = p.get("objective", "정보 전달")
    design_system: Optional[str] = p.get("design_system") or None
    add_script_as_source: bool = bool(p.get("add_script_as_source", True))

    work = _work_dir(job)

    # 0) auth ---------------------------------------------------------------
    job.set_stage("auth", 0.02)
    if not await nlm.check_auth(profile):
        raise nlm.NotebookLMError(
            "NotebookLM 인증이 필요합니다. 터미널에서 `nlm login` 후 다시 시도하세요."
        )

    # 1) (optional) design system via query ---------------------------------
    if not design_system and p.get("extract_design"):
        job.set_stage("design", 0.08)
        try:
            design_system = await nlm.query(notebook_id, prompts.DESIGN_EXTRACTION_PROMPT, profile=profile)
            job.log(f"design system extracted ({len(design_system)} chars)")
        except nlm.NotebookLMError as e:
            job.log(f"design extraction skipped: {e}")
            design_system = None

    # 2) master script (대본) ------------------------------------------------
    job.set_stage("script", 0.15)
    script_prompt = prompts.master_script_prompt(total_pages, target_audience, objective)
    raw_script = await nlm.query(notebook_id, script_prompt, profile=profile, timeout=240.0)
    (work / "master_script.txt").write_text(raw_script, encoding="utf-8")
    slides: List[Slide] = parse_master_script(raw_script)
    if not slides:
        raise nlm.NotebookLMError("마스터 대본을 파싱하지 못했습니다. (NotebookLM 응답 형식 확인 필요)")
    job.log(f"parsed {len(slides)} slide(s) from master script")

    # 2b) add the script as a source so slides match it 1:1 (optional) ------
    if add_script_as_source:
        job.set_stage("source", 0.22)
        try:
            await nlm.add_text_source(notebook_id, raw_script, title="VODStudio Master Script", profile=profile)
            job.log("master script added as notebook source")
        except nlm.NotebookLMError as e:
            job.log(f"add source failed (continuing): {e}")

    # 3) render slides in chunks (avoids 40-in-one crash) -------------------
    ranges = chunk_ranges(total_pages, chunk_size)
    job.log(f"render plan: {len(ranges)} chunk(s) {ranges}")
    seen_ids: set = set()
    pdf_paths: List[str] = []
    for ci, (start, end) in enumerate(ranges):
        frac = 0.25 + 0.45 * (ci / max(1, len(ranges)))
        job.set_stage(f"render {start}-{end}", frac)
        focus = prompts.slides_focus(
            design_system, start, end,
            is_first_chunk=(ci == 0),
            is_last_chunk=(ci == len(ranges) - 1),
        )
        await nlm.create_slide_deck(
            notebook_id, fmt=fmt, length=length, language=language, focus=focus, profile=profile,
        )
        art = await nlm.wait_for_slide_deck(notebook_id, profile=profile, exclude_ids=seen_ids)
        aid = nlm.artifact_id(art)
        if aid:
            seen_ids.add(aid)
        pdf_path = str(work / "pdf" / f"{ci + 1}-{start:02d}_{end:02d}.pdf")
        await nlm.download_slide_deck(notebook_id, pdf_path, fmt="pdf", artifact_id=aid, profile=profile)
        pdf_paths.append(pdf_path)
        job.log(f"chunk {start}-{end}: downloaded {Path(pdf_path).name}")

    # 4) merge + render pages -----------------------------------------------
    job.set_stage("merge", 0.75)
    merged = pdf_tools.merge_pdfs(pdf_paths, str(work / "merged.pdf"))
    job.set_stage("render-pages", 0.85)
    pages = pdf_tools.render_pages(merged, str(work / "imgs"), prefix="page")

    # 5) build review payload (zip slides <-> pages by position) ------------
    job.result["merged_pdf"] = merged
    _build_review_payload(job, slides, pages, design_system=design_system or "")


def _build_review_payload(job: Job, slides: List[Slide], pages, design_system: str = "") -> None:
    """Populate job.result with the review payload (slides zipped to page images)
    and move the job into the review state. Shared by the NotebookLM and manual paths."""
    warnings: List[str] = []
    if pages and len(pages) != len(slides):
        warnings.append(
            f"슬라이드 대본 {len(slides)}개 vs 렌더된 페이지 {len(pages)}개 불일치 — 순서대로 매칭했습니다."
        )
    review_slides: List[Dict[str, Any]] = []
    for i, slide in enumerate(slides):
        page = pages[i] if i < len(pages) else None
        review_slides.append({
            "index": i + 1,
            "number": slide.number,
            "title": slide.title,
            "screen_text": slide.screen_text,
            "narration": slide.narration,
            "image_index": page.index if page else None,
            "extracted_text": page.text if page else "",
        })
    job.result.update({
        "design_system": design_system or "",
        "slide_count": len(slides),
        "page_count": len(pages) if pages else 0,
        "warnings": warnings,
        "slides": review_slides,
    })
    job.set_stage("review", 0.9)
    job.status = STATUS_REVIEW
    job.log("검수 준비 완료 — 슬라이드/대본을 확인하고 번들을 생성하세요.")


def build_from_manual(job: Job, script_text: str, pdf_path: Optional[str]) -> None:
    """수동 모드: 사용자가 NotebookLM에서 직접 만든 대본 텍스트 + 슬라이드 PDF를
    받아 검수 페이로드를 만든다. NotebookLM 자동화/LLM/키 전부 불필요."""
    work = _work_dir(job)
    job.set_stage("parse", 0.3)
    slides = parse_or_split(script_text or "")
    if not slides:
        raise ValueError("대본 텍스트가 비어 있거나 파싱할 내용이 없습니다.")
    job.log(f"대본에서 {len(slides)}개 슬라이드 파싱")

    pages = []
    if pdf_path and Path(pdf_path).exists():
        job.set_stage("render-pages", 0.6)
        pages = pdf_tools.render_pages(pdf_path, str(work / "imgs"), prefix="page")
        job.log(f"PDF에서 {len(pages)}개 페이지 이미지 추출")
    else:
        job.log("슬라이드 PDF가 없어 이미지 없이 진행 (번들 생성 시 이미지 누락 경고)")

    _build_review_payload(job, slides, pages)


def render_images_only(job: Job, pdf_path: Optional[str]):
    """② 이미지 탭: 병합 PDF를 페이지별 이미지로 렌더만 한다(대본 없이). 미리보기용."""
    work = _work_dir(job)
    pages = pdf_tools.render_pages(pdf_path, str(work / "imgs"), prefix="page") if pdf_path else []
    job.result["pages"] = [
        {"index": p.index, "image_index": p.index, "extracted_text": p.text} for p in pages
    ]
    job.result["page_count"] = len(pages)
    job.log(f"{len(pages)} 페이지 이미지 생성")
    return pages


def save_with_script(
    job: Job, script_text: str, *,
    chapter: int, title: str, out_root: Optional[str] = None, subtitle: str = "",
    voice_style: str = "narrator",
) -> Dict[str, Any]:
    """③ 저장: 대본 파싱 + (이미 렌더된) 이미지와 순서대로 매칭 → mediaforge 번들 저장."""
    slides = parse_or_split(script_text or "")
    if not slides:
        raise ValueError("대본이 비어 있거나 파싱할 내용이 없습니다.")
    pages = job.result.get("pages", [])
    review: List[Dict[str, Any]] = []
    for i, s in enumerate(slides):
        pg = pages[i] if i < len(pages) else None
        review.append({
            "index": i + 1, "number": s.number, "title": s.title,
            "screen_text": s.screen_text, "narration": s.narration,
            "image_index": (pg["image_index"] if pg else None),
            "extracted_text": (pg["extracted_text"] if pg else ""),
        })
    job.result["slides"] = review
    job.result["slide_count"] = len(slides)
    if pages and len(pages) != len(slides):
        job.result["warnings"] = [f"대본 {len(slides)}개 vs 이미지 {len(pages)}개 불일치 — 순서대로 매칭"]
    return finalize_bundle(job, chapter=chapter, title=title, out_root=out_root, subtitle=subtitle, voice_style=voice_style)


def page_image_path(job: Job, image_index: int) -> Optional[Path]:
    """Resolve a rendered page PNG by 1-based index, for the image endpoint."""
    candidate = DATA_ROOT / job.id / "imgs" / f"page_{int(image_index):02d}.png"
    return candidate if candidate.exists() else None


def finalize_bundle(
    job: Job,
    *,
    chapter: int,
    title: str,
    edited_slides: Optional[List[Dict[str, Any]]] = None,
    subtitle: str = "",
    out_root: Optional[str] = None,
    voice_style: str = "narrator",
) -> Dict[str, Any]:
    """Build the mp4maker bundle from the (possibly edited) review slides.

    out_root: where to create `_assets/chNN_bundle/`. Defaults to the job's
    private data dir; pass e.g. 'D:\\00work\\260602-tech-historybook150' to write
    straight into a mediaforge/mp4maker project so it picks the bundle up."""
    src = edited_slides if edited_slides is not None else job.result.get("slides", [])
    slides: List[Slide] = []
    image_paths: List[Optional[str]] = []
    for item in src:
        slides.append(Slide(
            number=int(item.get("index") or item.get("number") or (len(slides) + 1)),
            title=item.get("title", ""),
            screen_text=item.get("screen_text", ""),
            narration=item.get("narration", ""),
        ))
        idx = item.get("image_index")
        ip = page_image_path(job, idx) if idx else None
        image_paths.append(str(ip) if ip else None)

    root = (out_root or "").strip() or str(DATA_ROOT / job.id / "bundle")
    Path(root).mkdir(parents=True, exist_ok=True)
    result = bundle_builder.build_bundle(
        root, chapter=chapter, title=title, slides=slides,
        image_paths=image_paths, subtitle=subtitle, voice_style=voice_style,
    )
    problems = bundle_builder.validate_bundle(result.bundle_dir)
    payload = {
        "bundle_dir": result.bundle_dir,
        "script_path": result.script_path,
        "scene_count": result.scene_count,
        "total_duration_seconds": result.total_duration_seconds,
        "build_issues": result.issues,
        "validation_problems": problems,
    }
    job.result["bundle"] = payload
    job.status = STATUS_DONE
    job.set_stage("done", 1.0)
    job.log(f"번들 생성 완료: {result.bundle_dir} (검증 문제 {len(problems)}건)")
    return payload
