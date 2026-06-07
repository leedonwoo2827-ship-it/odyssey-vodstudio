"""대본 강화용 프롬프트 빌더 + RAG 컨텍스트 조립 (영상공방).

순수 함수(동기) 모음 — LLM 호출 자체는 라우트에서 `_llm_generate`로 한다.
RAG 검색은 `local_rag.search`(로컬 FastEmbed, 도커 불필요)를 쓴다.

build_* 함수들은 모두 '명령줄 길이 한계(WinError 206)'를 넘지 않도록 컨텍스트를
max_chars 로 제한한 **유한 크기 프롬프트**를 만든다.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import local_rag
from . import prompts as vod_prompts


def gather_context(job_dir: str, queries: List[str], *, k: int = 6,
                   max_chars: int = 18000) -> str:
    """여러 질의로 RAG 검색 → 중복 제거 → max_chars 까지 모은 근거 텍스트."""
    seen = set()
    picked: List[str] = []
    total = 0
    # 라운드로빈으로 질의별 상위 결과를 섞어 다양한 근거 확보
    pools = [local_rag.search(job_dir, q, k=k) for q in queries if (q or "").strip()]
    i = 0
    while pools and total < max_chars:
        progressed = False
        for pool in pools:
            if i < len(pool):
                progressed = True
                h = pool[i]
                key = h["text"][:60]
                if key in seen:
                    continue
                seen.add(key)
                block = f"[{h['source']}]\n{h['text']}"
                if total + len(block) > max_chars:
                    continue
                picked.append(block)
                total += len(block)
        if not progressed:
            break
        i += 1
    return "\n\n---\n".join(picked)


def build_research_prompt(topic: str, context: str) -> str:
    """자료 심층분석(딥리서치) — 쟁점 분해 + 근거 정리 브리프 생성."""
    return (
        "당신은 교육 콘텐츠 리서처입니다. 아래 [자료]만 근거로, 영상 대본의 설계도가 될 "
        "'리서치 브리프'를 한국어로 작성하세요. 인터넷 지식이 아니라 자료에 있는 내용만 씁니다.\n\n"
        f"## 주제\n{topic}\n\n"
        "## 출력 형식 (반드시 따르기)\n"
        "1) 핵심 쟁점 8~12개 (한 줄씩, 자료 근거 조항/키워드 표기)\n"
        "2) 논리적 흐름 제안 (도입 → 본론 단계 → 마무리)\n"
        "3) 주의·오해하기 쉬운 점 3~5개\n"
        "4) 꼭 다뤄야 할 정의/용어 목록\n\n"
        f"## 자료\n{context}\n"
    )


def build_script_prompt(total_pages: int, target_audience: str, objective: str,
                        *, context: str, brief: str = "", memory: str = "") -> str:
    """RAG 근거(+브리프+시리즈 메모리)로 마스터 대본 생성. 자료 전문을 통째로 넣지 않는다."""
    base = vod_prompts.master_script_prompt(total_pages, target_audience, objective)
    extra = ""
    if memory.strip():
        extra += f"\n\n## 시리즈 일관성(이전 화 맥락 — 톤/용어 유지)\n{memory}"
    if brief.strip():
        extra += f"\n\n## 리서치 브리프(이 구조를 따르라)\n{brief}"
    return (
        base
        + extra
        + "\n\n## 근거 자료 (이 발췌들만 사실 근거로 사용 — 없는 내용은 지어내지 말 것)\n"
        + context
        + "\n\n위 근거에 기반해 위 형식대로 한국어로 작성하라. 각 슬라이드는 근거 자료와 일치해야 한다."
    )


def build_review_prompt(script_text: str, context: str) -> str:
    """대본 자동 검수 — 근거 대비 누락/부정확/과장 점검."""
    return (
        "당신은 법령 교육 콘텐츠 감수자입니다. 아래 [대본]을 [근거 자료]와 대조해 "
        "문제를 한국어로 점검하세요. 근거에 없는 주장, 사실과 다른 서술, 과장, 누락된 중요 항목을 찾습니다.\n\n"
        "## 출력 형식\n"
        "- 🔴 부정확/근거없음: (슬라이드 번호 — 문제 — 올바른 내용)\n"
        "- 🟡 과장/모호: (슬라이드 번호 — 문제 — 수정 제안)\n"
        "- 🟢 누락(자료엔 있는데 대본에 빠짐): (항목 — 어디에 넣으면 좋을지)\n"
        "- 한 줄 총평\n"
        "문제가 없으면 '발견된 문제 없음'이라고 쓰세요.\n\n"
        f"## 대본\n{script_text[:14000]}\n\n"
        f"## 근거 자료\n{context}\n"
    )


def build_youtube_prompt(script_text: str, timeline: str = "", title_hint: str = "",
                         total_dur: str = "") -> str:
    """YouTube 업로드 메타데이터 생성.

    timeline: 영상 전체를 덮는 'MM:SS 제목' 줄 목록(씬별 누적 시작시각). 이걸 근거로
    챕터를 영상 끝까지 만든다. (자막 전문을 넣지 않아 명령줄 한계도 안전)
    """
    ts_note = ""
    if timeline.strip():
        ts_note = (
            f"\n\n## 영상 타임라인 (총 길이 {total_dur or '미상'}) — 챕터는 반드시 이 범위를 "
            "처음(00:00)부터 끝까지 골고루 덮어야 함. 너무 잘게 말고 8~15개로 묶어라.\n" + timeline)
    return (
        "당신은 유튜브 SEO 카피라이터입니다. 아래 영상 대본으로 한국어 유튜브 업로드용 메타데이터를 만드세요.\n\n"
        "## 출력 형식\n"
        "제목: (60자 이내, 클릭 유도 + 핵심 키워드)\n"
        "설명: (3~5문단, 첫 2줄에 핵심 요약, 해시태그 3~5개 포함)\n"
        "태그: (쉼표로 12~15개)\n"
        "챕터(타임스탬프): 00:00 형식. 위 타임라인을 근거로 **영상 끝까지** 8~15개 구간. 첫 챕터는 반드시 00:00.\n"
        "업로드 안내: '자막 없는 클린 영상(chNN_final_nosub.mp4)을 올리고, 같은 폴더의 .srt를 유튜브 자막으로 따로 업로드' 한 줄\n\n"
        + (f"## 제목 힌트\n{title_hint}\n\n" if title_hint.strip() else "")
        + f"## 대본(요약 근거)\n{script_text[:9000]}"
        + ts_note
    )


def build_shorts_meta_prompt(script_text: str, original_url: str = "",
                             title_hint: str = "") -> str:
    """유튜브 쇼츠(세로 9:16, ~30초)용 업로드 메타데이터 생성.

    롱폼 메타와 달리 챕터(타임스탬프)는 없고, 짧고 후킹한 제목 + 원본 영상 링크가 핵심.
    original_url 은 설명 첫 줄 CTA(▶ 전체 영상)에 그대로 들어간다.
    """
    link_line = (original_url or "").strip() or "(원본 영상 링크를 여기에 넣으세요)"
    return (
        "당신은 유튜브 쇼츠 전문 카피라이터입니다. 아래 대본을 30초 세로 쇼츠로 만들 때 쓸 "
        "한국어 업로드 메타데이터를 작성하세요. 쇼츠는 빠른 후크와 호기심 유발이 생명입니다.\n\n"
        "## 출력 형식\n"
        "제목: (40자 이내, 강한 후크 + #shorts 포함)\n"
        "설명: (3~4줄. 첫 줄은 강력한 후크 한 문장, 그 다음 줄에 정확히 "
        f"'▶ 전체 영상 보기: {link_line}' 을 넣고, 마지막 줄에 해시태그 3~5개)\n"
        "태그: (쉼표로 10~12개, 쇼츠/주제 키워드)\n"
        "고정댓글: (원본 영상으로 유도하는 한 줄, 위 링크 포함)\n\n"
        + (f"## 제목 힌트\n{title_hint}\n\n" if title_hint.strip() else "")
        + f"## 대본(요약 근거)\n{script_text[:7000]}"
    )
