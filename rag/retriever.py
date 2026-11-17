"""
[B·D5] 공시 청크 검색기 — 임베딩(의미) + 키워드(BM25) 하이브리드, 기업 필터

- 의미 검색: rag/embeddings.py 의 청크 벡터와 질문 벡터의 코사인 유사도
- 키워드 검색: 한국어 띄어쓰기·조사에 덜 흔들리도록 2글자 조각(bigram) BM25. 'AX Unit', '스텔라이즈' 같은 고유 이름에 강함
- 합치기: 두 순위의 RRF(Reciprocal Rank Fusion, 1/(60+순위) 합) — 점수 단위가 달라도 순위로 합칠 수 있음
- 구 일치 가산점: 질문의 두 단어 구(조사 뗌, 예: 'AI 얼라이언스')가 그대로 들어간 청크는 순위 하나를 더 합친다
- 기업 필터: 질문에 대상 기업 이름이 있으면 그 기업 청크만 검색 (eval_retrieval: 필터 없이 4/10 → 필터 7/10 → 하이브리드 9/10)
- 거절은 여기서 하지 않는다: 답이 있는 질문과 없는 질문의 유사도가 겹쳐서(eval_retrieval) 유사도 경계값으로는 못 가름
  → 답변 단계(LLM)가 근거에 답이 있는지 판단해 거절한다 (rag/qa_chain.py)
"""
import math
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402
from rag.embeddings import chunk_embeddings, embed_texts  # noqa: E402

CORP_ALIASES = {"KT나스미디어": ["KT나스미디어", "케이티나스미디어", "나스미디어"], "이노션": ["이노션"], "인크로스": ["인크로스"],
                "제일기획": ["제일기획"], "플레이디": ["플레이디"]}
# 질문 말투(의문사·어미)는 키워드 점수에서 뺀다
STOP = {"어떤", "무엇", "무엇인가요", "무엇인지", "언제", "어떻게", "알려주세요", "알려줘", "있나요", "인가요", "했나요", "하나요",
        "몇", "개", "어디", "얼마", "얼마인가요", "궁금해요", "궁금합니다"}
RRF_K = 60
# 조사 떼기 (구 일치 가산점용): 'AI 얼라이언스에는' → 'AI 얼라이언스'
PARTICLES = ("에서는", "에서", "에는", "으로", "로는", "에게", "까지", "부터", "은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "로", "도")


def detect_corp(q: str) -> str | None:
    for corp, names in CORP_ALIASES.items():
        if any(n in q for n in names):
            return corp
    return None


def _strip(w: str) -> str:
    for p in PARTICLES:
        if w.endswith(p) and len(w) > len(p) + 1:
            return w[: -len(p)]
    return w


def phrases(question: str) -> list[str]:
    """질문에서 이어진 두 단어 구(조사 뗌, 의문사·기업명 제외). 예: 'AI 얼라이언스', '설문조사 서비스'"""
    corp_words = {n for names in CORP_ALIASES.values() for n in names}
    ws = [_strip(w) for w in re.findall(r"[0-9A-Za-z가-힣]+", question)]
    ws = [w for w in ws if w not in STOP and _strip(w) not in corp_words and not any(w.startswith(c) for c in corp_words)]
    return [f"{a} {b}" for a, b in zip(ws, ws[1:])]


def _grams(s: str) -> list[str]:
    out = []
    for t in re.findall(r"[0-9a-z가-힣]+", s.lower()):
        out += [t] if len(t) <= 2 else [t[i:i + 2] for i in range(len(t) - 1)]
    return out


class Retriever:
    def __init__(self, con: sqlite3.Connection | None = None):
        con = con or sqlite3.connect(settings.DB_PATH)
        meta = pd.read_sql(
            """SELECT k.chunk_id, c.corp_name, d.title AS report_name, k.report_date, k.rcept_no, k.section,
                      k.ai_level, k.text
               FROM disclosure_chunks k JOIN companies c USING(corp_code) JOIN documents d ON d.doc_id = k.doc_id""",
            con).set_index("chunk_id")
        self.ids, self.mat = chunk_embeddings(con)
        self.meta = meta.loc[self.ids].reset_index()
        self._docs = [Counter(_grams(t)) for t in self.meta.text]
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

    def search(self, question: str, k: int = 5, corp: str | None = "auto", mode: str = "hybrid") -> pd.DataFrame:
        """상위 k개 청크 (corp='auto' 면 질문에서 기업을 찾아 필터, None 이면 전체). mode: hybrid / embedding"""
        corp = detect_corp(question) if corp == "auto" else corp
        mask = (self.meta.corp_name.values == corp) if corp else np.ones(len(self.ids), bool)
        emb = self.mat @ embed_texts([question])[0]
        if mode == "embedding":
            score = emb
        else:
            kw = self._bm25(" ".join(w for w in question.split() if w not in STOP))
            rank = lambda x: np.argsort(np.argsort(-np.where(mask, x, -1e9))) + 1  # noqa: E731
            score = 1 / (RRF_K + rank(emb)) + 1 / (RRF_K + rank(kw))
            # 질문의 두 단어 구가 그대로 들어간 청크에 가산점 (예: 'AI 얼라이언스' — 의미·키워드 점수만으로는 22위였음)
            ph = phrases(question)
            if ph:
                norm = self.meta.text.str.replace(r"\s+", " ", regex=True)
                hit = sum(norm.str.contains(p, regex=False).astype(int) for p in ph).values
                score = score + np.where(hit > 0, 1 / (RRF_K + rank(hit.astype(float))), 0)
        order = np.argsort(-np.where(mask, score, -1e9))[:k]
        out = self.meta.iloc[order].copy()
        out["similarity"] = emb[order].round(3)
        out["corp_filter"] = corp
        return out.reset_index(drop=True)
