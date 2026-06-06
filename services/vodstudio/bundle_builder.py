"""mp4maker 호환 번들 빌더.

출력 레이아웃 (mp4maker BUNDLE_FORMAT.md):
    _assets/chNN_bundle/
      script/chNN_script.json
      images/chNN_XX_slide.png
      audio/        (비움 — 다운스트림 SuperTonic3가 채움)
      subtitles/    (비움)
      draft/

chNN_script.json 핵심 필드: version, chapter, title, aspect_ratio,
total_duration_seconds, narration_style, scenes[]. scene: scene(index),
scene_type, title, narration_text, narration_seconds, image_filename, scene_meta.
파일명 규칙 chNN_XX_*.png 은 mp4maker가 exact→stem→prefix glob 으로 매칭한다.
"""

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.vodstudio.master_script import Slide, estimate_narration_seconds

logger = logging.getLogger(__name__)

IMAGE_EXT = ".png"


@dataclass
class BundleResult:
    bundle_dir: str
    script_path: str
    scene_count: int
    total_duration_seconds: int
    issues: List[str]


def _scene_type(index: int, total: int) -> str:
    if index == 1:
        return "opening_title"
    if index == total:
        return "closing"
    return "body"


def build_bundle(
    out_root: str,
    *,
    chapter: int,
    title: str,
    slides: List[Slide],
    image_paths: List[Optional[str]],
    subtitle: str = "",
    aspect_ratio: str = "16:9",
    narration_style: Optional[Dict[str, str]] = None,
    voice_style: str = "narrator",
) -> BundleResult:
    """Write a mp4maker-compatible bundle under out_root/_assets/chNN_bundle/.

    slides and image_paths are zipped by position; mismatched lengths are
    tolerated (extra of either is reported in issues). image_paths entries may
    be None (no image yet) — the scene is still written, just without a copied
    image, which the review UI can flag.
    """
    issues: List[str] = []
    ch = f"ch{int(chapter):02d}"
    bundle_dir = Path(out_root) / "_assets" / f"{ch}_bundle"
    for sub in ("script", "images", "audio", "subtitles", "draft"):
        (bundle_dir / sub).mkdir(parents=True, exist_ok=True)

    n = len(slides)
    if len(image_paths) != n:
        issues.append(f"slide count ({n}) != image count ({len(image_paths)}); zipped by position")

    scenes: List[Dict[str, Any]] = []
    total_seconds = 0
    for i, slide in enumerate(slides, start=1):
        img_src = image_paths[i - 1] if i - 1 < len(image_paths) else None
        image_filename = f"{ch}_{i:02d}_slide{IMAGE_EXT}"
        if img_src and Path(img_src).exists():
            shutil.copyfile(img_src, bundle_dir / "images" / image_filename)
        else:
            issues.append(f"scene {i}: missing image (expected {image_filename})")

        narration = (slide.narration or slide.screen_text or slide.title or "").strip()
        if not narration:
            issues.append(f"scene {i}: empty narration_text")
        secs = estimate_narration_seconds(narration)
        total_seconds += secs
        scenes.append({
            "scene": i,
            "scene_type": _scene_type(i, n),
            "title": slide.title or f"Slide {i}",
            "narration_text": narration,
            "narration_seconds": secs,
            "image_filename": image_filename,
            "voice_style": (slide.voice_style or voice_style),  # VoiceWright(Supertonic3) 매핑용
            "scene_meta": {
                "screen_text": slide.screen_text,
                "source_slide_number": slide.number,
            },
        })

    doc = {
        "version": "1.0",
        "chapter": int(chapter),
        "title": title or f"Chapter {chapter}",
        "subtitle": subtitle,
        "aspect_ratio": aspect_ratio,
        "total_duration_seconds": total_seconds,
        "default_model": "nano_banana",
        "narration_style": narration_style or {"tone": "professional", "person": "3인칭", "tempo": "measured"},
        "scenes": scenes,
    }
    script_path = bundle_dir / "script" / f"{ch}_script.json"
    script_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Built bundle %s (%d scenes, ~%ds)", bundle_dir, n, total_seconds)
    return BundleResult(
        bundle_dir=str(bundle_dir),
        script_path=str(script_path),
        scene_count=n,
        total_duration_seconds=total_seconds,
        issues=issues,
    )


def validate_bundle(bundle_dir: str) -> List[str]:
    """Light pre-flight mirroring mp4maker load_bundle expectations.

    Checks the script JSON parses, every scene has narration_text, and each
    scene's image is resolvable by mp4maker's matching (exact -> stem -> prefix
    glob on chNN_XX_*). Returns a list of problems ([] == looks good).
    """
    problems: List[str] = []
    bdir = Path(bundle_dir)
    scripts = list((bdir / "script").glob("ch*_script.json"))
    if not scripts:
        return [f"no script/ch*_script.json under {bundle_dir}"]
    try:
        doc = json.loads(scripts[0].read_text(encoding="utf-8"))
    except Exception as e:
        return [f"script JSON failed to parse: {e}"]

    images_dir = bdir / "images"
    for sc in doc.get("scenes", []):
        idx = sc.get("scene")
        if not (sc.get("narration_text") or "").strip():
            problems.append(f"scene {idx}: empty narration_text")
        fname = sc.get("image_filename") or ""
        # mp4maker matching: exact -> stem (any ext) -> prefix glob chNN_XX_*
        exact = images_dir / fname
        stem = fname.rsplit(".", 1)[0]
        prefix = "_".join(stem.split("_")[:2])  # chNN_XX
        matched = (
            exact.exists()
            or any(images_dir.glob(stem + ".*"))
            or any(images_dir.glob(prefix + "_*"))
        )
        if not matched:
            problems.append(f"scene {idx}: no image matches '{fname}' in images/")
    return problems
