"""대본 강화용 프롬프트 빌더 + RAG 컨텍스트 조립 (영상공방).

순수 함수(동기) 모음 — LLM 호출 자체는 라우트에서 `_llm_generate`로 한다.
RAG 검색은 `local_rag.search`(로컬 FastEmbed, 도커 불필요)를 쓴다.

build_* 함수들은 모두 '명령줄 길이 한계(WinError 206)'를 넘지 않도록 컨텍스트를
max_chars 로 제한한 **유한 크기 프롬프트**를 만든다.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from . import local_rag
from . import prompts as vod_prompts


def split_slides(script_text: str, *, per: int = 12) -> List[str]:
    """대본을 '슬라이드 번호' 단위로 나눠 per개씩 묶는다.

    출력 토큰 한계(잘림)를 피하려고 수정/재작성을 묶음 단위로 처리하기 위함.
    마커가 없으면 통째로 한 덩어리로 돌려준다.
    """
    t = (script_text or "").strip()
    if not t:
        return []
    blocks = [b.strip() for b in re.split(r"(?=슬라이드\s*번호)", t) if "슬라이드" in b]
    if len(blocks) <= 1:
        return [t]
    return ["\n\n".join(blocks[i:i + per]) for i in range(0, len(blocks), per)]


def review_queries(script_text: str, *, n: int = 8) -> List[str]:
    """대본 전체를 골고루 덮는 RAG 질의 목록(특정 도메인 하드코딩 없이 대본에서 직접 추출).

    앞부분만 보던 기존 방식과 달리, 대본을 여러 구간으로 나눠 각 구간을 질의로 써서
    근거 검색이 자료 전체를 훑게 한다 → '근거에 없음' 오탐 방지.
    """
    t = (script_text or "").strip()
    if not t:
        return []
    blocks = [b.strip() for b in re.split(r"(?=슬라이드\s*번호)", t) if "슬라이드" in b]
    if len(blocks) < 2:
        size = max(1, len(t) // n)
        blocks = [t[i:i + size] for i in range(0, len(t), size)]
    if len(blocks) > n:
        step = len(blocks) / n
        blocks = [blocks[int(i * step)] for i in range(n)]
    return [b[:400] for b in blocks if b.strip()]


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
        "당신은 교육 콘텐츠 감수자입니다. 아래 [대본]을 [근거 자료]와 대조해 "
        "문제를 한국어로 점검하세요. 근거에 없는 주장, 사실과 다른 서술, 과장, 누락된 중요 항목을 찾습니다.\n\n"
        "## 중요 원칙\n"
        "- [근거 자료]는 전체의 '발췌'입니다. 발췌에서 확인되는 수치·서술은 정확한 것으로 인정하세요.\n"
        "- 발췌에 없다는 이유만으로 곧장 🔴로 단정하지 말고, 명백히 자료와 어긋날 때만 🔴로 표시하세요.\n\n"
        "## 출력 형식\n"
        "- 🔴 부정확/근거없음: (슬라이드 번호 — 문제 — 올바른 내용)\n"
        "- 🟡 과장/모호: (슬라이드 번호 — 문제 — 수정 제안)\n"
        "- 🟢 누락(자료엔 있는데 대본에 빠짐): (항목 — 어디에 넣으면 좋을지)\n"
        "- 한 줄 총평\n"
        "문제가 없으면 '발견된 문제 없음'이라고 쓰세요.\n\n"
        f"## 대본\n{script_text[:30000]}\n\n"
        f"## 근거 자료\n{context}\n"
    )


def build_revise_prompt(script_text: str, review_report: str, context: str = "") -> str:
    """검수 결과(특히 🟡 과장/모호)를 반영해 대본을 다듬어 **전체 그대로 재출력**.

    슬라이드 묶음 단위로 호출되므로 script_text 는 일부(예: 12장)일 수 있다.
    """
    ctx_block = f"\n## 근거 자료(사실 확인용 발췌)\n{context}\n" if context.strip() else ""
    return (
        "당신은 교육 콘텐츠 에디터입니다. 아래 [대본]을 [검수 결과]에 따라 수정해 "
        "**대본 전체를 같은 형식 그대로 다시 출력**하세요. 설명·머리말 없이 대본 본문만 출력합니다.\n\n"
        "## 수정 규칙 (반드시 지킬 것)\n"
        "1) 🟡(과장/모호) 지적은 모두 반영해 표현을 사실에 맞게 완화·명확화한다.\n"
        "2) 🟢(누락) 지적 중 자연스러운 것은 해당 슬라이드에 한 줄만 보강한다(분량 과다 금지).\n"
        "3) 🔴(근거없음)으로 표시된 수치라도 함부로 지우지 말 것 — 근거가 발췌라 생긴 오탐일 수 있다. "
        "근거 자료에서 확인되거나 일반적으로 타당하면 유지하고, 정말 자료와 어긋날 때만 고친다.\n"
        "4) 지적되지 않은 슬라이드·문장·수치·구조·슬라이드 번호·형식은 그대로 유지한다(임의 재작성 금지).\n"
        "5) 이 묶음에 포함된 슬라이드만, 받은 순서·번호 그대로 출력한다.\n\n"
        f"## 검수 결과\n{review_report[:6000]}\n"
        f"{ctx_block}"
        f"\n## 대본\n{script_text[:16000]}\n"
    )


def build_youtube_prompt(script_text: str, timeline: str = "", title_hint: str = "",
                         total_dur: str = "", *, genre: str = "", chapter_title: str = "",
                         book_title: str = "", chapter_no: str = "") -> str:
    """YouTube 업로드 메타데이터 생성 (고품격 다큐 형식).

    book_title(책/시리즈), chapter_no(장 번호), chapter_title(장 제목)이 주어지면 제목에
    그대로 사용한다 — 번호/장 제목을 LLM이 추측하지 않게 한다.
    timeline: 'HH:MM:SS 제목' 줄 목록(render_report 실제 시작시각). 설명 하단 챕터의 근거.
    """
    book = (book_title or "").strip()
    cno = str(chapter_no or "").strip()
    ctitle = (chapter_title or "").strip()
    ts_note = ""
    if timeline.strip():
        ts_note = (
            f"\n\n## 영상 타임라인 (총 길이 {total_dur or '미상'}) — 아래 시작시각을 그대로 근거로,\n"
            "설명 하단 챕터를 처음(00:00:00)부터 끝까지 골고루 덮어라. 씬을 1:1로 다 넣지 말고\n"
            "의미 단위로 묶어 10~18개 구간으로. 첫 챕터는 반드시 00:00:00.\n" + timeline)
    head = "## 확정 정보 (아래 값을 그대로 사용 — 추측 금지)\n"
    if book:
        head += f"- 책(시리즈) 제목: {book}\n"
    if cno:
        head += f"- 장 번호: {cno}\n"
    if ctitle:
        head += f"- 이 장(영상) 제목: {ctitle}\n"
    if genre.strip():
        head += f"- 장르: {genre}\n"
    head += "\n"
    num_part = f"{cno}. " if cno else ""
    core = ctitle if ctitle else "<핵심 제목>"
    title_rule = (
        f"제목: `[<라벨>] {num_part}{core} (<영상에서 다루는 소주제 5~8개를 쉼표로 나열>) #해시태그 #해시태그 …`\n"
        + (f"  - 장 번호({cno})와 장 제목('{ctitle}')은 **주어진 값 그대로** 쓴다(바꾸거나 추측 금지).\n" if (cno or ctitle) else "")
        + (f"  - <라벨>은 책 '{book}' 성격에 맞는 대괄호 다큐 라벨 (예: [역사 다큐], [교육의 역사]).\n" if book
           else "  - <라벨>은 내용에 맞는 대괄호 다큐 라벨 (예: [역사 다큐], [교육의 역사]).\n")
        + "  - 끝에 핵심 해시태그 3~5개를 붙인다.\n"
    )
    desc_first = (
        f"  - 첫 줄: 책 '{book}'의 {cno + '번째 ' if cno else ''}이야기임을 밝히며 '고품격 다큐멘터리를 준비했습니다' 류의 후킹 한 문장.\n"
        if book else
        "  - 첫 줄: 영상 제목을 인용하며 '고품격 다큐멘터리를 준비했습니다' 류의 후킹 한 문장.\n"
    )
    return (
        "당신은 고품격 다큐멘터리 유튜브 채널의 SEO 카피라이터입니다. 아래 영상 대본으로\n"
        "한국어 유튜브 업로드용 메타데이터를 아래 형식 그대로 작성하세요.\n\n"
        + head
        + "## 형식 (반드시 이 순서·스타일)\n"
        + title_rule
        + "설명:\n"
        + desc_first
        + "  - 본문: 4~6개 문단으로 영상의 서사 흐름을 시간/사건 순서대로 흥미진진하게 서술.\n"
        "    (각 문단은 실제 대본 내용에 근거 — 없는 사실은 지어내지 말 것.) 마지막에 가벼운 이모지 1개.\n"
        "  - 추천 한 줄: '~하고 싶은 모든 분들께 추천합니다.' 류.\n"
        "  - 그 다음 정확히 이 헤더 한 줄: `📌 타임라인(Chapters)으로 골라보기`\n"
        "  - 그 아래에 챕터 줄들: `HH:MM:SS <소제목> (<짧은 부연>)` 형식, 위 타임라인 시작시각 사용.\n"
        "태그: (쉼표로 12~15개)\n"
        "(그 외 안내문·업로드 설명 등은 출력하지 마라. 제목/설명/태그만.)\n\n"
        + (f"## 제목 힌트\n{title_hint}\n\n" if title_hint.strip() else "")
        + f"## 대본(요약 근거)\n{script_text[:9000]}"
        + ts_note
    )


def build_intro_script_prompt(script_text: str, duration: float = 15.0,
                              speed: float = 1.15) -> str:
    """본편 앞에 붙는 가로 인트로(목차/요약) 내레이션 대본 생성.

    길이(duration)와 말 속도(speed)에 맞춰 분량을 잡는다. 빠른 컷 위에 깔리는
    빠른 나레이션이라, 군더더기 없이 '이 영상에서 무엇을 배우는지'를 후킹+핵심만.
    """
    # 한국어 내레이션 ~5.2자/초(정상). speed로 빨라지므로 그만큼 더 담을 수 있다.
    target = max(40, int(round(duration * max(1.0, speed) * 5.2)))
    lo, hi = int(target * 0.85), int(target * 1.15)
    return (
        "당신은 교육 영상 '인트로 내레이션' 작가입니다. 아래 본편 대본을 보고, 영상 맨 앞에 "
        "붙일 인트로 내레이션을 쓰세요. 빠른 장면 전환 위에 깔리는 빠른 내레이션입니다.\n\n"
        "## 목표\n"
        "- 시청자가 끝까지 보고 싶게 만드는 강한 후크로 시작\n"
        "- '이 영상에서 무엇을 배우는지'를 핵심 목차/요약으로 압축\n"
        "- 마지막은 본편으로 자연스럽게 넘어가는 한마디\n\n"
        "## 분량/형식 (반드시 지킬 것)\n"
        f"- 약 {lo}~{hi}자(한국어), {duration:.0f}초 분량(말 속도 {speed:.2f}배 기준)\n"
        "- 실제로 '소리내어 읽는 문장'만 출력 (제목/머리말/번호/따옴표/마크다운/괄호설명 금지)\n"
        "- 2~4문장, 자연스러운 구어체\n\n"
        f"## 본편 대본(요약 근거)\n{script_text[:7000]}"
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
