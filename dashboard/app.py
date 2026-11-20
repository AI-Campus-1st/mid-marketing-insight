"""
[공동·D6] 대시보드 진입점 — 광고대행사 AI: 뉴스의 말과 공시의 실행

실행: streamlit run dashboard/app.py → http://localhost:8501
- 사이드바 메뉴: 공시 챗봇 (B) · 기획안 검토 (A) · 리포트 — 리포트는 맨 위 버튼으로 Q1 기사량·급증 / Q2 화제 이슈 (A) · Q3 공시 반영 (B)
- 공통 필터(사이드바): 기간 · 기업 · 카테고리 → 모든 차트에 반영 (가이드 필수). 카테고리는 뉴스(Q1·Q2)에만 있는 값
- 상단 결론 카드: 의사결정 관점의 결론 (가이드 필수) — 분석 화면(Q1·Q2·Q3)에 표시, 챗봇은 대화 영역을 위해 생략
- 필터는 '리포트'에서만 보인다. 다른 화면에 다녀와도 값은 '_filters' 에 저장해 두었다가 되살림 (리포트 Q1·Q2·Q3 선택도 유지)
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
MENU = ["공시 챗봇", "기획안 검토", "리포트"]   # 사이드바 메뉴 (챗봇이 첫 화면)
MENU_ICON = {"공시 챗봇": ":material/forum:", "기획안 검토": ":material/assignment:", "리포트": ":material/analytics:"}
MENU_TIP = {"공시 챗봇": "5개사 공시를 근거로 질문에 답합니다",          # 메뉴에 마우스를 대면 나오는 설명 (style.menu_tips)
            "기획안 검토": "내 기획안과 비슷한 AI 사례를 공시·뉴스에서 찾습니다",
            "리포트": "기사량·화제 이슈·공시 반영 정도를 한눈에 봅니다"}
REPORT_PAGES = ["Q1 기사량·급증", "Q2 화제 이슈", "Q3 공시 반영"]   # '리포트' 안에서 맨 위 버튼으로 고름
PAGE_ICON = {"Q1 기사량·급증": ":material/bar_chart:", "Q2 화제 이슈": ":material/trending_up:", "Q3 공시 반영": ":material/fact_check:"}

st.set_page_config(page_title=f"{ui.BRAND} · {ui.BRAND_SUB}", layout="wide")
ui.inject()   # 카드형 UI 공통 CSS (design_test)


FILTER_DEFAULTS = {"period": (START, END), **{f"corp_{c}": True for c in CORPS}, **{f"cat_{c}": True for c in CATEGORIES}}


def _reset():
    st.session_state.update(FILTER_DEFAULTS)


# 필터는 '리포트'에서만 보인다. Streamlit 은 그 회차에 그리지 않은 위젯의 값을 지우므로,
#   값을 위젯과 따로 '_filters' 에 저장해 두고 리포트로 돌아오면 그 값으로 위젯을 다시 채운다 (다른 화면에 다녀와도 유지)
saved = st.session_state.setdefault("_filters", dict(FILTER_DEFAULTS))


with st.sidebar:
    ui.brand()   # 로고 'AA 팩트체커' · 설명 (style.py, preview_b.py 와 같은 모양)
    menu = st.radio("화면", MENU, label_visibility="collapsed", format_func=lambda p: f"{MENU_ICON[p]}  {p}",
                    key="page_sel")   # key: 기획안 검토의 '챗봇에 물어보기'가 화면을 챗봇으로 바꿀 수 있게 (#46)
    ui.menu_tips("page_sel", [MENU_TIP[p] for p in MENU])   # 마우스를 대면 메뉴 설명
    chat = menu != "리포트"   # 필터를 쓰지 않는 화면 (공시 챗봇 · 기획안 검토)
    if not chat:   # 필터는 리포트에서만
        for k, v in saved.items():   # 다른 화면에 다녀오면 지워진 위젯 값을 저장해 둔 값으로 되살림
            if k not in st.session_state:
                st.session_state[k] = v
        # 구분선 → 굵은 제목 '필터' → 기간 · 기업 · 뉴스 카테고리 → 맨 아래 '필터 초기화'(＋ 새 기획안과 같은 옅은 파랑 버튼)
        st.divider()
        st.markdown("**필터**")
        period = st.date_input("기간", key="period", min_value=START, max_value=END)   # 기사 발행일 · 이벤트 발표일
        # 기업·뉴스 카테고리: '기간'과 같은 제목 글자 + 기간 입력칸과 같은 상자로 체크박스를 묶음 (style.py .sb-field · st-key-fbox_)
        # 체크박스: 체크한 것만 파랑 (style.py). 하나도 고르지 않으면 전체로 봄
        st.markdown('<div class="sb-field">기업</div>', unsafe_allow_html=True)
        with st.container(key="fbox_corp", gap=None):
            corps = [c for c in CORPS if st.checkbox(c, key=f"corp_{c}")]
        st.caption("뉴스는 제목에 기업명이 나온 기사로 좁힙니다 (제목 기준 137건)")
        st.markdown('<div class="sb-field">뉴스 카테고리</div>', unsafe_allow_html=True)
        with st.container(key="fbox_cat", gap=None):
            categories = [c for c in CATEGORIES if st.checkbox(c, key=f"cat_{c}")]
        st.caption("'기사량·급증', '화제 이슈' 화면에만 필터가 적용됩니다")
        st.button("필터 초기화", on_click=_reset, width="stretch", type="primary", key="filter_reset", icon=":material/restart_alt:")
        saved.update({k: st.session_state[k] for k in FILTER_DEFAULTS})   # 이번 값을 저장 (다음에 돌아올 때 씀)
    else:          # 챗봇·기획안 검토: 필터를 그리지 않고 저장해 둔 값만 (이 화면들은 필터를 쓰지 않음)
        period = saved["period"]
        corps = [c for c in CORPS if saved[f"corp_{c}"]]
        categories = [c for c in CATEGORIES if saved[f"cat_{c}"]]
    if chat:   # 아래에 챗봇·기획안 검토가 기록(새 대화·지난 대화 / 새 기획안·지난 기획안)을 붙인다
        st.divider()


def _keep_report_page():
    """리포트 페이지 버튼을 다시 눌러 선택이 풀리면(None) 직전 페이지로 되돌린다 — 빈 화면이 되지 않게"""
    if st.session_state.get("report_page") is None:
        st.session_state["report_page"] = st.session_state.get("_report_last", REPORT_PAGES[0])
    st.session_state["_report_last"] = st.session_state["report_page"]


# '리포트' 메뉴: 맨 위에서 Q1·Q2·Q3 중 하나를 고른다 (탭과 달리 고른 페이지만 그려서 빠름). 아래 코드는 page 로 분기
if menu == "리포트":
    st.markdown('<div class="report-nav-marker"></div>', unsafe_allow_html=True)
    if "report_page" not in st.session_state:   # 다른 화면에 다녀오면 지워진 값 → 직전 페이지로 (필터와 같은 이유)
        st.session_state["report_page"] = st.session_state.get("_report_last", REPORT_PAGES[0])
    page = st.segmented_control("리포트 페이지", REPORT_PAGES, key="report_page",
                                format_func=lambda p: f"{PAGE_ICON[p]}  {p}", label_visibility="collapsed",
                                on_change=_keep_report_page) or st.session_state.get("_report_last", REPORT_PAGES[0])
else:
    page = menu

if not (isinstance(period, tuple) and len(period) == 2):
    st.info("기간의 시작일과 끝일을 모두 골라 주십시오.")
    st.stop()
corps, categories = corps or CORPS, categories or CATEGORIES


# ── 상단 결론 카드 (공통 필터 반영) ─────────────────────────────────
# 문장은 dashboard/conclusion.py: 뉴스의 말(A) news_rows · 공시의 실행·판단(B) disclosure_rows — 필터(기간·기업·카테고리)로 다시 계산
#   뉴스는 기업·카테고리만 거른 전체 기간 기사를 넘긴다 (급증 기준선은 Q1 처럼 전체 기간, 문장은 선택 기간)
#   공시는 기업·기간(이벤트 발표일)으로 거른다. 카테고리는 뉴스에만 있는 값
def conclusion(news) -> None:
    from dashboard import conclusion as cc
    rows = (cc.news_rows(news, period, corps) if news is not None else [("뉴스", "뉴스 데이터가 이 DB 에 없어 표시하지 않습니다.")]) \
        + cc.disclosure_rows(corps, period)
    # 섹션(뉴스·공시·판단) 사이를 조금 띄워 구분 (.45rem → .85rem)
    body = "".join(f'<div style="display:flex;gap:.7rem;align-items:baseline;margin:.85rem 0">'
                   f'<span style="min-width:3.6rem">{ui.pill(k, "blue")}</span><span style="line-height:1.6">{v}</span></div>'
                   for k, v in rows)
    # 강조 카드 안에 접기 메뉴 — 제목 줄을 누르면 열고 닫힘 (처음엔 펼침). 범위 문구는 제목 옆 회색 (style.py)
    with st.container(key="hl_conclusion"), \
            st.expander(f"리포트 요약 :gray[{cc.scope_text(period, corps, categories, CATEGORIES)}]", expanded=True,
                        icon=":material/star:"):
        st.markdown(body, unsafe_allow_html=True)


# 기업·카테고리로 거른 전체 기간 뉴스 — 결론 카드와 Q1·Q2 가 같이 쓴다 (뉴스 결과가 없는 DB 면 None)
news_all, news_err = None, None
if not chat:
    try:
        news_all = dl.filter_news(dl.news(), (START, END), corps, categories, CORPS)
    except Exception as e:  # 뉴스 결과(news_preprocessed.csv·llm_results)가 없는 DB
        news_err = e

if not chat:
    conclusion(news_all)

# ── 화면 ─────────────────────────────────────────────────────────────
if page in ("Q1 기사량·급증", "Q2 화제 이슈"):
    news = news_all   # 기업·카테고리 (기간은 화면에서), 위에서 결론 카드와 같이 읽음
    if news is None:  # 뉴스 결과(news_preprocessed.csv·llm_results)가 없는 DB — Q3·챗봇과 같게 안내 문구 (B 리뷰, PR #22)
        st.warning(f"이 화면에 필요한 뉴스 데이터가 없습니다 — `analysis/handoff.py import` 로 A 의 뉴스 결과를 넣어 주십시오. "
                   f"({type(news_err).__name__}: {news_err})")
        st.stop()
    if set(corps) != set(CORPS):
        n = int(((news["date"] >= period[0]) & (news["date"] <= period[1])).sum())
        st.caption(f"기업 필터: 제목에 {', '.join(corps)} 이(가) 나온 기사 {n:,}건만 보고 있습니다.")
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
                st.caption("카테고리 필터는 뉴스 화면에만 적용됩니다.")
            st.markdown('<div class="q3page-marker"></div>', unsafe_allow_html=True)   # Q3 글자·지표 스타일 (style.py)
            q3_disclosure.render(corps, period)
        else:
            # 챗봇 화면 전용 스타일 표시 (style.py: stMain:has(.chatpage-marker)). 컨테이너로 감싸면 입력창 하단 고정이 풀려서 표시만 둠
            st.markdown('<div class="chatpage-marker"></div>', unsafe_allow_html=True)
            qa.render()
    except Exception as e:  # 공시 테이블·임베딩이 없는 DB (예: 뉴스만 적재한 로컬)
        st.warning(f"이 화면에 필요한 공시 데이터가 이 DB 에 없습니다. ({type(e).__name__}: {e})")

# 사이드바 맨 아래: 데이터 설명 (챗봇·기획안 검토의 기록 목록 · 리포트 필터 아래)
with st.sidebar:
    st.divider()
    st.markdown('<div class="sb-desc sb-foot">데이터: 제공 뉴스 6,450건 · DART 정기공시 5개사 · 앵커 이벤트 28개 '
                '(2023-10-19 ~ 2026-10-19)</div>', unsafe_allow_html=True)   # 맨 위 설명(sb-desc)과 같은 글자 크기·글꼴
