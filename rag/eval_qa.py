"""
[B·D5] 공시 Q&A 끝까지 평가 — 테스트 질문 15개(rag/test_questions.csv)로 답변·거절·출처를 확인

판정 (자동)
    답이 있는 질문(10): ① 답함(거절 안 함) ② 인용한 근거 중 하나에 evidence_pattern 이 있음(근거가 맞음)
    답이 없어야 하는 질문(5): 거절함
    → 사람 확인용으로 답변 전문과 기대 답을 나란히 저장 (내용이 맞는지는 사람이 읽고 'human_ok' 칸에 O/X)

실행: python rag/eval_qa.py
출력: data/processed/review/qa_eval.csv (질문·기대 답·챗봇 답·출처·자동 판정)
"""
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402
from rag.qa_chain import QA  # noqa: E402

QUESTIONS = Path(__file__).with_name("test_questions.csv")
OUT = settings.DATA_DIR / "processed" / "review" / "qa_eval.csv"


def main() -> None:
    qa = QA()
    con = sqlite3.connect(settings.DB_PATH)
    qs = pd.read_csv(QUESTIONS, dtype=str).fillna("")
    rows = []
    for q in qs.itertuples():
        r = qa.ask(q.question)
        cited = [s["chunk_id"] for s in r["sources"]]
        texts = [con.execute("SELECT text FROM disclosure_chunks WHERE chunk_id = ?", (c,)).fetchone()[0] for c in cited]
        if q.type == "answer":
            grounded = any(re.search(q.evidence_pattern, t) for t in texts)
            ok = r["answerable"] and grounded
            why = "거절함(놓침)" if not r["answerable"] else ("근거 맞음" if grounded else "답했지만 인용 근거에 정답 표현 없음")
        else:
            ok = not r["answerable"]
            why = "거절함" if ok else "답해 버림(환각 위험)"
        rows.append({"qid": q.qid, "type": q.type, "auto_ok": "O" if ok else "X", "why": why, "question": q.question,
                     "expected": q.expected_answer, "answer": r["answer"],
                     "sources": " / ".join(f"[{s['n']}] {s['report']} {s['report_date']}" for s in r["sources"]),
                     "corp_filter": r["corp_filter"], "human_ok": ""})
    df = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False, encoding="utf-8-sig")
    pd.set_option("display.width", 220)
    pd.set_option("display.max_colwidth", 90)
    for r in df.itertuples():
        print(f"\n{r.qid} [{r.auto_ok}] {r.why} | 필터: {r.corp_filter}\n  Q: {r.question}\n  A: {r.answer}\n  기대: {r.expected}\n  출처: {r.sources}")
    a, u = df[df.type == "answer"], df[df.type == "refuse"]
    print(f"\n[결과] 답이 있는 질문 {(a.auto_ok == 'O').sum()}/{len(a)} 근거와 함께 답함 · "
          f"답이 없어야 하는 질문 {(u.auto_ok == 'O').sum()}/{len(u)} 거절 → {OUT.relative_to(settings.ROOT)}")


if __name__ == "__main__":
    main()
