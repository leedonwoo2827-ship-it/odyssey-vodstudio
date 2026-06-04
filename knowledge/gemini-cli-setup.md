# Gemini CLI 설정 (구글 로그인 · API 키 없음 · 무료 티어)

회사 구성원이 **각자 자기 구글 계정으로 로그인**해서 **API 키 없이** Gemini로 대본을
생성하는 방법입니다. 영상공방 "직접 입력" 모드의 **"Gemini로 대본 생성"** 버튼이 이걸 씁니다.

> 원리: 구글 오픈소스 **Gemini CLI**(`gemini`)는 구글 계정 OAuth 로그인 시 무료 티어로
> Gemini를 제공합니다(키 없음). 우리 앱은 `nlm`·`mp4maker`처럼 이 `gemini`를 호출만 합니다.

---

## 1. Node.js 설치 (Gemini CLI 전제)
Gemini CLI는 Node 기반입니다.
```powershell
winget install OpenJS.NodeJS.LTS
```
또는 https://nodejs.org 에서 LTS 설치. 설치 후 새 터미널에서 확인:
```powershell
node --version
npm --version
```

## 2. Gemini CLI 설치
```powershell
npm install -g @google/gemini-cli
gemini --version
```

## 3. 구글 계정 로그인 (키 없음)
```powershell
gemini
```
- 브라우저가 열리면 **자기 구글 계정으로 로그인** → 끝. (API 키 입력 없음)
- 한 번 로그인하면 자격증명이 로컬에 저장됩니다.
- 간단 테스트: `gemini -p "한 문장으로 자기소개해줘"`

## 4. 영상공방에서 사용
1. `run.bat` 으로 앱 실행 → `http://127.0.0.1:7000/vodstudio`
2. **"직접 입력 (수동 · 키 없음)"** 모드 (기본 선택)
3. "Gemini로 대본 생성" 칸: 상단 배지가 **"gemini 설치됨"** 이면 준비 완료
4. 주제/타겟/목적 입력 → **[Gemini로 대본 생성]** → 아래 대본칸이 채워짐
5. (선택) 슬라이드 PDF 업로드 → **[검수 준비]** → 검수 → 번들 → 렌더(mp4maker)

---

## ⚠ 솔직한 주의점
- **무료 티어 쿼터 제한**: 개인 구글 계정 기준 일일/분당 호출 한도가 있습니다. 대량/무제한이
  필요하면 Gemini Code Assist 유료나 Vertex(GCP 결제) 경로가 필요합니다.
- **회사(Workspace) 계정 주의**: 조직 계정은 무료 개인 티어가 막혀 있을 수 있습니다(조직 정책).
  이 경우 ① 개인 Gmail로 로그인하거나 ② 조직이 Gemini Code Assist 라이선스를 부여해야 합니다.
- **이미지 생성은 별도**: Gemini CLI는 텍스트(대본)용입니다. 슬라이드 이미지는 NotebookLM에서
  PDF로 내려받아 업로드하는 방식(직접 입력 모드)이 가장 확실합니다.

## 환경변수(선택)
- `GEMINI_BIN` : `gemini` 실행 파일 경로를 직접 지정하고 싶을 때.
