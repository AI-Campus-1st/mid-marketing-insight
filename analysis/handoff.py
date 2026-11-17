"""
[A·D6] A → B 데이터 넘기기: 대시보드를 한 DB 로 띄우기 위해 A 에만 있는 결과를 B DB 로 옮긴다

A 가 실행: python analysis/handoff.py export   → data/handoff/A_to_B_<날짜>/ (+ .zip)
B 가 실행: python analysis/handoff.py import data/handoff/A_to_B_<날짜>   (B 의 project.db 에 넣음)

넘기는 것
  tables/llm_results.csv     뉴스 LLM 분류 (news_v1~v4, 화면은 news_v4)
  tables/news_company.csv    기사↔기업 태그 (in_title)
  tables/review_samples.csv  뉴스 검수 기록 (doc_id 있는 행, reviewer AB)
  files/news_preprocessed.csv  정제 본문 + AI·기업 태그 (대시보드 Q1·Q2 가 읽음 → data/processed/ 에 복사)
  files/q2_concern_articles.csv  사람이 거른 우려 기사 (있을 때만)
  check/documents_news.csv   A DB 의 제공 뉴스 doc_id·중복 표시·본문 해시 → import 전에 B DB 와 대조

import 는 먼저 대조하고(다르면 멈춤), 같은 키의 행만 지우고 다시 넣는다 (공시 쪽 행은 건드리지 않음)
"""

import argparse
import shutil
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402

PROCESSED = settings.DATA_DIR / "processed"
FILES = ["news_preprocessed.csv", "q2_concern_articles.csv"]
QUERIES = {
    "llm_results": "SELECT * FROM llm_results",
    "news_company": "SELECT * FROM news_company",
    "review_samples": "SELECT * FROM review_samples WHERE doc_id IS NOT NULL",
}
CHECK = ("SELECT doc_id, is_duplicate, body_hash FROM documents "
         "WHERE source_type = 'news' AND news_origin = 'provided' ORDER BY doc_id")


README = """# A → B 데이터 묶음 ({d})

대시보드(`streamlit run dashboard/app.py`)를 **B 의 DB 하나로** 띄우기 위해, A 에만 있는 뉴스 결과를 넘깁니다.

## 넣는 법 (B)
1. 이 폴더를 저장소의 `data/handoff/` 아래에 둔다 (`data/handoff/{d}/`)
2. B DB 의 제공 뉴스가 최신 규칙(중복 규칙 ② 24시간)으로 적재돼 있어야 함 — 아니면 `python collect/load_news.py`
3. `python analysis/handoff.py import data/handoff/{d}`
   - 브랜치에 `analysis/handoff.py` 가 아직 없으면 이 폴더의 `handoff.py` 를 `analysis/` 에 복사해서 실행
   - 먼저 제공 뉴스 doc_id·중복 표시·본문 해시를 A 와 대조하고, 다르면 아무것도 바꾸지 않고 멈춤
4. `streamlit run dashboard/app.py` → Q1·Q2(뉴스)와 Q3·챗봇(공시)이 모두 나오는지 확인

## 들어 있는 것
| 파일 | 내용 | 넣는 곳 |
|---|---|---|
| tables/llm_results.csv | 뉴스 LLM 분류 {llm_results}행 (news_v1~v4, 화면은 news_v4 6,448건) | `llm_results` (같은 doc_id·버전만 바꿈) |
| tables/news_company.csv | 기사↔기업 태그 {news_company}행 (in_title = 제목 등장) | `news_company` (전체 교체) |
| tables/review_samples.csv | 뉴스 검수 기록 {review_samples}행 (A·B 합의 판정, reviewer AB) | `review_samples` 의 뉴스 행만 교체 (공시 청크 행은 그대로) |
| files/ | {files} | `data/processed/` 에 복사 |
| check/documents_news.csv | A DB 제공 뉴스 {n_doc}건 (중복 표시 {n_dup}) | 대조용 |

공시 쪽 테이블(`disclosure_chunks`, `event_dart_mapping` 등)은 건드리지 않아요.
우려 기사 목록(`q2_concern_articles.csv`, A 사람 검수 결과)이 files/ 에 없으면 Q2 화면에 '검수 후 표시' 안내만 나와요.
뉴스 본문이 들어 있으니 저장소에는 올리지 마세요 (`data/handoff/` 는 .gitignore).
"""


def export() -> None:
    out = settings.DATA_DIR / "handoff" / f"A_to_B_{date.today():%Y%m%d}"
    if out.exists():
        shutil.rmtree(out)
    for sub in ["tables", "files", "check"]:
        (out / sub).mkdir(parents=True)
    with sqlite3.connect(settings.DB_PATH) as con:
        counts = {}
        for name, q in QUERIES.items():
            df = pd.read_sql(q, con)
            df.to_csv(out / "tables" / f"{name}.csv", index=False, encoding="utf-8-sig")
            counts[name] = len(df)
        chk = pd.read_sql(CHECK, con)
        chk.to_csv(out / "check" / "documents_news.csv", index=False, encoding="utf-8-sig")
    copied = []
    for f in FILES:
        if (PROCESSED / f).exists():
            shutil.copy2(PROCESSED / f, out / "files" / f)
            copied.append(f)
    shutil.copy2(Path(__file__), out / "handoff.py")   # B 가 브랜치를 받기 전에도 쓸 수 있게
    (out / "README.md").write_text(README.format(d=out.name, **counts, n_doc=len(chk), n_dup=int(chk.is_duplicate.sum()),
                                                 files=", ".join(copied) or "없음"), encoding="utf-8")
    zip_path = shutil.make_archive(str(out), "zip", out)
    print(f"→ {out.relative_to(settings.ROOT)}  (+ {Path(zip_path).name})")
    for k, v in counts.items():
        print(f"  tables/{k}.csv {v:,}행")
    print(f"  files/ {', '.join(copied)}  |  check/documents_news.csv {len(chk):,}행 (중복 표시 {int(chk.is_duplicate.sum())})")
    missing = [f for f in FILES if f not in copied]
    if missing:
        print(f"  (없어서 안 넣음: {', '.join(missing)})")


def import_(src: Path) -> None:
    src = Path(src)
    with sqlite3.connect(settings.DB_PATH) as con:
        # 1) 대조: 제공 뉴스 doc_id·중복 표시·본문 해시가 같아야 테이블이 맞게 연결된다
        mine = pd.read_sql(CHECK, con)
        theirs = pd.read_csv(src / "check" / "documents_news.csv", dtype={"is_duplicate": int})
        m = theirs.merge(mine, on="doc_id", how="outer", suffixes=("_A", "_B"), indicator=True)
        bad = m[(m["_merge"] != "both") | (m["is_duplicate_A"] != m["is_duplicate_B"])
                | (m["body_hash_A"].fillna("") != m["body_hash_B"].fillna(""))]
        if len(bad):
            print(f"멈춤: 제공 뉴스가 A 와 다름 {len(bad)}건 (예: {bad.doc_id.head(5).tolist()}).")
            print("→ 최신 develop 의 collect/load_news.py 로 다시 적재한 뒤 실행하세요 (중복 규칙 ② 24시간).")
            sys.exit(1)
        print(f"대조 OK: 제공 뉴스 {len(mine):,}건, 중복 표시 {int(mine.is_duplicate.sum())}건 일치")
        # 2) 테이블: 같은 키만 지우고 넣기
        con.execute("PRAGMA foreign_keys = ON")
        llm = pd.read_csv(src / "tables" / "llm_results.csv")
        con.executemany("DELETE FROM llm_results WHERE doc_id = ? AND prompt_version = ?",
                        llm[["doc_id", "prompt_version"]].values.tolist())
        llm.to_sql("llm_results", con, if_exists="append", index=False)
        nc = pd.read_csv(src / "tables" / "news_company.csv", dtype={"corp_code": str})
        con.execute("DELETE FROM news_company")
        nc.to_sql("news_company", con, if_exists="append", index=False)
        rs = pd.read_csv(src / "tables" / "review_samples.csv").drop(columns=["review_id"])
        con.execute("DELETE FROM review_samples WHERE doc_id IS NOT NULL")   # 뉴스 검수 행만 (공시 청크 행은 그대로)
        rs.to_sql("review_samples", con, if_exists="append", index=False)
        con.commit()
        print(f"  llm_results {len(llm):,} · news_company {len(nc):,} · review_samples(뉴스) {len(rs):,}행 넣음")
    # 3) 파일
    for f in (src / "files").glob("*.csv"):
        shutil.copy2(f, PROCESSED / f.name)
        print(f"  {f.name} → data/processed/")
    print("완료: streamlit run dashboard/app.py 로 모든 화면 확인")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["export", "import"])
    ap.add_argument("src", nargs="?", help="import 할 폴더 (data/handoff/A_to_B_날짜)")
    a = ap.parse_args()
    export() if a.action == "export" else import_(a.src)
