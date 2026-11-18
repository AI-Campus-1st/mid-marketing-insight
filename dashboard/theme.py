"""
[B·D6] 대시보드 색·차트 공통 설정 (dataviz 기준, 라이트·다크 각각 검증한 값)

- 판정(범주): 제때 / 공시가 먼저 / 늦게 = 범주 슬롯 1·3·2 (파랑·청록·주황), 미확인 = 중립 회색(없음)
  validate_palette.js: 라이트·다크 모두 PASS (청록은 라이트 대비 2.74:1 → 직접 라벨·표로 보완)
- AI 단계 L0~L3(순서): 파랑 한 가지 색의 진하기 (--ordinal PASS). 다크는 같은 램프에서 다크 표면용 단계를 따로 고름
- 글자는 색이 아니라 글자 토큰(본문·보조·흐린 글자)으로
"""
import streamlit as st

PALETTE = {
    "light": {
        # 글자·격자·바탕은 화면(.streamlit/config.toml, dashboard/style.py)과 같은 값
        "surface": "#ffffff", "text": "#1f2733", "text2": "#5b6575", "muted": "#8a93a3", "grid": "#e4e8ef",
        "verdict": {"제때": "#2a78d6", "공시가 먼저": "#1baf7a", "늦게": "#eb6834", "미확인": "#b4b3ad", "참고용": "#dcdbd6"},
        "level": {"L0": "#86b6ef", "L1": "#3987e5", "L2": "#256abf", "L3": "#104281"},
        "news": "#52514e",
    },
    "dark": {
        "surface": "#1a1a19", "text": "#f0efec", "text2": "#c3c2b7", "muted": "#8f8e88", "grid": "#33332f",
        "verdict": {"제때": "#3987e5", "공시가 먼저": "#199e70", "늦게": "#d95926", "미확인": "#6b6a66", "참고용": "#45443f"},
        "level": {"L0": "#184f95", "L1": "#2a78d6", "L2": "#6da7ec", "L3": "#b7d3f6"},
        "news": "#c3c2b7",
    },
}
# 차트 글꼴 = 화면 글꼴 (dashboard/style.py 가 Pretendard 전체 굵기를 불러옴)
FONT = "Pretendard, 'Pretendard Variable', -apple-system, 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif"
VERDICT_ORDER = ["제때", "늦게", "공시가 먼저", "미확인"]
VERDICT_KO = {"on_time": "제때", "preceded": "공시가 먼저", "not_found": "미확인", "reference": "참고용"}
LEVEL_ORDER = ["L0", "L1", "L2", "L3"]
LEVEL_NAME = {"L0": "L0 언급", "L1": "L1 계획", "L2": "L2 도입·적용", "L3": "L3 자체 개발·상용화"}


def mode() -> str:
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:  # 오래된 Streamlit
        return "light"


def colors() -> dict:
    return PALETTE[mode()]


def verdict_ko(v: str) -> str:
    if isinstance(v, str) and v.startswith("delayed"):
        return "늦게"
    return VERDICT_KO.get(v, v)


def style(fig, height: int = 360):
    """차트 공통: 투명 배경(앱 표면), 흐린 격자, 글자 토큰, 범례는 위쪽 한 줄"""
    c = colors()
    fig.update_layout(
        height=height, margin=dict(l=10, r=10, t=40, b=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=c["text"], size=13, family=FONT), hoverlabel=dict(font_size=13, font_family=FONT),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0, title=None, font=dict(color=c["text2"]), traceorder="normal"),
    )
    fig.update_xaxes(gridcolor=c["grid"], zerolinecolor=c["grid"], linecolor=c["grid"], tickfont=dict(color=c["text2"]), automargin=True)
    fig.update_yaxes(gridcolor=c["grid"], zerolinecolor=c["grid"], linecolor=c["grid"], tickfont=dict(color=c["text2"]), automargin=True)
    fig.update_traces(textangle=0, selector=dict(type="bar"))   # 막대 안 숫자가 세로로 눕지 않게 (design_test)
    return fig
