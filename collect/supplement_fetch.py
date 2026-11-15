"""
[A·D2] 앵커 이벤트 보강 기사 본문 수집 (언론사 원문 페이지)

supplement_search.py 가 만든 후보 목록(candidates.csv)의 원문 URL 에서 기사 전문을 추출해
제공 뉴스와 같은 컬럼 형식의 CSV 로 저장한다.

수집 규칙
- 사이트마다 robots.txt 를 먼저 확인하고, 막힌 URL 은 요청하지 않는다 (status = robots_disallow).
- 같은 사이트에는 REQUEST_DELAY_SEC 간격을 둔다.
- 받은 HTML 은 data/raw/news_supplement/html/ 에 저장하고, 다시 실행하면 저장본을 재사용한다.
- 본문 추출은 trafilatura 를 쓰고, 성공/실패를 fetch_status 로 남긴다.

수집 대상 선정 (기본값, --all 이면 전부)
- 제목에 기업명이 있는 기사 (in_title = 1)
- 이벤트 검색어(scope = event) 결과는 전부, 넓은 검색어(scope = broad) 결과는 제목에 AI 용어가 있는 것만
- 검색어가 달라도 같은 제목이면 한 번만 (가장 이른 기사)
- 제공 뉴스에 이미 있는 제목은 제외 (본문이 이미 있음)

실행: python collect/supplement_fetch.py [후보 CSV 경로] [--all]
      기본 입력: data/raw/news_supplement/naver/candidates.csv
입력 컬럼: corp_name, query, title, pub_date, url (선택: media, in_title, scope, title_norm)
결과: data/raw/news_supplement/supplement_news.csv
"""

import argparse
import hashlib
import re
import sys
import time
import unicodedata
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import requests
import trafilatura

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402
from collect.supplement_search import normalize_title  # noqa: E402

HTML_DIR = settings.SUPPLEMENT_DIR / "html"
DEFAULT_INPUT = settings.SUPPLEMENT_DIR / "naver" / "candidates.csv"
OUTPUT_CSV = settings.SUPPLEMENT_DIR / "supplement_news.csv"
MIN_BODY_LEN = 200  # 이보다 짧으면 본문 추출 실패로 본다

_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
_last_request: dict[str, float] = {}


def robots_allows(url: str) -> bool:
    """사이트별 robots.txt 를 한 번만 읽어서 캐시한다. 읽기 실패 시 보수적으로 막힌 것으로 본다."""
    parts = urlparse(url)
    base = f"{parts.scheme}://{parts.netloc}"
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            res = requests.get(
                f"{base}/robots.txt",
                headers={"User-Agent": settings.USER_AGENT},
                timeout=settings.REQUEST_TIMEOUT_SEC,
            )
            if res.status_code in (401, 403):
                rp.disallow_all = True
            elif res.status_code >= 400:
                rp.allow_all = True  # robots.txt 가 없으면 제한 없음
            else:
                rp.parse(res.text.splitlines())
            _robots[base] = rp
        except requests.RequestException:
            _robots[base] = None
    rp = _robots[base]
    return rp is not None and rp.can_fetch(settings.USER_AGENT, url)


def polite_get(url: str) -> requests.Response:
    host = urlparse(url).netloc
    wait = settings.REQUEST_DELAY_SEC - (time.time() - _last_request.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    _last_request[host] = time.time()
    return requests.get(url, headers={"User-Agent": settings.USER_AGENT}, timeout=settings.REQUEST_TIMEOUT_SEC)


def fetch_html(url: str) -> tuple[str | None, str]:
    """(html, status). 저장본이 있으면 재사용한다."""
    path = HTML_DIR / f"{hashlib.sha1(url.encode()).hexdigest()}.html"
    if path.exists():
        return path.read_text(encoding="utf-8"), "cached"
    if not robots_allows(url):
        return None, "robots_disallow"
    try:
        res = polite_get(url)
        res.raise_for_status()
    except requests.RequestException as e:
        return None, f"http_error:{getattr(e.response, 'status_code', type(e).__name__)}"
    res.encoding = res.apparent_encoding if res.encoding in (None, "ISO-8859-1") else res.encoding
    path.write_text(res.text, encoding="utf-8")
    return res.text, "fetched"


def provided_title_norms() -> set[str]:
    """제공 뉴스(묶음 파일)의 정규화 제목. macOS 파일명 NFD 대응"""
    for f in settings.RAW_NEWS_DIR.glob("01_*.csv"):
        if "묶음" in unicodedata.normalize("NFC", f.name):
            return set(pd.read_csv(f, usecols=["제목"])["제목"].map(normalize_title))
    return set()


def select_targets(cand: pd.DataFrame) -> pd.DataFrame:
    """전문 수집 대상 선정 규칙 (모듈 설명 참고). 단계별 건수를 출력한다."""
    print(f"후보 전체 {len(cand)}")
    cand = cand[cand["in_title"] == 1]
    print(f"  제목에 기업명 → {len(cand)}")
    is_ai = cand["title"].str.contains(settings.AI_TERM_PATTERN, regex=True)
    cand = cand[(cand["scope"] == "event") | is_ai]
    print(f"  이벤트 검색어 전부 + 넓은 검색어 중 제목에 AI → {len(cand)}")
    cand = cand.sort_values("pub_date").drop_duplicates("title_norm", keep="first")
    print(f"  검색어 간 같은 제목 제거 → {len(cand)}")
    cand = cand[~cand["title_norm"].isin(provided_title_norms())]
    print(f"  제공 뉴스에 이미 있는 제목 제외 → {len(cand)}")
    return cand


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", nargs="?", default=str(DEFAULT_INPUT))
    parser.add_argument("--all", action="store_true", help="선정 규칙 없이 입력 전부 수집")
    args = parser.parse_args()

    HTML_DIR.mkdir(parents=True, exist_ok=True)
    cand = pd.read_csv(args.input)
    if not args.all:
        cand = select_targets(cand)

    rows = []
    for c in cand.itertuples():
        html_text, status = fetch_html(c.url)
        body, sitename = None, getattr(c, "media", None)
        if html_text:
            body = trafilatura.extract(html_text, include_comments=False, include_tables=False)
            meta = trafilatura.extract_metadata(html_text)
            if not isinstance(sitename, str) and meta is not None:
                sitename = meta.sitename
            # 너무 짧거나, 본문 대신 HTML 코드가 추출된 경우는 실패로 본다
            if not body or len(body) < MIN_BODY_LEN or re.search(r"<!--|</?[a-zA-Z][^>]{0,50}>", body):
                status = "extract_failed"
            elif status in ("fetched", "cached"):
                status = "ok"
        rows.append(
            {
                "제목": c.title,
                "본문전문": body if status == "ok" else None,
                "원문URL": c.url,
                "발행일": pd.Timestamp(c.pub_date).strftime("%Y-%m-%dT%H:%M:%S"),
                "매체명": sitename or urlparse(c.url).netloc,
                "문서종류": "news",
                "수집키워드": c.query,
                "기업명": c.corp_name,
                "fetch_status": status,
            }
        )
        print(f"[{status}] {c.title[:40]}")

    out = pd.DataFrame(rows)
    out.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    print("\n상태별 건수:", out["fetch_status"].value_counts().to_dict())
    print(f"저장: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
