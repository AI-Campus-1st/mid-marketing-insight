"""
[A·D2] 제공 뉴스 CSV → documents 적재 (news_origin = 'provided')

입력: data/raw/news/ 의 묶음 파일 (01_마케팅_묶음_마케팅_6469건_*.csv)
      컬럼: 제목, 본문전문, 원문URL, 발행일, 매체명, 문서종류, 수집키워드
출력: data/processed/project.db 의 documents (DB 가 없으면 schema.sql 로 만든다)

처리 규칙
- doc_id = 'N' + 5자리 일련번호. 발행일·원문URL·제목 순으로 정렬한 순서라 같은 입력이면 항상 같은 번호다.
- 문서종류는 전부 'news' 라 저장하지 않는다.
- category: 가이드 2-3절의 키워드 → 카테고리 표 (수집키워드가 여럿이면 카테고리도 '; ' 로 합침)
- 중복 (가장 이른 기사만 is_duplicate = 0, 원문URL 은 기준으로 쓰지 않음 — 서로 다른 기사에 같은 URL 사례 있음):
  ① 같은 정규화 제목 + 같은 본문 해시  ② 같은 매체 + 같은 정규화 제목 + 24시간 이내 (수정·재송고)
  ② 는 처음 7일 → 사람 검수(규칙 ② 12쌍, X 4 = 모두 다른 날 다시 실은 기사) 반영해 24시간 (decisions.md)
  다른 매체가 같은 보도자료를 실은 기사는 별개 기사로 센다 (decisions.md)
- 본문 결측: 행은 적재하고 body = NULL. 기사량 집계(Q1)에는 포함, 본문이 필요한 LLM 분석(Q2)에서는 제외.
- 재실행 가능: doc_id 기준 upsert. 공시 행(source_type = 'disclosure')은 건드리지 않는다.

실행: python collect/load_news.py
"""

import hashlib
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402
from collect.supplement_search import normalize_title  # noqa: E402

# 가이드 2-3절 (마케팅 산업군)
KEYWORD_CATEGORY = {
    "마케팅 규제": "규제 및 정책",
    "AI 마케팅": "기술 및 트렌드",
    "마케팅 트렌드": "기술 및 트렌드",
    "마케팅 파트너십": "시장 및 경쟁",
    "마케팅 인프라": "공급망 및 인프라",
}


def find_bundle_csv() -> Path:
    """묶음 파일 찾기 (macOS 파일명은 NFD 라 NFC 로 바꿔 비교)"""
    for f in settings.RAW_NEWS_DIR.glob("01_*.csv"):
        if "묶음" in unicodedata.normalize("NFC", f.name):
            return f
    raise FileNotFoundError(f"{settings.RAW_NEWS_DIR} 에 묶음 CSV 가 없습니다")


def to_category(keyword: str) -> str | None:
    cats = []
    for k in str(keyword).split(";"):
        c = KEYWORD_CATEGORY.get(k.strip())
        if c and c not in cats:
            cats.append(c)
    return "; ".join(cats) or None


SAME_MEDIA_WINDOW = pd.Timedelta(hours=24)   # 규칙 ② 기간


def body_hash(body) -> str | None:
    if not isinstance(body, str) or not body.strip():
        return None
    return hashlib.sha1(re.sub(r"\s+", " ", body).strip().encode("utf-8")).hexdigest()


def build_rows(raw: pd.DataFrame) -> pd.DataFrame:
    df = pd.DataFrame({
        "title": raw["제목"].astype(str).str.strip(),
        "body": raw["본문전문"].where(raw["본문전문"].notna(), None),
        "published_date": pd.to_datetime(raw["발행일"]).dt.strftime("%Y-%m-%d %H:%M:%S"),
        "url": raw["원문URL"],
        "media": raw["매체명"],
        "keyword": raw["수집키워드"],
    })
    df["category"] = df["keyword"].map(to_category)
    df["title_norm"] = df["title"].map(normalize_title)
    df["body_hash"] = df["body"].map(body_hash)

    # 정렬 순서로 일련번호 → 같은 입력이면 같은 doc_id
    df = df.sort_values(["published_date", "url", "title"], kind="stable").reset_index(drop=True)
    df.insert(0, "doc_id", [f"N{i:05d}" for i in range(1, len(df) + 1)])

    # 중복 규칙 (가장 이른 기사만 남김)
    #  ① 같은 정규화 제목 + 같은 본문 → 같은 기사가 두 번 수집된 것
    #  ② 같은 매체 + 같은 정규화 제목 + 24시간 이내 → 같은 매체의 수정·재송고 기사 (본문이 조금 다름)
    #     다른 날 다시 실은 기사는 별도 노출로 센다 (사람 검수: 39시간·5일·7일 재게재를 '다른 기사'로 판정)
    #  다른 매체가 같은 보도자료를 실은 기사는 별개 기사로 본다 (기사량에 포함)
    df["is_duplicate"] = 0
    has_body = df["body_hash"].notna()
    dup1 = df[has_body].duplicated(subset=["title_norm", "body_hash"], keep="first")
    df.loc[dup1[dup1].index, "is_duplicate"] = 1

    ts = pd.to_datetime(df["published_date"])
    for _, g in df[df["is_duplicate"] == 0].groupby(["media", "title_norm"]):
        if len(g) < 2:
            continue
        first = ts[g.index[0]]  # 이미 날짜순 정렬
        later = [i for i in g.index[1:] if ts[i] - first <= SAME_MEDIA_WINDOW]
        df.loc[later, "is_duplicate"] = 1
    return df


def load(df: pd.DataFrame) -> None:
    settings.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(settings.DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript((settings.ROOT / "schema.sql").read_text(encoding="utf-8"))
    cols = ["doc_id", "title", "body", "published_date", "url", "media", "keyword",
            "category", "title_norm", "body_hash", "is_duplicate"]
    con.executemany(
        f"""INSERT INTO documents (source_type, news_origin, {", ".join(cols)})
            VALUES ('news', 'provided', {", ".join("?" * len(cols))})
            ON CONFLICT(doc_id) DO UPDATE SET
            {", ".join(f"{c} = excluded.{c}" for c in cols[1:])}""",
        df[cols].astype(object).where(df[cols].notna(), None).values.tolist(),
    )
    con.commit()
    n = con.execute("SELECT COUNT(*), SUM(is_duplicate), SUM(body IS NULL) FROM documents "
                    "WHERE source_type = 'news' AND news_origin = 'provided'").fetchone()
    con.close()
    print(f"[DB] {settings.DB_PATH.name}: 제공 뉴스 {n[0]}건 (중복 표시 {n[1]}, 본문 없음 {n[2]})")


def main() -> None:
    path = find_bundle_csv()
    raw = pd.read_csv(path)
    print(f"입력: {path.name} ({len(raw)}건)")
    df = build_rows(raw)

    valid = df[df["is_duplicate"] == 0]
    print(f"중복 {int(df['is_duplicate'].sum())}건 → 집계 대상 {len(valid)}건, "
          f"본문 없음 {int(df['body'].isna().sum())}건, "
          f"기간 {df['published_date'].min()} ~ {df['published_date'].max()}")
    print("카테고리별 (집계 대상):", valid["category"].value_counts().to_dict())
    load(df)


if __name__ == "__main__":
    main()
