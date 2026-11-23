"""
[A·D6] Q1 AI 마케팅 언급이 급증한 시기 (옛 제목: AI 마케팅 기사는 언제 급증했나) — 월별 기사량 + 급증 시점

render(df, period) : df = 기업·카테고리 필터만 거친 기사 (전체 기간), period = 화면에 보여 줄 기간
  급증 기준선(직전 6개월 평균)은 전체 기간으로 계산한 뒤 선택 기간만 잘라 보여 준다 — 기간을 좁혀도 판정이 바뀌지 않게
급증 기준(decisions.md): AI 언급 기사 월 건수 > 직전 6개월 평균 × 1.5, 미완 월(2023-10·2026-10) 제외 — analysis/trend.py 와 같은 함수
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analysis import trend
from dashboard import style as ui
from dashboard.theme import colors, mode, style

MIN_MONTHLY_AI = 30   # 필터로 월평균 AI 언급 기사가 이보다 적으면 급증 판정이 흔들려서 표시하지 않는다
MIN_FULL_MONTHS = 7   # 직전 6개월 평균과 비교하려면 완전한 달이 이만큼은 있어야 한다


def _ym(month: str) -> str:
    """'2024-04' → '2024년 04월'"""
    y, mo = month.split("-")
    return f"{y}년 {mo}월"


def _monthly(df: pd.DataFrame) -> pd.DataFrame:
    # 기사가 0건이면 빈 표 — 행 이름(월)을 글자 형식으로 둬야 아래 '2023-10' 같은 기간 비교에서 TypeError 가 나지 않는다
    #   (기업 하나 + '기술 및 트렌드' 끔 → 0건: 제목에 기업명이 나온 기사는 모두 그 카테고리)
    m = trend.monthly(df) if len(df) else pd.DataFrame(columns=["total", "ai", "ai_share", "partial"],
                                                       index=pd.Index([], dtype=object, name="month"))
    sp = trend.detect_spikes(m["ai"], m["partial"]) if len(m) else pd.DataFrame()
    return m.join(sp[["baseline", "ratio", "spike"]]) if len(sp) else m


LIGHT_BAR = {"light": "#d6e2f3", "dark": "#2b3a52"}   # 급증이 아닌 달 (레퍼런스: 강조만 진한 파랑, 나머지 옅게)


def _chart(m: pd.DataFrame, show_spikes: bool):
    """AI 언급 기사 월별 막대 — 급증 월만 진한 파랑, 미완 월은 빗금. 급증 기준선은 옅은 실선, 막대 뒤 (전체 기사 선은 뺌)"""
    c = colors()
    blue = c["verdict"]["제때"]
    x = pd.to_datetime(m.index)
    spike = m["spike"] if (show_spikes and "spike" in m) else pd.Series(False, index=m.index)
    colors_ = [blue if sp else LIGHT_BAR[mode()] for sp in spike]
    fig = go.Figure()
    # 기준선은 아래층(y), 막대·배수 글자는 위층(y2, y 와 같은 눈금) → 기준선이 막대 뒤로 지나감 (Plotly 는 같은 층이면 선을 막대 위에 그림)
    if show_spikes and "spike" in m:
        fig.add_scatter(x=x, y=m["baseline"] * trend.SPIKE_RATIO, name="급증 기준선", mode="lines",
                        line=dict(color=c["muted"], width=1.5), opacity=.6, hovertemplate="%{x|%Y년 %m월} 기준선 %{y:.0f}건<extra></extra>")
    fig.add_bar(x=x, y=m["ai"], name="AI 언급 기사", yaxis="y2", showlegend=False, marker=dict(color=colors_, cornerradius=6,
                pattern=dict(shape=["/" if p else "" for p in m["partial"]], fgcolor=blue, solidity=.25)),
                customdata=(m["ai_share"] * 100).round(0), hovertemplate="%{x|%Y년 %m월} AI 언급 기사 %{y}건 (전체의 %{customdata:.0f}%)<extra></extra>")
    if show_spikes and "spike" in m:
        s = m[m["spike"]]
        fig.add_scatter(x=pd.to_datetime(s.index), y=s["ai"], yaxis="y2", mode="text", showlegend=False, hoverinfo="skip",
                        text=[f"×{r:.1f}" for r in s["ratio"]], textposition="top center", textfont=dict(color=blue, size=13))
    # 범례는 빈 막대로 따로: 실제 막대를 쓰면 Plotly 가 첫 막대(빗금 있는 미완 월) 모양을 아이콘으로 써서 'AI 언급 기사 = 빗금'처럼 보임
    legend = [("AI 언급 기사", LIGHT_BAR[mode()], "")]
    if show_spikes and "spike" in m:
        legend.append(("급증한 달", blue, ""))
    if m["partial"].any():
        legend.append(("일부 수집", LIGHT_BAR[mode()], "/"))
    for name, col, shape in legend:
        fig.add_bar(x=[None], y=[None], name=name, yaxis="y2", hoverinfo="skip",
                    marker=dict(color=col, pattern=dict(shape=shape, fgcolor=blue, solidity=.25)))
    fig.update_yaxes(title=None, rangemode="tozero", showgrid=True)
    fig = style(fig, height=360)
    fig.update_layout(yaxis2=dict(overlaying="y", matches="y", visible=False, showgrid=False))
    # 아래 날짜: 3개월마다 '1월 · 4월 · 7월 · 10월'
    #   날짜를 기울이면 Plotly automargin 이 왼쪽 여백을 크게 잡아 차트가 오른쪽으로 밀림 → 수평 라벨·automargin 끔
    ticks = [d for d in x if d.month in (1, 4, 7, 10)]
    fig.update_xaxes(title=None, tickvals=ticks, ticktext=[f"{d.month}월" for d in ticks], tickangle=0, showgrid=False, automargin=False)
    # 연도 칸: 12월과 1월 막대 사이에 옅은 세로선(날짜 줄 아래까지) + 선과 선 사이(그 해 칸) 가운데에 연도
    half = pd.Timedelta(days=15)
    for k, d in enumerate(x):
        if d.month == 1 and k > 0:
            fig.add_shape(type="line", xref="x", yref="paper", x0=d - half, x1=d - half, y0=-.13, y1=1,
                          line=dict(color=c["grid"], width=1.5), layer="below")
    for y in sorted({d.year for d in x}):
        months = [d for d in x if d.year == y]
        mid = months[0] - half + ((months[-1] + half) - (months[0] - half)) / 2   # 그 해 첫 막대 왼쪽 끝 ~ 마지막 막대 오른쪽 끝의 가운데
        fig.add_annotation(x=mid, xref="x", y=-.14, yref="paper", text=f"{y}년", showarrow=False,
                           xanchor="center", yanchor="top", font=dict(color=c["text2"], size=12))
    # 범례 순서: AI 언급 기사 · 급증한 달 · 일부 수집 · 급증 기준선 (기준선은 아래층에 그리려고 먼저 넣었으므로 맨 뒤로)
    if fig.data and fig.data[0].name == "급증 기준선":
        fig.data[0].legendrank = 2000
    fig.update_layout(bargap=.28, barmode="overlay", legend=dict(x=1, xanchor="right"), margin=dict(l=10, r=10, t=40, b=70))
    return fig


def _spike_timeline(df: pd.DataFrame, m: pd.DataFrame, spikes: list[str]) -> None:
    steps = []
    for mo in spikes:
        terms = trend.spike_terms(df, mo, k=3)
        words = " · ".join(terms["term"]) if len(terms) else ""
        steps.append(("done", _ym(mo), [f"AI 언급 기사 {int(m.loc[mo, 'ai'])}건", words], f"×{m.loc[mo, 'ratio']:.1f}"))
    full = m[~m["partial"]]
    if len(full):
        last = full.index[-1]
        base = m.loc[last, "baseline"] * trend.SPIKE_RATIO if "baseline" in m and pd.notna(m.loc[last, "baseline"]) else None
        steps.append(("now", f"최근 {_ym(last)}", [f"AI 언급 기사 {int(full.loc[last, 'ai'])}건", f"기준선 {base:.0f}건" if base else ""]))
    ui.timeline(steps)


def render(df: pd.DataFrame, period) -> None:
    ui.page_title("Q1. AI 마케팅 언급이 급증한 시기", "뉴스 중 AI 마케팅 관련 월별 기사량과 급증 시점을 정리했습니다.")
    m_all = _monthly(df)   # 전체 기간으로 기준선·급증 계산
    lo, hi = period[0].strftime("%Y-%m"), period[1].strftime("%Y-%m")
    m = m_all.loc[(m_all.index >= lo) & (m_all.index <= hi)]
    df = df[(df["date"] >= period[0]) & (df["date"] <= period[1])]
    if df.empty or m.empty:
        st.info("선택한 필터에 해당하는 기사가 없습니다.")
        return
    full_all = m_all[~m_all["partial"]]
    mean_ai = float(full_all["ai"].mean()) if len(full_all) else 0.0
    show_spikes = len(full_all) >= MIN_FULL_MONTHS and mean_ai >= MIN_MONTHLY_AI
    full = m[~m["partial"]]
    spikes = list(m.index[m["spike"]]) if show_spikes and "spike" in m else []

    if spikes:
        first = spikes[0]
        after = full.loc[first:]
        ui.lead(f"<b>{_ym(first)}</b>에 AI 언급 기사가 직전 6개월 평균의 <b>{m.loc[first, 'ratio']:.1f}배</b>로 급증했고, "
                f"이후 월 {int(after['ai'].min())}건 아래로 내려가지 않았습니다.", big=True)
    elif not show_spikes:
        ui.lead(f"선택한 필터에서는 기사 수가 적어 급증 시점을 판정하지 않습니다 "
                f'<span style="font-weight:500;opacity:.6;font-size:.9rem">(AI 언급 기사 월평균 {mean_ai:.1f}건)</span>', big=True)
    else:
        ui.lead("선택한 기간에는 급증한 달이 없습니다.", big=True)

    # '기사'(전체 건수) 카드는 빼고 AI 언급 기사 카드 설명에 전체 건수를 함께 적음
    ui.kpis([("AI 언급 기사", f"{int(df['is_ai'].sum()):,}건", f"수집한 뉴스 {len(df):,}건 중 {df['is_ai'].mean():.0%}"),
             ("급증한 달", f"{len(spikes)}개" if show_spikes else "판정 안 함", "직전 6개월 평균 × 1.5 초과"),
             ("관측 기간", f"{len(m)}개월", f"{_ym(m.index.min())} ~ {_ym(m.index.max())} ({len(m)}개월)")], key="q1")

    left, right = st.columns([5, 2], gap="medium")
    with left, st.container(border=True, key="card_q1_chart"):
        ui.section("월별 AI 언급 기사량", ui.ICON_CALENDAR)
        st.plotly_chart(ui.lock(_chart(m, show_spikes)), width="stretch", config=ui.PLOT_CONFIG)
        st.caption("* AI 언급 기사 : 제목이나 본문에 AI 용어(AI·인공지능·생성형·LLM·GPT 등)가 나온 기사\n"
                   "* 일부 수집 : 날짜 제한으로 일부만 수집된 달 (2023년 10월은 19일부터, 2026년 10월은 19일까지)\n"
                   "* 급증 기준 : 직전 6개월 평균 × 1.5 초과")
    with right, st.container(border=True, key="card_q1_timeline"):
        ui.section("급증 타임라인", ui.ICON_CLOCK)
        if spikes or show_spikes:
            _spike_timeline(df, m, spikes)
        else:
            st.caption("기사가 적어 급증을 가릴 수 없습니다")   # 기업·카테고리 필터로 기사가 크게 줄었을 때 (위 show_spikes 조건)

    with st.container(key="q1_more"):   # 아래 접기 메뉴: 제목 글꼴을 카드 제목(월별 AI 언급 기사량)과 같게 (style.py)
        _more(df, m, spikes, show_spikes)


def _more(df: pd.DataFrame, m: pd.DataFrame, spikes: list[str], show_spikes: bool) -> None:
    if spikes:
        with st.expander("달별 늘어난 언급량 (급증 달 위주로)"):
            for mo in spikes:
                terms = trend.spike_terms(df, mo, k=8)
                if terms.empty:
                    continue
                st.markdown(f"**{_ym(mo)}** — AI 언급 기사 {int(m.loc[mo, 'ai'])}건 (직전 평균 {m.loc[mo, 'baseline']:.0f}건)")
                st.dataframe(terms.rename(columns={"term": "단어", "count": "이번 달", "expected": "평소 기대", "excess": "초과"}),
                             hide_index=True, width="content")
    with st.expander("엑셀 형식의 표로 보기"):
        t = m[["total", "ai", "ai_share"]].assign(ai_share=lambda x: (x["ai_share"] * 100).round(0))
        if "spike" in m:
            t["급증"] = m["spike"].map({True: "급증", False: ""}) if show_spikes else ""
        st.dataframe(t.rename(columns={"total": "전체", "ai": "AI 언급 기사", "ai_share": "AI 비율(%)"}), width="stretch")
