# knowledge — 설정·운영 매뉴얼 모음

영상공방(Odyssey VOD Studio) 운영에 필요한 한글 매뉴얼을 모아둡니다.

- [google-oauth-setup.md](google-oauth-setup.md) — "Google로 로그인" 설정 (Cloud Console OAuth 클라이언트 발급 → `.env` → 동작 확인 → 에러 해결)
- [notebooklm-slide-workflow.md](notebooklm-slide-workflow.md) — NotebookLM 슬라이드+대본 3단계 워크플로우(영상 레시피) + `nlm` CLI 매핑
- [vodstudio-usage.md](vodstudio-usage.md) — 전체 사용법 (설치 → `nlm login` → 스튜디오 화면 → 번들 → mp4maker 연동 → 트러블슈팅)
- [gemini-cli-setup.md](gemini-cli-setup.md) — Gemini CLI(구글 로그인·키 없음·무료 티어)로 대본 생성. "직접 입력" 모드용
- [vodstudio-review-workflow.md](vodstudio-review-workflow.md) — 대본 검수·수정·✨자동 정리 워크플로 + 🔴 오탐 주의 + "기조" 4원칙 + 실제 예시(Anthropic 보고서)

## 두 가지 사용 모드
- **직접 입력(수동, 기본)** — NotebookLM/ChatGPT/Gemini에서 만든 대본을 붙여넣고(또는 Gemini CLI로 생성), 슬라이드 PDF 업로드 → 번들 → 영상. **API 키 불필요.**
- **NotebookLM 자동** — `nlm login` 후 노트북에서 슬라이드/대본 자동 생성.
