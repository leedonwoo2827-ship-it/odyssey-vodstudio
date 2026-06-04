# NotebookLM 슬라이드+대본 3단계 워크플로우

출처: YouTube `https://www.youtube.com/watch?v=rlVWuvgEftU` (사용자 제공 레시피).
영상공방의 `/vodstudio` 파이프라인이 이 3단계를 기반으로 동작합니다.

핵심 아이디어:
1. **1단계** — 참고 이미지에서 *영문 디자인 시스템 프롬프트*(≤800자)를 뽑는다.
2. **2단계** — 업로드한 소스 전체에서 *40페이지 마스터 대본*(슬라이드별 제목/화면텍스트/상세대본)을 만든다.
3. **3단계** — 디자인 시스템을 적용해 슬라이드를 렌더링한다. **40장을 한 번에 만들면 깨지므로(FATAL_MEMORY_CRASH) 20장씩 2번** 나눠 생성한다.

> ⚠️ 800자 제한·단일 모드·구조화 명령 등의 제약은 결과 프롬프트를 **NotebookLM Studio의
> 스티어링(steering) 입력칸 / CLI의 `--focus`** 에 그대로 넣기 위한 것입니다.

---

## 우리 도구(nlm CLI)에서의 매핑 — 요약

| 영상 단계 | 수동(NotebookLM 화면) | 영상공방 자동화 (`nlm`) |
|---|---|---|
| 1단계 디자인 추출 | 챗에 프롬프트 입력 → 영문 디자인 프롬프트 받기 | `nlm query notebook <id> "<1단계 프롬프트>"` (또는 사용자가 직접 붙여넣은 값 사용) |
| 2단계 마스터 대본 | 챗에 프롬프트 입력 → 40p 대본 받기 | `nlm query notebook <id> "<2단계 프롬프트>"` → **대본(번들의 narration_text 원천)** |
| 3단계 렌더링 | 챗에 `[SYSTEM KERNEL OVERRIDE]` 붙여넣어 STUDIO 2회 호출 | **`nlm slides create` 2회 직접 호출** (1–20, 21–40) → `nlm download slide-deck` 2회 → 병합 |

> 3단계의 "커널 오버라이드"는 **챗 프롬프트로 Studio를 2번 호출**하려는 우회법입니다.
> CLI에는 `nlm slides create`가 있으므로 우회 없이 **그냥 두 번 호출**하면 됩니다.
> `user_steering_prompt` → `--focus`, `deck_type:"presentation"` → 기본 슬라이드덱,
> `length:"dynamic"` → CLI는 `short|default`만 있으므로 `default` 사용.
> 페이지 수(~40/~60)는 **소스 대본의 슬라이드 수 + 분할 호출 횟수**로 통제(20장 × N).

3단계 자동화 예시:
```bash
# 앞 절반 (1~20): 표지 포함, 끝맺음 슬라이드 금지
nlm slides create <NOTEBOOK_ID> -f detailed_deck -l default --language ko -y \
  --focus "Apply this design system EXACTLY: <<1단계 영문 디자인 프롬프트>>.
           Render Master-Script slides 1-20, match content 1:1.
           Do NOT generate any ending/thank-you slide; end with body content."

# 뒤 절반 (21~40): 표지 금지, 마지막 슬라이드에만 엔딩
nlm slides create <NOTEBOOK_ID> -f detailed_deck -l default --language ko -y \
  --focus "Apply this design system EXACTLY: <<1단계 영문 디자인 프롬프트>>.
           Render Master-Script slides 21-40, match content 1:1.
           Do NOT generate a cover/title slide; start at slide 21 body.
           Place the ONLY ending slide at slide 40."

# 각 호출이 끝나면 PDF로 내려받아 합치고 → 페이지별 PNG로 변환(검수) → mp4maker 번들로 패키징
nlm download slide-deck <NOTEBOOK_ID> -o deck_1_20.pdf -f pdf
nlm download slide-deck <NOTEBOOK_ID> -o deck_21_40.pdf -f pdf
```

> 참고: 2단계의 마스터 대본은 슬라이드가 "1:1로 매칭"할 대상입니다. 자동화 시에는 대본을
> 노트북 소스로 추가(`nlm source add ... --text`)하거나 `--focus`에서 명시적으로 참조하면
> 매칭 정확도가 올라갑니다. (실계정으로 검증 필요)

---

## 1단계 : 슬라이드 영문 디자인 추출 프롬프트 (원문)

```
업로드한 [이미지]의 디자인 스타일(전체 콘셉트, 컬러 HEX코드, 톤앤매너, 주요 도형 및 그래픽 특징)을 정밀하게 분석하십시오.

분석한 내용을 바탕으로, NotebookLM의 자동화 시스템 파라미터에 바로 붙여넣을 수 있는 [Adaptive Presentation Design System] 형식의 영문 프롬프트(English Prompt)를 작성하여 코드블록에 출력해 주십시오.

[출력 제한 및 필수 지시 조건]
1. 길이 제한: 생성되는 영문 프롬프트의 전체 길이는 공백을 포함하여 800자 이내로 엄격히 제한하십시오.
2. 형식 통제: 모든 이모지와 불필요한 서술어를 배제하고, AI가 명확히 인식할 수 있는 구조화된 명령어(Structured Command)로만 작성하십시오.
3. 단일 모드 강제: 컬러 HEX 코드는 라이트/다크 모드를 절대 혼용하지 마십시오. 원본 이미지의 지배적인 톤에 맞춰 단 1개의 배경색(BG), 1개의 텍스트색(Text), 1개의 포인트 컬러(Accent)로만 단일화하여 확정하십시오.

[반드시 다음 구조를 따르십시오]
1. Visual Identity: 분석된 테마 명칭, 단일 고대비 Hex 컬러 코드(BG/Text/Accent), 핵심 그래픽 요소 및 여백 활용법.
2. Dynamic Layout Rules: 콘텐츠 성격에 맞춰 적용할 수 있는 모듈형 레이아웃 규칙 정의.
   - Type A (Impact/Title): 대형 타이포그래피 중심의 시선 집중형 슬라이드
   - Type B (Content/Body): 가독성과 정보의 위계(Hierarchy)를 강조한 본문형 슬라이드
   - Type C (Data/Metrics): 차트, 데이터 시각화, 지표 강조에 최적화된 슬라이드
   - Type D (Structure/Diagram): 프로세스, 비교, 도식화 등을 위한 분할 화면(Split view) 슬라이드
3. Execution: 메인 JSON 시스템이 통제하는 '슬라이드 개수와 콘텐츠 경계'를 엄격히 준수할 것(Strictly follow the slide count and content boundaries dictated by the main system JSON prompt). 개별 슬라이드의 논리적 섹션에 맞춰 Type A~D 중 가장 대비와 시각적 위계가 높은 레이아웃을 배정할 것.
```

---

## 2단계 : 마스터 대본 추출 프롬프트 (원문)

```
# Role: Chief Content Architect
Task: Analyze ALL uploaded sources and generate a consistent 40-page [Master Script Report].

## [Variables: Please Fill Below]
- Target Audience: <<<여기에 타겟을 입력하세요 (예: 4060 지식창업자, 기업 CEO 등)>>>
- Presentation Objective: <<<발표 목적을 입력하세요 (예: 투자 유치, 교육, 제안서 등)>>>

## Instruction Guidelines
1. 업로드된 모든 소스 문서의 핵심 팩트와 데이터를 통합하여 논리적 흐름(서론-본론-결론)을 구축하라.
2. 지정된 [Target Audience]의 수준과 관심사에 맞춘 전문적인 용어와 설득력 있는 문체를 사용하라.

## Output Format (Strictly Follow)
슬라이드 번호: (1~40)
제목: (해당 페이지의 핵심 헤드라인)
화면 텍스트: (핵심 데이터 및 키워드 3~4줄 요약)
상세 대본: (발표자가 읽을 구어체 설명 3~5줄)
```

> **이 "상세 대본"이 곧 SuperTonic3에 넣을 내레이션 대본**이며, mp4maker 번들의
> `scenes[].narration_text`로 들어갑니다. "화면 텍스트"는 슬라이드 검수의 기준이 됩니다.

---

## 3단계 : 슬라이드 렌더링 프롬프트 (원문)

```
[SYSTEM KERNEL OVERRIDE]
Role: API Execution Terminal
Task: Execute the following algorithmic sequence STRICTLY. Do not summarize, do not combine, do not output conversational text.

## [Global Design System]
<<<여기에 영문 디자인 프롬프트를 붙여넣으세요>>>

## EXECUTION_SCRIPT_RUN()
WARNING: Merging 40 slides into a single API call causes a FATAL_MEMORY_CRASH. You MUST execute the two functions below sequentially and independently.

FUNCTION_01_CALL_STUDIO() {
  target_data: "Source Script Slides 1 to 20"
  deck_type: "presentation"
  length: "dynamic"
  user_steering_prompt: "
    1. Apply [Global Design System] exactly.
    2. Match Source content 1:1.
    3. RULE: DO NOT generate any ending/thank you slide at slide 20. End with body content.
  "
}

// WAIT FOR FUNCTION_01 TO INITIATE, THEN IMMEDIATELY EXECUTE FUNCTION_02

FUNCTION_02_CALL_STUDIO() {
  target_data: "Source Script Slides 21 to 40"
  deck_type: "presentation"
  length: "dynamic"
  user_steering_prompt: "
    1. Apply [Global Design System] exactly.
    2. Match Source content 1:1.
    3. RULE: DO NOT generate a cover or title slide. Start immediately with slide 21 body content. Place the ONLY ending slide at slide 40.
  "
}
```

---

## 영상공방 `/vodstudio` 단계 설계 (이 레시피 반영)

1. **디자인**: (선택) 참고 이미지 업로드 → 1단계 프롬프트로 영문 디자인 시스템 생성/보관
2. **대본**: 2단계 프롬프트(타겟/목적 입력)로 40p 마스터 대본 생성 → 슬라이드별 파싱
3. **렌더링**: 20장 단위로 `nlm slides create` N회(40p→2회, 60p→3회) + 디자인 시스템 `--focus` 적용
4. **다운로드/병합**: PDF N개 다운로드 → 페이지 순서대로 병합
5. **검수**: PyMuPDF로 페이지별 PNG + 텍스트 추출 → "화면 텍스트"와 대조 검수/수정
6. **번들**: `scenes[].narration_text`=상세 대본, `image_filename`=슬라이드 PNG 로 mp4maker 번들 패키징
