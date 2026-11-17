"""
[B·D6] B 화면 미리보기 — A 의 app.py 뼈대가 나오기 전에 Q3·공시 Q&A 탭을 따로 띄워 본다

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

st.set_page_config(page_title="공시 반영·Q&A (B 미리보기)", layout="wide")
st.title("말뿐인 AI인가, 실행하는 AI인가 — 공시 파트 미리보기")

f1, f2 = st.columns([3, 2])
corps = f1.multiselect("기업", CORPS, default=CORPS)
period = f2.date_input("이벤트 발표일 범위", value=(date(2023, 10, 19), date(2026, 10, 19)),
                       min_value=date(2023, 10, 19), max_value=date(2026, 10, 19))
if not isinstance(period, tuple) or len(period) != 2:
    st.stop()

tab3, tabqa = st.tabs(["Q3 공시 반영", "공시 Q&A"])
with tab3:
    q3_disclosure.render(corps or CORPS, period)
with tabqa:
    qa.render()
