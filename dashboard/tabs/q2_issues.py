"""
[A·D6] Q2 무엇이 화제인가 — 광고·마케팅 관련 기사의 이슈 이동 + 우려 기사

render(df) : df = data_loader.filter_news(...) 로 공통 필터를 거친 기사 (여기서 LLM 관련 = 1 만 씀)
구성(decisions.md Q2 확정): 메인 = 이슈별 분기 비중 추이 / 감성은 비율 차트 없이 한 줄 + 사람이 거른 우려 기사 목록
LLM 결과: llm_results (news_v4). 정확도는 새 표본 30건 A·B 합의 판정 기준
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard import data_loader as dl
from dashboard import style as ui
from dashboard.theme import FONT, colors, mode, style

# 내용 이슈 (형식 이슈 '제휴·협력'·'실적·투자·상장'·'행사·시상'·'기타'는 추이 차트에서 뺌). 색은 이슈에 고정 (순위가 아니라)
TREND_ISSUES = ["생성형 AI 콘텐츠 제작", "AI 개인화·타겟팅", "마케팅 자동화·애드테크", "AI 에이전트", "AI 검색·GEO"]
CATEGORICAL = {   # dataviz 기본 범주 팔레트 슬롯 1~5 (라이트·다크 각각 검증된 값)
    "light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"],
    "dark": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"],
}
PARTIAL_Q = {"2023Q4", "2026Q4"}   # 수집 기간이 분기 중간에 시작·끝남
MIN_QUARTER_N = 20                  # 분기 관련 기사가 이보다 적으면 비중이 흔들려 점을 흐리게

ACCURACY = """
- **관련 기사** 분류 (LLM `news_v4`): 새 표본 30건(A·B 합의 판정) 기준 관련 정확도 77%, 관련으로 고른 기사의 정밀도 약 73%.
- **이슈** 분류 (고정 목록 15개 중 기사마다 1~3개): 사람이 고른 이슈와 하나 이상 겹침 90%, LLM 이 고른 이슈의 정밀도 63% · 재현율 64%.
"""


def _yq(q: str) -> str:
    """'2024Q1' → '2024년 1분기'"""
    return f"{q[:4]}년 {q[-1]}분기"


def _yq_range(a: str, b: str) -> str:
    """'2024Q1', '2024Q3' → '2024년 1~3분기' (해가 다르면 '2024년 4분기~2025년 2분기')"""
    return f"{a[:4]}년 {a[-1]}~{b[-1]}분기" if a[:4] == b[:4] else f"{_yq(a)}~{_yq(b)}"


def _issue_colors() -> dict:
    return dict(zip(TREND_ISSUES, CATEGORICAL[mode()]))


def _quarterly_share(rel: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    n = rel.groupby("quarter").size()
    e = rel[["quarter", "issues"]].explode("issues")
    cnt = e.groupby(["quarter", "issues"]).size().unstack(fill_value=0).reindex(columns=TREND_ISSUES, fill_value=0)
    return cnt.div(n, axis=0) * 100, n


def _trend_chart(share: pd.DataFrame, n: pd.Series):
    c, ic = colors(), _issue_colors()
    fig = go.Figure()
    full = [q for q in share.index if q not in PARTIAL_Q]
    for iss in TREND_ISSUES:
        col = ic[iss]
        fig.add_scatter(x=full, y=share.loc[full, iss], name=iss, mode="lines+markers", line=dict(color=col, width=2),
                        marker=dict(size=7, color=col), customdata=[[n[q], _yq(q)] for q in full],
                        hovertemplate=f"%{{customdata[1]}} {iss} %{{y:.0f}}% (관련 기사 %{{customdata[0]}}건)<extra></extra>")
        part = [q for q in share.index if q in PARTIAL_Q]
        if part:
            fig.add_scatter(x=part, y=share.loc[part, iss], mode="markers", showlegend=False,
                            marker=dict(size=8, color="rgba(0,0,0,0)", line=dict(color=col, width=2)), customdata=[_yq(q) for q in part],
                            hovertemplate=f"%{{customdata}} {iss} %{{y:.0f}}% (일부 수집)<extra></extra>")
    # 선이 5개라 끝 라벨 대신 범례(위쪽) + 마우스 올리면 이슈 이름 (끝 값이 비슷해 라벨이 겹침)
    fig.update_yaxes(title=None, ticksuffix="%", rangemode="tozero")
    qs = list(share.index)
    fig.update_xaxes(title=None, categoryorder="array", categoryarray=qs, showgrid=False,
                     tickvals=qs, ticktext=[f"{q[-1]}분기" for q in qs], tickangle=0)
    fig = style(fig, height=420)
    # 연도 칸 (Q1 월별 차트와 같은 형식): 4분기와 1분기 사이에 옅은 세로선(날짜 줄 아래까지) + 선과 선 사이 가운데에 연도
    for k, q in enumerate(qs):
        if q.endswith("Q1") and k > 0:
            fig.add_shape(type="line", xref="x", yref="paper", x0=k - .5, x1=k - .5, y0=-.09, y1=1,
                          line=dict(color=c["grid"], width=1.5), layer="below")
    for y in sorted({q[:4] for q in qs}):
        ks = [k for k, q in enumerate(qs) if q[:4] == y]
        fig.add_annotation(x=(ks[0] + ks[-1]) / 2, xref="x", y=-.1, yref="paper", text=f"{y}년", showarrow=False,
                           xanchor="center", yanchor="top", font=dict(color=c["text2"], size=12))
    return fig


DIM, FOCUS_OTHERS = 0.28, 0.1   # 평소 선 투명도 / 한 이슈에 마우스를 올렸을 때 나머지 선


def _trend_html(share: pd.DataFrame, n: pd.Series, height: int = 430) -> str:
    """이슈별 비중 차트 (HTML): 평소엔 모든 선이 연하고, 범례 항목·선에 마우스를 올리면 그 이슈만 원래 색으로.
    st.plotly_chart 는 범례 hover 이벤트를 받을 수 없어 직접 만든 범례 + plotly.js 스크립트로 그린다"""
    import json
    c, ic = colors(), _issue_colors()
    fig = _trend_chart(share, n)
    groups = {iss: [] for iss in TREND_ISSUES}          # 이슈 → trace 번호들 (선 + 일부 기간 빈 점)
    names = [iss for iss in TREND_ISSUES for _ in range(2 if any(q in PARTIAL_Q for q in share.index) else 1)]
    for i, (tr, iss) in enumerate(zip(fig.data, names)):
        tr.opacity = DIM
        groups[iss].append(i)
    fig.update_layout(showlegend=False, height=height - 40, margin=dict(l=10, r=10, t=10, b=56),
                      font=dict(family=FONT))
    plot = fig.to_html(full_html=False, include_plotlyjs="cdn", div_id="q2trend",
                       config={"displayModeBar": False, "responsive": True}, default_width="100%")
    chips = "".join(f'<span class="chip" data-i="{k}"><i style="background:{ic[iss]}"></i>{iss}</span>'
                    for k, iss in enumerate(TREND_ISSUES))
    return f"""
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css">
<style>
  body {{ margin: 0; font-family: Pretendard, 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif; background: transparent; }}
  .legend {{ display: flex; flex-wrap: wrap; gap: .4rem; margin: 0 0 4px 4px; }}
  .chip {{ display: inline-flex; align-items: center; gap: .4rem; font-size: 12.5px; color: {c["text2"]}; cursor: default;
          padding: .25rem .6rem; border-radius: 999px; border: 1px solid transparent; opacity: .55; transition: all .12s; }}
  .chip i {{ width: 9px; height: 9px; border-radius: 50%; display: inline-block; }}
  .chip.on {{ opacity: 1; color: {c["text"]}; font-weight: 700; border-color: {c["grid"]}; }}
</style>
<div class="legend">{chips}</div>
{plot}
<script>
(function () {{
  const groups = {json.dumps(list(groups.values()))};
  const total = {len(fig.data)};
  const gd = document.getElementById('q2trend');
  const chips = [...document.querySelectorAll('.chip')];
  function show(k) {{
    const op = Array.from({{length: total}}, () => k === null ? {DIM} : {FOCUS_OTHERS});
    if (k !== null) groups[k].forEach(i => op[i] = 1);
    Plotly.restyle(gd, {{opacity: op}});
    chips.forEach((c, i) => c.classList.toggle('on', i === k));
  }}
  function ready(cb) {{ (window.Plotly && gd && gd.data) ? cb() : setTimeout(() => ready(cb), 50); }}
  ready(() => {{
    chips.forEach((c, k) => {{ c.addEventListener('mouseenter', () => show(k)); c.addEventListener('mouseleave', () => show(null)); }});
    gd.on('plotly_hover', e => {{ const t = e.points[0].curveNumber; const k = groups.findIndex(g => g.includes(t)); if (k >= 0) show(k); }});
    gd.on('plotly_unhover', () => show(null));
  }});
}})();
</script>"""


CONCERN_SHOW = 5   # 우려 기사: 처음에 보여 줄 카드 수 (나머지는 '더보기')
FORMAT_ISSUES = {"제휴·협력", "실적·투자·상장", "행사·시상"}   # 형식 이슈 (내용 이슈와 구분해 옅게)
LIGHT_BAR = {"light": "#d6e2f3", "dark": "#2b3a52"}


def _topn_chart(rel: pd.DataFrame):
    """이슈 순위 — 내용 이슈는 파랑, 형식 이슈(제휴·실적·행사)는 옅게 (레퍼런스: 강조만 진하게)"""
    c = colors()
    cnt = rel["issues"].explode().value_counts().drop("기타", errors="ignore").sort_values()
    col = [LIGHT_BAR[mode()] if i in FORMAT_ISSUES else c["verdict"]["제때"] for i in cnt.index]
    fig = go.Figure(go.Bar(x=cnt.values, y=cnt.index, orientation="h", marker=dict(color=col, cornerradius=6),
                           text=cnt.values, textposition="outside", textfont=dict(color=c["text2"]), cliponaxis=False,
                           hovertemplate="%{y} %{x}건<extra></extra>", showlegend=False))
    # 범례(빈 막대): 내용 이슈 = AI 를 어디에 썼나 / 형식 이슈 = 제휴·실적·행사 같은 일반 사업 소식
    for name, colr in (("AI 활용 분야", c["verdict"]["제때"]), ("일반 사업 소식", LIGHT_BAR[mode()])):
        fig.add_bar(x=[None], y=[None], orientation="h", name=name, marker=dict(color=colr), hoverinfo="skip")
    fig.update_xaxes(title=None, showgrid=False, showticklabels=False, range=[0, cnt.max() * 1.15])   # 막대 끝 숫자 자리
    fig.update_yaxes(title=None, showgrid=False)
    fig = style(fig, height=max(260, 26 * len(cnt) + 40))
    fig.update_layout(bargap=.35, barmode="overlay", legend=dict(x=1, xanchor="right", y=1.02, yanchor="bottom", orientation="h"))
    return fig


def _concerns(period) -> None:
    # #28 보강 5 (A·B 합의 PR #32): 새 화면 대신 이 목록을 광고주 관점 '도입 전 주의할 점'으로 (영역별 활용 사례는 기획안 검토 화면)
    ui.section("도입 전 주의할 점 — 우려 기사", ui.ICON_ALERT)
    st.caption("AI 마케팅에서 실제로 논란·반발이 된 사례예요. 카드를 누르면 원문이 열려요.")
    df = dl.concern_articles()
    if df is None:
        st.info("우려 기사 목록은 사람 검수 후 표시돼요 (`analysis/q2_concerns.py`).")
        return
    df = df[(df["date"] >= period[0]) & (df["date"] <= period[1])].sort_values("published_date", ascending=False)
    if df.empty:
        st.caption("선택한 기간에 우려 기사가 없어요.")
        return
    # 같은 사건 기사는 하나로 묶어 대표 기사(가장 이른 것) + 건수
    df["group"] = df["event_group"].fillna("").where(df["event_group"].fillna("") != "", df["doc_id"])
    g = df.sort_values("published_date").groupby("group", sort=False).agg(
        date=("date", "first"), title=("title", "first"), media=("media", "first"), url=("url", "first"),
        n=("doc_id", "size"), name=("event_group", "first")).sort_values("date", ascending=False)
    cards = [{"title": r.title,   # 카드 윗줄 꼬리표('우려 논조'·'같은 사건 기사 n건')는 없앰 — 같은 사건 기사는 여전히 하나로 묶음
              "meta": f"{r.media} · {r.date}", "url": r.url} for r in g.itertuples()]
    # 처음엔 최근 5개만, 나머지는 '더보기'로 펼침 (다시 누르면 접힘)
    opened = st.session_state.get("q2_concerns_all", False)
    ui.mini_cards(cards if opened else cards[:CONCERN_SHOW])
    if len(cards) > CONCERN_SHOW:
        st.button("접기" if opened else f"더보기 ({len(cards) - CONCERN_SHOW}건)", key="q2_concerns_more", width="stretch",   # 카드 줄 폭만큼 길게, 글자 가운데
                  icon=":material/expand_less:" if opened else ":material/expand_more:",
                  on_click=lambda: st.session_state.update(q2_concerns_all=not opened))


def render(df: pd.DataFrame, period) -> None:
    ui.page_title("Q2. AI 마케팅 화제 이슈의 변화", "광고·마케팅 관련 기사를 이슈별로 나눠 분기마다 어떤 이슈가 화제였는지 정리했어요.")
    rel = df[df["is_relevant"] == 1]
    if rel.empty:
        st.info("선택한 필터에 해당하는 광고·마케팅 관련 기사가 없어요.")
        return
    share, n = _quarterly_share(rel)
    full = [q for q in share.index if q not in PARTIAL_Q and n[q] >= MIN_QUARTER_N]

    # 한 줄 결론: 처음 몇 분기 대비 마지막 몇 분기에 비중이 가장 많이 오른 이슈 (데이터에서 계산 → 필터에 따라 바뀜)
    if len(full) >= 4:
        early, late = share.loc[full[:3]].mean(), share.loc[full[-3:]].mean()
        rise = (late - early).sort_values(ascending=False)
        top = rise.index[0]
        ui.lead(f'광고·마케팅 관련 기사에서 <b>{ui.esc(top)}</b> 비중이 '
                f'{early[top]:.0f}% → <b>{late[top]:.0f}%</b>로 가장 많이 늘었어요. '
                f'<span style="display:block;font-weight:500;opacity:.6;font-size:.9rem">({_yq_range(full[0], full[2])} 평균 → {_yq_range(full[-3], full[-1])} 평균)</span>',   # 괄호는 다음 줄에 (중간에서 줄바뀜 방지)
                big=True)
    neutral = (rel["sentiment"] == "neutral").mean()
    ui.kpis([("관련 기사", f"{len(rel):,}건", f"전체 기사의 {len(rel) / len(df):.0%}"),
             ("AI 활용 중립 반응", f"{neutral:.0%}", "출시·제휴·실적 발표 위주"),
             # 우려 기사(사람 검수 목록)에서 많이 나온 주제: 일자리 대체 · 과장·허위 홍보 · 보안·조작
             ("AI 활용 우려 반응", f"{int((rel['sentiment'] == 'negative').sum())}건", "일자리 대체 · 과장·허위 홍보 · 보안·조작 위주")], key="q2")

    with st.container(border=True, key="card_q2_trend"):
        ui.section("분기별 이슈 비중", ui.ICON_TREND)
        st.caption("범례나 선에 마우스를 올리면 그 이슈가 보여요.")   # 제목 아래 · 범례 위
        import streamlit.components.v1 as components
        components.html(_trend_html(share, n), height=440)
        st.caption("* 관련 기사 : AI(LLM)가 광고·마케팅 산업 활동으로 분류한 기사\n"
                   "* 이슈 비중 : 관련 기사 중 각 이슈가 붙은 기사 비율 (한 기사에 이슈가 여럿이라 합이 100%를 넘어요)\n"
                   "* 일부 수집 (빈 점) : 날짜 제한으로 일부만 수집된 분기 (2023년 4분기는 10월 19일부터, 2026년 4분기는 10월 19일까지)")

    # 위아래로: 이슈 순위(분기 비중에 이어 '전체 기간에 무엇이 많았나') → 우려 기사(도입 전 주의할 점, 부록)
    with st.container(border=True, key="card_q2_rank"):
        ui.section("이슈 순위", ui.ICON_RANK)
        st.plotly_chart(_topn_chart(rel), width="stretch", config={"displayModeBar": False})
    _concerns(period)

    with st.container(key="q2_more"):   # 아래 접기 메뉴: Q1 과 같은 제목 글꼴 (style.py)
        with st.expander("분류 정확도"):
            st.markdown(ACCURACY)
        with st.expander("엑셀 형식의 표로 보기"):
            t = share.round(0).assign(관련기사=n)
            t.index = [_yq(q) for q in t.index]
            st.dataframe(t, width="stretch")
