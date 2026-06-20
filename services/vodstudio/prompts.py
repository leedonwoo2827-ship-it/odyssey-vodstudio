"""3단계 워크플로우 프롬프트 템플릿 + slides-create focus 빌더.

원문 레시피: knowledge/notebooklm-slide-workflow.md
- 1단계: 참고 이미지 → 영문 디자인 시스템 프롬프트(≤800자)
- 2단계: 소스 전체 → N페이지 마스터 대본(슬라이드별 제목/화면텍스트/상세대본)
- 3단계: 디자인 적용 + 20장 분할 렌더링 (여기서는 챗 해킹 대신 nlm slides create --focus 사용)
"""

from typing import Optional


# 1단계 — 디자인 추출 (참고 이미지를 노트북에 넣고 query). 이미지가 없으면 생략 가능.
DESIGN_EXTRACTION_PROMPT = """\
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
"""


def master_script_prompt(total_pages: int, target_audience: str, objective: str) -> str:
    """2단계 — 마스터 대본 추출 프롬프트. 페이지 수/타겟/목적을 주입."""
    return f"""\
# Role: Chief Content Architect
Task: Analyze ALL uploaded sources and generate a consistent {total_pages}-page [Master Script Report].

## [Variables]
- Target Audience: {target_audience}
- Presentation Objective: {objective}

## Instruction Guidelines
1. 업로드된 모든 소스 문서의 핵심 팩트와 데이터를 통합하여 논리적 흐름(서론-본론-결론)을 구축하라.
2. 지정된 [Target Audience]의 수준과 관심사에 맞춘 전문적인 용어와 설득력 있는 문체를 사용하라.
3. [Target Audience]는 **톤·난이도·예시 선택에만** 활용하라. 청중을 **직접 호명하지 말 것**:
   "임직원 여러분", "여러분", "안녕하십니까" 같은 청중 지칭/인사말로 **시작하거나 끝내지 말고**,
   첫 슬라이드부터 곧바로 **주제·핵심 내용**으로 들어가라. (마지막도 인사 없이 핵심 메시지로 마무리)

## Output Format (Strictly Follow)
슬라이드 번호: (1~{total_pages})
제목: (해당 페이지의 핵심 헤드라인)
화면 텍스트: (핵심 데이터 및 키워드 3~4줄 요약)
상세 대본: (발표자가 읽을 구어체 설명 3~5줄)
"""


def slides_focus(
    design_system: Optional[str],
    start: int,
    end: int,
    is_first_chunk: bool,
    is_last_chunk: bool,
) -> str:
    """3단계 — `nlm slides create --focus` 에 넣을 스티어링 문자열.

    영상의 user_steering_prompt를 CLI focus로 옮긴 것. 20장 단위 분할 호출 시
    각 청크가 표지/엔딩을 중복 생성하지 않도록 경계 규칙을 준다.
    """
    lines = []
    if design_system and design_system.strip():
        lines.append(f"Apply this design system EXACTLY: {design_system.strip()}")
    lines.append(f"Render Master-Script slides {start} to {end}, match source content 1:1.")
    if not is_first_chunk:
        lines.append(f"Do NOT generate a cover or title slide. Start immediately with slide {start} body content.")
    if is_last_chunk:
        lines.append(f"Place the ONLY ending/closing slide at slide {end}.")
    else:
        lines.append(f"Do NOT generate any ending/thank-you slide at slide {end}. End with body content.")
    return " ".join(lines)
