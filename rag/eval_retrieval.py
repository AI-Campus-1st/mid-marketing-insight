"""
[B·D5] RAG 검색 평가 — 챗봇을 만들기 전에 '질문하면 근거 공시를 찾아오는지'와 '거절 기준'을 먼저 잰다

- 질문: rag/test_questions.csv (답이 있는 질문 10 + 답이 없어야 하는 질문 5)
- 정답: 답이 있는 질문은 '그 기업 청크 중 evidence_pattern(정규표현식, | 로 여러 표현)이 들어간 청크'가 상위 K 안에 있으면 맞음
        (같은 문장이 분기마다 반복되므로 특정 청크 하나가 아니라 표현으로 판단)
- 비교: ① 임베딩만, 기업 필터 없음 ② 임베딩 + 기업 필터 ③ 하이브리드(임베딩 + 키워드 BM25) + 기업 필터 (rag/retriever.py)
- 거절 기준: 답이 있는 질문과 없는 질문의 1위 유사도가 갈리는지 본다

실행: python rag/eval_retrieval.py [--k 5]
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rag.retriever import Retriever  # noqa: E402

QUESTIONS = Path(__file__).with_name("test_questions.csv")
MODES = {"① 임베딩·필터 없음": dict(mode="embedding", corp=None),
         "② 임베딩·기업 필터": dict(mode="embedding", corp="auto"),
         "③ 하이브리드·기업 필터": dict(mode="hybrid", corp="auto")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    k = ap.parse_args().k
    ret = Retriever()
    qs = pd.read_csv(QUESTIONS, dtype=str).fillna("")
    rows = []
    for q in qs.itertuples():
        for name, kw in MODES.items():
            top = ret.search(q.question, k=k, **kw)
            hit = None
            if q.type == "answer":
                ok = (top.corp_name == q.corp) & top.text.str.contains(q.evidence_pattern, regex=True)
                hit = int(ok.values.argmax()) + 1 if ok.any() else None
            rows.append({"qid": q.qid, "type": q.type, "방식": name, "hit_rank": hit, "top1_sim": top.similarity.iloc[0],
                         "top1": f"{top.corp_name.iloc[0]} | {top.report_name.iloc[0]} | {top.text.iloc[0][:30]}"})
    r = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    pd.set_option("display.max_colwidth", 60)
    print(r[r["방식"] == "③ 하이브리드·기업 필터"].to_string(index=False))
    print(f"\n[검색 정확도] 답이 있는 질문 10개 중 근거가 상위 {k} 안에 (1위)")
    for name in MODES:
        g = r[(r["방식"] == name) & (r.type == "answer")]
        print(f"  {name}: {g.hit_rank.notna().sum()}/10 ({(g.hit_rank == 1).sum()})")
    g = r[r["방식"] == "③ 하이브리드·기업 필터"]
    a, u = g[g.type == "answer"].top1_sim, g[g.type == "refuse"].top1_sim
    print(f"[거절 기준] 1위 유사도 — 답 있는 질문 {a.min():.3f}~{a.max():.3f} / 답 없어야 하는 질문 {u.min():.3f}~{u.max():.3f}"
          + (" → 겹침: 유사도 경계값으로는 거절 못 함, 답변 단계(LLM)에서 근거 확인" if a.min() <= u.max() else ""))


if __name__ == "__main__":
    main()
