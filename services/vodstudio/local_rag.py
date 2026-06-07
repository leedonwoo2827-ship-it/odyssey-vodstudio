"""도커/외부 서비스 없이 동작하는 로컬 RAG (영상공방 전용).

설계 의도:
- odysseus 의 **로컬 임베딩 클라이언트**(`src.embeddings.get_embedding_client` → FastEmbed,
  ONNX, 서비스/도커 불필요)를 그대로 재사용해 "정확한 근거 검색"만 가볍게 얹는다.
- ChromaDB(HTTP 서비스=도커)에 의존하지 않는다. 청크 벡터를 numpy 로 들고 코사인 검색한다.
- 잡(job)별로 `data/vodstudio/<job>/rag.npz` + `rag_chunks.json` 에 저장 → 대본 생성 시 로드.

공개 함수:
    chunk_text(text, ...)                  → 청크 리스트
    build_index(job_dir, sources)          → 첨부 자료를 임베딩·색인(디스크 저장). 통계 dict.
    load_index(job_dir)                    → (chunks, matrix) 또는 None
    search(job_dir, query, k)              → [{text, source, score}] 상위 k
    index_status(job_dir)                  → {indexed, chunks, sources}
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

_CHUNKS_NAME = "rag_chunks.json"
_MATRIX_NAME = "rag.npz"


def chunk_text(text: str, *, target_chars: int = 1100, overlap: int = 150) -> List[str]:
    """문단/조문 경계를 존중하며 ~target_chars 크기로 자른다(겹침 overlap).

    법령은 '제N조' 단위가 자연스러운 경계라 우선 그 경계로 split 후 합친다.
    """
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        return []
    # 1) 조문 경계 우선 분할 (제1조, 제12조의2 등) — 경계 토큰을 보존
    parts = re.split(r"(?=(?:^|\n)\s*제\s*\d+\s*조(?:의\s*\d+)?)", text)
    parts = [p.strip() for p in parts if p and p.strip()]
    if len(parts) <= 1:
        # 조문 형식이 아니면 빈 줄/문단 기준
        parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: List[str] = []
    buf = ""
    for p in parts:
        if len(buf) + len(p) + 1 <= target_chars:
            buf = (buf + "\n" + p).strip()
        else:
            if buf:
                chunks.append(buf)
            if len(p) <= target_chars:
                buf = p
            else:
                # 너무 긴 조문은 글자수로 재분할(겹침 적용)
                i = 0
                while i < len(p):
                    chunks.append(p[i:i + target_chars])
                    i += max(1, target_chars - overlap)
                buf = ""
    if buf:
        chunks.append(buf)
    return chunks


def _embed(texts: List[str]) -> np.ndarray:
    """odysseus 로컬 임베딩 클라이언트로 (N, dim) float32 정규화 행렬 반환."""
    from src.embeddings import get_embedding_client
    client = get_embedding_client()
    vecs = np.asarray(client.encode(texts), dtype=np.float32)
    if vecs.ndim == 1:
        vecs = vecs.reshape(1, -1)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vecs / norms


def build_index(job_dir: str | Path, sources: List[Tuple[str, str]],
                *, target_chars: int = 1100, overlap: int = 150) -> Dict[str, Any]:
    """sources=[(파일명, 텍스트), ...] 를 청크·임베딩해 job_dir 에 저장.

    반환: {"chunks": N, "sources": [{name, chunks}], "dim": d}
    """
    job = Path(job_dir)
    job.mkdir(parents=True, exist_ok=True)
    all_chunks: List[Dict[str, Any]] = []
    per_source: List[Dict[str, Any]] = []
    for name, text in sources:
        cs = chunk_text(text, target_chars=target_chars, overlap=overlap)
        for c in cs:
            all_chunks.append({"text": c, "source": name})
        per_source.append({"name": name, "chunks": len(cs)})
    if not all_chunks:
        raise ValueError("색인할 텍스트가 없습니다.")
    matrix = _embed([c["text"] for c in all_chunks])
    np.savez_compressed(job / _MATRIX_NAME, matrix=matrix)
    (job / _CHUNKS_NAME).write_text(
        json.dumps(all_chunks, ensure_ascii=False), encoding="utf-8")
    return {"chunks": len(all_chunks), "sources": per_source, "dim": int(matrix.shape[1])}


def load_index(job_dir: str | Path) -> Optional[Tuple[List[Dict[str, Any]], np.ndarray]]:
    job = Path(job_dir)
    cf, mf = job / _CHUNKS_NAME, job / _MATRIX_NAME
    if not (cf.exists() and mf.exists()):
        return None
    chunks = json.loads(cf.read_text(encoding="utf-8"))
    matrix = np.load(mf)["matrix"]
    return chunks, matrix


def search(job_dir: str | Path, query: str, k: int = 6) -> List[Dict[str, Any]]:
    """질의와 가장 가까운 청크 상위 k개 반환 [{text, source, score}]."""
    loaded = load_index(job_dir)
    if not loaded or not (query or "").strip():
        return []
    chunks, matrix = loaded
    q = _embed([query])[0]
    scores = matrix @ q  # 정규화돼 있으므로 코사인 유사도
    idx = np.argsort(-scores)[:max(1, k)]
    return [{"text": chunks[i]["text"], "source": chunks[i]["source"],
             "score": float(scores[i])} for i in idx]


def index_status(job_dir: str | Path) -> Dict[str, Any]:
    loaded = load_index(job_dir)
    if not loaded:
        return {"indexed": False, "chunks": 0, "sources": []}
    chunks, _ = loaded
    srcs: Dict[str, int] = {}
    for c in chunks:
        srcs[c["source"]] = srcs.get(c["source"], 0) + 1
    return {"indexed": True, "chunks": len(chunks),
            "sources": [{"name": n, "chunks": c} for n, c in srcs.items()]}
