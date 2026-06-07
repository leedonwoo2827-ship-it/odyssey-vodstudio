# NotebookLM 렌더 코드 & 디자인 시스템 가이드 (사용자 교육용)

> ① 대본 탭 하단 **"NotebookLM 렌더 코드"** 카드 사용법. 60장 슬라이드를 일관된 디자인으로
> 안정적으로 뽑기 위한 핵심 노하우. 디자인 취향은 개인차가 크므로 **프리셋을 만들어 저장**해 쓰세요.

---

## 1. 렌더 코드란?

NotebookLM 챗에 붙여넣으면 슬라이드덱을 만들어주는 **명령 코드**입니다. 대본(소스)을 보고
여러 개의 덱으로 나눠 생성합니다.

- **대본 = 소스(자료)**: NotebookLM에 `[+ 소스 추가]`로 붙여넣는다.
- **렌더 코드 = 챗 명령**: 그 소스를 어떻게 슬라이드로 만들지 지시한다.

## 2. 왜 청크(나눠 생성)인가

한 번에 60장을 만들라고 하면 NotebookLM이 중간에 누락하거나 멈춥니다(품질 저하). 그래서
**N개의 함수로 나눠 순차 실행**합니다. 청크가 **작을수록 일관성↑**(대신 NotebookLM 실행 횟수↑).

- 화면의 **📐 대본 보고 추천** → 대본 장수를 세서 **약 10개 청크**가 되도록 청크 값을 추천.
- 60장이면 청크 6 → 10개 함수. NotebookLM이 한 세션에서 ~45장에서 끊기면 청크를 더 줄이세요.
- 청크 경계 규칙이 자동으로 들어갑니다: 첫 청크 외에는 **표지 슬라이드 금지**, 마지막 청크에만 **엔딩 슬라이드**.

## 3. ⚠️ 디자인은 "독립 블록"으로 넣으면 거부된다 (중요)

처음엔 디자인을 이렇게 **별도 블록**으로 넣었더니:

```
## [Global Design System]
Style: Flat Vector ...
```

→ NotebookLM이 **"이 질문에 답할 수 없습니다"** 로 거부했습니다. `[SYSTEM KERNEL OVERRIDE]`와
합쳐진 독립적 "권위적 지시 블록"이 **시스템 프롬프트 주입**처럼 보여 안전 필터에 걸린 것입니다.
(디자인 블록을 빼면 통과했음)

**해결**: 디자인을 별도 블록으로 두지 않고, **각 함수의 `user_steering_prompt` 안에 한 줄 규칙으로**
녹여 넣습니다. 앱이 자동으로 이렇게 생성합니다:

```
user_steering_prompt: "
  1. Match the source content 1:1.
  2. Keep a consistent visual style on every slide — Style: Flat Vector ... Use point colors (Deep Blue) for emphasis.
  3. Do NOT make any ending/thank-you slide at slide 20; end with body content.
"
```

→ 일관성은 유지되고 거부도 피합니다. (검증됨: 2026-06-06, 60장·3청크 정상 생성)

> 그래도 거부되면 맨 위 `[SYSTEM KERNEL OVERRIDE]` 문구를 더 자연스럽게 바꾸세요
> (예: "Please generate the slide decks in sequence, independently.").

## 4. 디자인 프리셋 (개인차가 크니 저장해서 쓰기)

`🎨 디자인 시스템 ▾` 토글 → 드롭다운에서 프리셋 선택 / 제목·내용 편집 / `💾 프리셋 저장`.
저장 위치: **`data/vodstudio/design_presets.json`** (개인이 추가한 프리셋이 영구 저장됨).

기본 제공 2종:
1. **기본 · 플랫 벡터(파랑 강조)** — 깔끔·전문적. 흰 배경, 굵은 제목, Deep Blue 포인트.
2. **친근 · 파스텔 일러스트(주황 강조)** — 따뜻·친근. 크림 배경, 둥근 도형, Warm Orange 포인트.

### 나만의 디자인 만드는 팁 (영문 권장)
- **Style**: 그림체/배경 (예: Flat Vector / Soft Pastel / 3D Isometric / Minimal Line, 배경 HEX)
- **Typography**: 폰트 느낌 (Clean Sans-serif / Friendly rounded / Serif scholarly)
- **Layout**: 여백·불릿 개수 (예: Max 5 bullets, generous whitespace)
- **Tone**: 분위기 (Professional / Warm / Energetic)
- **Crucial**: 꼭 지킬 규칙 — **"Maintain strict visual consistency with previous parts."** 는 꼭 넣기(청크 간 통일).
- **Point color**: 강조색 1개 (Deep Blue / Warm Orange / Forest Green …)

## 5. 사용 순서 요약

1. ① 대본 만들기/붙여넣기 → **📐 대본 보고 추천**으로 청크 자동 설정
2. 🎨 디자인 시스템에서 프리셋 고르기(또는 내 것 저장)
3. **렌더 코드 생성** → 📋 복사
4. NotebookLM: 대본을 `[+ 소스 추가]` → 렌더 코드를 챗에 붙여넣기 → 덱들 생성 → 각 PDF 다운로드
5. ② 이미지에 PDF 순서대로 넣기 → ③ 음성/자막 → ④ 영상
