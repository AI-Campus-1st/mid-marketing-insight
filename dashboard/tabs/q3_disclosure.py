"""
[B·D6] Q3 공시 반영 — 뉴스에서 말한 AI 가 사업보고서에 언제·얼마나 구체적으로 실렸나

render(corps, period) 를 A 의 app.py 탭에서 부르면 된다. corps: 기업 목록, period: (시작일, 끝일) 이벤트 발표일 범위
1. 한눈에: 판정 대상 이벤트 수와 판정별 개수
2. 기업별 반영 결과 (가로 누적 막대)
3. 이벤트 타임라인 (뉴스 발표일 → 첫 반영 공시 접수일, 판정 색)
4. 기업별 공시 AI 단계 분포 (100% 누적 막대, L3 비중 중심)
5. 이벤트 표 (공시·원문 링크)
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard import data_loader as dl
from dashboard.theme import LEVEL_NAME, LEVEL_ORDER, VERDICT_ORDER, colors, style, verdict_ko

CAVEAT = ("**읽는 법** · 판정은 뉴스 발표일 이후 처음 끝나는 분기의 정기보고서를 기준으로 한다. "
          "'공시에서 미확인'은 실행하지 않았다는 뜻이 아니다 — 제휴·외부 솔루션 도입은 정기보고서에 실을 칸이 마땅치 않다. "
          "분기보고서에는 회사 연혁이 없어 연혁에만 실리는 이벤트는 반기·사업보고서 기준으로 본다.")


def _summary(r: pd.DataFrame) -> None:
    j = r[r.analysis_scope == "judge"]
    cols = st.columns(5)
    cols[0].metric("판정 대상 이벤트", f"{len(j)}개")
    for col, v in zip(cols[1:], VERDICT_ORDER):
        n = (j["판정"] == v).sum()
        col.metric(v if v != "미확인" else "공시에서 미확인", f"{n}개", help=None)


def _by_corp(r: pd.DataFrame) -> None:
    c = colors()
    j = r[r.analysis_scope == "judge"]
    t = pd.crosstab(j.corp_name, j["판정"]).reindex(columns=VERDICT_ORDER, fill_value=0)
    t = t.loc[t.sum(axis=1).sort_values().index]
    fig = go.Figure()
    for v in VERDICT_ORDER:
        fig.add_bar(y=t.index, x=t[v], name=v, orientation="h", marker=dict(color=c["verdict"][v], line=dict(color=c["surface"], width=2)),
                    text=[str(n) if n else "" for n in t[v]], textposition="inside", insidetextanchor="middle",
                    hovertemplate="%{y} · " + v + " %{x}개<extra></extra>")
    fig.update_layout(barmode="stack", bargap=0.35)
    fig.update_xaxes(title=None, dtick=1)
    st.plotly_chart(style(fig, 300), width="stretch")


def _timeline(r: pd.DataFrame) -> None:
    c = colors()
    d = r.sort_values(["corp_name", "official_date"]).copy()
    d["label"] = d.corp_name + " · " + d.event_name.str.slice(0, 22) + d.analysis_scope.map({"reference": " (참고용)"}).fillna("")
    fig = go.Figure()
    # 연결선: 발표일 → 첫 반영 공시 접수일
    for row in d[d.first_report_date.notna()].itertuples():
        fig.add_scatter(x=[row.official_date, row.first_report_date], y=[row.label, row.label], mode="lines",
                        line=dict(color=c["grid"], width=2), hoverinfo="skip", showlegend=False)
    fig.add_scatter(x=d.official_date, y=d.label, mode="markers", name="뉴스 발표일",
                    marker=dict(symbol="circle-open", size=10, color=c["news"], line=dict(width=2)),
                    customdata=d[["event_name", "판정"]],
                    hovertemplate="%{customdata[0]}<br>뉴스 발표 %{x|%Y-%m-%d}<br>판정: %{customdata[1]}<extra></extra>")
    for v in VERDICT_ORDER + ["참고용"]:
        s = d[(d["판정"] == v) & d.first_report_date.notna()]
        if len(s):
            fig.add_scatter(x=s.first_report_date, y=s.label, mode="markers", name=f"첫 반영 공시 ({v})",
                            marker=dict(size=11, color=c["verdict"][v], line=dict(color=c["surface"], width=2)),
                            customdata=s[["event_name", "first_report", "lag_days"]],
                            hovertemplate="%{customdata[0]}<br>%{customdata[1]} 접수 %{x|%Y-%m-%d}<br>발표 후 %{customdata[2]}일<extra></extra>")
    miss = d[d["판정"] == "미확인"]
    if len(miss):
        fig.add_scatter(x=miss.official_date, y=miss.label, mode="text", text=["  공시에서 미확인"] * len(miss), textposition="middle right",
                        textfont=dict(color=c["muted"], size=12), hoverinfo="skip", showlegend=False)
    # 행 순서 = 기업·발표일 순 (그리지 않은 행이 아래로 밀리지 않게 명시)
    fig.update_yaxes(categoryorder="array", categoryarray=list(d.label), autorange="reversed", title=None)
    fig.update_xaxes(title=None, range=["2023-09-01", "2026-11-30"])
    st.plotly_chart(style(fig, max(320, 24 * len(d) + 80)), width="stretch")


def _levels(corps: list[str]) -> None:
    c = colors()
    lv = dl.chunk_levels()
    lv = lv[lv.corp_name.isin(corps) & lv.level.notna()]
    t = pd.crosstab(lv.corp_name, lv.level).reindex(columns=LEVEL_ORDER, fill_value=0)
    share = t.div(t.sum(axis=1), axis=0)
    share = share.loc[share["L3"].sort_values().index]
    fig = go.Figure()
    for lvl in LEVEL_ORDER:
        fig.add_bar(y=share.index, x=share[lvl], name=LEVEL_NAME[lvl], orientation="h",
                    marker=dict(color=c["level"][lvl], line=dict(color=c["surface"], width=2)),
                    customdata=t.loc[share.index, lvl],
                    text=[f"L3 {v:.0%}" if lvl == "L3" and v > 0.08 else "" for v in share[lvl]], textposition="inside",
                    hovertemplate="%{y} · " + LEVEL_NAME[lvl] + " %{x:.0%} (%{customdata}개)<extra></extra>")
    fig.update_layout(barmode="stack", bargap=0.35)
    fig.update_xaxes(tickformat=".0%", range=[0, 1], title=None)
    st.plotly_chart(style(fig, 300), width="stretch")
    st.caption("공시 청크 중 AI 용어가 있는 것만 LLM 으로 분류 (프롬프트 chunk_v7, 새 표본 29개 A·B 합의 판정 기준 가중 정확도 87%). "
               "L3·L0 은 표본에서 100% 맞았지만 **L2(외부 AI 도입) 분류 정확도는 25%로 낮다** — L3 비중 중심으로 볼 것. "
               "KT나스미디어는 연결 자회사였던 플레이디 제품 근거 청크(2025-04 매각 전)를 뺐다.")


def _table(r: pd.DataFrame) -> None:
    t = r.sort_values(["analysis_scope", "corp_name", "official_date"])[
        ["corp_name", "event_name", "official_date", "판정", "first_report", "first_report_date", "lag_days", "dart_url", "primary_source_url"]]
    st.dataframe(t, hide_index=True, width="stretch", column_config={
        "corp_name": "기업", "event_name": "이벤트", "official_date": "뉴스 발표일", "판정": "판정",
        "first_report": "첫 반영 공시", "first_report_date": "접수일", "lag_days": st.column_config.NumberColumn("발표 후 일수"),
        "dart_url": st.column_config.LinkColumn("공시 원문", display_text="DART"),
        "primary_source_url": st.column_config.LinkColumn("뉴스 원문", display_text="기사")})


def render(corps: list[str], period: tuple) -> None:
    r = dl.reflection()
    r = r[r.corp_name.isin(corps) & (r.official_date >= str(period[0])) & (r.official_date <= str(period[1]))].copy()
    r["판정"] = r.verdict.map(verdict_ko)
    st.subheader("Q3. 뉴스에서 말한 AI, 사업보고서에도 실렸나")
    if r.empty:
        st.info("선택한 기업·기간에 이벤트가 없습니다.")
        return
    _summary(r)
    st.caption(CAVEAT)
    left, right = st.columns([1, 1])
    with left:
        st.markdown("**기업별 반영 결과** (판정 대상 이벤트)")
        _by_corp(r)
    with right:
        st.markdown("**공시 속 AI 는 어느 단계인가** (기업별 AI 청크 단계 비중)")
        _levels(corps)
    st.markdown("**이벤트 타임라인** — ○ 뉴스 발표일 → ● 첫 반영 공시 접수일")
    _timeline(r)
    with st.expander("이벤트 표 (공시·뉴스 원문 링크)"):
        _table(r)
