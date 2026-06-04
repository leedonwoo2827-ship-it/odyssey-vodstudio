"""NotebookLM integration — thin async wrapper around the `nlm` CLI
(notebooklm-mcp-cli, github.com/jacob-bd/notebooklm-mcp-cli).

We shell out to the `nlm` console script that ships with the package rather
than importing its internals, so we stay on its supported, versioned surface
and inherit its cookie/profile auth as-is. The CLI talks to NotebookLM's
internal API over httpx (no browser automation at call time), so these are
ordinary subprocess calls.

Auth model: the user runs `nlm login` once (opens Chrome, logs into their
Google account); cookies persist ~2-4 weeks per named profile. This is the
NotebookLM side of "Google sign-in" and is intentionally separate from the
app-login OAuth in src/google_oauth.py.

Pipeline this supports (verified against nlm v0.7.0):
  list_notebooks()        nlm list notebooks --json
  create_slide_deck()     nlm slides create <id> -f detailed_deck -l default --language ko --focus ... -y
  studio_status()         nlm studio status <id> --json        (poll readiness)
  wait_for_slide_deck()   polls studio_status until a slide deck is ready
  download_slide_deck()   nlm download slide-deck <id> -o <path> -f pdf|pptx
  query()                 nlm query notebook <id> "<question>" --json   (per-slide 대본)
"""

import asyncio
import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class NotebookLMError(RuntimeError):
    """Raised when an `nlm` command fails or its auth is invalid."""


@dataclass
class NlmResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def nlm_executable() -> str:
    """Resolve the `nlm` console script, preferring the one in THIS venv so we
    always use the same install we depend on regardless of the caller's PATH.
    """
    override = os.getenv("NLM_BIN")
    if override and Path(override).exists():
        return override
    scripts_dir = Path(sys.executable).parent  # venv/Scripts (win) or venv/bin (posix)
    for name in ("nlm.exe", "nlm"):
        cand = scripts_dir / name
        if cand.exists():
            return str(cand)
    found = shutil.which("nlm")
    if found:
        return found
    raise NotebookLMError(
        "`nlm` CLI not found. Install it into this environment: "
        "pip install notebooklm-mcp-cli"
    )


async def _run(args: List[str], timeout: float = 180.0) -> NlmResult:
    """Run `nlm <args>` and capture output. Never raises on non-zero exit —
    callers decide how to handle .ok / .stderr."""
    cmd = [nlm_executable(), *args]
    logger.info("nlm: %s", " ".join(args))
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        raise NotebookLMError(f"Failed to launch nlm: {e}") from e
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise NotebookLMError(f"nlm timed out after {timeout:.0f}s: {' '.join(args)}")
    return NlmResult(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=(out or b"").decode("utf-8", "replace"),
        stderr=(err or b"").decode("utf-8", "replace"),
    )


def _profile_args(profile: Optional[str]) -> List[str]:
    return ["--profile", profile] if profile else []


def _parse_json(text: str) -> Any:
    """Parse JSON from CLI output, tolerating leading/trailing non-JSON lines
    (rich/typer banners) by extracting the first {...} or [...] block."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if starts:
        start = min(starts)
        for end in range(len(text), start, -1):
            chunk = text[start:end].strip()
            if not chunk:
                continue
            try:
                return json.loads(chunk)
            except json.JSONDecodeError:
                continue
    raise NotebookLMError(f"Could not parse JSON from nlm output: {text[:300]}")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

async def check_auth(profile: Optional[str] = None) -> bool:
    """True if the given profile has valid NotebookLM cookies (`nlm login --check`)."""
    res = await _run(["login", "--check", *_profile_args(profile)], timeout=60.0)
    return res.ok


# ---------------------------------------------------------------------------
# Notebooks
# ---------------------------------------------------------------------------

async def add_text_source(
    notebook_id: str,
    text: str,
    *,
    title: str = "Master Script",
    wait: bool = True,
    profile: Optional[str] = None,
    timeout: float = 600.0,
) -> NlmResult:
    """Add a text source to the notebook (`nlm source add --text`). Used to put
    the 2단계 master script INTO the notebook so slides can match it 1:1."""
    args = ["source", "add", notebook_id, "--text", text, "--title", title]
    if wait:
        args.append("--wait")
    args += _profile_args(profile)
    res = await _run(args, timeout=timeout)
    if not res.ok:
        raise NotebookLMError(_auth_hint(res) or f"source add failed: {res.stderr[:300]}")
    return res


async def list_notebooks(profile: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return [{id, title, ...}] for the user's notebooks."""
    res = await _run(["list", "notebooks", "--json", *_profile_args(profile)], timeout=90.0)
    if not res.ok:
        raise NotebookLMError(_auth_hint(res))
    data = _parse_json(res.stdout)
    # Normalize: CLI may return a bare list or {"notebooks": [...]}.
    if isinstance(data, dict):
        data = data.get("notebooks") or data.get("items") or []
    return data if isinstance(data, list) else []


# ---------------------------------------------------------------------------
# Slide decks
# ---------------------------------------------------------------------------

async def create_slide_deck(
    notebook_id: str,
    *,
    fmt: str = "detailed_deck",      # detailed_deck | presenter_slides
    length: str = "default",          # short | default
    language: str = "ko",             # BCP-47
    focus: Optional[str] = None,      # steer page count / topic (e.g. "~40 slides ...")
    source_ids: Optional[str] = None,
    profile: Optional[str] = None,
    timeout: float = 300.0,
) -> NlmResult:
    """Kick off slide-deck generation. NotebookLM builds it asynchronously, so
    follow this with wait_for_slide_deck()."""
    args = [
        "slides", "create", notebook_id,
        "-f", fmt, "-l", length, "--language", language, "-y",
        *_profile_args(profile),
    ]
    if focus:
        args += ["--focus", focus]
    if source_ids:
        args += ["--source-ids", source_ids]
    res = await _run(args, timeout=timeout)
    if not res.ok:
        raise NotebookLMError(_auth_hint(res) or f"slides create failed: {res.stderr[:300]}")
    return res


async def studio_status(notebook_id: str, profile: Optional[str] = None) -> List[Dict[str, Any]]:
    """All studio artifacts for a notebook with their status."""
    res = await _run(["studio", "status", notebook_id, "--json", *_profile_args(profile)], timeout=90.0)
    if not res.ok:
        raise NotebookLMError(_auth_hint(res) or f"studio status failed: {res.stderr[:300]}")
    data = _parse_json(res.stdout)
    if isinstance(data, dict):
        data = data.get("artifacts") or data.get("items") or list(data.values())
    return data if isinstance(data, list) else []


def _is_slide_deck(artifact: Dict[str, Any]) -> bool:
    blob = json.dumps(artifact).lower()
    return "slide" in blob or "deck" in blob or "presentation" in blob


def _is_ready(artifact: Dict[str, Any]) -> bool:
    status = str(
        artifact.get("status") or artifact.get("state") or artifact.get("share_status") or ""
    ).lower()
    return any(k in status for k in ("ready", "complete", "done", "success", "generated"))


def artifact_id(artifact: Dict[str, Any]) -> Optional[str]:
    """Best-effort extraction of an artifact's id across CLI shape variations."""
    for key in ("id", "artifact_id", "artifactId", "studio_id", "source_id", "uuid"):
        val = artifact.get(key)
        if val:
            return str(val)
    return None


async def wait_for_slide_deck(
    notebook_id: str,
    *,
    profile: Optional[str] = None,
    exclude_ids: Optional[set] = None,
    poll_seconds: float = 10.0,
    max_wait: float = 900.0,
) -> Dict[str, Any]:
    """Poll studio status until a NEW slide-deck artifact reports ready.

    exclude_ids lets a chunked render skip decks created by earlier chunks, so
    each `slides create` call resolves to its own artifact. Returns the artifact
    (read its id via artifact_id())."""
    exclude_ids = exclude_ids or set()
    waited = 0.0
    last: Optional[Dict[str, Any]] = None
    while waited <= max_wait:
        for art in await studio_status(notebook_id, profile=profile):
            if not _is_slide_deck(art):
                continue
            if artifact_id(art) in exclude_ids:
                continue
            last = art
            if _is_ready(art):
                return art
        await asyncio.sleep(poll_seconds)
        waited += poll_seconds
    if last is not None:
        raise NotebookLMError(
            f"Slide deck not ready after {max_wait:.0f}s (last status: "
            f"{last.get('status') or last.get('state')})"
        )
    raise NotebookLMError("No new slide-deck artifact appeared; did slides create run?")


async def download_slide_deck(
    notebook_id: str,
    out_path: str,
    *,
    fmt: str = "pdf",                 # pdf | pptx
    artifact_id: Optional[str] = None,
    profile: Optional[str] = None,
    timeout: float = 300.0,
) -> str:
    """Download the slide deck to out_path. Returns the path on success."""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    args = [
        "download", "slide-deck", notebook_id,
        "-o", out_path, "-f", fmt, "--no-progress",
        *_profile_args(profile),
    ]
    if artifact_id:
        args += ["--id", artifact_id]
    res = await _run(args, timeout=timeout)
    if not res.ok or not Path(out_path).exists():
        raise NotebookLMError(_auth_hint(res) or f"download slide-deck failed: {res.stderr[:300]}")
    return out_path


# ---------------------------------------------------------------------------
# Narration script (대본) via notebook chat
# ---------------------------------------------------------------------------

async def query(
    notebook_id: str,
    question: str,
    *,
    profile: Optional[str] = None,
    source_ids: Optional[str] = None,
    timeout: float = 180.0,
) -> str:
    """Ask the notebook a question and return the answer text. Used to have
    NotebookLM author per-slide narration (대본) from the same sources."""
    args = ["query", "notebook", notebook_id, question, "--json", *_profile_args(profile)]
    if source_ids:
        args += ["--source-ids", source_ids]
    res = await _run(args, timeout=timeout)
    if not res.ok:
        raise NotebookLMError(_auth_hint(res) or f"query failed: {res.stderr[:300]}")
    try:
        data = _parse_json(res.stdout)
        if isinstance(data, dict):
            return str(data.get("answer") or data.get("text") or data.get("response") or "").strip()
        return str(data).strip()
    except NotebookLMError:
        # Non-JSON fallback: return raw stdout.
        return res.stdout.strip()


def _auth_hint(res: NlmResult) -> str:
    """If output smells like an auth failure, return a helpful message; else ''."""
    blob = (res.stdout + res.stderr).lower()
    if any(k in blob for k in ("login", "auth", "unauthor", "cookie", "expired", "sign in")):
        return (
            "NotebookLM authentication required or expired. Run `nlm login` "
            "(opens Chrome to sign in with your Google account), then retry."
        )
    return ""
