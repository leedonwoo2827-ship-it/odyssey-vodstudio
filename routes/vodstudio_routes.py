"""영상공방 (VOD Studio) API routes.

NotebookLM 슬라이드/대본 → 검수 → mp4maker 번들 파이프라인을 구동한다.
모든 라우트는 (인증 활성화 시) 로그인 사용자에 한정되며, 잡은 사용자별로 격리된다.
"""

import asyncio
import io
import logging
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from src.auth_helpers import require_user
from services import notebooklm_service as nlm
from services import gemini_cli_service as gemini
from services.vodstudio import orchestrator, mp4_render
from services.vodstudio import prompts as vod_prompts
from services.vodstudio.jobs import JobManager

logger = logging.getLogger(__name__)


def _owner(request: Request) -> str:
    return require_user(request) or "local"


class StartJobRequest(BaseModel):
    notebook_id: str
    total_pages: int = 40
    target_audience: str = "일반 청중"
    objective: str = "정보 전달"
    design_system: Optional[str] = None
    extract_design: bool = False
    add_script_as_source: bool = True
    chunk_size: int = 20
    language: str = "ko"
    fmt: str = "detailed_deck"      # detailed_deck | presenter_slides
    length: str = "default"          # short | default
    profile: Optional[str] = None


class SlideEdit(BaseModel):
    index: int
    number: Optional[int] = None
    title: str = ""
    screen_text: str = ""
    narration: str = ""
    image_index: Optional[int] = None


class BuildBundleRequest(BaseModel):
    chapter: int = 1
    title: str = "VOD Studio Deck"
    subtitle: str = ""
    slides: Optional[List[SlideEdit]] = None
    output_dir: Optional[str] = None   # 예: D:\00work\260602-tech-historybook150 (→ _assets\chNN_bundle\)


class RenderRequest(BaseModel):
    mode: str = "silent"            # silent (placeholder audio) | voiced (user-supplied WAVs)
    resolution: str = "1920x1080"   # or 1280x720 for a faster preview


def setup_vodstudio_routes() -> APIRouter:
    router = APIRouter(prefix="/api/vodstudio", tags=["vodstudio"])
    manager = JobManager()

    @router.get("/auth")
    async def auth_status(request: Request, profile: Optional[str] = None):
        """Is the NotebookLM (`nlm`) session authenticated for this profile?"""
        _owner(request)
        try:
            ok = await nlm.check_auth(profile)
        except nlm.NotebookLMError as e:
            return {"authenticated": False, "error": str(e)}
        return {"authenticated": ok}

    @router.get("/notebooks")
    async def notebooks(request: Request, profile: Optional[str] = None):
        _owner(request)
        try:
            items = await nlm.list_notebooks(profile)
        except nlm.NotebookLMError as e:
            raise HTTPException(502, str(e))
        return {"notebooks": items}

    @router.post("/jobs")
    async def start_job(body: StartJobRequest, request: Request):
        owner = _owner(request)
        if not body.notebook_id.strip():
            raise HTTPException(400, "notebook_id is required")
        job = manager.create(owner, body.model_dump())
        manager.run(job, orchestrator.run_pipeline)
        return {"job_id": job.id, "status": job.status}

    @router.post("/manual")
    async def manual_build(
        request: Request,
        script_text: str = Form(...),
        pdfs: Optional[List[UploadFile]] = File(None),
    ):
        """대본 텍스트 + 슬라이드 PDF 여러 개(순서대로)를 받아 검수 단계까지 만든다.
        PDF들은 넣은 순서대로 병합되어 페이지가 1→N 으로 매겨진다(번호 로직)."""
        owner = _owner(request)
        if not (script_text or "").strip():
            raise HTTPException(400, "대본 텍스트가 비어 있습니다")
        from services.vodstudio import pdf_tools
        job = manager.create(owner, {"mode": "manual"})
        work = orchestrator._work_dir(job)
        merged_path = None
        saved = []
        for i, up in enumerate(pdfs or []):
            if up is None or not (up.filename or "").lower().endswith(".pdf"):
                continue
            p = work / f"slides_{i + 1:02d}.pdf"
            p.write_bytes(await up.read())
            saved.append(str(p))
        if saved:
            merged_path = str(work / "merged.pdf")
            await asyncio.to_thread(pdf_tools.merge_pdfs, saved, merged_path)
        try:
            await asyncio.to_thread(orchestrator.build_from_manual, job, script_text, merged_path)
        except Exception as e:  # noqa: BLE001
            job.status = "error"; job.error = str(e)
            raise HTTPException(400, str(e))
        return job.to_public()

    @router.post("/save-bundle")
    async def save_bundle(
        request: Request,
        script_text: str = Form(...),
        chapter: int = Form(2),
        title: str = Form("VOD Studio Deck"),
        output_dir: str = Form(""),
        pdfs: Optional[List[UploadFile]] = File(None),
    ):
        """한 방에: 대본(텍스트) + 슬라이드 PDF(순서대로) → 파싱·병합·이미지 → mediaforge
        번들(script/chNN_script.json + images/) 로 저장. '검수/번들생성' 통합."""
        owner = _owner(request)
        if not (script_text or "").strip():
            raise HTTPException(400, "대본이 비어 있습니다")
        from services.vodstudio import pdf_tools
        job = manager.create(owner, {"mode": "save"})
        work = orchestrator._work_dir(job)
        saved = []
        for i, up in enumerate(pdfs or []):
            if up is None or not (up.filename or "").lower().endswith(".pdf"):
                continue
            p = work / f"slides_{i + 1:02d}.pdf"
            p.write_bytes(await up.read())
            saved.append(str(p))
        merged = None
        if saved:
            merged = str(work / "merged.pdf")
            await asyncio.to_thread(pdf_tools.merge_pdfs, saved, merged)
        try:
            await asyncio.to_thread(orchestrator.build_from_manual, job, script_text, merged)
            payload = await asyncio.to_thread(
                orchestrator.finalize_bundle, job,
                chapter=int(chapter), title=title, out_root=(output_dir or None),
            )
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, str(e))
        try:
            payload["script_json"] = Path(payload["script_path"]).read_text(encoding="utf-8")
        except Exception:
            payload["script_json"] = ""
        payload["slide_count"] = job.result.get("slide_count")
        payload["page_count"] = job.result.get("page_count")
        payload["job_id"] = job.id
        return payload

    # ---- Gemini CLI (구글 로그인 · API 키 없음 · 무료 티어) ----
    @router.get("/gemini/status")
    async def gemini_status(request: Request):
        _owner(request)
        if not gemini.available():
            return {"installed": False, "version": None}
        return {"installed": True, "version": await gemini.version()}

    class GeminiScriptRequest(BaseModel):
        topic: str
        total_pages: int = 40
        target_audience: str = "일반 청중"
        objective: str = "정보 전달"
        model: Optional[str] = None

    @router.post("/gemini/script")
    async def gemini_script(body: GeminiScriptRequest, request: Request):
        """Gemini CLI로 마스터 대본을 생성해 텍스트로 반환(수동 모드 입력칸 채우기용)."""
        _owner(request)
        if not gemini.available():
            raise HTTPException(503, "gemini CLI 미설치 — Node + `npm i -g @google/gemini-cli` 후 `gemini` 로그인")
        prompt = (
            vod_prompts.master_script_prompt(body.total_pages, body.target_audience, body.objective)
            + f"\n\n## 주제/소스 요약\n{body.topic}\n\n위 주제로 위 형식에 맞춰 한국어로 작성하라."
        )
        try:
            text = await gemini.generate(prompt, model=body.model)
        except gemini.GeminiCliError as e:
            raise HTTPException(502, str(e))
        return {"script": text}

    @router.post("/gemini/from-file")
    async def gemini_from_file(
        request: Request,
        file: UploadFile = File(...),
        total_pages: int = Form(40),
        target_audience: str = Form("일반 청중"),
        objective: str = Form("정보 전달"),
        extra: str = Form(""),
    ):
        """첨부 파일(PDF/Word/PPT/Excel)의 텍스트를 추출 → 소스로 Gemini가 마스터 대본 생성."""
        _owner(request)
        if not gemini.available():
            raise HTTPException(503, "gemini CLI 미설치 — Node + `npm i -g @google/gemini-cli` 후 `gemini` 로그인")
        import tempfile
        from services.vodstudio import doc_extract
        data = await file.read()
        ext = Path(file.filename or "src.pdf").suffix or ".pdf"
        tmp = Path(tempfile.gettempdir()) / f"vod_src_{abs(hash(data)) % 10**8}{ext}"
        tmp.write_bytes(data)
        try:
            source_text = await asyncio.to_thread(doc_extract.extract, str(tmp))
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
        if not source_text.strip():
            raise HTTPException(400, "파일에서 텍스트를 추출하지 못했습니다(스캔 이미지 PDF 등일 수 있음).")
        prompt = (
            vod_prompts.master_script_prompt(total_pages, target_audience, objective)
            + "\n\n## 소스 내용 (이 내용을 근거로 작성)\n" + source_text
            + (f"\n\n## 추가 지시\n{extra}" if extra.strip() else "")
            + "\n\n위 소스 내용을 바탕으로 위 형식에 맞춰 한국어로 작성하라."
        )
        try:
            text = await gemini.generate(prompt)
        except gemini.GeminiCliError as e:
            raise HTTPException(502, str(e))
        return {"script": text, "source_chars": len(source_text)}

    # ---- ② 이미지: PDF 여러 개(순서대로) → 페이지 이미지 미리보기 ----
    @router.post("/preview-images")
    async def preview_images(request: Request, pdfs: Optional[List[UploadFile]] = File(None)):
        owner = _owner(request)
        from services.vodstudio import pdf_tools
        job = manager.create(owner, {"mode": "tab"})
        work = orchestrator._work_dir(job)
        saved = []
        for i, up in enumerate(pdfs or []):
            if up is None or not (up.filename or "").lower().endswith(".pdf"):
                continue
            p = work / f"slides_{i + 1:02d}.pdf"
            p.write_bytes(await up.read())
            saved.append(str(p))
        merged = None
        if saved:
            merged = str(work / "merged.pdf")
            await asyncio.to_thread(pdf_tools.merge_pdfs, saved, merged)
        pages = await asyncio.to_thread(orchestrator.render_images_only, job, merged)
        return {"job_id": job.id, "page_count": len(pages), "images": [p.index for p in pages]}

    # ---- ③ 저장: 대본 + (렌더된)이미지 → mediaforge 번들 ----
    @router.post("/jobs/{job_id}/save")
    async def save_job(
        job_id: str, request: Request,
        script_text: str = Form(...),
        chapter: int = Form(2),
        title: str = Form("VOD Studio Deck"),
        output_dir: str = Form(""),
        voice_style: str = Form("narrator"),
    ):
        job = manager.get(job_id, _owner(request))
        if not job:
            raise HTTPException(404, "Job not found")
        try:
            payload = await asyncio.to_thread(
                orchestrator.save_with_script, job, script_text,
                chapter=int(chapter), title=title, out_root=(output_dir or None),
                voice_style=voice_style,
            )
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, str(e))
        try:
            payload["script_json"] = Path(payload["script_path"]).read_text(encoding="utf-8")
        except Exception:
            payload["script_json"] = ""
        payload["slide_count"] = job.result.get("slide_count")
        payload["page_count"] = job.result.get("page_count")
        return payload

    # ---- ③ 음성: 무음(미리보기) WAV 생성 — 빠진 씬만 채움 ----
    @router.post("/jobs/{job_id}/gen-audio")
    async def gen_audio(job_id: str, request: Request):
        job = manager.get(job_id, _owner(request))
        if not job:
            raise HTTPException(404, "Job not found")
        bdir = (job.result.get("bundle") or {}).get("bundle_dir")
        if not bdir:
            raise HTTPException(400, "먼저 ③에서 번들을 저장하세요")
        made = await mp4_render.ensure_silent_audio(bdir)
        status = mp4_render.audio_status(bdir)
        status["generated"] = made
        return status

    @router.get("/jobs")
    async def list_jobs(request: Request):
        owner = _owner(request)
        return {"jobs": [j.to_public() for j in manager.list_for(owner)]}

    @router.get("/jobs/{job_id}")
    async def get_job(job_id: str, request: Request):
        job = manager.get(job_id, _owner(request))
        if not job:
            raise HTTPException(404, "Job not found")
        return job.to_public()

    @router.get("/jobs/{job_id}/image/{image_index}")
    async def job_image(job_id: str, image_index: int, request: Request):
        job = manager.get(job_id, _owner(request))
        if not job:
            raise HTTPException(404, "Job not found")
        path = orchestrator.page_image_path(job, image_index)
        if not path:
            raise HTTPException(404, "Image not found")
        return FileResponse(str(path), media_type="image/png")

    @router.post("/jobs/{job_id}/bundle")
    async def build_bundle(job_id: str, body: BuildBundleRequest, request: Request):
        job = manager.get(job_id, _owner(request))
        if not job:
            raise HTTPException(404, "Job not found")
        if not job.result.get("slides"):
            raise HTTPException(400, "Job has no review data yet (still running?)")
        edited = [s.model_dump() for s in body.slides] if body.slides is not None else None
        try:
            payload = orchestrator.finalize_bundle(
                job, chapter=body.chapter, title=body.title,
                subtitle=body.subtitle, edited_slides=edited,
                out_root=body.output_dir,
            )
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"Bundle build failed: {e}")
        return payload

    @router.get("/jobs/{job_id}/bundle/download")
    async def download_bundle(job_id: str, request: Request):
        job = manager.get(job_id, _owner(request))
        if not job:
            raise HTTPException(404, "Job not found")
        bundle = job.result.get("bundle") or {}
        bundle_dir = bundle.get("bundle_dir")
        if not bundle_dir or not Path(bundle_dir).exists():
            raise HTTPException(404, "Bundle not built yet")
        # Zip the _assets/chNN_bundle tree on the fly.
        root = Path(bundle_dir)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in root.rglob("*"):
                if f.is_file():
                    zf.write(f, arcname=str(Path(root.name) / f.relative_to(root)))
        buf.seek(0)
        fname = f"{root.name}.zip"
        return StreamingResponse(
            buf, media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    # ---- mp4maker render (final step) ------------------------------------
    async def _run_render(job, mode: str, resolution: str) -> None:
        job.result["rendering"] = True
        job.result["render_error"] = ""
        job.result["render_logs"] = []
        job.result.pop("render", None)
        job.updated = time.time()

        def _line(l: str) -> None:
            job.result["render_logs"].append(l)
            job.updated = time.time()

        try:
            bundle = job.result.get("bundle") or {}
            bdir = bundle.get("bundle_dir")
            if not bdir or not Path(bdir).exists():
                raise mp4_render.RenderError("번들이 아직 생성되지 않았습니다.")
            if mode == "silent":
                made = await mp4_render.ensure_silent_audio(bdir)
                _line(f"[silent] 무음 내레이션 WAV {made}개 생성")
            final = await mp4_render.render(bdir, resolution=resolution, on_line=_line)
            job.result["render"] = {"path": final, "mode": mode, "resolution": resolution}
            _line(f"[ok] {final}")
        except Exception as e:  # noqa: BLE001
            job.result["render_error"] = str(e)
            _line(f"[error] {e}")
        finally:
            job.result["rendering"] = False
            job.updated = time.time()

    @router.get("/jobs/{job_id}/audio-status")
    async def audio_status_route(job_id: str, request: Request):
        job = manager.get(job_id, _owner(request))
        if not job:
            raise HTTPException(404, "Job not found")
        bundle = job.result.get("bundle") or {}
        bdir = bundle.get("bundle_dir")
        if not bdir:
            raise HTTPException(400, "번들이 아직 생성되지 않았습니다")
        try:
            return mp4_render.audio_status(bdir)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, str(e))

    @router.post("/jobs/{job_id}/render")
    async def start_render(job_id: str, body: RenderRequest, request: Request):
        job = manager.get(job_id, _owner(request))
        if not job:
            raise HTTPException(404, "Job not found")
        if not (job.result.get("bundle") or {}).get("bundle_dir"):
            raise HTTPException(400, "먼저 번들을 생성하세요")
        if not mp4_render.available():
            raise HTTPException(503, "mp4maker 체크아웃이 없습니다 (./mp4maker)")
        if job.result.get("rendering"):
            return {"started": False, "reason": "이미 렌더 중"}
        asyncio.create_task(_run_render(job, body.mode, body.resolution))
        return {"started": True, "mode": body.mode, "resolution": body.resolution}

    @router.get("/jobs/{job_id}/video")
    async def serve_video(job_id: str, request: Request):
        job = manager.get(job_id, _owner(request))
        if not job:
            raise HTTPException(404, "Job not found")
        render = job.result.get("render") or {}
        path = render.get("path")
        if not path or not Path(path).exists():
            raise HTTPException(404, "렌더된 영상이 없습니다")
        return FileResponse(path, media_type="video/mp4", filename=Path(path).name)

    logger.info("VOD Studio routes initialized")
    return router
