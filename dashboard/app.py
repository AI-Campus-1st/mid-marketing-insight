"""
[공동·D6] 대시보드 진입점 — 광고대행사 AI: 뉴스의 말과 공시의 실행

실행: streamlit run dashboard/app.py → http://localhost:8501
- 화면: Q1 기사량·급증 / Q2 화제 이슈 (A) · Q3 공시 반영 / 공시 챗봇 (B)
- 공통 필터(사이드바): 기간 · 기업 · 카테고리 → 모든 차트에 반영 (가이드 필수). 카테고리는 뉴스(Q1·Q2)에만 있는 값
- 상단 결론 카드: 의사결정 관점의 결론 (가이드 필수) — 분석 화면(Q1·Q2·Q3)에 표시, 챗봇은 대화 영역을 위해 생략
- 챗봇 화면: 사이드바에 B 의 대화 기록(새 대화·지난 대화, qa.py)이 붙으므로 필터는 접어 둔다 (값은 유지)
- 화면은 DB·CSV 에 저장된 결과만 읽는다. 예외는 공시 챗봇의 질문 1건당 검색 + LLM 1회
"""
import sys
from datetime import date
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard import data_loader as dl  # noqa: E402
from dashboard.tabs import q1_issue_trend, q2_issues  # noqa: E402

CORPS = ["제일기획", "이노션", "플레이디", "인크로스", "KT나스미디어"]
CATEGORIES = ["기술 및 트렌드", "시장 및 경쟁", "공급망 및 인프라", "규제 및 정책"]
START, END = date(2023, 10, 19), date(2026, 10, 19)
PAGES = ["공시 챗봇", "Q1 기사량·급증", "Q2 화제 이슈", "Q3 공시 반영"]  # 챗봇이 첫 화면

st.set_page_config(page_title="광고대행사 AI, 말과 실행", layout="wide")


def _reset():
    st.session_state.update(corps=CORPS, categories=CATEGORIES, period=(START, END))


with st.sidebar:
    st.markdown("### 광고대행사 AI, 말과 실행")
    st.caption("뉴스에 나온 AI 이슈와 5개사의 AI 사업이 사업보고서에서 얼마나 확인되는지 봐요.")
    page = st.radio("화면", PAGES, label_visibility="collapsed")
    st.divider()
    chat = page == "공시 챗봇"
    st.session_state.setdefault("corps", CORPS)
    st.session_state.setdefault("categories", CATEGORIES)
    st.session_state.setdefault("period", (START, END))
    # 챗봇 화면에서는 접어 둔다 — 위젯을 아예 숨기면 Streamlit 이 값을 지워 다른 화면으로 돌아올 때 초기화됨
    with st.expander("필터 (챗봇에는 적용 안 됨)" if chat else "필터 (모든 화면에 적용)", expanded=not chat):
        period = st.date_input("기간 (기사 발행일 · 이벤트 발표일)", key="period", min_value=START, max_value=END)
        corps = st.multiselect("기업", CORPS, key="corps", help="뉴스는 제목에 기업명이 나온 기사로 좁혀요 (제목 기준 137건)")
        categories = st.multiselect("카테고리 (뉴스)", CATEGORIES, key="categories",
                                    help="수집 키워드 카테고리 — 뉴스 화면(Q1·Q2)에만 적용")
        st.button("필터 초기화", on_click=_reset, width="stretch")
    st.caption("데이터: 제공 뉴스 6,450건 · DART 정기공시 5개사 · 앵커 이벤트 28개 (2023-10-19 ~ 2026-10-19)")
    if chat:
        st.divider()   # 아래에 qa.render() 가 대화 기록(새 대화·지난 대화)을 붙인다

if not (isinstance(period, tuple) and len(period) == 2):
    st.info("기간의 시작일과 끝일을 모두 골라 주세요.")
    st.stop()
corps, categories = corps or CORPS, categories or CATEGORIES


# ── 상단 결론 카드 (필터와 무관한 전체 기준) ─────────────────────────
def conclusion() -> None:
    lines = ["**뉴스의 말:** 제공 뉴스의 AI 마케팅 기사는 **2024-04** 에 직전 반년 평균의 2.3배로 급증한 뒤 그 수준을 유지했고, "
             "화제는 생성형 콘텐츠(2024) → **AI 에이전트**(2025) → **AI 검색·GEO**(2026) 로 옮겨 가고 있다."]
    try:
        r = dl.reflection()
        j = r[r["analysis_scope"] == "judge"]
        shown = int((j["verdict"] != "not_found").sum())
        lines.append(f"**공시의 실행:** 5개사가 발표한 AI 이벤트 {len(j)}개 중 **{shown}개**가 사업보고서에서 확인된다 "
                     f"(공시에서 미확인 {len(j) - shown}개 — 제휴·외부 솔루션 도입은 공시에 실을 칸이 마땅치 않음).")
    except Exception:
        lines.append("**공시의 실행:** 공시 데이터가 이 DB 에 없어 표시하지 않아요.")
    lines.append("**판단:** 대행사의 AI 경쟁력은 기사량이 아니라 **사업보고서로 확인되는 자체 개발·상용화(L3)** 를 기준으로 비교한다.")
    with st.container(border=True):
        st.markdown("  \n".join(lines))
        st.caption("결론 초안 — 수치는 전체 기간·5개사 기준 (필터와 무관)")


if page != "공시 챗봇":
    conclusion()

# ── 화면 ─────────────────────────────────────────────────────────────
if page in ("Q1 기사량·급증", "Q2 화제 이슈"):
    try:
        news = dl.filter_news(dl.news(), (START, END), corps, categories, CORPS)   # 기업·카테고리 (기간은 화면에서)
    except Exception as e:  # 뉴스 결과(news_preprocessed.csv·llm_results)가 없는 DB — Q3·챗봇과 같게 안내 문구 (B 리뷰, PR #22)
        st.warning(f"이 화면에 필요한 뉴스 데이터가 없어요 — `analysis/handoff.py import` 로 A 의 뉴스 결과를 넣어 주세요. ({type(e).__name__}: {e})")
        st.stop()
    if set(corps) != set(CORPS):
        n = int(((news["date"] >= period[0]) & (news["date"] <= period[1])).sum())
        st.caption(f"기업 필터: 제목에 {', '.join(corps)} 이(가) 나온 기사 {n:,}건만 보고 있어요.")
    if page == "Q1 기사량·급증":
        q1_issue_trend.render(news, period)
    else:
        q2_issues.render(news[(news["date"] >= period[0]) & (news["date"] <= period[1])], period)
else:
    try:
        from dashboard.tabs import q3_disclosure, qa
        if page == "Q3 공시 반영":
            if set(categories) != set(CATEGORIES):
                st.caption("카테고리 필터는 뉴스 화면에만 적용돼요.")
            q3_disclosure.render(corps, period)
        else:
            qa.render()
    except Exception as e:  # 공시 테이블·임베딩이 없는 DB (예: 뉴스만 적재한 로컬)
        st.warning(f"이 화면에 필요한 공시 데이터가 이 DB 에 없어요. ({type(e).__name__}: {e})")
