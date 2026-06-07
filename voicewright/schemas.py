from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field


class SynthesizeRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)
    voice: str | None = None
    voice_style: str | None = None
    speed: float | None = None
    total_step: int | None = None
    lang: str = "ko"


class VoiceInfoOut(BaseModel):
    code: str
    gender: Literal["male", "female"]
    default_for_unknown: bool = False


class VoiceListResponse(BaseModel):
    voices: list[VoiceInfoOut]
    voice_map: dict[str, str]
    default: str


class ScriptScene(BaseModel):
    scene: int = Field(..., validation_alias=AliasChoices("scene", "scene_number"))
    narration_text: str = Field(..., validation_alias=AliasChoices("narration_text", "narration", "text"))
    # 자막에 들어갈 원본 텍스트 (없으면 narration_text 사용). 발음 변환 적용 후
    # narration_text는 한자어 음역 결과(TTS 입력용)이지만 자막은 원문을 유지하기 위함.
    srt_text: str | None = Field(default=None, validation_alias=AliasChoices("srt_text", "subtitle_text"))
    narration_seconds: float | None = Field(default=None, validation_alias=AliasChoices("narration_seconds", "duration", "duration_seconds"))
    voice_style: str | None = None
    image_filename: str | None = Field(default=None, validation_alias=AliasChoices("image_filename", "image", "image_file"))

    model_config = {"extra": "ignore", "populate_by_name": True}


class Script(BaseModel):
    chapter: str | int | None = None
    scenes: list[ScriptScene]

    model_config = {"extra": "ignore"}


class BatchSubmitResponse(BaseModel):
    job_id: str
    scene_count: int
    chapter: str
    status_url: str


class JobProgress(BaseModel):
    completed: int = 0
    total: int = 0
    current_scene: int | None = None


class JobStatus(BaseModel):
    job_id: str
    status: Literal["queued", "running", "done", "error"]
    progress: JobProgress
    output_dir: str
    files: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
