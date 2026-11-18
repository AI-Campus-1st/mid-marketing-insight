"""
[B·D6] 대시보드 DB 조회 (B 파트: 공시·이벤트 반영·AI 단계). 화면은 저장된 결과만 읽는다 (DART·분류 LLM 호출 없음)
A 의 뼈대가 나오면 같은 파일에 A 파트 함수를 더하거나, 이 함수들을 그대로 가져다 쓴다.
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402

DART_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"
# 기업 비교 지표에서 뺄 KT나스미디어 청크: 근거가 자회사 플레이디 제품 (2025-04 SOOP 매각 전 보고서, decisions.md)
PLAYD_PRODUCTS = "플레이디|All it|올잇|센스엔|Ato|크레파스|ChatGPT|techHUB"
PLAYD_SOLD = "2025-05-14"


def _con():
    return sqlite3.connect(settings.DB_PATH)


@st.cache_data
def reflection() -> pd.DataFrame:
    """이벤트별 공시 반영 판정 (event_reflection 뷰) + 첫 반영 공시 링크·뉴스 원문 링크"""
    df = pd.read_sql(
        """SELECT r.*, k.rcept_no AS first_rcept_no, e.primary_source_url
           FROM event_reflection r
           LEFT JOIN disclosure_chunks k ON k.chunk_id = r.first_chunk_id
           LEFT JOIN ai_events e ON e.event_id = r.event_id""", _con())
    df["dart_url"] = df.first_rcept_no.map(lambda x: DART_URL.format(x) if isinstance(x, str) else None)
    return df


@st.cache_data
def chunk_levels(exclude_playd: bool = True) -> pd.DataFrame:
    """AI 청크의 단계 (분류된 것만). exclude_playd: KT나스미디어 공시 속 플레이디 제품 근거 청크를 뺀다"""
    df = pd.read_sql(
        """SELECT k.chunk_id, c.corp_name, k.report_date, d.title AS report_name, k.section, k.ai_level, k.reason
           FROM disclosure_chunks k JOIN companies c USING(corp_code) JOIN documents d ON d.doc_id = k.doc_id
           WHERE k.model IS NOT NULL""", _con())
    if exclude_playd:
        playd = (df.corp_name == "KT나스미디어") & (df.report_date <= PLAYD_SOLD) & \
            df.reason.str.contains(PLAYD_PRODUCTS, regex=True, na=False)
        df = df[~playd]
    df["level"] = df.ai_level.map(lambda x: None if pd.isna(x) else f"L{int(x)}")
    return df


# ── A 파트: 뉴스 (Q1 기사량·급증, Q2 이슈) ─────────────────────────────
# 제공 뉴스 집계 대상(중복 제외)만. 정제·태그는 analysis/preprocess.py, LLM 결과는 llm_results(news_v4) 에서 읽는다
NEWS_PROMPT = "news_v4"
CONCERN_CSV = settings.DATA_DIR / "processed" / "q2_concern_articles.csv"


@st.cache_data
def _sentence(text: str, quote: str, width: int = 140) -> str:
    """청크 본문에서 인용 구절이 든 문장 (앞뒤 문장 끝·줄바꿈까지, 너무 길면 자름). 구절을 못 찾으면 구절 그대로"""
    import re
    i = text.find(quote)
    if i < 0:
        return quote
    ends = [m.end() for m in re.finditer(r"(다\.|\.\s|\n)", text)]
    start = max([e for e in ends if e <= i], default=0)
    stop = min([e for e in ends if e >= i + len(quote)], default=len(text))
    out = re.sub(r"\s+", " ", text[start:stop]).strip()
    if len(out) > width:   # 표 한 줄처럼 긴 경우: 구절 앞뒤로 자름
        j = out.find(quote)
        a = max(0, j - (width - len(quote)) // 2)
        out = ("…" if a else "") + out[a:a + width] + ("…" if a + width < len(out) else "")
    return out


@st.cache_data
def ai_cases(corp: str, n: int = 8) -> pd.DataFrame:
    """회사의 공시 속 AI 사례 — 분류 결과(chunk_v7)에서 '자체 개발·상용화'(L3)·'도입·적용'(L2) 문단의 인용 구절이 든 문장.
    같은 문장은 분기마다 반복되므로 하나로 묶고(한 문장이 다른 문장 안에 들어 있어도 하나로) '처음 실린 공시'를 보여 준다.
    기업 비교와 같게 KT나스미디어 속 플레이디 제품 문단은 뺀다.
    → sentence · level · report_name · report_date(처음) · url · n_reports (단계 높은 순, 최근에 처음 실린 순)"""
    from difflib import SequenceMatcher
    cols = ["sentence", "level", "report_name", "report_date", "url", "n_reports"]
    lv = chunk_levels()
    ids = lv[(lv.corp_name == corp) & (lv.ai_level >= 2)].chunk_id.tolist()
    if not ids:
        return pd.DataFrame(columns=cols)
    df = pd.read_sql(
        f"""SELECT k.chunk_id, k.ai_level, k.reason, k.text, k.report_date, k.rcept_no, d.title AS report_name
            FROM disclosure_chunks k JOIN documents d ON d.doc_id = k.doc_id
            WHERE k.chunk_id IN ({','.join('?' * len(ids))})""", _con(), params=ids)
    df["quote"] = df.reason.str.extract(r"^L\d: '([^']+)'")[0]
    df = df[df.quote.notna()].copy()
    df["sentence"] = [_sentence(t, q) for t, q in zip(df.text, df.quote)]
    df["key"] = df.sentence.str.replace(r"[^0-9A-Za-z가-힣]", "", regex=True)
    df = df.sort_values("report_date")
    groups: list[dict] = []   # 같은 문장(또는 서로 포함) 묶음
    for row in df.itertuples():
        g = next((g for g in groups if row.key in g["key"] or g["key"] in row.key
                  or SequenceMatcher(None, row.key, g["key"]).ratio() >= 0.9), None)   # 거의 같은 문장('2024년 3월' vs '3월')도 하나로
        if g is None:
            groups.append({"key": row.key, "sentence": row.sentence, "level": int(row.ai_level), "report_name": row.report_name,
                           "report_date": row.report_date, "url": DART_URL.format(row.rcept_no), "n_reports": 1})
        else:
            g["n_reports"] += 1
            g["level"] = max(g["level"], int(row.ai_level))
            if len(row.key) > len(g["key"]):   # 더 긴(온전한) 문장으로
                g["key"], g["sentence"] = row.key, row.sentence
    out = pd.DataFrame(groups, columns=["key"] + cols)
    return out.sort_values(["level", "report_date"], ascending=[False, False]).head(n)[cols].reset_index(drop=True)


def news() -> pd.DataFrame:
    """기사 1건 = 1행: 발행일·월·카테고리(목록)·AI 기사 여부·제목 속 기업(목록)·LLM 관련·감성·이슈(목록)"""
    import json
    from analysis import trend
    df = trend.load_articles()   # 집계 대상 6,450건 + month, is_ai, keyword, category
    llm = pd.read_sql("SELECT doc_id, is_relevant, is_ai_related, sentiment, issue_keywords FROM llm_results "
                      "WHERE prompt_version = ?", _con(), params=(NEWS_PROMPT,))
    df = df.merge(llm, on="doc_id", how="left")
    df["date"] = pd.to_datetime(df["published_date"]).dt.date
    df["quarter"] = pd.PeriodIndex(df["published_date"].str[:10], freq="Q").astype(str)
    df["categories"] = df["category"].fillna("").str.split("; ")
    df["title_corps"] = df["corps_in_title"].fillna("").map(lambda s: [c for c in s.split("; ") if c])
    df["issues"] = df["issue_keywords"].map(lambda s: json.loads(s) if isinstance(s, str) else [])
    return df[["doc_id", "published_date", "date", "month", "quarter", "media", "title", "categories", "is_ai",
               "title_corps", "is_relevant", "is_ai_related", "sentiment", "issues"]]


@st.cache_data
def concern_articles() -> pd.DataFrame | None:
    """사람이 거른 우려 기사 (analysis/q2_concerns.py --apply). 아직 없으면 None"""
    if not CONCERN_CSV.exists():
        return None
    df = pd.read_csv(CONCERN_CSV)
    url = pd.read_sql("SELECT doc_id, url FROM documents WHERE news_origin = 'provided'", _con())
    df = df.merge(url, on="doc_id", how="left")
    df["date"] = pd.to_datetime(df["published_date"]).dt.date
    return df


def filter_news(df: pd.DataFrame, period, corps: list[str], categories: list[str], all_corps: list[str]) -> pd.DataFrame:
    """공통 필터: 기간(발행일) · 기업(제목에 기업명, 5곳 모두면 거르지 않음) · 카테고리(수집 키워드)"""
    out = df[(df["date"] >= period[0]) & (df["date"] <= period[1])]
    if set(corps) != set(all_corps):
        out = out[out["title_corps"].map(lambda cs: any(c in corps for c in cs))]
    out = out[out["categories"].map(lambda cs: any(c in categories for c in cs))]
    return out
