"""
[B·D6] B 화면 미리보기 — A 의 app.py 뼈대가 나오기 전에 Q3·공시 Q&A 를 따로 띄워 본다

실행: streamlit run dashboard/preview_b.py
A 의 app.py 에서는 공통 필터(기업·기간) 값을 넘겨 q3_disclosure.render(corps, period), qa.render() 만 부르면 된다.
디자인은 app.py 와 같다: 공통 CSS(ui.inject) · 사이드바 로고(ui.brand) · 화면 메뉴 아이콘 · 챗봇/Q3 화면 표시(marker)
"""
import sys
from datetime import date
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard import style as ui  # noqa: E402
from dashboard.tabs import q3_disclosure, qa  # noqa: E402

CORPS = ["제일기획", "이노션", "플레이디", "인크로스", "KT나스미디어"]

PAGES = ["공시 챗봇", "Q3 공시 반영"]
PAGE_ICON = {"공시 챗봇": ":material/forum:", "Q3 공시 반영": ":material/fact_check:"}  # app.py 와 같은 아이콘

st.set_page_config(page_title=f"{ui.BRAND} · {ui.BRAND_SUB} (B 미리보기)", layout="wide")
ui.inject()   # app.py 와 같은 카드형 UI 공통 CSS

with st.sidebar:
    ui.brand("B 화면 미리보기 — 공시 챗봇과 Q3 공시 반영만 띄웁니다. 전체 화면은 streamlit run dashboard/app.py")
    st.markdown('<div class="sb-label">화면</div>', unsafe_allow_html=True)
    page = st.radio("화면", PAGES, label_visibility="collapsed", format_func=lambda p: f"{PAGE_ICON[p]}  {p}")
    st.divider()
    if page == "Q3 공시 반영":  # 필터는 Q3 화면에서만 쓴다
        st.session_state.setdefault("corps", CORPS)
        corps = st.multiselect("기업", CORPS, key="corps")
        st.button("기업 초기화 (5곳 모두)", width="stretch",
                  on_click=lambda: st.session_state.update(corps=CORPS))
        period = st.date_input("이벤트 발표일", value=(date(2023, 10, 19), date(2026, 10, 19)),
                               min_value=date(2023, 10, 19), max_value=date(2026, 10, 19))
    st.caption("데이터: DART 정기공시 5개사 · 뉴스 이벤트 28개 (2023-10-19 ~ 2026-10-19)")

if page == "공시 챗봇":
    st.markdown('<div class="chatpage-marker"></div>', unsafe_allow_html=True)   # 챗봇 화면 스타일 (app.py 와 같음)
    qa.render()
elif isinstance(period, tuple) and len(period) == 2:
    st.markdown('<div class="q3page-marker"></div>', unsafe_allow_html=True)     # Q3 글자·지표 스타일 (app.py 와 같음)
    q3_disclosure.render(corps or CORPS, period)
