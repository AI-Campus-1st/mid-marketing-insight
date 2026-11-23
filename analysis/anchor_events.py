"""
[A·D2] 확정 앵커 이벤트 → ai_events 적재

입력: data/processed/anchor_events.csv (고유 이벤트 30개, event_review_sheet.py --export 결과)
      config/companies.csv (ai_events.corp_code 가 companies 를 참조하므로 먼저 적재)
출력: data/processed/project.db 의 companies, ai_events

- ai_events 는 이벤트 파일과 같은 형식이다 (A·B 합의, Day 2).
- 재실행 가능: event_id 기준 upsert. 매칭 스크립트가 채우는 search_terms 는 덮어쓰지 않는다.
  공시 반영 결과는 이 표에 없고 schema.sql 의 event_reflection 뷰가 계산한다.

실행: python analysis/anchor_events.py
"""

import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402

EVENTS = settings.DATA_DIR / "processed" / "anchor_events.csv"
EVENT_COLS = ["event_id", "corp_code", "event_name", "event_type", "official_date",
              "ai_level_claimed", "analysis_scope", "primary_source_url"]
COMPANY_COLS = ["corp_code", "corp_name", "stock_code", "parent_group", "segment", "is_control"]


def rows(df: pd.DataFrame, cols: list[str]) -> list[list]:
    return df[cols].astype(object).where(df[cols].notna(), None).values.tolist()


def main() -> None:
    if not EVENTS.exists():
        raise SystemExit(f"{EVENTS} 가 없습니다 (이벤트 확정 파일을 data/processed/ 에 넣어 주세요)")
    events = pd.read_csv(EVENTS, dtype=str)
    events["ai_level_claimed"] = pd.to_numeric(events["ai_level_claimed"], errors="coerce").astype("Int64")
    companies = pd.read_csv(settings.COMPANIES_CSV, dtype=str)

    missing = set(events["corp_code"]) - set(companies["corp_code"])
    if missing:
        raise SystemExit(f"companies.csv 에 없는 corp_code: {missing}")

    settings.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(settings.DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript((settings.ROOT / "schema.sql").read_text(encoding="utf-8"))

    con.executemany(
        f"""INSERT INTO companies ({", ".join(COMPANY_COLS)}) VALUES ({", ".join("?" * len(COMPANY_COLS))})
            ON CONFLICT(corp_code) DO UPDATE SET
            {", ".join(f"{c} = excluded.{c}" for c in COMPANY_COLS[1:])}""",
        rows(companies, COMPANY_COLS),
    )
    con.executemany(
        f"""INSERT INTO ai_events ({", ".join(EVENT_COLS)}) VALUES ({", ".join("?" * len(EVENT_COLS))})
            ON CONFLICT(event_id) DO UPDATE SET
            {", ".join(f"{c} = excluded.{c}" for c in EVENT_COLS[1:])}""",
        rows(events, EVENT_COLS),
    )
    # 파일에서 빠진 이벤트는 DB 에서도 지운다 (예: 원문 확인 후 제외한 E013). 공시 매칭 기록이 붙은 건 지우지 않는다
    #   (event_dart_mapping 이 event_id 를 참조 — 예전에는 늘 비어 있던 임시 칸 first_rcept_no 로 확인해 실제로는 막지 못했음)
    keep = list(events["event_id"])
    stale = con.execute(
        f"SELECT event_id FROM ai_events WHERE event_id NOT IN ({', '.join('?' * len(keep))})", keep
    ).fetchall()
    for (eid,) in stale:
        has_result = con.execute("SELECT EXISTS (SELECT 1 FROM event_dart_mapping WHERE event_id = ?)", (eid,)).fetchone()[0]
        if has_result:
            print(f"⚠ {eid}: 파일에는 없지만 공시 매칭 기록(event_dart_mapping)이 있어 지우지 않았습니다 — 매칭 기록을 확인한 뒤 지워 주십시오")
        else:
            con.execute("DELETE FROM ai_events WHERE event_id = ?", (eid,))
            print(f"삭제: {eid} (이벤트 파일에서 제외됨)")
    # 원문 확인 기록은 note 칸에 남긴다
    if "verification" in events:
        con.executemany("UPDATE ai_events SET note = ? WHERE event_id = ?",
                        events[["verification", "event_id"]].values.tolist())
    con.commit()
    summary = pd.read_sql(
        """SELECT c.corp_name, e.analysis_scope, COUNT(*) AS n
           FROM ai_events e JOIN companies c USING (corp_code)
           GROUP BY 1, 2 ORDER BY 1, 2""", con)
    con.close()
    print(f"[DB] ai_events {len(events)}개 적재 (companies {len(companies)}개사)")
    print(summary.pivot(index="corp_name", columns="analysis_scope", values="n").fillna(0).astype(int))


if __name__ == "__main__":
    main()
