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

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from src.auth_helpers import require_user
from services import notebooklm_service as nlm
from services.vodstudio import orchestrator, mp4_render
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
