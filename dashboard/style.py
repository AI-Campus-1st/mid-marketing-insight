"""
[공동·D6 design_test] 화면 공통 스타일 — docs/design/references 의 카드형 UI

- inject(): 전체 CSS (카드 그림자·강조 카드·사이드바 메뉴·KPI·접기 메뉴). app.py 맨 위에서 한 번
- 색은 반투명 값(rgba)으로 써서 라이트·다크 테마 모두에서 어울리게 (테마 색 자체는 .streamlit/config.toml)
- 컴포넌트: section() 아이콘 제목, pill() 배지, kpis() KPI 카드 줄, timeline() 세로 단계, mini_cards() 작은 카드 그리드
"""
import html

import streamlit as st

BLUE = "#2a78d6"

FONT = "Pretendard, 'Pretendard Variable', -apple-system, 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif"
PRETENDARD_CSS = "https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css"   # 굵기 9종 전부

CSS = """
<style>
@import url("PRETENDARD_URL");
/* 글꼴: 얇은 글씨·굵은 글씨·제목·버튼·입력칸·차트 글자까지 모두 Pretendard 한 가지 (굵기만 다름) */
/* 글자 요소에만 지정 — 전체(*)에 걸면 아이콘 글꼴(Material Symbols)까지 덮어 'forum' 같은 글자로 깨짐 */
html, body, .stApp, .stApp p, .stApp li, .stApp label, .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6,
.stApp [data-testid="stMarkdownContainer"], .stApp [data-testid="stCaptionContainer"], .stApp [data-testid="stMetricValue"],
.stApp table, .stApp td, .stApp th, button, input, textarea, select, .js-plotly-plot text {
  font-family: FONT_STACK !important;
}
/* 본문 폭·여백 */
.block-container { padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1280px; }

/* 카드 (container key 가 card_ 로 시작, border=True): 둥글게 + 옅은 그림자
   Streamlit 1.51 은 테두리 container 가 별도 wrapper 없이 stVerticalBlock 자체라 key 클래스로 잡는다 */
div[class*="st-key-card_"] {
  border-radius: 16px !important;
  box-shadow: 0 1px 2px rgba(16, 24, 40, .04), 0 4px 16px rgba(16, 24, 40, .05);
}
/* 강조 카드 (container key 가 hl_ 로 시작): 옅은 파랑 바탕 + 파랑 테두리 */
div[class*="st-key-hl_"] {
  background: #f2f7fd;
  border: 1px solid #b4d0f1;
  border-radius: 16px; padding: 1.1rem 1.3rem;
}
/* KPI 카드 */
div[class*="st-key-kpi_"] {
  background: #f7f8f9; border: 1px solid #ebecef;
  border-radius: 14px; padding: .9rem 1.1rem;
}
.kpi-label { font-size: .82rem; color: #5b6575; margin-bottom: .15rem; }
.kpi-value { font-size: 1.65rem; font-weight: 800; letter-spacing: -.02em; line-height: 1.25; }
.kpi-note  { font-size: .78rem; color: #8a93a3; margin-top: .15rem; }

/* 섹션 제목 + 배지 */
.sec { display: flex; align-items: center; gap: .55rem; margin: 1.6rem 0 .6rem; }
.sec-icon { width: 30px; height: 30px; border-radius: 9px; display: inline-flex; align-items: center; justify-content: center;
            background: #e5effa; color: #2a78d6; font-size: 1rem; }
.sec-title { font-size: 1.12rem; font-weight: 800; letter-spacing: -.01em; }
.sec-sub { margin-left: auto; font-size: .8rem; color: #8a93a3; font-style: italic; }
.pill { display: inline-block; padding: .12rem .55rem; border-radius: 999px; font-size: .72rem; font-weight: 700;
        letter-spacing: .02em; vertical-align: middle; }
.pill-gray { background: #ebecef; color: #5b6575; }
.pill-blue { background: #e5effa; color: #2a78d6; }
.pill-line { border: 1.5px solid #2a78d6; color: #2a78d6; background: transparent; }
.pill-orange { background: #fceae3; color: #d95926; }

/* 페이지 제목 */
.page-title { font-size: 2.15rem; font-weight: 800; letter-spacing: -.02em; margin: .2rem 0 .1rem; }
.page-sub { color: #5b6575; margin-bottom: 1rem; }
.lead { font-size: 1.12rem; font-weight: 700; line-height: 1.5; margin: .4rem 0 1rem; }
.lead b { color: #2a78d6; }

/* 세로 타임라인 (레퍼런스 로드맵) */
.tl { list-style: none; padding: 0; margin: .3rem 0 0; }
.tl li { position: relative; padding: 0 0 1.05rem 2.4rem; }
.tl li::before { content: ""; position: absolute; left: 13px; top: 28px; bottom: -2px; width: 2px; background: #dfe2e7; }
.tl li:last-child::before { display: none; }
.tl .dot { position: absolute; left: 0; top: 0; width: 28px; height: 28px; border-radius: 50%; display: flex;
           align-items: center; justify-content: center; font-size: .85rem; font-weight: 800; }
.tl .done .dot { background: #2a78d6; color: #fff; }
.tl .now .dot { background: transparent; border: 2px solid #2a78d6; color: #2a78d6; }
.tl .next .dot { background: #e8eaee; color: #8c95a8; }
.tl .t { font-weight: 800; }
.tl .now .t { color: #2a78d6; }
.tl .d { font-size: .82rem; color: #5b6575; }

/* 작은 카드 그리드 (레퍼런스 2) */
.mc-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: .75rem; }
.mc { background: #f2f7fd; border: 1px solid #e1ecf9; border-radius: 14px;
      padding: .85rem .95rem; text-decoration: none !important; color: inherit !important; display: block; }
.mc:hover { border-color: #94bcea; }
.mc-tag { font-size: .74rem; font-weight: 700; color: #2a78d6; margin-bottom: .35rem; }
.mc-title { font-weight: 700; line-height: 1.4; font-size: .92rem; display: -webkit-box; -webkit-line-clamp: 3;
            -webkit-box-orient: vertical; overflow: hidden; }
.mc-meta { font-size: .76rem; color: #8a93a3; margin-top: .45rem; }

/* 사이드바: 메뉴형 라디오 + 소제목 */
section[data-testid="stSidebar"] .sb-label { font-size: .78rem; font-weight: 700; color: #8a93a3; letter-spacing: .03em; margin: .4rem 0 .3rem; }
section[data-testid="stSidebar"] div[role="radiogroup"] { gap: .15rem; }
section[data-testid="stSidebar"] div[role="radiogroup"] > label {
  padding: .45rem .6rem; border-radius: 10px; width: 100%; margin: 0;
}
section[data-testid="stSidebar"] div[role="radiogroup"] > label:hover { background: #f2f3f5; }
section[data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) {
  background: #eaf2fb; font-weight: 700;
}
.sb-brand { display: flex; gap: .55rem; align-items: center; font-weight: 800; font-size: 1.05rem; margin-bottom: .2rem; }
.sb-brand .logo { width: 28px; height: 28px; border-radius: 8px; background: #2a78d6; color: #fff; display: flex;
                  align-items: center; justify-content: center; font-size: .85rem; }

/* 접기 메뉴·표 */
div[data-testid="stExpander"] details { border-radius: 14px; }
div[data-testid="stExpander"] summary { font-weight: 600; }

/* ── 사이드바: 화면 메뉴 (라디오 동그라미 숨기고 메뉴 항목처럼) ── */
section[data-testid="stSidebar"] div[role="radiogroup"] label[data-baseweb="radio"] > div:first-child { display: none; }
section[data-testid="stSidebar"] div[role="radiogroup"] > label { padding: .55rem .75rem; font-size: .95rem; }
section[data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) { color: #2a78d6; }
section[data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) p { color: #2a78d6; font-weight: 700; }

/* 사이드바: 필터 카드 (접기 메뉴를 레퍼런스 '기본정보' 카드처럼) */
section[data-testid="stSidebar"] div[data-testid="stExpander"] details {
  border: 1px solid #e3e5ea; border-radius: 14px;
  box-shadow: 0 1px 2px rgba(16, 24, 40, .04), 0 4px 14px rgba(16, 24, 40, .05);
}
section[data-testid="stSidebar"] div[data-testid="stExpander"] summary { font-weight: 700; padding-top: .7rem; padding-bottom: .7rem; }
section[data-testid="stSidebar"] label[data-testid="stWidgetLabel"] p { font-size: .8rem; font-weight: 700; color: #5b6575; }
section[data-testid="stSidebar"] div[data-testid="stDateInput"] input { font-size: .85rem; }
/* 선택 칩: 진한 파랑 → 옅은 파랑 칩 */
span[data-baseweb="tag"] { background: #e5effa !important; border-radius: 8px !important; }
span[data-baseweb="tag"] span, span[data-baseweb="tag"] svg { color: #2a78d6 !important; fill: #2a78d6 !important; font-weight: 600; }
/* 필터 초기화: 파랑 테두리 버튼 */
div.st-key-reset_filters button { border: 1.5px solid #2a78d6; color: #2a78d6; background: transparent; font-weight: 700; }
div.st-key-reset_filters button:hover { background: #eef4fc; }
/* 챗봇 대화 기록 목록 (qa.py 의 hist_ 버튼): 목록 항목처럼 */
div[class*="st-key-hist_"] button { border: none; background: transparent; justify-content: flex-start; text-align: left;
  padding: .5rem .65rem; border-radius: 10px; }
div[class*="st-key-hist_"] button:hover { background: #f2f3f5; }
div[class*="st-key-hist_"] button p { font-size: .85rem; line-height: 1.35; }

/* ── 챗봇 화면 (app.py 가 넣는 .chatpage-marker 가 있는 본문) ── */
.stMain:has(.chatpage-marker) h1 { font-size: 2.15rem !important; font-weight: 800 !important; letter-spacing: -.02em; padding-bottom: .1rem; }
.stMain:has(.chatpage-marker) h5 { font-size: 1.05rem; font-weight: 800; margin-top: 1rem; }
/* 안내·답변(assistant) = 결론 카드와 같은 강조 카드, 질문(user) = 회색 카드 */
.stMain:has(.chatpage-marker) div[data-testid="stChatMessage"] {
  background: #f2f7fd; border: 1px solid #b4d0f1; border-radius: 16px; padding: 1rem 1.15rem;
}
.stMain:has(.chatpage-marker) div[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
  background: #f6f7f8; border-color: #e5e7ec;
}
/* 노란 로봇 아이콘 → 파랑 아이콘 (섹션 아이콘과 같은 모양) */
.stMain:has(.chatpage-marker) [data-testid="stChatMessageAvatarAssistant"] {
  background: #e1ecf9 !important; color: #2a78d6 !important; border-radius: 9px;
}
.stMain:has(.chatpage-marker) [data-testid="stChatMessageAvatarAssistant"] svg { fill: #2a78d6 !important; color: #2a78d6 !important; }
.stMain:has(.chatpage-marker) [data-testid="stChatMessageAvatarUser"] { border-radius: 9px; }
/* 예시 질문 카드: 옅은 파랑 카드 + 파랑 태그 + 굵은 질문 */
.stMain:has(.chatpage-marker) div[data-testid="stVerticalBlock"]:has(> div[class*="st-key-ex_"]) {
  background: #f4f8fd; border-color: #d9e7f7 !important; box-shadow: none;
}
.stMain:has(.chatpage-marker) div[data-testid="stVerticalBlock"]:has(> div[class*="st-key-ex_"]):hover { border-color: #94bcea !important; }
.stMain:has(.chatpage-marker) div[data-testid="stVerticalBlock"]:has(> div[class*="st-key-ex_"]) div[data-testid="stCaptionContainer"] p {
  color: #2a78d6; font-weight: 700; font-size: .78rem; opacity: 1;
}
.stMain:has(.chatpage-marker) div[data-testid="stVerticalBlock"]:has(> div[class*="st-key-ex_"]) button {
  border: none; background: transparent; justify-content: flex-start; text-align: left; padding: .2rem 0; min-height: 0;
}
.stMain:has(.chatpage-marker) div[data-testid="stVerticalBlock"]:has(> div[class*="st-key-ex_"]) button p { font-weight: 700; font-size: .95rem; }
/* 입력창: 둥근 알약 + 그림자 */
div[data-testid="stChatInput"] > div { border-radius: 999px !important; border: 1.5px solid #b4d0f1 !important;
  box-shadow: 0 4px 16px rgba(16, 24, 40, .06); }

/* 사이드바 '＋ 새 대화' (qa.py 의 primary 버튼): 꽉 찬 파랑 → 옅은 파랑 + 파랑 테두리 */
section[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"] {
  background: #eaf2fb !important; color: #2a78d6 !important; border: 1.5px solid #aac9ef !important;
  font-weight: 700; border-radius: .6rem;
}
section[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"]:hover { background: #d9e7f8 !important; }
section[data-testid="stSidebar"] button[data-testid="stBaseButton-primary"] p { color: #2a78d6 !important; font-weight: 700; }

/* ── Q3 화면 (app.py 가 넣는 .q3page-marker): 다른 화면과 같은 글자 체계 ── */
.stMain:has(.q3page-marker) h2 { font-size: 2.15rem !important; font-weight: 800 !important; letter-spacing: -.02em; padding: .2rem 0 .3rem; }
.stMain:has(.q3page-marker) h4 { font-size: 1.12rem !important; font-weight: 700 !important; line-height: 1.5; margin: .5rem 0 .9rem; padding: 0; }
.stMain:has(.q3page-marker) h4 strong { color: #2a78d6; }
.stMain:has(.q3page-marker) h5 { font-size: 1.12rem !important; font-weight: 800 !important; display: flex; align-items: center;
  gap: .55rem; margin: 1.4rem 0 .5rem; padding: 0; }
.stMain:has(.q3page-marker) h5::before { content: "▤"; width: 30px; height: 30px; border-radius: 9px; display: inline-flex;
  align-items: center; justify-content: center; background: #e5effa; color: #2a78d6; font-size: 1rem; flex: none; }
/* st.metric → KPI 카드 (Q1·Q2 의 kpis() 와 같은 모양) */
.stMain:has(.q3page-marker) div[data-testid="stMetric"] {
  background: #f7f8f9; border: 1px solid #ebecef; border-radius: 14px; padding: .9rem 1.1rem;
}
.stMain:has(.q3page-marker) div[data-testid="stMetricLabel"] p { font-size: .82rem; color: #5b6575; }
.stMain:has(.q3page-marker) div[data-testid="stMetricValue"] { font-size: 1.65rem; font-weight: 800; letter-spacing: -.02em; }

/* ── 상호작용 요소는 모두 파랑 (테마 primaryColor 와 같은 값 — 테마를 새로고침 전에도 빨강이 보이지 않게) ── */
button[role="tab"][aria-selected="true"] p { color: #2a78d6 !important; font-weight: 700; }
button[role="tab"]:hover p { color: #2a78d6 !important; }
div[data-baseweb="tab-highlight"] { background-color: #2a78d6 !important; }
label[data-baseweb="checkbox"] > span:first-child { border-color: #c4cad4 !important; background-color: #ffffff !important; }   /* 체크 안 됨 = 회색 테두리 */
label[data-baseweb="checkbox"]:has(input:checked) > span:first-child { background-color: #2a78d6 !important; border-color: #2a78d6 !important; }
div[data-baseweb="select"] > div:focus-within, div[data-baseweb="input"]:focus-within { border-color: #2a78d6 !important; }
a, a:visited { color: #2a78d6; }

/* ── Q3 하위 탭: 다른 화면과 같은 요소 모양 ── */
/* 본문 가로 라디오(이벤트 타임라인 기업 선택) → 알약 버튼 */
.stMain:has(.q3page-marker) div[role="radiogroup"] { gap: .4rem; flex-wrap: wrap; }
.stMain:has(.q3page-marker) div[role="radiogroup"] label[data-baseweb="radio"] > div:first-child { display: none; }
.stMain:has(.q3page-marker) div[role="radiogroup"] > label {
  padding: .35rem .9rem; border-radius: 999px; border: 1px solid #d9dce2; margin: 0;
}
.stMain:has(.q3page-marker) div[role="radiogroup"] > label:hover { border-color: #94bcea; }
.stMain:has(.q3page-marker) div[role="radiogroup"] > label:has(input:checked) {
  background: #eaf2fb; border-color: #2a78d6;
}
.stMain:has(.q3page-marker) div[role="radiogroup"] > label:has(input:checked) p { color: #2a78d6; font-weight: 700; }
/* 본문 보조 버튼(이벤트 표 '초기화' 등) → 필터 초기화와 같은 파랑 테두리 */
.stMain:has(.q3page-marker) button[data-testid="stBaseButton-secondary"] {
  border: 1.5px solid #2a78d6; color: #2a78d6; background: transparent; font-weight: 700;
}
.stMain:has(.q3page-marker) button[data-testid="stBaseButton-secondary"]:hover { background: #eef4fc; }
.stMain:has(.q3page-marker) button[data-testid="stBaseButton-secondary"] p { color: #2a78d6; font-weight: 700; }
/* 위젯 라벨(기업, 표에서 볼 기업) → 작은 회색 굵은 글씨 (사이드바와 같게) */
.stMain:has(.q3page-marker) label[data-testid="stWidgetLabel"] p { font-size: .8rem; font-weight: 700; color: #5b6575; }
/* 표: 둥근 카드 */
.stMain div[data-testid="stDataFrame"] { border-radius: 14px; overflow: hidden; }

/* 결론 카드 안 접기 메뉴: 테두리 없이 카드에 녹이고, 제목 줄은 파랑 굵은 글씨 + 파랑 아이콘 */
div[class*="st-key-hl_"] { padding: .35rem .6rem; }
div[class*="st-key-hl_"] div[data-testid="stExpander"] details { border: none !important; background: transparent !important; box-shadow: none !important; }
div[class*="st-key-hl_"] div[data-testid="stExpander"] summary { padding: .6rem .7rem; border-radius: 12px; background: transparent !important; }
div[class*="st-key-hl_"] div[data-testid="stExpander"] summary:hover { background: #e3eefb; }
div[class*="st-key-hl_"] div[data-testid="stExpander"] summary p { color: #2a78d6 !important; font-weight: 800 !important; font-size: 1.12rem; }
div[class*="st-key-hl_"] div[data-testid="stExpander"] summary [data-testid="stIconMaterial"],
div[class*="st-key-hl_"] div[data-testid="stExpander"] summary span[data-testid="stExpanderIcon"] { color: #2a78d6 !important; }
/* 결론 제목 옆 범위 문구 (:gray[...]) — 제목 파랑 규칙을 덮어 작은 회색으로 */
div[class*="st-key-hl_"] div[data-testid="stExpander"] summary p span { color: #8a93a3 !important; font-weight: 500 !important;
  font-size: .82rem !important; margin-left: .5rem; }
div[class*="st-key-hl_"] div[data-testid="stExpanderDetails"] { padding-top: 0; border-top: none !important; }
</style>
"""


def inject() -> None:
    st.markdown(CSS.replace("PRETENDARD_URL", PRETENDARD_CSS).replace("FONT_STACK", FONT), unsafe_allow_html=True)


def esc(s) -> str:
    return html.escape(str(s))


def pill(text: str, kind: str = "gray") -> str:
    return f'<span class="pill pill-{kind}">{esc(text)}</span>'


def page_title(title: str, sub: str = "") -> None:
    st.markdown(f'<div class="page-title">{esc(title)}</div>' + (f'<div class="page-sub">{esc(sub)}</div>' if sub else ""),
                unsafe_allow_html=True)


def lead(html_text: str) -> None:
    """한 줄 결론 (html_text 안의 <b> 는 파랑 강조). 호출하는 쪽에서 값은 esc() 로"""
    st.markdown(f'<div class="lead">{html_text}</div>', unsafe_allow_html=True)


def section(title: str, icon: str = "◆", badge: str | None = None, badge_kind: str = "gray", sub: str | None = None) -> None:
    b = f" {pill(badge, badge_kind)}" if badge else ""
    s = f'<span class="sec-sub">{esc(sub)}</span>' if sub else ""
    st.markdown(f'<div class="sec"><span class="sec-icon">{icon}</span><span class="sec-title">{esc(title)}</span>{b}{s}</div>',
                unsafe_allow_html=True)


def kpis(items: list[tuple[str, str, str]], key: str) -> None:
    """KPI 카드 한 줄: (라벨, 값, 아래 작은 글씨)"""
    for i, (col, (label, value, note)) in enumerate(zip(st.columns(len(items)), items)):
        with col, st.container(key=f"kpi_{key}_{i}"):
            st.markdown(f'<div class="kpi-label">{esc(label)}</div><div class="kpi-value">{esc(value)}</div>'
                        + (f'<div class="kpi-note">{esc(note)}</div>' if note else ""), unsafe_allow_html=True)


def timeline(steps: list[tuple[str, str, str]]) -> None:
    """세로 단계: (상태 done/now/next, 제목, 설명)"""
    mark = {"done": "✓", "now": "•", "next": ""}
    lis = "".join(f'<li class="{s}"><span class="dot">{mark[s]}</span><div class="t">{esc(t)}</div><div class="d">{esc(d)}</div></li>'
                  for s, t, d in steps)
    st.markdown(f'<ul class="tl">{lis}</ul>', unsafe_allow_html=True)


def mini_cards(cards: list[dict]) -> None:
    """작은 카드 그리드: dict(tag, title, meta, url)"""
    items = "".join(
        f'<a class="mc" href="{esc(c.get("url") or "#")}" target="_blank">'
        f'<div class="mc-tag">{esc(c["tag"])}</div><div class="mc-title">{esc(c["title"])}</div>'
        f'<div class="mc-meta">{esc(c.get("meta", ""))}</div></a>' for c in cards)
    st.markdown(f'<div class="mc-grid">{items}</div>', unsafe_allow_html=True)
