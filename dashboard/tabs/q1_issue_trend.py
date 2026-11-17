"""
[A·D6] Q1 AI 마케팅 기사는 언제 급증했나 — 월별 기사량 + 급증 시점

render(df, period) : df = 기업·카테고리 필터만 거친 기사 (전체 기간), period = 화면에 보여 줄 기간
  급증 기준선(직전 6개월 평균)은 전체 기간으로 계산한 뒤 선택 기간만 잘라 보여 준다 — 기간을 좁혀도 판정이 바뀌지 않게
급증 기준(decisions.md): AI 기사 월 건수 > 직전 6개월 평균 × 1.5, 미완 월(2023-10·2026-10) 제외 — analysis/trend.py 와 같은 함수
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analysis import trend
from dashboard.theme import colors, style

MIN_MONTHLY_AI = 30   # 필터로 월평균 AI 기사가 이보다 적으면 급증 판정이 흔들려서 표시하지 않는다

HOW_TO_READ = f"""
- **AI 기사** = 제목이나 본문(정제 후)에 AI 용어(AI·인공지능·생성형·LLM·GPT 등)가 나온 기사. 제공 뉴스(키워드 수집) 기준이다.
- **급증** = 그 달 AI 기사 수가 **직전 6개월 평균의 1.5배**를 넘은 달. 수집량 자체가 늘고 있어서 전체 평균이 아니라 직전 반년과 비교한다.
  기간 3·6·12개월 × 배율 1.3·1.5·2.0 의 9개 조합 모두에서 2024-04 가 급증으로 나온다 (decisions.md).
- 2023-10(19일부터)·2026-10(19일까지)은 **미완 월**이라 빈 점으로 표시하고 급증 판정에서 뺀다.
- 필터로 월평균 AI 기사가 {MIN_MONTHLY_AI}건보다 적어지면 급증 판정을 표시하지 않는다(건수가 적어 우연한 변동이 커짐).
"""


def _monthly(df: pd.DataFrame) -> pd.DataFrame:
    m = trend.monthly(df) if len(df) else pd.DataFrame(columns=["total", "ai", "ai_share", "partial"])
    sp = trend.detect_spikes(m["ai"], m["partial"]) if len(m) else pd.DataFrame()
    return m.join(sp[["baseline", "ratio", "spike"]]) if len(sp) else m


def _chart(m: pd.DataFrame, show_spikes: bool):
    c = colors()
    blue, orange = c["verdict"]["제때"], c["verdict"]["늦게"]
    x = pd.to_datetime(m.index)
    fig = go.Figure()
    fig.add_scatter(x=x, y=m["total"], name="전체 기사", mode="lines", line=dict(color=c["muted"], width=2),
                    hovertemplate="%{x|%Y-%m} 전체 %{y}건<extra></extra>")
    fig.add_scatter(x=x, y=m["ai"], name="AI 기사", mode="lines", line=dict(color=blue, width=2),
                    customdata=(m["ai_share"] * 100).round(0), hovertemplate="%{x|%Y-%m} AI %{y}건 (%{customdata:.0f}%)<extra></extra>")
    part = m[m["partial"]]
    if len(part):
        fig.add_scatter(x=pd.to_datetime(part.index), y=part["ai"], name="미완 월", mode="markers",
                        marker=dict(size=10, color="rgba(0,0,0,0)", line=dict(color=blue, width=2)),
                        hovertemplate="%{x|%Y-%m} 미완 월<extra></extra>")
    if show_spikes and "spike" in m:
        fig.add_scatter(x=x, y=m["baseline"] * trend.SPIKE_RATIO, name="급증 기준선 (직전 6개월 평균 × 1.5)", mode="lines",
                        line=dict(color=c["muted"], width=1.5, dash="dash"), hovertemplate="%{x|%Y-%m} 기준선 %{y:.0f}건<extra></extra>")
        s = m[m["spike"]]
        fig.add_scatter(x=pd.to_datetime(s.index), y=s["ai"], name="급증", mode="markers+text",
                        marker=dict(size=13, color=orange, line=dict(color=c["surface"], width=2)),
                        text=[f"{i} ×{r:.1f}" for i, r in zip(s.index, s["ratio"])], textposition="top center",
                        textfont=dict(color=c["text"], size=12), hovertemplate="%{x|%Y-%m} 급증 %{y}건<extra></extra>")
    fig.update_yaxes(title=None, rangemode="tozero")
    fig.update_xaxes(title=None, dtick="M3", tickformat="%Y-%m")
    return style(fig, height=380)


def render(df: pd.DataFrame, period) -> None:
    st.markdown("### Q1. AI 마케팅 기사는 언제 급증했나")
    m_all = _monthly(df)   # 전체 기간으로 기준선·급증 계산
    lo, hi = period[0].strftime("%Y-%m"), period[1].strftime("%Y-%m")
    m = m_all.loc[(m_all.index >= lo) & (m_all.index <= hi)]
    df = df[(df["date"] >= period[0]) & (df["date"] <= period[1])]
    if df.empty or m.empty:
        st.info("선택한 필터에 해당하는 기사가 없어요.")
        return
    full_all = m_all[~m_all["partial"]]
    show_spikes = len(full_all) >= 7 and full_all["ai"].mean() >= MIN_MONTHLY_AI
    full = m[~m["partial"]]
    spikes = list(m.index[m["spike"]]) if show_spikes and "spike" in m else []

    if spikes:
        first = spikes[0]
        after = full.loc[first:]
        st.markdown(f"#### **{first}** 에 AI 기사가 직전 6개월 평균의 **{m.loc[first, 'ratio']:.1f}배**로 급증했고, "
                    f"이후 월 {int(after['ai'].min())}건 아래로 내려가지 않았다")
    elif not show_spikes:
        st.markdown("#### 선택한 필터에서는 기사 수가 적어 급증 시점을 판정하지 않아요")
    else:
        st.markdown("#### 선택한 기간에는 급증한 달이 없어요")

    a, b, c_, d = st.columns(4)
    a.metric("기사", f"{len(df):,}건")
    b.metric(f"AI 기사 (전체의 {df['is_ai'].mean():.0%})", f"{int(df['is_ai'].sum()):,}건")
    c_.metric("급증한 달", f"{len(spikes)}개" if show_spikes else "판정 안 함")
    d.metric("기간", f"{len(m)}개월", help=f"{m.index.min()} – {m.index.max()}")

    st.plotly_chart(_chart(m, show_spikes), width="stretch", config={"displayModeBar": False})
    st.caption("제공 뉴스(키워드 수집, 중복 제외) 기준 · 빈 점 = 미완 월 · 점선 = 급증 기준선")

    if spikes:
        with st.expander("급증한 달에 무엇이 늘었나 (제목 단어)"):
            for mo in spikes:
                terms = trend.spike_terms(df, mo, k=8)
                if terms.empty:
                    continue
                st.markdown(f"**{mo}** — AI 기사 {int(m.loc[mo, 'ai'])}건 (직전 평균 {m.loc[mo, 'baseline']:.0f}건)")
                st.dataframe(terms.rename(columns={"term": "단어", "count": "이번 달", "expected": "평소 기대", "excess": "초과"}),
                             hide_index=True, width="content")
    with st.expander("읽는 법 · 급증 기준"):
        st.markdown(HOW_TO_READ)
    with st.expander("표로 보기"):
        t = m[["total", "ai", "ai_share"]].assign(ai_share=lambda x: (x["ai_share"] * 100).round(0))
        if "spike" in m:
            t["급증"] = m["spike"].map({True: "급증", False: ""}) if show_spikes else ""
        st.dataframe(t.rename(columns={"total": "전체", "ai": "AI 기사", "ai_share": "AI 비율(%)"}), width="stretch")
