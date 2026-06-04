# 영상공방 사용법 (NotebookLM → 슬라이드/대본 → mp4maker 번들)

NotebookLM에 드래그해 둔 회사 자산으로부터 **슬라이드 이미지 + per-slide 내레이션 대본**을
뽑아 mp4maker 번들로 패키징하는 전체 흐름입니다. (음성/영상은 다운스트림 SuperTonic3·mp4maker가 처리)

## 0. 설치 (최초 1회)
```bat
setup.bat
```
venv 생성 + 의존성 설치(`notebooklm-mcp-cli`, `PyMuPDF` 포함) + `.env` 생성까지 합니다.
그다음 [google-oauth-setup.md](google-oauth-setup.md)를 따라 `.env`에 Google OAuth 키를 채웁니다.

## 1. NotebookLM 로그인 (최초 1회, 쿠키 2~4주 유지)
```bat
venv\Scripts\nlm login
```
크롬이 열리면 **구글 Pro 계정**으로 로그인합니다. 회사 자산은 NotebookLM 웹에서
**노트북에 직접 드래그**해 미리 만들어 두세요. (이 도구는 자동 업로드하지 않습니다.)

확인:
```bat
venv\Scripts\nlm login --check
venv\Scripts\nlm list notebooks --json
```

## 2. 서버 실행
```bat
run.bat
```
브라우저가 `http://127.0.0.1:7000/vodstudio` 로 열립니다. (앱 로그인은 "Google로 로그인")

## 3. 스튜디오 화면 흐름
1. **노트북 선택** — 자산을 넣어둔 노트북 고르기 (상단 배지가 "NotebookLM: 연결됨"이어야 함)
2. **생성 설정**
   - 총 슬라이드 수(~40/~60), 청크 크기(기본 20 — 40장은 2회, 60장은 3회로 분할 호출)
   - 타겟 청중 / 발표 목적 (2단계 대본 프롬프트에 주입)
   - (선택) **디자인 시스템 프롬프트**: 1단계로 만든 영문 프롬프트를 붙여넣으면 전 슬라이드에 적용
   - "마스터 대본을 노트북 소스로 추가" 체크 → 슬라이드가 대본과 1:1로 더 잘 매칭됨
3. **생성 시작** → 자동 진행:
   대본 생성(2단계) → (소스 추가) → 20장씩 분할 슬라이드 생성(3단계) → PDF 다운로드/병합 → 페이지 렌더
4. **검수 & 번들** (status=review)
   - 슬라이드별 이미지 + **대본(narration)** 확인/수정, PDF 추출 텍스트로 화면 텍스트 검수
   - chapter 번호/제목 입력 후 **"mp4maker 번들 생성"**
   - "mp4maker load_bundle 검증 통과" 뜨면 OK → **번들 ZIP 다운로드**

## 4. 영상 렌더 (mp4maker) — 5단계 카드
번들이 만들어지면 화면에 **5단계: 영상 렌더(mp4maker)** 카드가 나타납니다. ffmpeg가 PATH에
있어야 하고, `./mp4maker` 체크아웃이 있어야 합니다(`setup.bat`이 자동 클론·점검).

두 가지 모드:
- **무음 미리보기** — 대본 길이만큼 무음 WAV를 자동 생성해 바로 렌더. SuperTonic3 음성 없이도
  *슬라이드 + Ken Burns + 자막(대본)* 으로 영상이 어떻게 나오는지 즉시 확인. (가장 빠른 확인용)
- **음성 포함** — `audio\chNN_XX_narration.wav` 를 SuperTonic3로 채운 뒤 선택하면 실제 음성으로 렌더.

해상도(1280x720 빠른 미리보기 / 1920x1080)를 고르고 **영상 렌더 시작** → 진행바·로그가 흐르고,
끝나면 화면에 **플레이어**로 바로 재생됩니다. 최종 파일은 번들의
`draft\chNN_final.mp4` (자막 하드번 본편) + `draft\chNN_final.softsub.mp4` + `draft\chNN_project.mlt`(Shotcut).

### 번들 직접 구조
ZIP을 풀면:
```
_assets/chNN_bundle/
  script/chNN_script.json   # scenes[].narration_text = 대본, image_filename = 슬라이드
  images/chNN_XX_slide.png  # 슬라이드 이미지
  audio/chNN_XX_narration.wav   # (무음 미리보기 시 자동 생성 / 음성은 SuperTonic3로 교체)
  subtitles/  draft/        # draft/ 에 최종 MP4 산출
```
> 실제 배포 영상은 무음 WAV를 **SuperTonic3 음성으로 교체**한 뒤 '음성 포함'으로 다시 렌더하세요.

## 5. 3단계 워크플로우 원리
영상 레시피(1·2·3단계)와 `nlm` CLI 매핑은 [notebooklm-slide-workflow.md](notebooklm-slide-workflow.md) 참고.
요점: 영상의 "[SYSTEM KERNEL OVERRIDE]" 챗 해킹 대신, 우리는 `nlm slides create`를 20장 단위로
직접 N회 호출하고 디자인 시스템을 `--focus`로 주입합니다.

## 6. 트러블슈팅
| 증상 | 원인/해결 |
|---|---|
| 배지 "로그인 필요" | `nlm login` 다시 실행 (쿠키 만료 2~4주) |
| 노트북 목록 502 | 인증 만료 또는 네트워크. `nlm login --check` 확인 |
| "마스터 대본 파싱 실패" | NotebookLM 응답이 형식을 벗어남 — 타겟/목적을 명확히 하고 재시도 |
| 페이지 수 ≠ 대본 수 경고 | 검수 화면에서 순서대로 매칭됨. 필요시 대본 수동 조정 |
| 슬라이드가 대본과 안 맞음 | "마스터 대본을 노트북 소스로 추가" 켜고 재생성 |
| 검증 문제(빨강) | 이미지 누락/빈 대본 — 검수 화면에서 보완 후 다시 번들 생성 |
