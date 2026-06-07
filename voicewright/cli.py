from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

from . import settings as settings_module
from .audio_io import write_wav
from .batch import parse_script, run_batch
from .engine import Engine
from .voices import ALL_VOICE_CODES, load_voice_map

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="voicewright — 로컬 한국어 TTS (Supertonic)",
)
console = Console()


@app.command()
def synth(
    text: str = typer.Argument(..., help="합성할 한국어 텍스트"),
    voice: str = typer.Option("F2", "--voice", "-v", help=f"보이스 코드. {', '.join(ALL_VOICE_CODES)}"),
    out: Path = typer.Option(Path("synth.wav"), "--out", "-o", help="출력 wav 경로"),
    speed: float = typer.Option(1.00, "--speed", help="발화 속도 (0.9~1.5)"),
    total_step: int = typer.Option(5, "--total-step", help="디노이징 스텝 (높을수록 품질↑/시간↑)"),
    lang: str = typer.Option("ko", "--lang", help="언어 코드"),
):
    """한 문장을 합성해 wav로 저장."""
    async def _run():
        engine = await Engine.get()
        with console.status(f"[bold]합성 중 ({voice}, {engine.providers[0]})..."):
            wav = await engine.synth(text, voice_code=voice, lang=lang, total_step=total_step, speed=speed)
        write_wav(out, wav, engine.sample_rate)
        console.print(f"[green]저장됨[/green] {out}  (sr={engine.sample_rate}Hz, samples={len(wav)})")

    asyncio.run(_run())


@app.command()
def batch(
    script_path: Path = typer.Argument(..., exists=True, dir_okay=False, help="ch{NN}_script.json 경로"),
    chapter: str = typer.Option(None, "--chapter", help="챕터 ID 강제 지정 (예: 05)"),
    output_root: Path = typer.Option(None, "--output-root", help="기본은 ./workspace"),
    voice_override: str = typer.Option(None, "--voice-override", help="모든 scene을 이 보이스로 강제"),
    speed: float = typer.Option(None, "--speed"),
    total_step: int = typer.Option(None, "--total-step"),
):
    """script JSON을 받아 챕터의 모든 scene을 합성."""
    raw = script_path.read_bytes()
    script = parse_script(raw)

    async def _run():
        engine = await Engine.get()
        s = settings_module.load()
        vmap = load_voice_map(s.voice_map_path)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task_id = progress.add_task(f"[cyan]합성 중 ({engine.providers[0]})", total=len(script.scenes))

            async def cb(completed: int, total: int, current: int | None):
                progress.update(task_id, completed=completed, description=f"[cyan]scene {current}/{total}")

            result = await run_batch(
                engine=engine,
                voice_map=vmap,
                script=script,
                chapter_id_explicit=chapter,
                filename_hint=script_path.name,
                output_root=output_root,
                voice_override=voice_override,
                speed=speed,
                total_step=total_step,
                on_progress=cb,
            )

        console.print(f"[green]완료[/green] ch{result.chapter_id}: {len(result.files)}개 파일 → {result.output_dir}")
        if result.warnings:
            console.print("[yellow]경고:[/yellow]")
            for w in result.warnings:
                console.print(f"  - {w}")

    asyncio.run(_run())


@app.command()
def voices():
    """사용 가능한 보이스와 voice_map.yaml 내용을 출력."""
    s = settings_module.load()
    vmap = load_voice_map(s.voice_map_path)

    t = Table(title="Voices (Supertonic)")
    t.add_column("Code")
    t.add_column("Gender")
    t.add_column("Default", justify="center")
    for code in ALL_VOICE_CODES:
        gender = "male" if code.startswith("M") else "female"
        is_default = "✔" if code == vmap.default else ""
        t.add_row(code, gender, is_default)
    console.print(t)

    m = Table(title=f"voice_map.yaml ({s.voice_map_path})")
    m.add_column("voice_style")
    m.add_column("→")
    m.add_column("voice code")
    m.add_row("(default)", "→", vmap.default)
    for k, v in sorted(vmap.styles.items()):
        m.add_row(k, "→", v)
    console.print(m)


@app.command()
def doctor():
    """assets/, GPU 가용성, 더미 합성 테스트."""
    s = settings_module.load()
    console.print(f"[bold]project_root[/bold]      {s.project_root}")
    console.print(f"[bold]onnx_dir[/bold]          {s.onnx_dir}")
    console.print(f"[bold]voice_styles_dir[/bold]  {s.voice_styles_dir}")
    console.print(f"[bold]workspace_root[/bold]    {s.workspace_root}")
    console.print(f"[bold]use_gpu_mode[/bold]      {s.use_gpu_mode}")

    try:
        import onnxruntime as ort
        console.print(f"[bold]onnxruntime[/bold]       {ort.__version__}")
        console.print(f"[bold]ort.get_device()[/bold]  {ort.get_device()}")
        console.print(f"[bold]available providers[/bold] {ort.get_available_providers()}")
    except Exception as e:
        console.print(f"[red]onnxruntime import 실패:[/red] {e}")
        raise typer.Exit(1)

    use_gpu = s.resolve_use_gpu()
    console.print(f"[bold]resolved use_gpu[/bold]  {use_gpu}")

    async def _smoke():
        engine = await Engine.get()
        console.print(f"[bold]engine.providers[/bold] {engine.providers}")
        console.print(f"[bold]sample_rate[/bold]     {engine.sample_rate}Hz")
        with console.status("[bold]더미 합성 테스트 (F2)..."):
            wav = await engine.synth("안녕하세요. 테스트입니다.", voice_code="F2")
        console.print(f"[green]더미 합성 성공[/green] samples={len(wav)} (~{len(wav)/engine.sample_rate:.2f}s)")

    try:
        asyncio.run(_smoke())
    except Exception as e:
        console.print(f"[red]doctor 실패:[/red] {e}")
        raise typer.Exit(1)


@app.command()
def serve(
    host: str = typer.Option(None, "--host"),
    port: int = typer.Option(None, "--port"),
):
    """FastAPI 웹 서버 실행 (localhost + LAN)."""
    import uvicorn
    s = settings_module.load()
    h = host or s.host
    p = port or s.port
    console.print(f"[bold]voicewright serve[/bold] → http://{h}:{p}  (Ctrl+C로 종료)")
    if h == "0.0.0.0":
        console.print("  본인:    http://localhost:%d" % p)
        console.print("  팀원:    http://<your-LAN-ip>:%d  (방화벽 Private 허용 필요)" % p)
    uvicorn.run(
        "voicewright.server.app:create_app",
        host=h,
        port=p,
        factory=True,
        workers=1,
        reload=False,
    )


def main(argv: list[str] | None = None) -> None:
    app(argv)


if __name__ == "__main__":
    main(sys.argv[1:])
