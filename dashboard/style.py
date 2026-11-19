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
div[class*="st-key-kpi_"] [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }   /* Streamlit 기본 -1rem 때문에 카드 아래 여백이 사라짐 → 위와 같게 */

/* 섹션 제목 + 배지 */
.sec { display: flex; align-items: center; gap: .55rem; margin: 1.6rem 0 .6rem; }
.sec-icon { width: 30px; height: 30px; border-radius: 9px; display: inline-flex; align-items: center; justify-content: center;
            background: #e5effa; color: #2a78d6; font-size: 1rem; }
.sec-title { font-size: 1.12rem; font-weight: 800; letter-spacing: -.01em; }
.sec-sub { margin-left: auto; font-size: .8rem; color: #8a93a3; font-style: italic; }
/* 리포트 Q1 카드(월별 기사량 · 급증 타임라인) 안 제목: 카드 위 여백이 커 보여서 줄임 */
.st-key-card_q1_chart .sec, .st-key-card_q1_timeline .sec, .st-key-card_q2_trend .sec, .st-key-card_q2_rank .sec { margin-top: .6rem; }
/* 급증 타임라인 내용(제목 제외)을 남은 칸의 위아래 가운데로 */
.st-key-card_q1_timeline > div:has(.tl) { flex: 1 1 auto; display: flex; flex-direction: column; justify-content: center; margin-top: -.5rem; }   /* -.5rem: 제목과의 칸 간격(1rem) 때문에 아래로 쏠리는 만큼 보정 → 위아래 여백 같게 */
.st-key-card_q1_timeline .tl { margin: 0; }
.st-key-card_q1_timeline .tl li:last-child { padding-bottom: 0; }   /* 마지막 항목 아래 빈칸을 빼야 실제 글자 기준으로 가운데 */
.st-key-card_q1_timeline div:has(> .tl) { margin-bottom: 0 !important; }   /* Streamlit 기본 -1rem 이 가운데를 아래로 끌지 않게 */
/* 급증 타임라인 카드를 옆 월별 기사량 카드와 같은 높이로 (내용은 그대로, 상자만 칸 높이까지 늘림) */
div[data-testid="stLayoutWrapper"]:has(> .st-key-card_q1_timeline) { flex: 1 1 auto; }
.st-key-card_q1_timeline { flex: 1 1 auto; }
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
.lead.lead-lg { font-size: 1.45rem; letter-spacing: -.01em; word-break: keep-all; }   /* 페이지 제목(2.15rem)보다 작고 보통 결론(1.12rem)보다 크게. keep-all: 단어 중간에서 줄이 바뀌지 않게 */

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
.tl .tl-badge { display: inline-block; vertical-align: 2px; margin-left: .3rem; padding: .05rem .45rem; border-radius: 999px;
  background: #fff3c4; color: #8a6100; font-size: .72rem; font-weight: 700; }   /* 급증 배수 (×2.3) 노랑 배지 */

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
.sb-brand { display: flex; gap: .65rem; align-items: center; font-weight: 800; font-size: 1.4rem; letter-spacing: -.02em; margin: 11px 0 .35rem; }
/*   위 11px: 사이드바 로고 줄을 오른쪽 화면 제목(h1, 챗봇·기획안 검토) 줄과 같은 높이로 — 이름 글자 위끝 = 제목 글자 위끝, 로고 가운데 = 제목 가운데 */
.sb-brand .logo { width: 38px; height: 38px; border-radius: 10px; background: #2a78d6; color: #fff; display: flex;
                  align-items: center; justify-content: center; font-size: 1.05rem; letter-spacing: -.02em; flex: 0 0 38px; }
.sb-brand .name { display: flex; flex-direction: column; line-height: 1.2; }
.sb-brand .sub { font-size: .78rem; font-weight: 600; color: #6b7686; letter-spacing: .01em; }
.sb-desc { font-size: .82rem; line-height: 1.45; color: #6b7686; margin: .9rem 0 1.1rem; }   /* 이름 밑 설명: 행간 좁게, 이름과 띄움 */
.sb-desc.sb-foot { margin: 0; }   /* 맨 아래 데이터 설명: 맨 위 설명과 같은 글자, 여백만 없앰 */
/* 사이드바의 작은 회색 설명(st.caption — 필터 '기업'·'뉴스 카테고리' 밑 설명 등): 맨 위 설명(sb-desc)과 같은 글자 크기·행간. 색은 Streamlit 기본 그대로 */
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"],
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p { font-size: .82rem; line-height: 1.45; font-weight: 400; }

/* 접기 메뉴·표 */
div[data-testid="stExpander"] details { border-radius: 14px; }
div[data-testid="stExpander"] summary { font-weight: 600; }
.st-key-q1_more div[data-testid="stExpander"] summary p, .st-key-q2_more div[data-testid="stExpander"] summary p { font-weight: 400; letter-spacing: -.01em; color: #1f2733; }   /* 리포트 Q1 아래 접기 메뉴 제목: 카드 제목과 같은 자간·색, 굵게 하지 않음 */
.st-key-q2_more { margin-top: 1.5rem; }   /* 리포트 Q2: 우려 기사 카드와 아래 접기 메뉴 사이 띄움 */
.st-key-q2_concerns_more { margin-top: .6rem; }   /* 우려 기사 '더보기·접기' 버튼: 카드와 띄움 */
/* 버튼 글자: 위 설명 줄(st.caption)과 같은 스타일 — 14px · 보통 굵기 · 60% 흐린 본문색 */
.st-key-q2_concerns_more button p { font-size: .875rem !important; font-weight: 400; color: rgba(31, 39, 51, .6) !important; }
.st-key-q2_concerns_more button [data-testid="stIconMaterial"] { font-size: 1.1rem; color: rgba(31, 39, 51, .6); }

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
/* 리포트 필터: '기업'·'뉴스 카테고리' 제목은 '기간' 위젯 제목과 같게, 체크박스는 기간 입력칸과 같은 상자로 묶음 */
section[data-testid="stSidebar"] .sb-field { font-size: .8rem; font-weight: 700; color: #5b6575; line-height: 1.6; margin: .5rem 0 .25rem; }
section[data-testid="stSidebar"] div[class*="st-key-fbox_"],
section[data-testid="stSidebar"] div[data-testid="stDateInput"] div[data-baseweb="input"] {
  background: #f4f6fa !important; border: 1px solid #e4e8ef !important; border-radius: 12px !important; }   /* 기간 입력칸도 같은 색을 직접 지정 (테마 값에 맡기면 다르게 보일 수 있음) */
section[data-testid="stSidebar"] div[class*="st-key-fbox_"] { padding: .45rem .8rem; }
section[data-testid="stSidebar"] div[data-testid="stDateInput"] div[data-baseweb="input"] > div { background: transparent !important; }
section[data-testid="stSidebar"] div[class*="st-key-fbox_"] label[data-baseweb="checkbox"] { padding: .18rem 0; }
/*   상자 안 글자는 기간 입력칸과 같은 크기(.85rem), 체크 표시는 16px → 13px (체크 그림은 배경 이미지라 크기도 같이) */
section[data-testid="stSidebar"] div[class*="st-key-fbox_"] label[data-baseweb="checkbox"] p { font-size: .85rem !important; }
section[data-testid="stSidebar"] div[class*="st-key-fbox_"] label[data-baseweb="checkbox"] > span:first-child {
  width: 13px !important; height: 13px !important; min-width: 13px; border-radius: 4px !important; margin-top: 4px !important;
  background-size: 9px 9px !important; background-position: center !important; }
/* 선택 칩: 진한 파랑 → 옅은 파랑 칩 */
span[data-baseweb="tag"] { background: #e5effa !important; border-radius: 8px !important; }
span[data-baseweb="tag"] span, span[data-baseweb="tag"] svg { color: #2a78d6 !important; fill: #2a78d6 !important; font-weight: 600; }
/* 필터 초기화: 파랑 테두리 버튼 */
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
/* 사용자 아이콘 = 진한 파랑 바탕 + 흰 그림 (AI 아이콘은 옅은 파랑 바탕 + 파랑 그림 — 같은 파랑 계열이지만 채움으로 구분) */
.stMain:has(.chatpage-marker) [data-testid="stChatMessageAvatarUser"] {
  background: #2a78d6 !important; color: #ffffff !important; border-radius: 9px;
}
.stMain:has(.chatpage-marker) [data-testid="stChatMessageAvatarUser"] svg { fill: #ffffff !important; color: #ffffff !important; }
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
div[data-baseweb="select"] > div:focus-within, div[data-baseweb="input"]:focus-within,
div[data-baseweb="textarea"]:focus-within { border-color: #2a78d6 !important; }   /* textarea: 기획안 검토 '직접 입력' 칸 */
a, a:visited { color: #2a78d6; }
/* 본문의 강조(primary) 버튼은 모두 파랑 — Streamlit 기본 강조색(빨강)이 보이지 않게 (기획안 검토 '검토 시작' 등, 사이드바는 위 옅은 파랑 규칙) */
.stMain button[data-testid="stBaseButton-primary"] { background: #2a78d6 !important; border-color: #2a78d6 !important; color: #ffffff !important; }
.stMain button[data-testid="stBaseButton-primary"]:hover { background: #1f66bd !important; border-color: #1f66bd !important; }
.stMain button[data-testid="stBaseButton-primary"] p { color: #ffffff !important; }
/* 리포트 페이지 선택 버튼(segmented control) · 탭 · 제목 옆 링크 — 테마가 늦게 오거나 기본 테마로 바뀌어도 빨강(#ff4b4b)이 보이지 않게
   (강조색을 빨강으로 띄운 서버에서 화면마다 빨간 요소를 찾아 확인) */
button[data-testid="stBaseButton-segmented_controlActive"] { background: #eef4fc !important; border-color: #2a78d6 !important; color: #2a78d6 !important; }
button[data-testid="stBaseButton-segmented_controlActive"] * { color: #2a78d6 !important; fill: #2a78d6 !important; }
button[data-testid="stBaseButton-segmented_controlActive"]:hover { background: #e0ecfa !important; }
button[data-testid="stBaseButton-segmented_control"]:hover { border-color: #2a78d6 !important; color: #2a78d6 !important; }
button[data-testid="stBaseButton-segmented_control"]:hover * { color: #2a78d6 !important; fill: #2a78d6 !important; }
button[data-testid^="stBaseButton-segmented"]:focus-visible, button[data-testid="stTab"]:focus-visible { outline-color: #2a78d6 !important; }
button[data-testid="stTab"]:hover, button[data-testid="stTab"]:hover *,
button[data-testid="stTab"][aria-selected="true"], button[data-testid="stTab"][aria-selected="true"] * { color: #2a78d6 !important; }
[data-testid="stHeaderActionElements"] a, [data-testid="stHeaderActionElements"] a * { color: #8592a3 !important; border-color: transparent !important; }
[data-testid="stHeaderActionElements"] a:hover, [data-testid="stHeaderActionElements"] a:hover * { color: #2a78d6 !important; }

/* ── Q3 하위 탭: 다른 화면과 같은 요소 모양 ── */
/* 본문 가로 라디오(이벤트 타임라인 기업 선택) → 알약 버튼 */
.stMain:has(.q3page-marker) div[role="radiogroup"] { gap: .4rem; flex-wrap: wrap; }
/* 리포트 맨 위 선택 버튼(segmented control)도 radiogroup 이라 위 Q3 규칙이 걸려 버튼 사이가 6px 벌어짐 → 붙여 둔다 */
.stMain [data-testid="stButtonGroup"] div[role="radiogroup"] { gap: 0 !important; flex-wrap: nowrap; }
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
/* 지난 대화·지난 기획안: 지금 보고 있는 항목 = 둥근 파란 선 테두리 (전에는 이름 앞 '▶'). key 가 hist_cur_ · hist_pr_cur_ 로 시작 */
div[class*="st-key-hist_cur_"] button, div[class*="st-key-hist_pr_cur_"] button {
  border: 1.5px solid #2a78d6 !important; border-radius: 10px !important; background: transparent !important;
  box-shadow: 0 1px 2px rgba(16, 24, 40, .06), 0 3px 10px rgba(42, 120, 214, .14) !important; }   /* 테두리만 + 옅은 그림자, 채우지 않음 */
/* 마우스를 대면 나오는 말풍선(버튼 help — 지난 대화·지난 기획안 제목 등): 메뉴 설명(menu_tips)과 같은 색·글꼴·여백·그림자 */
div[data-baseweb="tooltip"] { box-shadow: none !important; background: transparent !important; }
div[data-baseweb="tooltip"] > div { background: transparent !important; }
div[data-baseweb="tooltip"] [data-testid="stTooltipContent"] { background: TIP_BG; color: #1f2733; border: 1px solid #e4e8ef;
  border-radius: 8px; padding: .45rem .6rem; box-shadow: 0 4px 14px rgba(16, 24, 40, .10); }
div[data-baseweb="tooltip"] [data-testid="stTooltipContent"], div[data-baseweb="tooltip"] [data-testid="stTooltipContent"] * {
  font-size: .78rem !important; font-weight: 500; line-height: 1.4; color: #1f2733; margin: 0; }
</style>
"""


def inject() -> None:
    st.markdown(CSS.replace("PRETENDARD_URL", PRETENDARD_CSS).replace("FONT_STACK", FONT).replace("TIP_BG", TIP_BG), unsafe_allow_html=True)


BRAND = "AA 팩트체커"
BRAND_SUB = "Ad & AI Fact-Checker"
BRAND_DESC = "공시와 뉴스를 대조해 주요 광고사 5곳의 AI 적용·활용 단계를 확인할 수 있어요."


def brand(desc: str = BRAND_DESC) -> None:
    """사이드바 맨 위 로고·이름·설명 — app.py 와 B 미리보기(preview_b.py)가 같은 모양을 쓰도록 한 곳에"""
    st.markdown(f'<div class="sb-brand"><span class="logo">AA</span><span class="name">{esc(BRAND)}'
                f'<span class="sub">{esc(BRAND_SUB)}</span></span></div><div class="sb-desc">{esc(desc)}</div>',
                unsafe_allow_html=True)


TIP_BG = "#fafbfd"   # 말풍선 배경 (옅은 흰색) — 메뉴 설명(menu_tips)과 지난 대화·지난 기획안(hover_tips)·버튼 help 같은 색. CSS 의 stTooltipContent 규칙과 맞춤


def hover_tips(pairs: list[tuple[str, str]], shift: str = "-.25rem, 2.1rem") -> None:
    """마우스를 대면 그 항목 바로 아래, 왼쪽 맞춰 설명 말풍선 — pairs = [(CSS 선택자, 설명 글)]. shift = 말풍선이 그려지는 기본 자리에서 옮길 거리(가로, 세로)
    — 기본 자리는 항목이 flex(메뉴 라벨)면 항목 안 왼쪽 위, block(지난 대화 칸)이면 항목 바로 아래 왼쪽. 화면에서는 둘 다 항목 왼쪽 +8px 에 맞춤
    사이드바 메뉴(menu_tips)·지난 대화·지난 기획안이 같은 모양·같은 정렬로 (Streamlit 버튼 help 말풍선은 위치를 Streamlit 이 정해서 쓰지 않음)
    CSS ::after 로 그린다 — 글은 CSS 문자열이라 따옴표·역슬래시·꺾쇠·줄바꿈을 막는다"""
    if not pairs:
        return
    sels = [sel for sel, _ in pairs]
    rules = [", ".join(f"{x}::after" for x in sels) +
             # position: fixed + 위치(top·left) 없음 → 항목 자리에 붙어 그려지되 사이드바 밖으로 넘어가도 잘리지 않음 (한 줄로 길게)
             f' {{ position: fixed; width: max-content; white-space: nowrap; z-index: 1000; transform: translate({shift});'
             f' padding: .45rem .6rem; border-radius: 8px; background: {TIP_BG}; color: #1f2733; font-size: .78rem;'
             ' font-weight: 500; line-height: 1.4; border: 1px solid #e4e8ef; box-shadow: 0 4px 14px rgba(16, 24, 40, .10);'
             ' opacity: 0; visibility: hidden; pointer-events: none; transition: opacity .15s ease .35s, visibility 0s linear .35s; }',
             ", ".join(f"{x}:hover::after" for x in sels) + " { opacity: 1; visibility: visible; }"]
    for sel, tip in pairs:
        t = str(tip).replace("\\", "").replace('"', "'").replace("<", "").replace("\n", " ")
        rules.append(f'{sel}::after {{ content: "{t}"; }}')
    st.markdown("<style>" + "\n".join(rules) + "</style>", unsafe_allow_html=True)


def menu_tips(key: str, tips: list[str]) -> None:
    """사이드바 메뉴(st.radio, key=key)의 각 항목 설명 말풍선. tips 는 메뉴 순서대로 (st.radio 는 항목별 도움말이 없음)"""
    base = f'div.st-key-{key} div[role="radiogroup"] > label'
    hover_tips([(f"{base}:nth-child({i})", t) for i, t in enumerate(tips, 1)])


def esc(s) -> str:
    return html.escape(str(s))


def pill(text: str, kind: str = "gray") -> str:
    return f'<span class="pill pill-{kind}">{esc(text)}</span>'


def page_title(title: str, sub: str = "") -> None:
    st.markdown(f'<div class="page-title">{esc(title)}</div>' + (f'<div class="page-sub">{esc(sub)}</div>' if sub else ""),
                unsafe_allow_html=True)


def lead(html_text: str, big: bool = False) -> None:
    """한 줄 결론 (html_text 안의 <b> 는 파랑 강조). 호출하는 쪽에서 값은 esc() 로. big: 페이지 제목보다 한 단계 작은 큰 글자 (Q1)"""
    st.markdown(f'<div class="lead{" lead-lg" if big else ""}">{html_text}</div>', unsafe_allow_html=True)


# 선 아이콘 (SVG, 글자색을 따름) — section() 의 icon 으로 넘긴다. 이모지 대신 파란 아이콘 상자에 맞는 모양
_SVG = ('<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round" style="display:block">{}</svg>')
ICON_CALENDAR = _SVG.format('<rect x="3" y="4.5" width="18" height="16.5" rx="2.5"/><path d="M3 9.5h18M8 2.5v4M16 2.5v4"/>')
ICON_CLOCK = _SVG.format('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.2 2"/>')
ICON_TREND = _SVG.format('<path d="M3 3v18h18"/><path d="M7 15l4-5 3 3 5-7"/>')                        # 꺾은선 차트
ICON_RANK = _SVG.format('<path d="M4 6h16M4 12h11M4 18h6"/>')                                         # 순위 막대
ICON_ALERT = _SVG.format('<path d="M12 3.5 2.5 20h19z"/><path d="M12 10v4.5M12 17.2v.3"/>')            # 주의


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


def timeline(steps: list[tuple]) -> None:
    """세로 단계: (상태 done/now/next, 제목, 설명[, 배지]) — 설명은 글 하나 또는 줄 목록(줄마다 줄바꿈), 배지는 제목 옆 작은 노랑 배지"""
    mark = {"done": "✓", "now": "•", "next": ""}
    lis = []
    for s, t, d, *rest in steps:
        badge = f' <span class="tl-badge">{esc(rest[0])}</span>' if rest and rest[0] else ""
        desc = "<br>".join(esc(x) for x in ([d] if isinstance(d, str) else d) if x)
        lis.append(f'<li class="{s}"><span class="dot">{mark[s]}</span><div class="t">{esc(t)}{badge}</div><div class="d">{desc}</div></li>')
    st.markdown(f'<ul class="tl">{"".join(lis)}</ul>', unsafe_allow_html=True)


def mini_cards(cards: list[dict]) -> None:
    """작은 카드 그리드: dict(tag, title, meta, url)"""
    items = "".join(
        f'<a class="mc" href="{esc(c.get("url") or "#")}" target="_blank">'
        f'<div class="mc-tag">{esc(c["tag"])}</div><div class="mc-title">{esc(c["title"])}</div>'
        f'<div class="mc-meta">{esc(c.get("meta", ""))}</div></a>' for c in cards)
    st.markdown(f'<div class="mc-grid">{items}</div>', unsafe_allow_html=True)
