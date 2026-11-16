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
※ 기업 뉴스룸은 B 와 상의해 사용하지 않기로 함 (2026-11-16, docs/decisions.md)

묶는 규칙: 같은 기업 + 묶음 첫 기사로부터 7일 이내 + 제목 문자 3-gram 자카드 유사도 ≥ 0.25

실행
  python analysis/event_review_sheet.py                 # 시트 생성 (이미 입력한 판정은 유지)
  python analysis/event_review_sheet.py --make-review   # 사람이 확인할 행만 to_review.csv 로 뽑기
  python analysis/event_review_sheet.py --merge-review  # to_review.csv 에 입력한 판정을 시트에 합치기
  python analysis/event_review_sheet.py --export        # 판정 Y 인 행만 확정 이벤트로 내보내기
결과
  data/raw/news_supplement/review/event_review_sheet.csv   ← 전체 시트 (네이버 원본과 함께 삭제 대상)
  data/raw/news_supplement/review/to_review.csv            ← 사람이 확인할 행만 (여기에 입력)
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

# 기업 표기는 config/companies.csv 의 corp_name 과 같게 (Day 2: 나스미디어 → KT나스미디어 통일, 에코마케팅 제외)
# 기사 제목에는 옛 이름("나스미디어")만 나오는 경우가 있어 매칭할 때는 별칭도 함께 쓴다
CORP_ALIASES = {
    "제일기획": ["제일기획"],
    "이노션": ["이노션"],
    "플레이디": ["플레이디"],
    "인크로스": ["인크로스"],
    "KT나스미디어": ["KT나스미디어", "나스미디어"],
}
CORPS = list(CORP_ALIASES)
ALIAS_NAMES = [a for names in CORP_ALIASES.values() for a in names]  # 이벤트명 유사도에서 뺄 이름들
SHEET = settings.SUPPLEMENT_DIR / "review" / "event_review_sheet.csv"
MEMBERS = settings.SUPPLEMENT_DIR / "review" / "cluster_members.csv"  # 묶음별 기사 목록 (event_classify.py 입력)
TO_REVIEW = settings.SUPPLEMENT_DIR / "review" / "to_review.csv"      # 사람이 확인할 행만
CONFIRMED = settings.DATA_DIR / "processed" / "anchor_events_confirmed.csv"  # 확정 판정 행 (event_id 포함)
EVENTS = settings.DATA_DIR / "processed" / "anchor_events.csv"               # 고유 이벤트 (B 에게 넘기는 목록)

WINDOW_DAYS = 7
SIM_THRESHOLD = 0.25

# 사람이 채우는 칸 (재실행해도 유지)
HUMAN_COLS = ["is_event", "event_type", "event_name", "official_date", "ai_level_claimed",
              "primary_source_url", "reviewer", "note",
              "judged_by", "llm_reason",   # judged_by: human / llm (event_classify.py 가 채움)
              "llm_is_event"]              # 사람이 고치기 전 LLM 판정 (LLM 정확도 계산용)

# 대표 기사 우선순위: 1차 출처일수록 앞
SOURCE_RANK = {"web": 0, "provided": 1, "naver": 2}

# to_review.csv 에서 사람이 입력하는 칸
REVIEW_INPUT_COLS = ["final_is_event", "event_type", "event_name", "official_date",
                     "ai_level_claimed", "primary_source_url", "note"]

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
            for corp, aliases in CORP_ALIASES.items():
                hit = df[df["제목"].str.contains("|".join(aliases), na=False)]
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
            "suggested_official_date": g["date"].min().strftime("%Y-%m-%d"),
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


def keep_previous_input(sheet: pd.DataFrame, keep_llm: bool = True) -> pd.DataFrame:
    """이미 입력한 판정을 새 시트의 같은 묶음으로 옮긴다.
    1차: 같은 key, 2차: key 가 바뀐 경우 대표 기사 URL 이 같은 묶음.
    keep_llm=False 이면 사람 판정만 옮긴다 (입력 출처가 바뀌어 LLM 판정을 다시 해야 할 때)."""
    if not SHEET.exists():
        return sheet
    prev_all = pd.read_csv(SHEET, dtype=str)
    if not keep_llm:
        prev_all = prev_all[prev_all["judged_by"].eq("human")]
    prev_all = prev_all.dropna(subset=["is_event"])
    sheet = sheet.set_index("key")
    moved = lost = 0
    for r in prev_all.itertuples():
        target = r.key if r.key in sheet.index else None
        if target is None:  # 묶음 구성이 바뀌어 key 가 달라진 경우 대표 URL 로 찾기
            hit = sheet.index[(sheet["rep_url"] == r.rep_url)
                              | sheet["other_urls"].fillna("").str.contains(re.escape(str(r.rep_url)))]
            target = hit[0] if len(hit) else None
        if target is None:
            lost += 1
            continue
        for col in HUMAN_COLS:
            sheet.loc[target, col] = getattr(r, col, None)
        moved += 1
    print(f"이전 판정 옮김 {moved}건" + (f" / ⚠ 매칭 실패 {lost}건 (백업 확인)" if lost else ""))
    return sheet.reset_index()


def make_review() -> None:
    """사람이 확인할 행만 to_review.csv 로 뽑는다.
    - llm_Y   : LLM 이 이벤트(Y)로 판정한 행 (표본 검토상 Y 정밀도 약 50% → 전부 확인)
    - naver   : 네이버 기사만 있어 약관상 LLM 판정이 불가한 1·2순위 행 중 아직 사람이 판정하지 않은 행
    LLM 의 N 판정은 표본 20/20 일치라 확인 대상에서 뺀다."""
    sheet = pd.read_csv(SHEET, dtype=str)
    llm_y = sheet["judged_by"].eq("llm") & sheet["is_event"].eq("Y")
    naver = (sheet["only_naver"].eq("1") & (sheet["priority"].astype(int) <= 2)
             & ~sheet["judged_by"].eq("human"))
    rv = sheet[llm_y | naver].copy()
    rv.insert(0, "review_reason", llm_y[rv.index].map({True: "llm_Y 확인", False: "네이버만 (사람 판정)"}))
    rv = rv.rename(columns={"is_event": "llm_is_event_now"})
    rv["final_is_event"] = None  # ← 여기에 Y/N 입력
    cols = (["review_reason", "key", "priority", "corp", "first_date", "suggested_official_date",
             "auto_category", "sources", "rep_title", "rep_url", "other_urls", "llm_is_event_now", "llm_reason"]
            + REVIEW_INPUT_COLS)
    rv = rv.sort_values(["review_reason", "corp", "first_date"])[cols]
    rv.to_csv(TO_REVIEW, index=False, encoding="utf-8-sig")
    print(f"확인할 행 {len(rv)}개 → {TO_REVIEW}")
    print(rv.groupby(["review_reason", "corp"]).size().unstack(fill_value=0))


def merge_review(path: Path = TO_REVIEW) -> None:
    """검토 파일에서 final_is_event 를 입력한 행을 시트에 반영한다 (judged_by=human).
    파일 이름을 바꿔 저장했다면 경로를 넘긴다: --merge-review data/.../to_review_completed.csv"""
    sheet = pd.read_csv(SHEET, dtype=str).set_index("key")
    rv = pd.read_csv(path, dtype=str)
    print(f"입력 파일: {path}")
    done = rv[rv["final_is_event"].str.strip().str.upper().isin(["Y", "N"])]
    for r in done.itertuples():
        if r.key not in sheet.index:
            continue
        if sheet.loc[r.key, "judged_by"] == "llm":  # LLM 정확도 계산용으로 원래 판정 보관
            sheet.loc[r.key, "llm_is_event"] = sheet.loc[r.key, "is_event"]
        final = r.final_is_event.strip().upper()
        sheet.loc[r.key, "is_event"] = final
        for col in REVIEW_INPUT_COLS[1:]:
            val = getattr(r, col)
            sheet.loc[r.key, col] = val if final == "Y" or col == "note" else None
        sheet.loc[r.key, "judged_by"] = "human"
        sheet.loc[r.key, "reviewer"] = "A"
    sheet.reset_index().to_csv(SHEET, index=False, encoding="utf-8-sig")
    print(f"반영 {len(done)}행 / 미입력 {len(rv) - len(done)}행")
    checked = sheet[sheet["llm_is_event"].notna()]
    if len(checked):
        agree = (checked["llm_is_event"] == checked["is_event"]).mean()
        print(f"LLM Y 판정 중 사람이 Y 로 확인한 비율: {agree:.0%} ({len(checked)}행 기준)")


def export_confirmed() -> None:
    if not SHEET.exists():
        print(f"검토 시트가 없습니다 ({SHEET}). 네이버 원본 삭제(2026-11-16) 이후에는 "
              f"확정 이벤트 파일({EVENTS})을 기준으로 사용하세요.")
        return
    sheet = pd.read_csv(SHEET, dtype=str)
    done = sheet[sheet["is_event"].str.upper().eq("Y")]
    if done.empty:  # 판정 없는 시트로 확정 이벤트 파일을 덮어쓰지 않도록
        print("판정 Y 인 행이 없어 내보내지 않습니다 (기존 확정 이벤트 파일 유지)")
        return
    missing = done[done["primary_source_url"].isna()]
    if len(missing):
        print(f"⚠ 1차 출처가 비어 있는 확정 이벤트 {len(missing)}건 — 네이버 링크 대신 뉴스룸·언론사 원문 URL 을 넣어 주세요")
    # 반영 판정 기준일 이후 이벤트는 참고용 (타임라인 표시만, 반영 판정·기업 비교 집계 제외)
    date = pd.to_datetime(done["official_date"].fillna(done["suggested_official_date"]), errors="coerce")
    done = done.assign(analysis_scope=(date > pd.Timestamp(settings.REFLECTION_CUTOFF)).map(
        {True: "reference", False: "judge"}))
    cols = ["corp", "event_name", "event_type", "official_date", "ai_level_claimed", "analysis_scope",
            "primary_source_url", "reviewer", "judged_by", "note", "n_articles", "n_media"]
    CONFIRMED.parent.mkdir(parents=True, exist_ok=True)
    done = done.assign(event_id=assign_event_ids(done))
    done = done.assign(corp_code=done["corp"].map(corp_code_map()))  # 공시와 연결하는 키
    cols = ["corp_code"] + cols
    done[["event_id"] + cols].sort_values(["event_id", "official_date"]).to_csv(
        CONFIRMED, index=False, encoding="utf-8-sig")
    n_ref = int((done["analysis_scope"] == "reference").sum())
    print(f"확정 판정 {len(done)}행 (반영 판정 {len(done) - n_ref} / 참고용 {n_ref}) → {CONFIRMED}")

    events = summarize_events(done)
    events.to_csv(EVENTS, index=False, encoding="utf-8-sig")
    print(f"고유 이벤트 {len(events)}개 (확인 필요 {int(events['needs_check'].sum())}개) → {EVENTS}")
    print(events.groupby(["corp", "analysis_scope"]).size().unstack(fill_value=0))


# ── 같은 사건 묶기 (확정 판정 행 → 고유 이벤트) ──────────────
# 같은 기업 + 발표일 EVENT_DAYS 이내 + (이벤트명 유사도 ≥ NAME_SIM 또는 같은 유형)
EVENT_DAYS = 3
NAME_SIM = 0.3


def corp_code_map() -> dict[str, str]:
    """기업 표기 → corp_code (config/companies.csv). 이벤트와 공시는 이 코드로만 연결한다."""
    c = pd.read_csv(settings.COMPANIES_CSV, dtype=str)
    return dict(zip(c["corp_name"], c["corp_code"]))


def _name_grams(name: str) -> set[str]:
    s = re.sub(r"[^0-9A-Za-z가-힣]", "", str(name)).lower()
    for name in sorted(ALIAS_NAMES, key=len, reverse=True):  # 기업명(별칭 포함)은 유사도에서 뺀다
        s = s.replace(name, "")
    return {s[i: i + 2] for i in range(max(len(s) - 1, 1))}


def assign_event_ids(done: pd.DataFrame) -> pd.Series:
    rows = done.assign(_d=pd.to_datetime(done["official_date"], errors="coerce")).reset_index()
    parent = list(range(len(rows)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    grams = rows["event_name"].map(_name_grams)
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows.loc[i], rows.loc[j]
            if a["corp"] != b["corp"] or pd.isna(a["_d"]) or pd.isna(b["_d"]):
                continue
            if abs((a["_d"] - b["_d"]).days) > EVENT_DAYS:
                continue
            sim = len(grams[i] & grams[j]) / max(len(grams[i] | grams[j]), 1)
            if sim >= NAME_SIM or a["event_type"] == b["event_type"]:
                parent[find(i)] = find(j)

    roots = [find(i) for i in range(len(rows))]
    # 기업·날짜 순으로 E001… 번호
    order = (rows.assign(_root=roots).groupby("_root")
             .agg(corp=("corp", "first"), d=("_d", "min")).sort_values(["corp", "d"]))
    ids = {r: f"E{n:03d}" for n, r in enumerate(order.index, 1)}
    return pd.Series([ids[r] for r in roots], index=done.index)


def summarize_events(done: pd.DataFrame) -> pd.DataFrame:
    out = []
    for eid, g in done.groupby("event_id"):
        g = g.assign(_d=pd.to_datetime(g["official_date"], errors="coerce")).sort_values("_d")
        urls = g["primary_source_url"].dropna()
        good = urls[~urls.str.contains("naver.com")]  # 1차 출처는 언론사 원문 우선
        names = g["event_name"].dropna()
        out.append({
            "event_id": eid,
            "corp_code": g["corp_code"].iloc[0],
            "corp": g["corp"].iloc[0],
            "event_name": names.loc[names.str.len().idxmin()] if len(names) else None,  # 가장 짧은 이름
            "event_type": g["event_type"].mode().iloc[0] if g["event_type"].notna().any() else None,
            "official_date": g["_d"].min().strftime("%Y-%m-%d") if g["_d"].notna().any() else None,
            "ai_level_claimed": pd.to_numeric(g["ai_level_claimed"], errors="coerce").max(),
            "analysis_scope": g["analysis_scope"].iloc[0],
            "primary_source_url": good.iloc[0] if len(good) else (urls.iloc[0] if len(urls) else None),
            "n_rows": len(g),
            "all_names": " | ".join(names.unique()),
            # 이름이 여럿이거나 유형이 섞이면 사람이 한 번 보기
            "needs_check": int(names.nunique() > 1 and g["event_type"].nunique() > 1
                               or (len(urls) and not len(good))),
        })
    return pd.DataFrame(out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", action="store_true")
    parser.add_argument("--make-review", action="store_true")
    parser.add_argument("--merge-review", nargs="?", const=str(TO_REVIEW), default=None, metavar="PATH",
                        help="검토 파일을 시트에 합치기 (경로 생략 시 to_review.csv)")
    parser.add_argument("--reset-llm", action="store_true",
                        help="시트를 다시 만들 때 LLM 판정은 버리고 사람 판정만 유지 (입력 출처가 바뀐 경우)")
    args = parser.parse_args()
    if args.export:
        export_confirmed()
        return
    if args.make_review:
        make_review()
        return
    if args.merge_review:
        merge_review(Path(args.merge_review))
        return

    if EVENTS.exists() and not (settings.SUPPLEMENT_DIR / "naver" / "candidates.csv").exists():
        print("네이버 원본이 삭제되어(2026-11-16) 검토 시트를 다시 만들 수 없습니다. "
              f"확정 이벤트는 {EVENTS} 를 사용하세요.")
        return
    sources = {"provided": load_provided(), "naver": load_naver(), "web": load_web()}
    for name, d in sources.items():
        print(f"입력 {name}: {len(d)}건")
    df = cluster(pd.concat([d for d in sources.values() if len(d)], ignore_index=True))
    sheet = keep_previous_input(summarize(df), keep_llm=not args.reset_llm)

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
