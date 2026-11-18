"""
[공동·D6] 대시보드 진입점 — 광고대행사 AI: 뉴스의 말과 공시의 실행

실행: streamlit run dashboard/app.py → http://localhost:8501
- 화면: Q1 기사량·급증 / Q2 화제 이슈 / 기획안 검토 (A) · Q3 공시 반영 / 공시 챗봇 (B)
- 공통 필터(사이드바): 기간 · 기업 · 카테고리 → 모든 차트에 반영 (가이드 필수). 카테고리는 뉴스(Q1·Q2)에만 있는 값
- 상단 결론 카드: 의사결정 관점의 결론 (가이드 필수) — 분석 화면(Q1·Q2·Q3)에 표시, 챗봇은 대화 영역을 위해 생략
- 챗봇 화면: 사이드바에 B 의 대화 기록(새 대화·지난 대화, qa.py)이 붙으므로 필터는 접어 둔다 (값은 유지)
- 화면은 DB·CSV 에 저장된 결과만 읽는다. 예외는 공시 챗봇의 질문 1건당 검색 + LLM 1회, 기획안 검토 1건당 검색 + LLM 2회
"""
import sys
from datetime import date
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard import data_loader as dl  # noqa: E402
from dashboard import style as ui  # noqa: E402
from dashboard.tabs import q1_issue_trend, q2_issues  # noqa: E402

CORPS = ["제일기획", "이노션", "플레이디", "인크로스", "KT나스미디어"]
CATEGORIES = ["기술 및 트렌드", "시장 및 경쟁", "공급망 및 인프라", "규제 및 정책"]
START, END = date(2023, 10, 19), date(2026, 10, 19)
PAGES = ["공시 챗봇", "기획안 검토", "Q1 기사량·급증", "Q2 화제 이슈", "Q3 공시 반영"]  # 챗봇이 첫 화면
PAGE_ICON = {"공시 챗봇": ":material/forum:", "기획안 검토": ":material/assignment:", "Q1 기사량·급증": ":material/bar_chart:",
             "Q2 화제 이슈": ":material/trending_up:", "Q3 공시 반영": ":material/fact_check:"}

st.set_page_config(page_title="광고대행사 AI, 말과 실행", layout="wide")
ui.inject()   # 카드형 UI 공통 CSS (design_test)


def _reset():
    st.session_state.update(period=(START, END), **{f"corp_{c}": True for c in CORPS}, **{f"cat_{c}": True for c in CATEGORIES})


with st.sidebar:
    st.markdown('<div class="sb-brand"><span class="logo">AI</span>광고대행사 AI, 말과 실행</div>', unsafe_allow_html=True)
    st.caption("뉴스에 나온 AI 이슈와 5개사의 AI 사업이 사업보고서에서 얼마나 확인되는지 봐요.")
    st.markdown('<div class="sb-label">화면</div>', unsafe_allow_html=True)
    page = st.radio("화면", PAGES, label_visibility="collapsed", format_func=lambda p: f"{PAGE_ICON[p]}  {p}")
    st.markdown('<div class="sb-label">필터</div>', unsafe_allow_html=True)
    chat = page in ("공시 챗봇", "기획안 검토")   # 필터를 쓰지 않는 화면
    for c in CORPS:
        st.session_state.setdefault(f"corp_{c}", True)
    for c in CATEGORIES:
        st.session_state.setdefault(f"cat_{c}", True)
    st.session_state.setdefault("period", (START, END))
    # 챗봇 화면에서는 접어 둔다 — 위젯을 아예 숨기면 Streamlit 이 값을 지워 다른 화면으로 돌아올 때 초기화됨
    with st.expander("필터 (이 화면에는 적용 안 됨)" if chat else "필터 (모든 화면에 적용)", expanded=not chat,
                     icon=":material/tune:"):
        period = st.date_input("기간 (기사 발행일 · 이벤트 발표일)", key="period", min_value=START, max_value=END)
        # 체크박스: 체크한 것만 파랑 (style.py). 하나도 고르지 않으면 전체로 봄
        st.markdown('<div class="sb-label" style="margin-top:.6rem">기업</div>', unsafe_allow_html=True)
        corps = [c for c in CORPS if st.checkbox(c, key=f"corp_{c}")]
        st.caption("뉴스는 제목에 기업명이 나온 기사로 좁혀요 (제목 기준 137건)")
        st.markdown('<div class="sb-label" style="margin-top:.6rem">뉴스 카테고리</div>', unsafe_allow_html=True)
        categories = [c for c in CATEGORIES if st.checkbox(c, key=f"cat_{c}")]
        st.caption("수집 키워드 카테고리 — 뉴스 화면(Q1·Q2)에만 적용")
        st.button("필터 초기화", on_click=_reset, width="stretch", key="reset_filters", icon=":material/restart_alt:")
    st.caption("데이터: 제공 뉴스 6,450건 · DART 정기공시 5개사 · 앵커 이벤트 28개 (2023-10-19 ~ 2026-10-19)")
    if chat:   # 아래에 챗봇·기획안 검토가 기록(새 대화·지난 대화 / 새 기획안·지난 기획안)을 붙인다
        st.divider()

if not (isinstance(period, tuple) and len(period) == 2):
    st.info("기간의 시작일과 끝일을 모두 골라 주세요.")
    st.stop()
corps, categories = corps or CORPS, categories or CATEGORIES


# ── 상단 결론 카드 (필터와 무관한 전체 기준) ─────────────────────────
# 결론이 어떤 범위로 계산됐는지 (제목 옆 회색 글씨). 지금은 필터와 무관한 전체 기준
CONCLUSION_SCOPE = "전체 기간 · 5개사 기준 (필터 무관)"
# TODO(Day 7, A·B): 결론을 공통 필터(기간·기업·뉴스 카테고리)에 맞춰 다시 계산
#   - 뉴스의 말: trend.detect_spikes(필터 뉴스) 의 첫 급증 월·배율, q2 의 '가장 많이 오른 이슈' 계산을 함수로 빼서 재사용
#   - 공시의 실행: dl.reflection() 을 corps·period(이벤트 발표일)로 걸러 판정 대상 수·반영 수
#   - 판단: 필터 기업의 L3 비중(dl.chunk_levels) 순위 등 데이터 문장으로
#   - 범위 문구: CONCLUSION_SCOPE 를 필터 값으로 (예: "2025-01 – 2026-10 · 이노션·인크로스 기준")
#   - 기업 필터 시 뉴스는 제목 기준 137건뿐 → 급증 판정 생략 규칙(q1 MIN_MONTHLY_AI)을 결론에도 적용
def conclusion() -> None:
    rows = [("뉴스의 말", "제공 뉴스의 AI 마케팅 기사는 <b>2024-04</b> 에 직전 반년 평균의 2.3배로 급증한 뒤 그 수준을 유지했고, "
                      "화제는 생성형 콘텐츠(2024) → <b>AI 에이전트</b>(2025) → <b>AI 검색·GEO</b>(2026) 로 옮겨 가고 있다.")]
    try:
        r = dl.reflection()
        j = r[r["analysis_scope"] == "judge"]
        shown = int((j["verdict"] != "not_found").sum())
        rows.append(("공시의 실행", f"5개사가 발표한 AI 이벤트 {len(j)}개 중 <b>{shown}개</b>가 사업보고서에서 확인된다 "
                                  f"(공시에서 미확인 {len(j) - shown}개 — 제휴·외부 솔루션 도입은 공시에 실을 칸이 마땅치 않음)."))
    except Exception:
        rows.append(("공시의 실행", "공시 데이터가 이 DB 에 없어 표시하지 않아요."))
    rows.append(("판단", "대행사의 AI 경쟁력은 기사량이 아니라 <b>사업보고서로 확인되는 자체 개발·상용화(L3)</b> 를 기준으로 비교한다."))
    body = "".join(f'<div style="display:flex;gap:.7rem;align-items:baseline;margin:.45rem 0">'
                   f'<span style="min-width:5.2rem">{ui.pill(k, "blue")}</span><span style="line-height:1.6">{v}</span></div>'
                   for k, v in rows)
    # 강조 카드 안에 접기 메뉴 — 제목 줄을 누르면 열고 닫힘 (처음엔 펼침). 범위 문구는 제목 옆 회색 (style.py)
    with st.container(key="hl_conclusion"), \
            st.expander(f"한눈에 보는 결론 :gray[{CONCLUSION_SCOPE}]", expanded=True, icon=":material/star:"):
        st.markdown(body, unsafe_allow_html=True)


if not chat:
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
        from dashboard.tabs import plan_review, q3_disclosure, qa
        if page == "기획안 검토":
            st.markdown('<div class="chatpage-marker"></div>', unsafe_allow_html=True)   # 제목·소제목 스타일은 챗봇과 같게
            plan_review.render()
        elif page == "Q3 공시 반영":
            if set(categories) != set(CATEGORIES):
                st.caption("카테고리 필터는 뉴스 화면에만 적용돼요.")
            st.markdown('<div class="q3page-marker"></div>', unsafe_allow_html=True)   # Q3 글자·지표 스타일 (style.py)
            q3_disclosure.render(corps, period)
        else:
            # 챗봇 화면 전용 스타일 표시 (style.py: stMain:has(.chatpage-marker)). 컨테이너로 감싸면 입력창 하단 고정이 풀려서 표시만 둠
            st.markdown('<div class="chatpage-marker"></div>', unsafe_allow_html=True)
            qa.render()
    except Exception as e:  # 공시 테이블·임베딩이 없는 DB (예: 뉴스만 적재한 로컬)
        st.warning(f"이 화면에 필요한 공시 데이터가 이 DB 에 없어요. ({type(e).__name__}: {e})")
