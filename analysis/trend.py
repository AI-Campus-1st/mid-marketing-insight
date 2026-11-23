"""Q1 기사량 추이·급증 시점 계산 (Day 3, 담당 A)

eda.ipynb 와 대시보드(Q1 화면)가 같은 함수를 쓴다.
입력: analysis/preprocess.py 가 만든 news_preprocessed.csv + documents.keyword·category

급증 기준 (decisions.md '급증 기준'): 이번 달 AI 기사 수 > 직전 6개월 평균 × 1.5
  - 수집 기간 첫 달·마지막 달(19일부터/19일까지)은 미완 월이라 판정에서 뺀다
  - 직전 기간이 3개월 미만이면 판정하지 않는다
"""

import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402
from analysis.preprocess import OUT_CSV  # noqa: E402

SPIKE_WINDOW = 6       # 직전 몇 개월과 비교
SPIKE_RATIO = 1.5      # 직전 평균의 몇 배를 넘으면 급증
MIN_HISTORY = 3        # 직전 기간 최소 개월 수
PARTIAL_MONTHS = {settings.PERIOD_START[:7], settings.PERIOD_END[:7]}   # 2023-10, 2026-10

# 제목 단어 비교에서 뺄 말 (모든 달에 흔해서 원인 설명에 쓸모없음)
STOP_TERMS = {"AI", "인공지능", "생성형", "마케팅", "있다", "위한", "기반", "활용", "대표", "진행", "통해", "nbsp"}


def load_articles() -> pd.DataFrame:
    """집계 대상(중복 제외) 제공 뉴스 + month, is_ai, keyword, category"""
    out = pd.read_csv(OUT_CSV)
    with sqlite3.connect(settings.DB_PATH) as con:
        meta = pd.read_sql("SELECT doc_id, keyword, category FROM documents WHERE news_origin = 'provided'", con)
    df = out[out["is_duplicate"] == 0].merge(meta, on="doc_id")
    df["month"] = df["published_date"].str[:7]
    df["is_ai"] = df["ai_count"] > 0
    return df


def monthly(df: pd.DataFrame) -> pd.DataFrame:
    """월별 전체 기사 수, AI 기사 수, AI 비율, 미완 월 표시"""
    t = df.groupby("month").agg(total=("doc_id", "size"), ai=("is_ai", "sum"))
    t["ai_share"] = t["ai"] / t["total"]
    t["partial"] = t.index.isin(PARTIAL_MONTHS)
    return t


def detect_spikes(counts: pd.Series, partial: pd.Series | None = None,
                  window: int = SPIKE_WINDOW, ratio: float = SPIKE_RATIO) -> pd.DataFrame:
    """월별 건수 → 직전 평균, 배율, 급증 여부. 미완 월은 판정·기준선 계산 모두에서 뺀다"""
    s = counts.astype(float).copy()
    if partial is not None:
        s[partial] = float("nan")
    base = s.shift(1).rolling(window, min_periods=MIN_HISTORY).mean()
    res = pd.DataFrame({"count": counts, "baseline": base, "ratio": s / base})
    res["spike"] = (res["ratio"] > ratio).fillna(False)
    return res


def spike_terms(df: pd.DataFrame, month: str, window: int = SPIKE_WINDOW, k: int = 12, min_n: int = 5) -> pd.DataFrame:
    """급증 월 AI 기사 제목에서 평소보다 많이 나온 단어 (이번 달 건수 − 직전 기간 비율로 본 기대 건수)"""
    ai = df[df["is_ai"]]
    months = sorted(ai["month"].unique())
    i = months.index(month)
    base, cur = ai[ai["month"].isin(months[max(0, i - window):i])], ai[ai["month"] == month]

    def words(t):
        return {w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", re.sub(r"\[.*?\]", "", t)) if w not in STOP_TERMS}

    cb = Counter(w for t in base["title"] for w in words(t))
    cc = Counter(w for t in cur["title"] for w in words(t))
    rows = [{"term": w, "count": n, "expected": cb[w] / max(len(base), 1) * len(cur)} for w, n in cc.items() if n >= min_n]
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["excess"] = out["count"] - out["expected"]
    return out.sort_values("excess", ascending=False).head(k).round(1).reset_index(drop=True)


def category_monthly(df: pd.DataFrame) -> pd.DataFrame:
    """월 × 카테고리 기사 수 (수집키워드가 여럿이면 각 카테고리에 한 번씩)"""
    c = df.assign(category=df["category"].str.split("; ")).explode("category")
    return c.groupby(["month", "category"]).size().unstack(fill_value=0)


def company_quarterly(df: pd.DataFrame, title_only: bool = False) -> pd.DataFrame:
    """분기 × 기업 기사 수 (기업 태그 기준, 월별은 건수가 너무 적어 분기)"""
    col = "corps_in_title" if title_only else "corps"
    c = df.assign(corp=df[col].fillna("").str.split("; ")).explode("corp")
    c = c[c["corp"] != ""]
    c["quarter"] = pd.PeriodIndex(c["published_date"].str[:10], freq="Q")
    allq = pd.period_range(settings.PERIOD_START, settings.PERIOD_END, freq="Q")   # 기사 0건 분기도 행으로 남김
    t = c.groupby(["quarter", "corp"]).size().unstack(fill_value=0).reindex(allq, fill_value=0)
    t.index = t.index.astype(str)
    return t
