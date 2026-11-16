"""
[B·D2] DART 공시 목록(list.json) 수집

하는 일
    1. config/companies.csv 의 기업별로 수집 기간(settings.PERIOD_START ~ PERIOD_END)의 공시 목록을 받는다.
    2. API 응답 원본(JSON)을 data/raw/dart/list/ 에 그대로 저장한다. (재실행 시 저장된 원본 재사용)
    3. 전체 목록을 data/raw/dart/disclosure_list.csv 로 합친다.
    4. DB(project.db)에 적재한다.
       - companies: config/companies.csv (documents.corp_code 외래키 때문에 먼저 넣는다)
       - documents: 공시 메타데이터 (doc_id = 'D' + rcept_no, source_type = 'disclosure')
         본문(body)은 비워 두고 collect/dart_document.py 가 채운다.
       DB 파일이 없으면 schema.sql 로 만든다. (테이블은 IF NOT EXISTS 라 기존 데이터는 그대로)

왜 공시 유형별로 따로 부르나
    list.json 응답에는 공시 유형(pblntf_ty) 컬럼이 없다.
    유형(A~J)을 하나씩 지정해 조회하면 결과마다 유형을 붙일 수 있다. (schema.sql documents.report_type)

실행
    python collect/dart_collect.py            # 저장된 원본이 있으면 재사용
    python collect/dart_collect.py --refresh  # 원본을 새로 받기

다음 단계
    collect/dart_document.py 가 disclosure_list.csv 의 정기공시(A) 원문을 내려받는다.
"""
import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402

LIST_DIR = settings.RAW_DART_DIR / "list"
OUT_CSV = settings.RAW_DART_DIR / "disclosure_list.csv"

# DART 공시 유형 코드 (OpenDART 개발가이드 공시검색 기준)
PBLNTF_TYPES = {
    "A": "정기공시", "B": "주요사항보고", "C": "발행공시", "D": "지분공시", "E": "기타공시",
    "F": "외부감사관련", "G": "펀드공시", "H": "자산유동화", "I": "거래소공시", "J": "공정위공시",
}
PAGE_COUNT = 100  # 한 페이지 최대 건수 (API 최대값)

BGN_DE = settings.PERIOD_START.replace("-", "")  # API 는 YYYYMMDD 형식
END_DE = settings.PERIOD_END.replace("-", "")


def fetch_page(corp_code: str, ty: str, page: int, refresh: bool) -> dict:
    """
    한 기업·한 유형·한 페이지의 공시 목록을 받는다. 저장된 원본이 있으면 재사용한다.

    Returns:
        list.json 응답(dict). status '000' = 정상, '013' = 조회 결과 없음
    """
    path = LIST_DIR / f"{corp_code}_{ty}_p{page}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))

    resp = requests.get(f"{settings.DART_BASE_URL}/list.json", params={
        "crtfc_key": settings.DART_API_KEY, "corp_code": corp_code,
        "bgn_de": BGN_DE, "end_de": END_DE, "pblntf_ty": ty,
        "page_no": page, "page_count": PAGE_COUNT,
    }, timeout=settings.REQUEST_TIMEOUT_SEC)
    resp.raise_for_status()
    data = resp.json()
    time.sleep(settings.REQUEST_DELAY_SEC)

    if data.get("status") not in ("000", "013"):
        # 인증키 오류, 호출 한도 초과 등은 저장하지 않고 멈춘다. (잘못된 원본이 재사용되지 않게)
        sys.exit(f"[오류] {corp_code} {ty} p{page}: {data.get('status')} {data.get('message')}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def collect_company(corp_code: str, refresh: bool) -> list[dict]:
    """한 기업의 모든 유형·페이지 공시를 모아 행 목록으로 돌려준다."""
    rows = []
    for ty, ty_name in PBLNTF_TYPES.items():
        page = 1
        while True:
            data = fetch_page(corp_code, ty, page, refresh)
            for item in data.get("list", []):
                rows.append({**item, "pblntf_ty": ty, "pblntf_ty_nm": ty_name})
            if page >= int(data.get("total_page", 1) or 1):
                break
            page += 1
    return rows


def load_to_db(df: pd.DataFrame) -> None:
    """
    companies 와 공시 메타데이터를 DB 에 넣는다. 재실행해도 중복되지 않는다.
    이미 있는 공시는 메타데이터만 갱신하고, dart_document.py 가 채운 body·raw_path 는 건드리지 않는다.
    """
    settings.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(settings.DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript((settings.ROOT / "schema.sql").read_text(encoding="utf-8"))

    companies = pd.read_csv(settings.COMPANIES_CSV, dtype=str)
    con.executemany(
        """INSERT INTO companies (corp_code, corp_name, stock_code, parent_group, segment, is_control)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(corp_code) DO UPDATE SET
             corp_name = excluded.corp_name, stock_code = excluded.stock_code,
             parent_group = excluded.parent_group, segment = excluded.segment,
             is_control = excluded.is_control""",
        companies[["corp_code", "corp_name", "stock_code", "parent_group", "segment", "is_control"]]
        .astype(object).where(companies.notna(), None).values.tolist(),
    )

    rows = [(
        "D" + r.rcept_no,
        r.report_nm.strip(),                                            # 공시명
        f"{r.rcept_dt[:4]}-{r.rcept_dt[4:6]}-{r.rcept_dt[6:]} 00:00:00",  # 접수일 (시각 정보 없음)
        r.dart_url,
        r.corp_code,
        r.rcept_no,
        r.pblntf_ty_nm,                                                 # 공시 유형 (정기공시 등)
    ) for r in df.itertuples()]
    con.executemany(
        """INSERT INTO documents (doc_id, source_type, title, published_date, url,
                                  corp_code, rcept_no, report_type)
           VALUES (?, 'disclosure', ?, ?, ?, ?, ?, ?)
           ON CONFLICT(doc_id) DO UPDATE SET
             title = excluded.title, published_date = excluded.published_date, url = excluded.url,
             corp_code = excluded.corp_code, rcept_no = excluded.rcept_no,
             report_type = excluded.report_type""",
        rows,
    )
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM documents WHERE source_type = 'disclosure'").fetchone()[0]
    con.close()
    print(f"[DB] {settings.DB_PATH.name}: companies {len(companies)}개사, 공시 {n}건")


def main():
    parser = argparse.ArgumentParser(description="DART 공시 목록 수집")
    parser.add_argument("--refresh", action="store_true", help="저장된 원본을 무시하고 새로 받기")
    args = parser.parse_args()

    if not settings.DART_API_KEY:
        sys.exit("[오류] .env 에 DART_API_KEY 가 없습니다.")

    companies = pd.read_csv(settings.COMPANIES_CSV, dtype=str)
    print(f"[정보] 수집 기간 {BGN_DE} ~ {END_DE}, 대상 {len(companies)}개사")

    rows = []
    for _, c in companies.iterrows():
        got = collect_company(c["corp_code"], args.refresh)
        print(f"  {c['corp_name']}: {len(got)}건")
        rows += got

    df = pd.DataFrame(rows)
    # 정정 공시 표시: 보고서명 앞에 [기재정정], [첨부정정] 등이 붙는다.
    df["is_correction"] = df["report_nm"].str.startswith("[").astype(int)
    df["dart_url"] = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + df["rcept_no"]
    df = df.drop_duplicates("rcept_no").sort_values(["corp_code", "rcept_dt", "rcept_no"])
    df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    print(f"[저장] {OUT_CSV} ({len(df)}건)")
    print(df.groupby(["corp_name", "pblntf_ty_nm"]).size().unstack(fill_value=0).to_string())

    load_to_db(df)


if __name__ == "__main__":
    main()
