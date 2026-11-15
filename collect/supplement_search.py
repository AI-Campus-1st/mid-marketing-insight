"""
[A·D2] 앵커 이벤트 보강 기사 검색 (네이버 검색 API)

config/anchor_queries.csv 의 검색어로 네이버 뉴스를 검색해서
수집 기간 안의 후보 기사 목록을 만든다. 본문은 supplement_fetch.py 가 가져온다.

- 원본 응답은 검색어별 JSON 으로 그대로 저장한다 (재현·검증용).
- 같은 검색어 안에서는 제목 정규화(title_norm)로 중복을 제거한다.
- 제목에 기업명이 있는지(in_title)를 표시해서 잡음 기사를 걸러낼 수 있게 한다.

실행: python collect/supplement_search.py [--refresh]
      이미 받은 검색어(raw/*.json)는 저장본을 쓰고, 새 검색어만 API 를 호출한다.
필요: .env 에 NAVER_CLIENT_ID, NAVER_CLIENT_SECRET (선택: NAVER_SEARCH_URL)
결과: data/raw/news_supplement/naver/raw/*.json, data/raw/news_supplement/naver/candidates.csv

참고: 네이버 검색 API 는 2026-07-31부터 개발자센터 신규 신청이 막혀서 NAVER API HUB(NCP)에서 받는다.
      API HUB 는 호출 주소와 인증 헤더가 개발자센터와 다르고, 개발자센터 키로는 호출할 수 없다.
      (출처: https://api.ncloud-docs.com/docs/naver-api-hub-search-news)
      요청 파라미터(display ≤ 100, start ≤ 1000, sort)와 응답 필드는 개발자센터와 같다. 하루 호출 한도 25,000회.
"""

import argparse
import html
import json
import os
import re
import sys
import time
from email.utils import parsedate_to_datetime
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402

SOURCE = "naver"
OUT_DIR = settings.SUPPLEMENT_DIR / SOURCE
RAW_DIR = OUT_DIR / "raw"
QUERIES_CSV = settings.CONFIG_DIR / "anchor_queries.csv"

DEFAULT_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"
PAGE_SIZE = 100      # display 최대값
MAX_START = 1000     # start 최대값


def auth_headers() -> dict:
    cid, secret = os.getenv("NAVER_CLIENT_ID"), os.getenv("NAVER_CLIENT_SECRET")
    if not cid or not secret:
        raise SystemExit(".env 에 NAVER_CLIENT_ID, NAVER_CLIENT_SECRET 를 넣어주세요")
    # NAVER API HUB 인증 헤더 (개발자센터의 X-Naver-Client-Id/Secret 과 다름)
    return {"X-NCP-APIGW-API-KEY-ID": cid, "X-NCP-APIGW-API-KEY": secret}


def clean(text: str) -> str:
    """응답의 <b> 태그와 HTML 엔티티 제거"""
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def normalize_title(title: str) -> str:
    """중복 판단용 제목: 괄호 머리말·특수문자·공백 제거"""
    title = re.sub(r"^\s*[\[\(【].*?[\]\)】]\s*", "", title)
    return re.sub(r"[^0-9A-Za-z가-힣]", "", title).lower()


def search(query: str, url: str, headers: dict, start_date: pd.Timestamp) -> list[dict]:
    """최신순으로 페이지를 넘기고, 수집 시작일보다 오래된 기사가 나오면 멈춘다."""
    items, start = [], 1
    while start <= MAX_START:
        res = requests.get(
            url,
            headers=headers,
            params={"query": query, "display": PAGE_SIZE, "start": start, "sort": "date"},
            timeout=settings.REQUEST_TIMEOUT_SEC,
        )
        res.raise_for_status()
        batch = res.json().get("items", [])
        items.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        oldest = parsedate_to_datetime(batch[-1]["pubDate"]).replace(tzinfo=None)
        if oldest < start_date:
            break
        start += PAGE_SIZE
        time.sleep(settings.REQUEST_DELAY_SEC)
    return items


def to_rows(items: list[dict], corp: str, query: str) -> list[dict]:
    rows = []
    for it in items:
        title = clean(it.get("title"))
        rows.append(
            {
                "corp_name": corp,
                "query": query,
                "title": title,
                "description": clean(it.get("description")),
                "pub_date": parsedate_to_datetime(it["pubDate"]).replace(tzinfo=None),
                "url": it.get("originallink") or it.get("link"),  # 원문(언론사) 링크 우선
                "naver_link": it.get("link"),
                "in_title": int(corp in title),
                "title_norm": normalize_title(title),
                "source": SOURCE,
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="이미 받은 검색어도 API를 다시 호출")
    args = parser.parse_args()

    load_dotenv(settings.ROOT / ".env")
    url = os.getenv("NAVER_SEARCH_URL", DEFAULT_URL)
    start_date = pd.Timestamp(settings.PERIOD_START)
    end_date = pd.Timestamp(settings.PERIOD_END) + pd.Timedelta(days=1)

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    queries = pd.read_csv(QUERIES_CSV)
    all_rows, headers = [], None

    for q in queries.itertuples():
        raw_path = RAW_DIR / f"{q.query.replace(' ', '_')}.json"
        if raw_path.exists() and not args.refresh:
            items, origin = json.loads(raw_path.read_text(encoding="utf-8")), "저장본"
        else:
            headers = headers or auth_headers()
            items, origin = search(q.query, url, headers, start_date), "API"
            raw_path.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
            time.sleep(settings.REQUEST_DELAY_SEC)

        rows = [r for r in to_rows(items, q.corp_name, q.query) if start_date <= r["pub_date"] < end_date]
        oldest = min((r["pub_date"] for r in rows), default=None)
        # 1,000건 한도에 걸려 수집 시작일까지 못 내려간 검색어 표시 (기간 편향 → 건수 비교에 쓰지 않음)
        capped = int(len(items) >= MAX_START and (oldest is None or oldest > start_date + pd.Timedelta(days=30)))
        for r in rows:
            r["scope"], r["capped"] = q.scope, capped
        all_rows.extend(rows)
        print(
            f"[{q.corp_name}] {q.query} ({origin}): 응답 {len(items)} / 기간 내 {len(rows)} / "
            f"제목에 기업명 {sum(r['in_title'] for r in rows)}"
            + (f" / ⚠ 한도 도달, {oldest:%Y-%m-%d} 이후만" if capped else "")
        )

    if not all_rows:
        raise SystemExit("수집된 기사가 없습니다")

    df = pd.DataFrame(all_rows).sort_values("pub_date")
    before = len(df)
    df = df.drop_duplicates(subset=["query", "title_norm"], keep="first")  # 같은 검색어 안의 재배포 기사
    df.to_csv(OUT_DIR / "candidates.csv", index=False, encoding="utf-8-sig")

    print(f"\n후보 {len(df)}건 (중복 {before - len(df)}건 제거), 제목에 기업명 {df['in_title'].sum()}건")
    print(f"저장: {OUT_DIR / 'candidates.csv'}")


if __name__ == "__main__":
    main()
