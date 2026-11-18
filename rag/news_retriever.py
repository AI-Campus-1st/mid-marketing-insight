"""
[A·D7] 뉴스 기사 검색기 — 챗봇이 공시 다음으로 참고하는 두 번째 근거

- 대상: 제공 뉴스(news_origin = 'provided') 중 중복 아님 + LLM(news_v4)이 광고·마케팅 산업 관련으로 고른 기사 (2,492건)
        네이버 보강 기사(supplement)는 약관상 AI 입력 금지라 넣지 않는다 (decisions.md Day 2)
- 입력: 제목 + 정제 본문 앞 1,500자 (뉴스 분류 LLM 입력과 같은 범위, data/processed/news_preprocessed.csv)
- 검색: 공시 검색기(rag/retriever.py)와 같은 방식 — 임베딩 + 2글자 조각 BM25 를 RRF 로 합침
- 기업 필터: 질문에 5개사 이름이 있으면 본문에 그 이름이 나온 기사만 (없으면 전체)
- 캐시: data/vectordb/news_emb_{모델}.npz — doc_id + 입력 해시가 같으면 다시 부르지 않는다 (처음 한 번 약 2,500건, $0.1 미만)
"""
import math
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402
from rag.embeddings import _hash, embed_texts  # noqa: E402
from rag.retriever import CORP_ALIASES, RRF_K, STOP, _grams, detect_corp  # noqa: E402

NEWS_PROMPT = "news_v4"
BODY_CHARS = 1500
CACHE = settings.VECTORDB_DIR / f"news_emb_{settings.EMBED_MODEL}.npz"
CSV = settings.DATA_DIR / "processed" / "news_preprocessed.csv"


def load_news(con: sqlite3.Connection) -> pd.DataFrame:
    meta = pd.read_sql(
        """SELECT d.doc_id, d.title, d.media, substr(d.published_date, 1, 10) AS date, d.url
           FROM documents d JOIN llm_results l ON l.doc_id = d.doc_id AND l.prompt_version = ?
           WHERE d.source_type = 'news' AND d.news_origin = 'provided' AND d.is_duplicate = 0 AND l.is_relevant = 1
           ORDER BY d.doc_id""", con, params=(NEWS_PROMPT,))
    body = pd.read_csv(CSV, usecols=["doc_id", "body_clean"])
    df = meta.merge(body, on="doc_id", how="left")
    df["text"] = df.body_clean.fillna("").str[:BODY_CHARS]
    return df.drop(columns="body_clean")


def news_embeddings(df: pd.DataFrame) -> np.ndarray:
    inp = (df.title + "\n" + df.text).tolist()
    keys = [f"{d}:{_hash(t)}" for d, t in zip(df.doc_id, inp)]
    cached = {}
    if CACHE.exists():
        z = np.load(CACHE, allow_pickle=False)
        cached = {f"{i}:{h}": v for i, h, v in zip(z["ids"], z["hashes"], z["vecs"])}
    todo = [i for i, k in enumerate(keys) if k not in cached]
    if todo:
        print(f"뉴스 임베딩: 새로 {len(todo)}개 (캐시 {len(keys) - len(todo)}개)")
        for i, v in zip(todo, embed_texts([inp[i] for i in todo])):
            cached[keys[i]] = v
        settings.VECTORDB_DIR.mkdir(parents=True, exist_ok=True)
        np.savez(CACHE, ids=np.array([k.split(":")[0] for k in keys]), hashes=np.array([k.split(":")[1] for k in keys]),
                 vecs=np.stack([cached[k] for k in keys]))
    return np.stack([cached[k] for k in keys])


class NewsRetriever:
    def __init__(self, con: sqlite3.Connection | None = None):
        con = con or sqlite3.connect(settings.DB_PATH)
        self.meta = load_news(con)
        self.mat = news_embeddings(self.meta)
        self._docs = [Counter(_grams(t + " " + b)) for t, b in zip(self.meta.title, self.meta.text)]
        self._len = np.array([sum(d.values()) for d in self._docs])
        self._df = Counter(g for d in self._docs for g in d)

    def _bm25(self, q: str, k1: float = 1.2, b: float = 0.75) -> np.ndarray:
        n, avg, sc = len(self._docs), self._len.mean(), np.zeros(len(self._docs))
        for g in set(_grams(q)):
            if g not in self._df:
                continue
            idf = math.log(1 + (n - self._df[g] + .5) / (self._df[g] + .5))
            for i, d in enumerate(self._docs):
                f = d.get(g)
                if f:
                    sc[i] += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * self._len[i] / avg))
        return sc

    def search(self, question: str, k: int = 5, corp: str | None = "auto", qvec: np.ndarray | None = None) -> pd.DataFrame:
        """상위 k개 기사 (corp='auto' 면 질문에서 기업을 찾아 그 이름이 나온 기사만, None 이면 전체)"""
        corp = detect_corp(question) if corp == "auto" else corp
        mask = np.ones(len(self.meta), bool)
        if corp:
            pat = "|".join(CORP_ALIASES[corp])
            m = (self.meta.title + " " + self.meta.text).str.contains(pat, regex=True).values
            mask = m if m.any() else mask
        emb = self.mat @ (qvec if qvec is not None else embed_texts([question])[0])
        kw = self._bm25(" ".join(w for w in question.split() if w not in STOP))
        rank = lambda x: np.argsort(np.argsort(-np.where(mask, x, -1e9))) + 1  # noqa: E731
        score = 1 / (RRF_K + rank(emb)) + 1 / (RRF_K + rank(kw))
        order = np.argsort(-np.where(mask, score, -1e9))[:k]
        out = self.meta.iloc[order].copy()
        out["similarity"] = emb[order].round(3)
        return out.reset_index(drop=True)


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "GEO 생성형 엔진 최적화 서비스"
    for r in NewsRetriever().search(q, k=5).itertuples():
        print(f"{r.date} {r.media} | {r.title} ({r.similarity})")
