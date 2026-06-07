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
from services import llm_backend
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
    no_subtitles: bool = False      # True → 자막 안 구운 클린본(chNN_final_nosub.mp4) + .srt (유튜브용)
    dry_run: bool = False           # 검증만 (ffmpeg 호출 없이 플랜만)


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

    # ---- LLM 공급자 (codex/OpenAI ↔ agy/Gemini · API 키 없음 · 계정 로그인) ----
    async def _llm_generate(prompt: str, model: Optional[str] = None) -> str:
        """활성 공급자(codex/agy)로 대본 생성. 동기 subprocess라 스레드로 감싼다."""
        from services import llm_errors
        client = llm_backend.active_client()
        mdl = (model or "").strip() or (llm_backend.get_model() or None)

        def _call() -> str:
            return client.chat(mdl, [{"role": "user", "content": prompt}], max_tokens=8000).text

        try:
            return await asyncio.to_thread(_call)
        except llm_errors.LLMNotInstalled as e:
            raise HTTPException(503, str(e))
        except llm_errors.LLMNotAuthenticated as e:
            raise HTTPException(401, str(e))
        except llm_errors.LLMError as e:
            raise HTTPException(502, str(e))

    @router.get("/llm/status")
    async def llm_status(request: Request):
        """두 공급자(codex/agy) 설치·로그인·이메일 + 현재 활성 공급자."""
        _owner(request)
        return await asyncio.to_thread(llm_backend.status_all)

    class LLMProviderRequest(BaseModel):
        provider: str

    @router.post("/llm/provider")
    async def llm_set_provider(body: LLMProviderRequest, request: Request):
        _owner(request)
        if not llm_backend.set_provider(body.provider):
            raise HTTPException(400, f"알 수 없는 공급자: {body.provider} (codex|agy)")
        return await asyncio.to_thread(llm_backend.status_all)

    class LLMLoginRequest(BaseModel):
        provider: Optional[str] = None

    @router.post("/llm/login")
    async def llm_login(body: LLMLoginRequest, request: Request):
        """공급자 로그인 명령(codex login / agy)을 새 터미널 창에서 실행 → 브라우저 OAuth."""
        _owner(request)
        import subprocess, sys
        provider = (body.provider or "").strip() or llm_backend.get_provider()
        if provider not in llm_backend.VALID:
            raise HTTPException(400, f"알 수 없는 공급자: {provider}")
        cmd = llm_backend.login_cmd(provider)
        try:
            if sys.platform == "win32":
                # 새 콘솔 창에서 로그인 명령 실행(완료 후에도 창 유지)
                subprocess.Popen(["cmd", "/c", "start", "", "cmd", "/k", *cmd])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-a", "Terminal", cmd[0]])
            else:
                subprocess.Popen(cmd)
        except Exception as e:
            raise HTTPException(500, f"로그인 터미널 실행 실패: {e}")
        return {"ok": True, "provider": provider, "cmd": cmd}

    @router.post("/llm/logout")
    async def llm_logout(body: LLMLoginRequest, request: Request):
        _owner(request)
        provider = (body.provider or "").strip() or llm_backend.get_provider()
        if provider not in llm_backend.VALID:
            raise HTTPException(400, f"알 수 없는 공급자: {provider}")
        auth = llm_backend.login_cmd  # noqa: just to keep import warm
        from services import llm_backend as _lb
        _r, a, _c = _lb._modules(provider)
        ok = await asyncio.to_thread(a.logout)
        return {"ok": bool(ok), "provider": provider}

    # 구버전 UI 호환: /gemini/status → 활성 공급자 상태
    @router.get("/gemini/status")
    async def gemini_status(request: Request):
        _owner(request)
        st = await asyncio.to_thread(llm_backend.status_all)
        active = st.get("active", {})
        return {"installed": active.get("installed", False),
                "version": active.get("email") or st.get("label")}

    class GeminiScriptRequest(BaseModel):
        topic: str
        total_pages: int = 40
        target_audience: str = "일반 청중"
        objective: str = "정보 전달"
        model: Optional[str] = None

    @router.post("/gemini/script")
    async def gemini_script(body: GeminiScriptRequest, request: Request):
        """활성 공급자로 마스터 대본을 생성해 텍스트로 반환(수동 모드 입력칸 채우기용)."""
        _owner(request)
        prompt = (
            vod_prompts.master_script_prompt(body.total_pages, body.target_audience, body.objective)
            + f"\n\n## 주제/소스 요약\n{body.topic}\n\n위 주제로 위 형식에 맞춰 한국어로 작성하라."
        )
        text = await _llm_generate(prompt, model=body.model)
        return {"script": text}

    @router.post("/gemini/from-file")
    async def gemini_from_file(
        request: Request,
        files: List[UploadFile] = File(...),
        total_pages: int = Form(40),
        target_audience: str = Form("일반 청중"),
        objective: str = Form("정보 전달"),
        extra: str = Form(""),
    ):
        """첨부 파일 여러 개(PDF/Word/PPT/Excel)의 텍스트를 추출·병합 → 소스로 마스터 대본 생성."""
        _owner(request)
        import tempfile
        from services.vodstudio import doc_extract
        parts: List[str] = []
        names: List[str] = []
        for up in (files or []):
            if up is None:
                continue
            data = await up.read()
            if not data:
                continue
            name = up.filename or "src.pdf"
            ext = Path(name).suffix or ".pdf"
            tmp = Path(tempfile.gettempdir()) / f"vod_src_{abs(hash(data)) % 10**8}{ext}"
            tmp.write_bytes(data)
            try:
                txt = await asyncio.to_thread(doc_extract.extract, str(tmp))
            finally:
                try:
                    tmp.unlink()
                except OSError:
                    pass
            if txt and txt.strip():
                names.append(name)
                parts.append(f"\n\n===== {name} =====\n{txt.strip()}")
        source_text = "".join(parts)
        if not source_text.strip():
            raise HTTPException(400, "파일에서 텍스트를 추출하지 못했습니다(스캔 이미지 PDF 등일 수 있음).")
        prompt = (
            vod_prompts.master_script_prompt(total_pages, target_audience, objective)
            + "\n\n## 소스 내용 (아래 문서들을 모두 근거로 통합 작성)\n" + source_text
            + (f"\n\n## 추가 지시\n{extra}" if extra.strip() else "")
            + "\n\n위 소스 내용을 바탕으로 위 형식에 맞춰 한국어로 작성하라."
        )
        text = await _llm_generate(prompt)
        return {"script": text, "source_chars": len(source_text), "files": names}

    # ==================================================================
    # 자료 강화 (도커 0 · 로컬): 📚 RAG · 🔬 딥리서치 · RAG대본 · ✅검수 · 📺유튜브
    #   설명/근거: knowledge/vodstudio-rag-research.md
    # ==================================================================
    async def _extract_uploads(files: List[UploadFile]) -> List[tuple]:
        import tempfile
        from services.vodstudio import doc_extract
        out = []
        for up in (files or []):
            if up is None:
                continue
            data = await up.read()
            if not data:
                continue
            name = up.filename or "src.pdf"
            ext = Path(name).suffix or ".pdf"
            tmp = Path(tempfile.gettempdir()) / f"vod_rag_{abs(hash(data)) % 10**8}{ext}"
            tmp.write_bytes(data)
            try:
                txt = await asyncio.to_thread(doc_extract.extract, str(tmp))
            finally:
                try:
                    tmp.unlink()
                except OSError:
                    pass
            if txt and txt.strip():
                out.append((name, txt.strip()))
        return out

    @router.post("/rag/index")
    async def rag_index(request: Request, files: List[UploadFile] = File(...),
                        job_id: str = Form("")):
        """📚 RAG: 첨부 자료를 로컬 임베딩으로 색인(도커 불필요). job_id 반환(있으면 재사용)."""
        owner = _owner(request)
        from services.vodstudio import local_rag
        sources = await _extract_uploads(files)
        if not sources:
            raise HTTPException(400, "텍스트를 추출할 수 있는 파일이 없습니다.")
        job = (manager.get(job_id, owner) if job_id else None) or manager.create(owner, {"mode": "rag"})
        work = orchestrator._work_dir(job)
        try:
            stat = await asyncio.to_thread(local_rag.build_index, str(work), sources)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"색인 실패: {e}")
        job.result["rag"] = stat
        return {"job_id": job.id, **stat}

    @router.get("/jobs/{job_id}/rag-status")
    async def rag_status(job_id: str, request: Request):
        job = manager.get(job_id, _owner(request))
        if not job:
            raise HTTPException(404, "Job not found")
        from services.vodstudio import local_rag
        return local_rag.index_status(str(orchestrator._work_dir(job)))

    def _facet_queries(topic: str, audience: str, objective: str) -> List[str]:
        return [q for q in [
            topic, f"{objective} {audience}",
            "목적 정의 적용범위", "의무 책임 안전조치", "벌칙 제재 위반 과태료",
            "절차 방법 기준 신고", "권리 보호 대상",
        ] if (q or "").strip()]

    class ResearchRequest(BaseModel):
        topic: str = ""
        target_audience: str = "임직원·실무자"
        objective: str = "교육"

    @router.post("/jobs/{job_id}/research")
    async def research(job_id: str, body: ResearchRequest, request: Request):
        """🔬 딥리서치(자료 심층분석): RAG 근거로 쟁점 브리프 생성 → 잡에 저장."""
        job = manager.get(job_id, _owner(request))
        if not job:
            raise HTTPException(404, "Job not found")
        from services.vodstudio import enrich, local_rag
        work = str(orchestrator._work_dir(job))
        if not local_rag.load_index(work):
            raise HTTPException(400, "먼저 📚 RAG로 자료를 학습(색인)하세요.")
        topic = body.topic.strip() or "첨부 자료의 핵심"
        ctx = await asyncio.to_thread(
            enrich.gather_context, work, _facet_queries(topic, body.target_audience, body.objective))
        brief = await _llm_generate(enrich.build_research_prompt(topic, ctx))
        job.result["research_brief"] = brief
        return {"brief": brief}

    class RagScriptRequest(BaseModel):
        topic: str = ""
        total_pages: int = 60
        target_audience: str = "임직원·실무자"
        objective: str = "교육"
        series_key: str = "default"

    @router.post("/jobs/{job_id}/generate-script")
    async def generate_script(job_id: str, body: RagScriptRequest, request: Request):
        """✦ RAG 근거 대본 생성(자료 전문을 통째로 안 넣음 → WinError 206 없음)."""
        job = manager.get(job_id, _owner(request))
        if not job:
            raise HTTPException(404, "Job not found")
        from services.vodstudio import enrich, local_rag, series_memory
        work = str(orchestrator._work_dir(job))
        if not local_rag.load_index(work):
            raise HTTPException(400, "먼저 📚 RAG로 자료를 학습(색인)하세요. (또는 직접 대본 붙여넣기)")
        topic = body.topic.strip() or "첨부 자료"
        ctx = await asyncio.to_thread(
            enrich.gather_context, work, _facet_queries(topic, body.target_audience, body.objective))
        prompt = enrich.build_script_prompt(
            body.total_pages, body.target_audience, body.objective,
            context=ctx, brief=job.result.get("research_brief", ""),
            memory=series_memory.memory_brief(body.series_key),
        )
        text = await _llm_generate(prompt)
        return {"script": text, "grounded": True, "context_chars": len(ctx)}

    class ReviewRequest(BaseModel):
        script_text: str

    @router.post("/jobs/{job_id}/review-script")
    async def review_script(job_id: str, body: ReviewRequest, request: Request):
        """✅ 대본 자동 검수: RAG 근거와 대조해 누락/부정확/과장 점검."""
        job = manager.get(job_id, _owner(request))
        if not job:
            raise HTTPException(404, "Job not found")
        from services.vodstudio import enrich, local_rag
        work = str(orchestrator._work_dir(job))
        if not local_rag.load_index(work):
            raise HTTPException(400, "검수는 📚 RAG 근거가 필요합니다. 먼저 자료를 학습하세요.")
        ctx = await asyncio.to_thread(enrich.gather_context, work,
                                     [body.script_text[:500], "의무 벌칙 정의 절차"])
        report = await _llm_generate(enrich.build_review_prompt(body.script_text, ctx))
        return {"report": report}

    class YouTubeRequest(BaseModel):
        script_text: str
        title_hint: str = ""

    @router.post("/jobs/{job_id}/youtube-meta")
    async def youtube_meta(job_id: str, body: YouTubeRequest, request: Request):
        """📺 유튜브 메타(제목/설명/태그/챕터) 생성. 자막 있으면 타임스탬프 참고."""
        job = manager.get(job_id, _owner(request))
        if not job:
            raise HTTPException(404, "Job not found")
        from services.vodstudio import enrich, voice_studio as vs
        # 영상 전체를 덮는 압축 타임라인(씬별 누적 시작시각 + 제목) — 챕터가 끝까지 나오게.
        timeline, total_dur = "", ""
        bdir = (job.result.get("bundle") or {}).get("bundle_dir")
        if bdir and Path(bdir).exists():
            try:
                st = await asyncio.to_thread(vs.bundle_status, bdir)
                lines, cum = [], 0.0
                for s in st.get("scenes", []):
                    mm, ss = divmod(int(cum), 60)
                    lines.append(f"{mm:02d}:{ss:02d} 씬{s['scene']} {s.get('title','')}")
                    cum += float(s.get("audio_duration") or s.get("narration_seconds") or 0)
                timeline = "\n".join(lines)
                mm, ss = divmod(int(cum), 60)
                total_dur = f"{mm:02d}:{ss:02d}"
            except Exception:
                pass
        meta = await _llm_generate(
            enrich.build_youtube_prompt(body.script_text, timeline, body.title_hint, total_dur))
        return {"meta": meta}

    class SeriesMemoryRequest(BaseModel):
        series_key: str = "default"
        audience: Optional[str] = None
        objective: Optional[str] = None
        tone: Optional[str] = None

    @router.get("/series-memory")
    async def series_memory_get(request: Request, series_key: str = "default"):
        _owner(request)
        from services.vodstudio import series_memory
        return {"series_key": series_key, "memory": series_memory.get_series(series_key),
                "brief": series_memory.memory_brief(series_key),
                "store_path": str(series_memory.STORE_PATH.resolve())}

    @router.post("/series-memory")
    async def series_memory_set(body: SeriesMemoryRequest, request: Request):
        _owner(request)
        from services.vodstudio import series_memory
        s = series_memory.set_series(body.series_key, body.model_dump(exclude={"series_key"}))
        return {"series_key": body.series_key, "memory": s,
                "store_path": str(series_memory.STORE_PATH.resolve())}

    # ---- 🎨 NotebookLM 디자인 시스템 프리셋 (개인이 추가/저장 — 슬라이드 일관성) ----
    # 기본 = 대학생·일반인 교육용(초록). NotebookLM 통과 검증된 플랫벡터 구조.
    # (파스텔/일러스트 스타일 지시는 NotebookLM 안전필터에 자주 거부되므로 플랫벡터로 통일.)
    _DESIGN_DEFAULT = {
        "name": "기본 · 대학생·일반인 교육(초록 강조)",
        "text": ("Style: Flat Vector, Clean Illustration (Pure white background #FFFFFF)\n"
                 "Typography: Clean Sans-serif fonts (Title: Bold, Body: Regular)\n"
                 "Layout: Keep it spacious; Max 5 bullet points per slide\n"
                 "Tone: Clear, friendly, and easy to follow\n"
                 "Crucial:\n"
                 "Use \"Noun-ending\" or \"Short-form\" for all texts.\n"
                 "Maintain strict visual consistency with previous parts.\n"
                 "Use point colors (e.g., Teal Green) for emphasis."),
    }
    _DESIGN_ALT = {
        "name": "전문 · 플랫(파랑 강조)",
        "text": ("Style: Flat Vector, Clean Illustration (Pure white background #FFFFFF)\n"
                 "Typography: Clean Sans-serif fonts (Title: Bold, Body: Regular)\n"
                 "Layout: Keep it spacious; Max 5 bullet points per slide\n"
                 "Tone: Professional, scholarly, and organized\n"
                 "Crucial:\n"
                 "Use \"Noun-ending\" or \"Short-form\" for all texts.\n"
                 "Maintain strict visual consistency with previous parts.\n"
                 "Use point colors (e.g., Deep Blue) for emphasis."),
    }

    def _design_path() -> Path:
        return orchestrator.DATA_ROOT / "design_presets.json"

    def _load_design_presets() -> list:
        import json as _json
        p = _design_path()
        if p.is_file():
            try:
                ps = _json.loads(p.read_text(encoding="utf-8")).get("presets", [])
                if ps:
                    return ps
            except Exception:
                pass
        return [_DESIGN_DEFAULT, _DESIGN_ALT]

    @router.get("/design-presets")
    async def get_design_presets(request: Request):
        _owner(request)
        return {"presets": _load_design_presets(), "path": str(_design_path().resolve())}

    class DesignPresetRequest(BaseModel):
        name: str
        text: str

    @router.post("/design-presets")
    async def save_design_preset(body: DesignPresetRequest, request: Request):
        _owner(request)
        import json as _json
        presets = _load_design_presets()
        name = (body.name or "").strip() or "이름없음"
        existing = next((x for x in presets if x.get("name") == name), None)
        if existing:
            existing["text"] = body.text
        else:
            presets.append({"name": name, "text": body.text})
        p = _design_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_json.dumps({"presets": presets}, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"presets": presets, "saved": name}

    # ---- ② 이미지: PDF 여러 개(순서대로) → 페이지 이미지 미리보기 ----
    @router.post("/preview-images")
    async def preview_images(request: Request, pdfs: Optional[List[UploadFile]] = File(None),
                             job_id: str = Form("")):
        owner = _owner(request)
        from services.vodstudio import pdf_tools
        job = (manager.get(job_id, owner) if job_id else None) or manager.create(owner, {"mode": "tab"})
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
        images_dir = str((orchestrator._work_dir(job) / "imgs").resolve())
        return {"job_id": job.id, "page_count": len(pages),
                "images": [p.index for p in pages], "images_dir": images_dir}

    @router.post("/jobs/{job_id}/replace-image")
    async def replace_image(job_id: str, request: Request,
                            index: int = Form(...), file: UploadFile = File(...)):
        """씬 이미지 교체 — 업로드 PNG로 미리보기 이미지(+저장된 번들 이미지)를 덮어쓴다."""
        job = manager.get(job_id, _owner(request))
        if not job:
            raise HTTPException(404, "Job not found")
        data = await file.read()
        if not data:
            raise HTTPException(400, "빈 파일입니다.")
        targets = [orchestrator._work_dir(job) / "imgs" / f"page_{int(index):02d}.png"]
        # 이미 번들로 저장/불러온 상태면 번들 이미지도 함께 교체
        bdir = (job.result.get("bundle") or {}).get("bundle_dir")
        if bdir and Path(bdir).exists():
            from services.vodstudio import voice_studio as vs
            chap = vs._chap(Path(bdir)) or "00"
            bimgs = Path(bdir) / "images"
            hits = sorted(bimgs.glob(f"ch{chap}_{int(index):02d}*")) + sorted(bimgs.glob(f"{int(chap)}_{int(index):02d}*"))
            targets.append(hits[0] if hits else (bimgs / f"ch{chap}_{int(index):02d}_slide.png"))

        def _save():
            from PIL import Image
            im = Image.open(io.BytesIO(data)).convert("RGB")
            for dest in targets:
                dest.parent.mkdir(parents=True, exist_ok=True)
                im.save(str(dest), "PNG")
        try:
            await asyncio.to_thread(_save)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"이미지 변환 실패: {e}")
        return {"ok": True, "index": int(index), "targets": [str(t) for t in targets]}

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
        # 🧠 시리즈 메모리: 챕터 저장 시 일관성 정보 기록 (data/vodstudio/series_memory.json)
        try:
            from services.vodstudio import series_memory
            series_memory.remember_chapter("default", chapter=int(chapter),
                                            title=title, updated=time.strftime("%Y-%m-%d"))
        except Exception:
            pass
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
    async def _run_render(job, mode: str, resolution: str,
                          no_subtitles: bool = False, dry_run: bool = False) -> None:
        job.result["rendering"] = True
        job.result["render_error"] = ""
        job.result["render_logs"] = []
        if not dry_run:
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
            if dry_run:
                _line("[dry-run] 검증만 — ffmpeg 호출 없이 플랜 확인")
                await mp4_render.render(bdir, resolution=resolution,
                                        no_subtitles=no_subtitles,
                                        dry_run=True, on_line=_line)
                _line("[dry-run] OK — 번들 구성 이상 없음")
                return
            if mode == "silent":
                made = await mp4_render.ensure_silent_audio(bdir)
                _line(f"[silent] 무음 내레이션 WAV {made}개 생성")
            final = await mp4_render.render(bdir, resolution=resolution,
                                            no_subtitles=no_subtitles, on_line=_line)
            job.result["render"] = {"path": final, "mode": mode, "resolution": resolution,
                                    "no_subtitles": no_subtitles}
            _line(f"[ok] {final}")
        except Exception as e:  # noqa: BLE001
            job.result["render_error"] = str(e)
            _line(f"[error] {e}")
        finally:
            job.result["rendering"] = False
            job.updated = time.time()

    # ---- 🎞️ 쇼츠(세로 9:16) 렌더 ----------------------------------------
    async def _run_shorts(job, original_url: str, duration: float, bottom_mode: str) -> None:
        job.result["shorts_generating"] = True
        job.result["shorts_error"] = ""
        job.result["shorts_logs"] = []
        job.result.pop("shorts", None)
        job.updated = time.time()

        def _line(l: str) -> None:
            job.result["shorts_logs"].append(l)
            job.updated = time.time()

        try:
            bdir = (job.result.get("bundle") or {}).get("bundle_dir")
            if not bdir or not Path(bdir).exists():
                raise mp4_render.RenderError("번들이 아직 생성되지 않았습니다.")
            final = await mp4_render.render_shorts(
                bdir, original_url=original_url, duration=duration,
                bottom_mode=bottom_mode, on_line=_line)
            job.result["shorts"] = {"path": final, "original_url": original_url}
            _line(f"[ok] {final}")
        except Exception as e:  # noqa: BLE001
            job.result["shorts_error"] = str(e)
            _line(f"[error] {e}")
        finally:
            job.result["shorts_generating"] = False
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
        asyncio.create_task(_run_render(job, body.mode, body.resolution,
                                        no_subtitles=body.no_subtitles, dry_run=body.dry_run))
        return {"started": True, "mode": body.mode, "resolution": body.resolution,
                "no_subtitles": body.no_subtitles, "dry_run": body.dry_run}

    @router.get("/jobs/{job_id}/video")
    async def serve_video(job_id: str, request: Request):
        job = manager.get(job_id, _owner(request))
        if not job:
            raise HTTPException(404, "Job not found")
        render = job.result.get("render") or {}
        path = render.get("path")
        if not path or not Path(path).exists():
            raise HTTPException(404, "렌더된 영상이 없습니다")
        # inline → '새 탭에서 열기' 시 다운로드 대신 브라우저에서 재생
        return FileResponse(path, media_type="video/mp4", filename=Path(path).name,
                            content_disposition_type="inline")

    class ShortsRequest(BaseModel):
        original_url: str = ""
        duration: float = 30.0
        bottom_mode: str = "subtitle"   # subtitle | chat

    @router.post("/jobs/{job_id}/shorts")
    async def start_shorts(job_id: str, body: ShortsRequest, request: Request):
        """🎞️ 세로 9:16 ~30초 쇼츠 생성(3분할 변동 레이아웃)."""
        job = manager.get(job_id, _owner(request))
        if not job:
            raise HTTPException(404, "Job not found")
        if not (job.result.get("bundle") or {}).get("bundle_dir"):
            raise HTTPException(400, "먼저 번들을 생성하세요")
        if not mp4_render.available():
            raise HTTPException(503, "mp4maker 체크아웃이 없습니다 (./mp4maker)")
        if job.result.get("shorts_generating"):
            return {"started": False, "reason": "이미 쇼츠 생성 중"}
        asyncio.create_task(_run_shorts(job, body.original_url, body.duration, body.bottom_mode))
        return {"started": True, "original_url": body.original_url, "duration": body.duration}

    @router.get("/jobs/{job_id}/shorts-video")
    async def serve_shorts_video(job_id: str, request: Request):
        job = manager.get(job_id, _owner(request))
        if not job:
            raise HTTPException(404, "Job not found")
        shorts = job.result.get("shorts") or {}
        path = shorts.get("path")
        if not path or not Path(path).exists():
            raise HTTPException(404, "생성된 쇼츠가 없습니다")
        return FileResponse(path, media_type="video/mp4", filename=Path(path).name,
                            content_disposition_type="inline")

    class ShortsMetaRequest(BaseModel):
        script_text: str
        original_url: str = ""
        title_hint: str = ""

    @router.post("/jobs/{job_id}/shorts-meta")
    async def shorts_meta(job_id: str, body: ShortsMetaRequest, request: Request):
        """📺 쇼츠용 메타(짧은 제목/설명/태그 + 원본 링크) 생성."""
        job = manager.get(job_id, _owner(request))
        if not job:
            raise HTTPException(404, "Job not found")
        from services.vodstudio import enrich
        meta = await _llm_generate(
            enrich.build_shorts_meta_prompt(body.script_text, body.original_url, body.title_hint))
        return {"meta": meta}

    @router.post("/jobs/{job_id}/open-draft")
    async def open_draft(job_id: str, request: Request):
        """결과(draft) 폴더를 OS 파일 탐색기로 연다 (로컬 전용)."""
        job = manager.get(job_id, _owner(request))
        if not job:
            raise HTTPException(404, "Job not found")
        bdir = (job.result.get("bundle") or {}).get("bundle_dir")
        if not bdir or not Path(bdir).exists():
            raise HTTPException(400, "먼저 번들을 저장/렌더하세요")
        import os as _os, sys as _sys, subprocess as _sp
        target = Path(bdir) / "draft"
        if not target.is_dir():
            target = Path(bdir)
        try:
            if _sys.platform == "win32":
                _os.startfile(str(target))  # noqa: S606
            elif _sys.platform == "darwin":
                _sp.Popen(["open", str(target)])
            else:
                _sp.Popen(["xdg-open", str(target)])
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"폴더 열기 실패: {e}")
        return {"opened": str(target)}

    # ---- 기존 번들 불러오기 (재시작 후에도 작업 이어가기) -----------------
    @router.get("/bundles")
    async def list_existing_bundles(request: Request, root: str = ""):
        """디스크의 기존 번들 목록. root(출력폴더)를 주면 거기도 스캔."""
        _owner(request)
        import glob, json as _json
        pats = [str(orchestrator.DATA_ROOT / "*" / "bundle" / "_assets" / "*_bundle")]
        if root.strip():
            base = Path(root.strip())
            pats += [str(base / "_assets" / "*_bundle"), str(base / "*_bundle")]
        seen, out = set(), []
        for pat in pats:
            for p in glob.glob(pat):
                pp = Path(p)
                if not pp.is_dir() or str(pp) in seen:
                    continue
                seen.add(str(pp))
                title, scenes = "", 0
                hits = sorted((pp / "script").glob("*_script.json"))
                if hits:
                    try:
                        doc = _json.loads(hits[0].read_text(encoding="utf-8"))
                        title = doc.get("title") or ""
                        scenes = len(doc.get("scenes") or [])
                    except Exception:
                        pass
                draft = pp / "draft"
                has_render = draft.is_dir() and any(draft.glob("*_final*.mp4"))
                try:
                    mtime = pp.stat().st_mtime
                except OSError:
                    mtime = 0
                out.append({"bundle_dir": str(pp), "name": pp.name, "title": title,
                            "scenes": scenes, "has_render": bool(has_render), "mtime": mtime})
        out.sort(key=lambda b: b["mtime"], reverse=True)
        return {"bundles": out}

    class LoadBundleRequest(BaseModel):
        bundle_dir: str

    @router.post("/load-bundle")
    async def load_bundle(body: LoadBundleRequest, request: Request):
        """디스크의 번들을 새 잡에 연결 → 음성/자막·렌더·유튜브·폴더열기 그대로 이어감."""
        owner = _owner(request)
        import json as _json
        bdir = Path(body.bundle_dir.strip())
        if not bdir.is_dir() or not any(bdir.glob("script/*_script.json")):
            raise HTTPException(400, "유효한 번들 폴더가 아닙니다 (script/*_script.json 없음).")
        job = manager.create(owner, {"mode": "loaded"})
        job.result["bundle"] = {"bundle_dir": str(bdir)}
        try:
            doc = _json.loads(sorted(bdir.glob("script/*_script.json"))[0].read_text(encoding="utf-8"))
            job.result["slide_count"] = len(doc.get("scenes") or [])
            job.result["loaded_title"] = doc.get("title") or ""
        except Exception:
            pass
        from services.vodstudio import voice_studio as vs
        status = await asyncio.to_thread(vs.bundle_status, str(bdir))
        # 최종 영상이 있으면 ④에서 바로 재생되도록 잡에 연결
        final = status.get("final_mp4") or status.get("final_nosub_mp4")
        if final and Path(final).exists():
            job.result["render"] = {"path": final, "mode": "loaded",
                                    "no_subtitles": not status.get("final_mp4")}
        return {"job_id": job.id, "bundle_dir": str(bdir), "status": status,
                "final_mp4": status.get("final_mp4"), "final_nosub_mp4": status.get("final_nosub_mp4")}

    # ==================================================================
    # ③ 음성/자막 — 로컬 CPU TTS (VoiceWright/Supertonic-3) per-scene 편집
    # ==================================================================
    def _bundle_dir_of(job) -> str:
        bdir = (job.result.get("bundle") or {}).get("bundle_dir")
        if not bdir or not Path(bdir).exists():
            raise HTTPException(400, "먼저 ③에서 번들을 저장하세요")
        return bdir

    @router.get("/voices")
    async def list_voices(request: Request):
        _owner(request)
        from services.vodstudio import voice_studio as vs
        return {"voices": vs.list_voices()}

    class VoicePreviewRequest(BaseModel):
        voice_style: str = "narrator"
        text: Optional[str] = None

    @router.post("/voice-preview")
    async def voice_preview(body: VoicePreviewRequest, request: Request):
        """선택한 목소리로 짧은 샘플을 로컬 합성 → WAV 스트리밍('▶ 들어보기')."""
        _owner(request)
        from services.vodstudio import voice_studio as vs
        try:
            data = await vs.preview_wav_bytes(body.voice_style, body.text)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"미리듣기 합성 실패: {e}")
        return StreamingResponse(io.BytesIO(data), media_type="audio/wav")

    @router.get("/jobs/{job_id}/bundle-status")
    async def bundle_status_route(job_id: str, request: Request):
        job = manager.get(job_id, _owner(request))
        if not job:
            raise HTTPException(404, "Job not found")
        from services.vodstudio import voice_studio as vs
        bdir = _bundle_dir_of(job)
        return await asyncio.to_thread(vs.bundle_status, bdir)

    @router.get("/jobs/{job_id}/file/{kind}/{filename}")
    async def serve_bundle_file(job_id: str, kind: str, filename: str, request: Request):
        job = manager.get(job_id, _owner(request))
        if not job:
            raise HTTPException(404, "Job not found")
        if kind not in ("audio", "images", "subtitles", "draft"):
            raise HTTPException(400, "invalid kind")
        if "/" in filename or "\\" in filename or ".." in filename:
            raise HTTPException(400, "invalid filename")
        bdir = _bundle_dir_of(job)
        path = Path(bdir) / kind / filename
        if not path.exists():
            raise HTTPException(404, "file not found")
        media = {"audio": "audio/wav", "images": "image/png",
                 "subtitles": "text/plain; charset=utf-8", "draft": "video/mp4"}.get(kind)
        return FileResponse(str(path), media_type=media)

    class SceneSynthRequest(BaseModel):
        scene: int
        text: str
        srt_text: Optional[str] = None
        voice: Optional[str] = None
        speed: Optional[float] = None
        reset_subtitle: bool = False

    @router.post("/jobs/{job_id}/scene-synth")
    async def scene_synth(job_id: str, body: SceneSynthRequest, request: Request):
        """한 씬만 로컬 TTS 재합성 + 자막 타이밍 재계산."""
        job = manager.get(job_id, _owner(request))
        if not job:
            raise HTTPException(404, "Job not found")
        from services.vodstudio import voice_studio as vs
        bdir = _bundle_dir_of(job)
        try:
            return await vs.synth_scene_text(
                bdir, body.scene, body.text, srt_text=body.srt_text,
                voice=body.voice, speed=body.speed, reset_subtitle=body.reset_subtitle,
            )
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, str(e))

    class SceneCue(BaseModel):
        text: str
        start: float
        end: float

    class SceneSrtRequest(BaseModel):
        scene: int
        cues: List[SceneCue]

    @router.post("/jobs/{job_id}/scene-srt")
    async def scene_srt(job_id: str, body: SceneSrtRequest, request: Request):
        """편집한 자막 큐(시작/끝/텍스트)를 per-scene SRT로 저장 + 통합 SRT 갱신."""
        job = manager.get(job_id, _owner(request))
        if not job:
            raise HTTPException(404, "Job not found")
        from services.vodstudio import voice_studio as vs
        bdir = _bundle_dir_of(job)
        try:
            return await asyncio.to_thread(
                vs.save_scene_cues, bdir, body.scene, [c.model_dump() for c in body.cues]
            )
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, str(e))

    class PronRequest(BaseModel):
        text: str

    @router.post("/to-pronunciation")
    async def to_pronunciation(body: PronRequest, request: Request):
        _owner(request)
        from services.vodstudio import voice_studio as vs
        return {"text": await asyncio.to_thread(vs.to_pronunciation, body.text)}

    # ---- 📖 발음 사전 편집 (config/pronunciation_map.yaml) ----
    @router.get("/pronunciation")
    async def get_pronunciation(request: Request):
        _owner(request)
        from voicewright import settings as _S
        from voicewright.pronunciation import load_pronunciation_map
        path = _S.load().pronunciation_map_path
        pm = load_pronunciation_map(path)
        return {"rules": pm.rules, "path": str(path.resolve())}

    class PronSaveRequest(BaseModel):
        rules: Dict[str, str]

    @router.post("/pronunciation")
    async def save_pronunciation(body: PronSaveRequest, request: Request):
        _owner(request)
        import yaml
        from voicewright import settings as _S
        path = _S.load().pronunciation_map_path
        clean = {str(k).strip(): str(v).strip() for k, v in (body.rules or {}).items()
                 if str(k).strip() and str(v).strip()}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(yaml.safe_dump({"rules": clean}, allow_unicode=True, sort_keys=True),
                            encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"발음 사전 저장 실패: {e}")
        return {"saved": len(clean), "path": str(path.resolve())}

    class SynthAllRequest(BaseModel):
        only: Optional[List[int]] = None
        voice_override: Optional[str] = None
        speed: Optional[float] = None

    async def _run_synth(job, only, voice_override, speed) -> None:
        from services.vodstudio import voice_studio as vs
        job.result["synthesizing"] = True
        job.result["synth_error"] = ""
        job.result["synth_progress"] = {"completed": 0, "total": 0, "scene": None}
        job.updated = time.time()

        def _cb(completed: int, total: int, scene):
            job.result["synth_progress"] = {"completed": completed, "total": total, "scene": scene}
            job.updated = time.time()

        try:
            bdir = (job.result.get("bundle") or {}).get("bundle_dir")
            if not bdir or not Path(bdir).exists():
                raise RuntimeError("번들이 아직 생성되지 않았습니다.")
            res = await vs.synthesize(bdir, only=only, voice_override=voice_override,
                                      speed=speed, on_progress=_cb)
            job.result["synth"] = {"scenes_done": res.get("scenes_done"),
                                   "chapter_srt": res.get("chapter_srt")}
        except Exception as e:  # noqa: BLE001
            job.result["synth_error"] = str(e)
        finally:
            job.result["synthesizing"] = False
            job.updated = time.time()

    @router.post("/jobs/{job_id}/synth")
    async def synth_all(job_id: str, body: SynthAllRequest, request: Request):
        """전체(또는 선택) 씬을 로컬 TTS로 합성 — 백그라운드 잡, /jobs/{id} 로 진행 폴링."""
        job = manager.get(job_id, _owner(request))
        if not job:
            raise HTTPException(404, "Job not found")
        _bundle_dir_of(job)
        if job.result.get("synthesizing"):
            return {"started": False, "reason": "이미 합성 중"}
        asyncio.create_task(_run_synth(job, body.only, body.voice_override, body.speed))
        return {"started": True}

    @router.post("/jobs/{job_id}/clear-draft")
    async def clear_draft(job_id: str, request: Request):
        job = manager.get(job_id, _owner(request))
        if not job:
            raise HTTPException(404, "Job not found")
        bdir = _bundle_dir_of(job)
        draft = Path(bdir) / "draft"
        removed = 0
        if draft.is_dir():
            for f in draft.iterdir():
                if f.is_file():
                    try:
                        f.unlink(); removed += 1
                    except OSError:
                        pass
        job.result.pop("render", None)
        return {"removed": removed}

    logger.info("VOD Studio routes initialized")
    return router
