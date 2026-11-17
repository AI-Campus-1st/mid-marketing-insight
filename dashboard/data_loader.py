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
