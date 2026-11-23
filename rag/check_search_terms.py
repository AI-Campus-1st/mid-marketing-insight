"""
[B·D3] 공시 매칭용 검색어(search_terms) 점검

config/event_search_terms.csv 의 이벤트별 검색어를 공시 청크(disclosure_chunks)에 대 보고,
검색어마다 몇 개 청크에 걸리는지, 처음 걸린 보고서가 이벤트 전인지 후인지 보여준다.
검색어를 고칠 때마다 다시 실행해 오탐(엉뚱한 문장)과 누락을 확인한다.

    - 이벤트 전에 걸림: '공시가 먼저 말함' 이거나 같은 단어가 다른 뜻으로 쓰인 오탐 → 문맥 확인
    - 0건: 공시에 이름 없이 설명만 나오거나 실제로 없음 → 의미 검색(임베딩)으로 보완
    - 검색 범위: 같은 기업 청크 중 report_date 가 이벤트 1년 전 ~ 수집 종료 (실행 설계서 4절)

실행: python rag/check_search_terms.py [--context]   # --context: 처음 걸린 문장 일부도 출력
"""
import argparse
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402

TERMS_CSV = settings.CONFIG_DIR / "event_search_terms.csv"


def main():
    parser = argparse.ArgumentParser(description="공시 매칭용 검색어 점검")
    parser.add_argument("--context", action="store_true", help="처음 걸린 문장 일부 출력")
    args = parser.parse_args()

    events = pd.read_csv(TERMS_CSV, dtype=str)
    con = sqlite3.connect(settings.DB_PATH)
    chunks = pd.read_sql(
        """SELECT c.corp_name, k.report_date, k.text FROM disclosure_chunks k
           JOIN companies c ON c.corp_code = k.corp_code""", con)

    summary = []
    for ev in events.itertuples():
        start = (date.fromisoformat(ev.official_date) - timedelta(days=365)).isoformat()
        pool = chunks[(chunks.corp_name == ev.corp_name) & (chunks.report_date >= start)]
        scope = "참고용" if ev.official_date > settings.REFLECTION_CUTOFF else "판정"
        terms = json.loads(ev.search_terms)
        hit = pool[pool.text.apply(lambda t: any(term in t for term in terms))].sort_values("report_date")

        before = (hit.report_date < ev.official_date).sum()
        first = hit.report_date.iloc[0] if len(hit) else "-"
        summary.append((ev.corp_name, ev.official_date, ev.event_name, scope, len(hit), before, first))

        if args.context and len(hit):
            text = hit.text.iloc[0]
            term = next(t for t in terms if t in text)
            i = text.find(term)
            print(f"  {ev.corp_name} {ev.event_name} [{hit.report_date.iloc[0]}] …{text[max(0, i - 40):i + 60]}…"
                  .replace("\n", " "))

    df = pd.DataFrame(summary, columns=["기업", "이벤트일", "이벤트", "범위", "후보 청크", "이벤트 전", "처음 걸린 공시"])
    print(df.to_string(index=False))
    found = df[df["후보 청크"] > 0]
    print(f"\n[요약] 이벤트 {len(df)}개 중 검색어로 후보가 나온 이벤트 {len(found)}개, "
          f"0건 {len(df) - len(found)}개 (의미 검색으로 보완), 이벤트 전에도 걸린 이벤트 {(df['이벤트 전'] > 0).sum()}개")


if __name__ == "__main__":
    main()
