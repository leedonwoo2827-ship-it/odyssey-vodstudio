# 영상공방 (VOD Studio)

소스 문서(법령·보고서 등)를 넣으면 **대본 → 슬라이드 이미지 → 음성/자막 → MP4**까지 한 흐름으로 만드는 로컬 영상 제작 도구입니다.
NotebookLM 슬라이드 스타일이나 대본 기반 제작을 좋아하는 분들을 위한 워크플로우.

- **API 키 불필요** — 대본 생성은 본인 계정(ChatGPT 또는 Google)에 로그인한 CLI에 위임.
- **도커·외부 서비스 불필요** — TTS·RAG 모두 **로컬에서** 동작(클라우드 없음).
- 결과물은 [mp4maker](https://github.com/leedonwoo2827-ship-it/mp4maker) 호환 번들 → 그대로 MP4 렌더.

> 같은 odysseus 베이스의 [문서공방](https://github.com/leedonwoo2827-ship-it/odyssey-munseo-gongbang)이 문서를 만든다면, **영상공방은 영상을 만든다.**

---

## ✨ 기능

| 단계 | 기능 |
|---|---|
| ① 대본 | **codex(OpenAI/ChatGPT) ↔ agy(Antigravity→Gemini/Google) 토글**, 소스 파일 여러 개 첨부, **📚 RAG(로컬 색인)** · **🔬 자료 딥리서치** · **✅ 대본 자동 검수**, 목소리 미리듣기 |
| ② 이미지 | NotebookLM 슬라이드 PDF 임포트 → 씬별 그리드. **렌더 코드 생성**(디자인 프리셋·청크 자동 추천) |
| ③ 음성/자막 | **로컬 CPU TTS(VoiceWright/Supertonic-3, ONNX)** — 씬별 음성/자막 생성·재생성, 자막 타이밍 편집, **📖 발음 사전** |
| ④ 영상 | mp4maker 합성 — **자막 없는 클린본(유튜브용) + `.srt`** 또는 자막 구운본, dry-run, **📺 유튜브 메타 생성** |
| 공통 | **📂 번들 불러오기**(재시작 후 이어가기), **🧠 시리즈 메모리**(챕터 간 톤·용어 일관성) |

자세한 사용법·설계는 [`knowledge/`](knowledge/) 폴더 참고.

---

## ⚙ 설치 & 실행 (Windows)

### 0. 사전 준비 (없는 것만, PowerShell)
```powershell
winget install Python.Python.3.12
winget install Git.Git
winget install Gyan.FFmpeg
```
설치 후 **새 터미널**에서 확인: `python --version` / `git --version` / `ffmpeg -version`

### 1. 더블클릭 2번
1. **`setup.bat`** — venv·의존성 설치 + **agy·Node·codex 자동 설치** + **로컬 TTS 모델 다운로드(~380MB, HuggingFace)** + mp4maker 클론 + 환경 점검 (`.env` 자동 생성)
2. **`run.bat`** — 웹 서버 실행, 브라우저가 `http://127.0.0.1:7000/vodstudio` 자동 오픈

> 대본을 직접 붙여넣어 쓰면 로그인 없이도 ②~④ 진행 가능합니다.

### 2. 대본 생성 로그인 (최초 1회)
① 대본 탭에서 공급자 토글(**OpenAI** / **Gemini**) 선택 → **🖥️ 로그인(터미널)** 버튼 →
뜬 터미널에서 브라우저로 계정 로그인 → 배지가 초록(이메일 표시)으로 바뀌면 끝.
- **OpenAI**: `codex login` (ChatGPT 계정)
- **Gemini**: `agy` (Google 계정 · 개인 Gmail 권장)

API 키 입력 없음 — 본인 계정 할당량으로 사용합니다.

---

## 🗂 워크플로우

1. **① 대본** — 소스 파일 첨부 → 📚 자료 학습(RAG) → (선택)🔬 딥리서치 → ✦ 대본 생성 → ✅ 검수
2. **② 이미지** — 🎨 디자인 프리셋 고르고 **렌더 코드 생성** → NotebookLM에 [소스 추가]+[코드 붙여넣기] → 슬라이드덱 PDF 다운로드 → 순서대로 임포트
3. **③ 음성/자막** — 번들 저장 → 🔊 전체 음성/자막 생성(로컬 TTS) → 씬별 발음/자막/타이밍 다듬기 (발음 이상하면 📖 발음 사전에 추가)
4. **④ 영상** — 🔍 dry-run으로 검증 → 🎬 풀 렌더(기본: 자막 없는 클린본 + `.srt`) → 📺 유튜브 메타 생성

> **유튜브**: 자막 없는 `chNN_final_nosub.mp4`를 업로드하고, 같은 폴더의 `.srt`를 유튜브 자막으로 따로 올리세요.

---

## 📁 산출물 위치

```
<출력폴더 또는 data/vodstudio/<job>/bundle>/_assets/chNN_bundle/
  script/      chNN_script.json
  images/      chNN_XX_*.png
  audio/       chNN_XX_narration.wav      (로컬 TTS)
  subtitles/   chNN_XX_narration.srt + chNN.srt
  draft/       chNN_final_nosub.mp4 (또는 chNN_final.mp4) + chNN.srt
```
- 시리즈 메모리: `data/vodstudio/series_memory.json`
- 디자인 프리셋: `data/vodstudio/design_presets.json`
- 발음 사전: `config/pronunciation_map.yaml`

---

## 📚 문서 ([`knowledge/`](knowledge/))
- `vodstudio-usage.md` — 전체 사용법
- `notebooklm-render-design.md` — 렌더 코드 & 디자인 시스템(프리셋·일관성)
- `vodstudio-rag-research.md` — 로컬 RAG·딥리서치 원리
- `notebooklm-slide-workflow.md` · `gemini-cli-setup.md` · `google-oauth-setup.md`

---

## 🧱 스택
FastAPI + Vanilla JS · 로컬 임베딩(FastEmbed/ONNX) · 로컬 TTS(VoiceWright/Supertonic-3/ONNX) · mp4maker(ffmpeg) ·
LLM은 `codex`/`agy` CLI 위임. **외부 서비스(ChromaDB·SearXNG·Docker) 미사용 — 전부 로컬.**

> 베이스: [odysseus](https://github.com/pewdiepie-archdaemon/odysseus) 포크. 영상공방 전용 모듈은
> `services/vodstudio/*`, `services/{agy,codex}/*`, `services/llm_backend.py`, `voicewright/*`,
> `routes/vodstudio_routes.py`, `static/vodstudio/*`.
