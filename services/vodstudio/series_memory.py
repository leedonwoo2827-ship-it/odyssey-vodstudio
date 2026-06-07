"""시리즈 메모리 — 챕터(영상) 간 톤·용어·청중을 기억해 일관성 유지 (로컬 JSON, 도커 불필요).

저장 위치: data/vodstudio/series_memory.json
구조:
{
  "series": {
    "<series_key>": {
      "audience": "임직원·실무자",
      "objective": "교육",
      "tone": "정중하고 명확한 설명체",
      "glossary": {"KOSHA": "한국산업안전보건공단", ...},
      "chapters": [{"chapter": 2, "title": "...", "updated": "..."}]
    }
  }
}

series_key 기본값은 "default" — 한 시리즈만 쓰면 신경 쓸 필요 없다.
odysseus 의 무거운 벡터 메모리(ChromaDB) 대신, 도커 없이 동작하는 가벼운 키-값 메모리다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

STORE_PATH = Path("data") / "vodstudio" / "series_memory.json"


def _load() -> Dict[str, Any]:
    try:
        if STORE_PATH.is_file():
            return json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"series": {}}


def _save(data: Dict[str, Any]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_series(series_key: str = "default") -> Dict[str, Any]:
    data = _load()
    return data.get("series", {}).get(series_key, {})


def memory_brief(series_key: str = "default") -> str:
    """대본 생성 프롬프트에 끼워 넣을 한 줄 요약(없으면 빈 문자열)."""
    s = get_series(series_key)
    if not s:
        return ""
    bits = []
    if s.get("audience"):
        bits.append(f"청중={s['audience']}")
    if s.get("objective"):
        bits.append(f"목적={s['objective']}")
    if s.get("tone"):
        bits.append(f"톤={s['tone']}")
    gl = s.get("glossary") or {}
    if gl:
        terms = ", ".join(f"{k}={v}" for k, v in list(gl.items())[:20])
        bits.append(f"용어: {terms}")
    chaps = s.get("chapters") or []
    if chaps:
        bits.append("이전 화: " + ", ".join(f"ch{c.get('chapter')}:{c.get('title','')}" for c in chaps[-5:]))
    return " · ".join(bits)


def remember_chapter(series_key: str, *, chapter: int, title: str,
                     audience: Optional[str] = None, objective: Optional[str] = None,
                     tone: Optional[str] = None, glossary: Optional[Dict[str, str]] = None,
                     updated: str = "") -> Dict[str, Any]:
    """챕터 저장 시 시리즈 메모리 갱신(이미 있으면 항목 업데이트/추가)."""
    data = _load()
    series = data.setdefault("series", {})
    s = series.setdefault(series_key, {"chapters": []})
    if audience:
        s["audience"] = audience
    if objective:
        s["objective"] = objective
    if tone:
        s["tone"] = tone
    if glossary:
        g = s.setdefault("glossary", {})
        g.update(glossary)
    chaps: List[Dict[str, Any]] = s.setdefault("chapters", [])
    existing = next((c for c in chaps if c.get("chapter") == chapter), None)
    if existing:
        existing.update({"title": title, "updated": updated})
    else:
        chaps.append({"chapter": chapter, "title": title, "updated": updated})
    _save(data)
    return s


def set_series(series_key: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    data = _load()
    s = data.setdefault("series", {}).setdefault(series_key, {})
    for k in ("audience", "objective", "tone"):
        if k in fields and fields[k] is not None:
            s[k] = fields[k]
    if isinstance(fields.get("glossary"), dict):
        s.setdefault("glossary", {}).update(fields["glossary"])
    _save(data)
    return s
