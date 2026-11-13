"""
[A·D3] 앵커 이벤트 검토 시트 만들기 (규칙 기반, AI 미사용)

여러 출처의 기업 기사를 '사건 묶음(cluster)'으로 묶어, 사람이 묶음마다 대표 기사 하나만 읽고
이벤트 여부를 판정할 수 있는 CSV 를 만든다.

왜 AI 를 쓰지 않나
- 네이버 검색 API 약관(NAVER API HUB, 2026-09-20 시행)은 검색 결과와 파생물을 AI 에 입력·학습·평가에
  쓰는 것을 금지한다. 네이버 보강 기사가 섞인 이 단계는 규칙 + 사람 판정으로만 진행한다.
- 같은 약관상 검색 결과 저장은 제한적(이력 조회 목적 최대 21일)이므로, 판정이 끝나면 --export 로
  사람이 입력한 값과 1차 출처만 남기고 네이버 원본은 삭제한다. (수집일 2026-11-13 → 기한 2026-12-04 무렵)

입력 (있는 것만 사용)
- provided : 제공 뉴스 중 제목에 기업명이 있는 기사
- naver    : 네이버 검색 후보 (supplement_fetch.py 와 같은 선정 규칙)
- web      : 웹 검색으로 직접 확인한 앵커 기사 (anchor_supplement.csv)
- newsroom : 기업 뉴스룸 보도자료 (newsroom_collect.py 결과)

묶는 규칙: 같은 기업 + 묶음 첫 기사로부터 7일 이내 + 제목 문자 3-gram 자카드 유사도 ≥ 0.25

실행
  python analysis/event_review_sheet.py            # 시트 생성 (이미 입력한 판정은 유지)
  python analysis/event_review_sheet.py --export   # 판정 Y 인 행만 확정 이벤트로 내보내기
결과
  data/raw/news_supplement/review/event_review_sheet.csv   ← 여기에 직접 입력 (네이버 원본과 함께 삭제 대상)
  data/processed/anchor_events_confirmed.csv               ← --export 결과 (네이버 데이터 미포함, 보관)
"""

import argparse
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402
from collect.supplement_search import normalize_title  # noqa: E402

CORPS = ["제일기획", "이노션", "플레이디", "인크로스", "나스미디어", "에코마케팅"]
SHEET = settings.SUPPLEMENT_DIR / "review" / "event_review_sheet.csv"
MEMBERS = settings.SUPPLEMENT_DIR / "review" / "cluster_members.csv"  # 묶음별 기사 목록 (event_classify.py 입력)
CONFIRMED = settings.DATA_DIR / "processed" / "anchor_events_confirmed.csv"

WINDOW_DAYS = 7
SIM_THRESHOLD = 0.25

# 사람이 채우는 칸 (재실행해도 유지)
HUMAN_COLS = ["is_event", "event_type", "event_name", "official_date", "ai_level_claimed",
              "primary_source_url", "reviewer", "note",
              "judged_by", "llm_reason"]  # judged_by: human / llm (event_classify.py 가 채움)

# 대표 기사 우선순위: 1차 출처일수록 앞
SOURCE_RANK = {"newsroom": 0, "web": 1, "provided": 2, "naver": 3}

CATEGORY_RULES = [  # 위에서부터 먼저 걸리는 것
    ("실적", r"영업익|영업이익|매출|실적|분기|순이익|목표가|주가|배당|증권"),
    ("인사·경영", r"대표이사|사장|선임|인사|부사장|CEO|CCO|임원|최대주주|매각"),
    ("순위·평판", r"평판|순위"),
    ("수상", r"수상|대상|금상|은상|동상|그랑프리|어워드"),
    ("이벤트 후보", r"출시|공개|도입|선보|오픈|론칭|런칭|신설|출범|개발|특허|협약|MOU|맞손|파트너|제휴|구축|적용|활용|투자|인수|캠페인"),
    ("행사·전망", r"전망|트렌드|보고서|리포트|세미나|컨퍼런스|포럼|참가|개최|쇼케이스"),
]
TYPE_RULES = [  # 추천값일 뿐, 사람이 고친다
    ("organization", r"신설|출범|조직|TF|팀|유닛|Unit"),
    ("launch", r"출시|공개|오픈|론칭|런칭|선보"),
    ("adoption", r"도입|적용|활용"),
    ("investment", r"투자|인수|지분"),
    ("partnership", r"협약|MOU|맞손|파트너|제휴"),
    ("award", r"수상|대상|금상|은상|동상|그랑프리"),
    ("campaign", r"캠페인"),
]


def first_match(rules, text: str):
    for name, pat in rules:
        if re.search(pat, text):
            return name
    return None


def grams(title: str) -> set[str]:
    s = normalize_title(title)
    return {s[i: i + 3] for i in range(max(len(s) - 2, 1))}


# ── 입력 읽기 ───────────────────────────────────────────

def load_provided() -> pd.DataFrame:
    for f in settings.RAW_NEWS_DIR.glob("01_*.csv"):
        if "묶음" in unicodedata.normalize("NFC", f.name):
            df = pd.read_csv(f, usecols=["제목", "원문URL", "발행일", "매체명"])
            rows = []
            for corp in CORPS:
                hit = df[df["제목"].str.contains(corp, na=False)]
                rows.append(pd.DataFrame({
                    "corp": corp, "title": hit["제목"], "url": hit["원문URL"],
                    "date": pd.to_datetime(hit["발행일"]), "media": hit["매체명"],
                    "source": "provided", "has_body": 1,
                }))
            return pd.concat(rows)
    return pd.DataFrame()


def load_naver() -> pd.DataFrame:
    path = settings.SUPPLEMENT_DIR / "naver" / "candidates.csv"
    if not path.exists():
        return pd.DataFrame()
    c = pd.read_csv(path, parse_dates=["pub_date"])
    c = c[(c["in_title"] == 1)
          & ((c["scope"] == "event") | c["title"].str.contains(settings.AI_TERM_PATTERN, regex=True))]
    ok_urls = set()
    fetched = settings.SUPPLEMENT_DIR / "supplement_news.csv"
    if fetched.exists():
        f = pd.read_csv(fetched)
        ok_urls = set(f.loc[f["fetch_status"] == "ok", "원문URL"])
    return pd.DataFrame({
        "corp": c["corp_name"], "title": c["title"], "url": c["url"], "date": c["pub_date"],
        "media": c["url"].str.extract(r"https?://([^/]+)")[0], "source": "naver",
        "has_body": c["url"].isin(ok_urls).astype(int),
    }).drop_duplicates("url")


def load_web() -> pd.DataFrame:
    path = settings.SUPPLEMENT_DIR / "anchor_supplement.csv"
    if not path.exists():
        return pd.DataFrame()
    a = pd.read_csv(path)
    return pd.DataFrame({
        "corp": a["기업명"], "title": a["제목"], "url": a["원문URL"],
        "date": pd.to_datetime(a["발행일"]), "media": a["매체명"], "source": "web", "has_body": 1,
    })


def load_newsroom() -> pd.DataFrame:
    path = settings.RAW_DIR / "newsroom" / "newsroom_news.csv"
    if not path.exists():
        return pd.DataFrame()
    n = pd.read_csv(path)
    n = n[n["문서종류"] == "press_release"]  # 칼럼(insight)은 이벤트가 아니므로 제외
    return pd.DataFrame({
        "corp": n["기업명"], "title": n["제목"], "url": n["원문URL"],
        "date": pd.to_datetime(n["발행일"]), "media": n["매체명"], "source": "newsroom",
        "has_body": (n["fetch_status"] == "ok").astype(int),
    })


# ── 묶기 ────────────────────────────────────────────────

def cluster(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(subset=["title", "date"]).sort_values("date").reset_index(drop=True)
    df["g"] = df["title"].map(grams)
    clusters: list[dict] = []
    labels = []
    for r in df.itertuples():
        hit = None
        for k in reversed(clusters):  # 최근 묶음부터 비교
            if k["corp"] != r.corp:
                continue
            if (r.date - k["start"]).days > WINDOW_DAYS:
                if (r.date - k["start"]).days > 60:
                    break
                continue
            inter = len(r.g & k["g"])
            if inter / max(len(r.g | k["g"]), 1) >= SIM_THRESHOLD:
                hit = k
                break
        if hit is None:
            hit = {"id": len(clusters), "corp": r.corp, "start": r.date, "g": set(r.g)}
            clusters.append(hit)
        labels.append(hit["id"])
    df["cluster"] = labels
    return df


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for cid, g in df.groupby("cluster"):
        g = g.assign(rank=g["source"].map(SOURCE_RANK))
        # 대표: 1차 출처 우선 → 본문 있는 것 → 이른 것
        rep = g.sort_values(["rank", "has_body", "date"], ascending=[True, False, True]).iloc[0]
        titles = " ".join(g["title"])
        newsroom_dates = g.loc[g["source"] == "newsroom", "date"]
        category = first_match(CATEGORY_RULES, titles) or "기타"
        ai = bool(re.search(settings.AI_TERM_PATTERN, titles))
        priority = 1 if ai and category in ("이벤트 후보", "수상") else 2 if ai or category == "이벤트 후보" else 3
        key = f"{rep['corp']}|{g['date'].min():%Y-%m-%d}|{normalize_title(rep['title'])[:20]}"
        df.loc[g.index, "key"] = key
        rows.append({
            "key": key,
            "priority": priority,
            # 네이버 기사만 있는 묶음 = 약관상 AI 판정 불가 → 사람이 판정
            "only_naver": int(set(g["source"]) == {"naver"}),
            "corp": rep["corp"],
            "first_date": g["date"].min().strftime("%Y-%m-%d %H:%M"),
            "suggested_official_date": (newsroom_dates.min() if len(newsroom_dates) else g["date"].min()).strftime("%Y-%m-%d"),
            "n_articles": len(g),
            "n_media": g["media"].nunique(),
            "sources": ", ".join(f"{s}:{n}" for s, n in g["source"].value_counts().items()),
            "ai_in_title": int(ai),
            "auto_category": category,
            "suggested_type": first_match(TYPE_RULES, titles),
            "rep_source": rep["source"],
            "rep_title": rep["title"],
            "rep_url": rep["url"],
            "rep_has_body": int(rep["has_body"]),
            "other_urls": " ; ".join(g.loc[g["url"] != rep["url"], "url"].head(5)),
        })
    # 사람이 볼 행(네이버만)을 우선순위 안에서 위로
    out = pd.DataFrame(rows).sort_values(["priority", "only_naver", "corp", "first_date"],
                                         ascending=[True, False, True, True])
    for col in HUMAN_COLS:
        out[col] = None
    return out


def keep_previous_input(sheet: pd.DataFrame) -> pd.DataFrame:
    """이미 입력한 판정이 있으면 같은 key 의 행에 옮긴다."""
    if not SHEET.exists():
        return sheet
    prev = pd.read_csv(SHEET, dtype=str).set_index("key").reindex(columns=HUMAN_COLS)
    prev = prev.dropna(how="all")
    sheet = sheet.set_index("key")
    sheet.update(prev)
    lost = set(prev.index) - set(sheet.index)
    if lost:
        print(f"⚠ 이전 판정 {len(lost)}건이 새 묶음과 매칭되지 않았습니다 (묶음 기준이 바뀐 경우). 백업을 확인하세요.")
    return sheet.reset_index()


def export_confirmed() -> None:
    sheet = pd.read_csv(SHEET, dtype=str)
    done = sheet[sheet["is_event"].str.upper().eq("Y")]
    missing = done[done["primary_source_url"].isna()]
    if len(missing):
        print(f"⚠ 1차 출처가 비어 있는 확정 이벤트 {len(missing)}건 — 네이버 링크 대신 뉴스룸·언론사 원문 URL 을 넣어 주세요")
    cols = ["corp", "event_name", "event_type", "official_date", "ai_level_claimed",
            "primary_source_url", "reviewer", "note", "n_articles", "n_media"]
    CONFIRMED.parent.mkdir(parents=True, exist_ok=True)
    done[cols].to_csv(CONFIRMED, index=False, encoding="utf-8-sig")
    print(f"확정 이벤트 {len(done)}건 → {CONFIRMED}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", action="store_true")
    args = parser.parse_args()
    if args.export:
        export_confirmed()
        return

    sources = {"provided": load_provided(), "naver": load_naver(), "web": load_web(), "newsroom": load_newsroom()}
    for name, d in sources.items():
        print(f"입력 {name}: {len(d)}건")
    df = cluster(pd.concat([d for d in sources.values() if len(d)], ignore_index=True))
    sheet = keep_previous_input(summarize(df))

    SHEET.parent.mkdir(parents=True, exist_ok=True)
    if SHEET.exists():
        SHEET.replace(SHEET.with_suffix(".bak.csv"))  # 덮어쓰기 전 백업
    sheet.to_csv(SHEET, index=False, encoding="utf-8-sig")
    df[["key", "source", "corp", "date", "title", "url", "has_body"]].to_csv(
        MEMBERS, index=False, encoding="utf-8-sig")

    print(f"\n묶음 {len(sheet)}개 (기사 {len(df)}건)")
    print("우선순위별:", sheet["priority"].value_counts().sort_index().to_dict(),
          "  ← 1: AI + 이벤트/수상 (꼭 보기) / 2: AI 또는 이벤트 / 3: 실적·인사 등 (제목만 훑기)")
    print(pd.crosstab(sheet["corp"], sheet["priority"], margins=True))
    print(f"저장: {SHEET}")


if __name__ == "__main__":
    main()
