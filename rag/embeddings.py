"""
[B·D4] 공시 청크 임베딩 (캐시) — 매칭 후보(rag/match_candidates.py)와 RAG(Day 5)가 같이 쓴다

- 모델: settings.EMBED_MODEL (text-embedding-3-small, 1536차원)
- 입력: 섹션 경로 + 청크 본문 (어느 장의 문장인지도 의미에 포함)
- 캐시: data/vectordb/chunk_emb_{모델}.npz — chunk_id + 본문 해시가 같으면 다시 부르지 않는다
        (청킹을 다시 돌려 본문이 바뀐 청크만 새로 임베딩)
- 벡터는 길이 1로 정규화해 저장 → 내적 = 코사인 유사도
필요: .env 의 OPENAI_API_KEY
"""
import hashlib
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402

CACHE = settings.VECTORDB_DIR / f"chunk_emb_{settings.EMBED_MODEL}.npz"
BATCH = 100


def _client():
    from dotenv import load_dotenv
    from openai import OpenAI
    load_dotenv(settings.ROOT / ".env")
    # A 의 event_classify.py 와 같은 이유로 gzip 만 받는다 (httpx 압축 패키지 버전 충돌)
    return OpenAI(default_headers={"Accept-Encoding": "gzip, deflate"})


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _normalize(m: np.ndarray) -> np.ndarray:
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def embed_texts(texts: list[str], client=None) -> np.ndarray:
    """문장 목록 → 정규화된 벡터 (재시도 3회)"""
    client = client or _client()
    out = []
    for i in range(0, len(texts), BATCH):
        for attempt in range(3):
            try:
                res = client.embeddings.create(model=settings.EMBED_MODEL, input=texts[i:i + BATCH])
                out += [d.embedding for d in res.data]
                break
            except Exception as e:  # 연결·속도 제한 오류는 잠시 쉬고 다시
                if attempt == 2:
                    raise
                print(f"  재시도 {attempt + 1}/3 ({type(e).__name__})")
                time.sleep(5 * (attempt + 1))
    return _normalize(np.array(out, dtype=np.float32))


def chunk_embeddings(con: sqlite3.Connection) -> tuple[list[str], np.ndarray]:
    """모든 공시 청크의 (chunk_id 목록, 벡터 행렬). 캐시에 없거나 본문이 바뀐 청크만 API 호출"""
    df = pd.read_sql("SELECT chunk_id, section, text FROM disclosure_chunks ORDER BY chunk_id", con)
    df["inp"] = df.section.fillna("") + "\n" + df.text
    df["h"] = df.inp.map(_hash)
    cached = {}
    if CACHE.exists():
        z = np.load(CACHE, allow_pickle=False)
        cached = {f"{c}:{h}": v for c, h, v in zip(z["ids"], z["hashes"], z["vecs"])}
    keys = (df.chunk_id + ":" + df.h).tolist()
    todo = [i for i, k in enumerate(keys) if k not in cached]
    if todo:
        print(f"임베딩: 새로 {len(todo)}개 (캐시 {len(df) - len(todo)}개)")
        vecs = embed_texts(df.inp.iloc[todo].tolist())
        for i, v in zip(todo, vecs):
            cached[keys[i]] = v
        settings.VECTORDB_DIR.mkdir(parents=True, exist_ok=True)
        np.savez(CACHE, ids=df.chunk_id.to_numpy(str), hashes=df.h.to_numpy(str),
                 vecs=np.stack([cached[k] for k in keys]))
    return df.chunk_id.tolist(), np.stack([cached[k] for k in keys])
