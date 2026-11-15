"""
[A·D2] 기업 뉴스룸(보도자료) 수집 — 회사가 직접 발표한 "주장"의 1차 출처

앵커 이벤트의 공식 발표일(official_date)과 회사가 주장하는 AI 수준을 확인하는 용도.
뉴스룸은 회사의 말이므로 '실행의 증거'로 쓰지 않는다. 실행 판정은 공시(DART)에서만 한다.

사이트별 수집 방식 (2026-11-13 구조 기준, 바뀌면 파서 수정)
- 제일기획  : 뉴스 페이지가 쓰는 JSON 목록 /kr/news/newsList?page=N  (Insight 칼럼은 magazine.cheil.com 링크)
- 이노션    : sitemap 의 /ww-ko/about/press/{id} 상세 페이지. 본문은 스크립트로 그려져서 og:description(리드 문단)만 사용
- 인크로스  : /ko/promotion/news.asp?page=N 목록 HTML → ?mode=view&idx=N 상세
- 나스미디어: WordPress REST API /wp-json/wp/v2/press (본문 포함)
- 플레이디·에코마케팅: 자체 보도자료 뉴스룸 없음 (플레이디 PRESS 메뉴는 네이버 뉴스 링크)

수집 규칙: robots.txt 확인, 사이트별 1초 간격, 받은 HTML 은 data/raw/newsroom/html/ 에 저장·재사용
저작권: 각 사이트 Copyright 표시 있음 → 본문 전문은 저장소·대시보드에 노출하지 않는다 (제목·날짜·링크·자체 요약만)

실행: python collect/newsroom_collect.py
결과: data/raw/newsroom/newsroom_news.csv (제공 뉴스와 같은 컬럼 + 기업명, doc_kind, body_type, fetch_status)
"""

import hashlib
import html
import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import trafilatura

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402
from collect.supplement_fetch import polite_get, robots_allows  # noqa: E402

OUT_DIR = settings.RAW_DIR / "newsroom"
HTML_DIR = OUT_DIR / "html"
START = date.fromisoformat(settings.PERIOD_START)
END = date.fromisoformat(settings.PERIOD_END)
MAX_PAGES = 200  # 무한 루프 방지


def get(url: str, params: dict | None = None):
    """robots.txt 확인 후 요청. 막혀 있으면 None"""
    full = url if not params else f"{url}?{'&'.join(f'{k}={v}' for k, v in params.items())}"
    if not robots_allows(full):
        print(f"  [robots_disallow] {full}")
        return None
    res = polite_get(full)
    res.raise_for_status()
    return res


def cached_html(url: str) -> str | None:
    path = HTML_DIR / f"{hashlib.sha1(url.encode()).hexdigest()}.html"
    if path.exists():
        return path.read_text(encoding="utf-8")
    res = get(url)
    if res is None:
        return None
    res.encoding = res.apparent_encoding if res.encoding in (None, "ISO-8859-1") else res.encoding
    path.write_text(res.text, encoding="utf-8")
    return res.text


def row(corp, d, title, url, body, doc_kind, body_type, status):
    return {
        "제목": html.unescape(title).strip(),
        "본문전문": body,
        "원문URL": url,
        "발행일": d.isoformat() if d else None,
        "매체명": f"{corp} 뉴스룸",
        "문서종류": doc_kind,          # press_release / insight
        "수집키워드": None,
        "기업명": corp,
        "body_type": body_type,        # full / lead / none
        "fetch_status": status,
    }


def in_period(d: date | None) -> bool:
    return d is not None and START <= d <= END


# ── 사이트별 수집기 ──────────────────────────────────────

def collect_cheil() -> list[dict]:
    rows = []
    for page in range(1, MAX_PAGES):
        items = get("https://www.cheil.com/kr/news/newsList", {"page": page}).json().get("list") or []
        if not items:
            break
        for x in items:
            d = date(int(x["ns_year"]), int(x["ns_month"]), int(x["na_day"]))
            if not in_period(d):
                continue
            url = x["ns_link"] if x.get("ns_type") == "link" and x.get("ns_link") else \
                f"https://www.cheil.com/kr/news/newsDetail?nsNo={x['ns_no']}"
            page_html = cached_html(url)
            body = trafilatura.extract(page_html) if page_html else None
            kind = "insight" if x.get("ns_gubun") == "Insight" else "press_release"
            rows.append(row("제일기획", d, x["ns_title"], url, body, kind,
                            "full" if body else "none", "ok" if body else "extract_failed"))
        oldest = min(date(int(x["ns_year"]), int(x["ns_month"]), int(x["na_day"])) for x in items)
        if oldest < START:
            break
    return rows


def collect_innocean() -> list[dict]:
    sitemap = get("https://www.innocean.com/sitemap.xml").text
    ids = sorted({int(i) for i in re.findall(r"ww-ko/about/press/(\d+)</loc>", sitemap)}, reverse=True)
    rows, older = [], 0
    for i in ids:  # 최신 id 부터, 수집 시작일보다 오래된 글이 5개 나오면 중단
        url = f"https://www.innocean.com/ww-ko/about/press/{i}"
        page_html = cached_html(url) or ""
        title = re.search(r'og:title" content="([^"]+)"', page_html)
        lead = re.search(r'og:description" content="([^"]+)"', page_html)
        title = title.group(1) if title else ""
        # 날짜: 본문 영역에서 제목이 다시 나온 뒤 첫 날짜 (목록 표의 날짜와 구분)
        d = None
        for m in list(re.finditer(re.escape(title[:12]), page_html))[1:] if title else []:
            dm = re.search(r"(20\d\d)[-.](\d\d)[-.](\d\d)", page_html[m.start(): m.start() + 3000])
            if dm:
                d = date(*map(int, dm.groups()))
                break
        if d and d < START:
            older += 1
            if older >= 5:
                break
            continue
        if in_period(d):
            body = html.unescape(lead.group(1)) if lead else None
            rows.append(row("이노션", d, title, url, body, "press_release",
                            "lead" if body else "none", "ok" if body else "extract_failed"))
    return rows


def collect_incross() -> list[dict]:
    base = "https://incross.com/ko/promotion/news.asp"
    rows = []
    for page in range(1, MAX_PAGES):
        list_html = get(base, {"page": page}).text
        items = re.findall(
            r"<a href='(\?mode=view[^']*idx=\d+)'[^>]*>(.*?)</a>", list_html, flags=re.S
        )
        found = []
        for href, block in items:
            dm = re.search(r'class="day">(20\d\d-\d\d-\d\d)<', block)
            tm = re.search(r"<dt[^>]*>(.*?)</dt>", block, flags=re.S)
            if not dm:
                continue
            d = date.fromisoformat(dm.group(1))
            found.append(d)
            if not in_period(d):
                continue
            title = re.sub(r"<[^>]+>|\s+", " ", tm.group(1)).strip() if tm else ""
            url = base + href.replace("&amp;", "&")
            page_html = cached_html(url)
            body = trafilatura.extract(page_html) if page_html else None
            rows.append(row("인크로스", d, title, url, body, "press_release",
                            "full" if body else "none", "ok" if body else "extract_failed"))
        if not found or min(found) < START:
            break
    # 같은 글이 슬라이더와 목록에 중복으로 나올 수 있어 URL 기준 제거
    return list({r["원문URL"]: r for r in rows}.values())


def collect_nasmedia() -> list[dict]:
    rows = []
    for page in range(1, MAX_PAGES):
        res = get("https://www.nasmedia.co.kr/wp-json/wp/v2/press",
                  {"per_page": 100, "page": page, "after": f"{settings.PERIOD_START}T00:00:00"})
        items = res.json() if res is not None and res.ok else []
        for x in items:
            d = date.fromisoformat(x["date"][:10])
            if not in_period(d):
                continue
            body = trafilatura.extract(f"<html><body>{x['content']['rendered']}</body></html>")
            title = re.sub(r"<[^>]+>", "", x["title"]["rendered"])
            rows.append(row("나스미디어", d, title, x["link"], body, "press_release",
                            "full" if body else "none", "ok" if body else "extract_failed"))
        if len(items) < 100:
            break
    return rows


COLLECTORS = {
    "제일기획": collect_cheil,
    "이노션": collect_innocean,
    "인크로스": collect_incross,
    "나스미디어": collect_nasmedia,
}


def main() -> None:
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    all_rows = []
    for corp, fn in COLLECTORS.items():
        try:
            rows = fn()
        except Exception as e:  # 한 사이트가 실패해도 나머지는 진행
            print(f"[{corp}] 실패: {type(e).__name__}: {e}")
            rows = []
        all_rows.extend(rows)
        print(f"[{corp}] {len(rows)}건")

    df = pd.DataFrame(all_rows)
    out = OUT_DIR / "newsroom_news.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print("\n기업 × 상태:\n", pd.crosstab(df["기업명"], df["fetch_status"], margins=True))
    print(f"저장: {out}")


if __name__ == "__main__":
    main()
