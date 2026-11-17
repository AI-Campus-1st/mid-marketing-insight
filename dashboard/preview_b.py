"""
[B·D6] B 화면 미리보기 — A 의 app.py 뼈대가 나오기 전에 Q3·공시 Q&A 를 따로 띄워 본다

실행: streamlit run dashboard/preview_b.py
A 의 app.py 에서는 공통 필터(기업·기간) 값을 넘겨 q3_disclosure.render(corps, period), qa.render() 만 부르면 된다.
"""
import sys
from datetime import date
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard.tabs import q3_disclosure, qa  # noqa: E402

CORPS = ["제일기획", "이노션", "플레이디", "인크로스", "KT나스미디어"]

st.set_page_config(page_title="AI 실행 확인 챗봇", layout="wide")

with st.sidebar:
    st.markdown("### 광고대행사 AI, 공시로 확인하기")
    st.caption("뉴스에 나온 AI 사업이 사업보고서에도 적혀 있는지 5개사 공시로 확인해요.")
    page = st.radio("화면", ["AI 실행 확인 챗봇", "Q3 공시 반영"], label_visibility="collapsed")
    st.divider()
    if page == "Q3 공시 반영":  # 필터는 Q3 화면에서만 쓴다
        corps = st.multiselect("기업", CORPS, default=CORPS)
        period = st.date_input("이벤트 발표일", value=(date(2023, 10, 19), date(2026, 10, 19)),
                               min_value=date(2023, 10, 19), max_value=date(2026, 10, 19))
    st.caption("데이터: DART 정기공시 5개사 · 뉴스 이벤트 28개 (2023-10-19 ~ 2026-10-19)")

if page == "AI 실행 확인 챗봇":
    qa.render()
elif isinstance(period, tuple) and len(period) == 2:
    q3_disclosure.render(corps or CORPS, period)
