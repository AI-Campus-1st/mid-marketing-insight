"""
[B·D6] Q3 공시 반영 — 뉴스에서 말한 AI 가 사업보고서에 언제·얼마나 구체적으로 실렸나

render(corps, period) 를 A 의 app.py 에서 부르면 된다. corps: 기업 목록, period: (시작일, 끝일) 이벤트 발표일 범위
화면은 하위 탭 4개로 나눈다 (한 화면에 차트 하나):
    요약 · 이벤트 타임라인 · 공시 AI 단계 · 이벤트 표
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard import data_loader as dl
from dashboard.theme import LEVEL_NAME, LEVEL_ORDER, VERDICT_ORDER, colors, style, verdict_ko

HOW_TO_READ = """
- **판정 기준:** 뉴스 발표일 이후 처음 끝나는 분기의 정기보고서(기대 보고서)에 실렸으면 *제때*, 그 뒤 보고서에서 처음이면 *늦게*,
  발표 전 보고서에 이미 있었으면 *공시가 먼저*, 수집 기간(~2026-10) 끝까지 없으면 *공시에서 미확인*.
- **'공시에서 미확인' ≠ 실행 안 함.** 제휴·외부 솔루션 도입은 정기보고서에 실을 칸이 마땅치 않다.
- 분기보고서에는 회사 연혁이 없어서, 연혁에만 실리는 이벤트는 반기·사업보고서를 기대 보고서로 본다.
- 2026-07 이후 발표된 3개 이벤트는 다음 보고서가 아직 없어 판정하지 않는다(참고용).
"""


EVENT_TYPE_KO = {"partnership": "제휴", "adoption": "도입", "launch": "출시", "campaign": "캠페인", "organization": "조직"}


def _label(v: str) -> str:
    return "공시에서 미확인" if v == "미확인" else v


def _summary(r: pd.DataFrame) -> None:
    j = r[r.analysis_scope == "judge"]
    n = {v: int((j["판정"] == v).sum()) for v in VERDICT_ORDER}
    st.markdown(f"#### 판정 대상 이벤트 {len(j)}개 중 **{n['제때'] + n['늦게'] + n['공시가 먼저']}개**가 사업보고서에 실렸다")
    for col, v in zip(st.columns(4), VERDICT_ORDER):
        col.metric(_label(v), f"{n[v]}개")

    # 핵심 문장 (데이터에서 계산 — 필터에 따라 바뀐다)
    bullets = []
    zero = j.groupby("corp_name")["판정"].apply(lambda x: int((x != "미확인").sum()))
    zero = [c for c, v in zero.items() if v == 0]
    if zero:
        bullets.append(f"**{', '.join(zero)}**: 뉴스로 알려진 AI 이벤트가 공시에서 하나도 확인되지 않았다.")
    lag = j[j.lag_days.notna() & j["판정"].isin(["제때", "늦게"])].lag_days
    if len(lag):
        bullets.append(f"공시에 실린 이벤트는 뉴스 발표 뒤 보통 **{int(lag.median())}일**(중앙값) 만에 보고서에 나왔다.")
    if n["미확인"]:
        kinds = j[j["판정"] == "미확인"].event_type.map(EVENT_TYPE_KO).fillna("기타").value_counts()
        bullets.append(f"공시에서 미확인 {n['미확인']}개: " + ", ".join(f"{k} {v}" for k, v in kinds.items())
                       + " — 제휴·도입은 정기보고서에 실을 칸이 마땅치 않다.")
    if bullets:
        st.markdown("\n".join(f"- {b}" for b in bullets))

    st.markdown("##### 기업별 반영 결과")
    _by_corp(j)
    with st.expander("읽는 법"):
        st.markdown(HOW_TO_READ)


def _by_corp(j: pd.DataFrame) -> None:
    c = colors()
    t = pd.crosstab(j.corp_name, j["판정"]).reindex(columns=VERDICT_ORDER, fill_value=0)
    t = t.loc[t.sum(axis=1).sort_values().index]
    fig = go.Figure()
    for v in VERDICT_ORDER:
        fig.add_bar(y=t.index, x=t[v], name=_label(v), orientation="h",
                    marker=dict(color=c["verdict"][v], line=dict(color=c["surface"], width=2)),
                    text=[str(x) if x else "" for x in t[v]], textposition="inside", insidetextanchor="middle",
                    hovertemplate="%{y} · " + _label(v) + " %{x}개<extra></extra>")
    fig.update_layout(barmode="stack", bargap=0.4)
    fig.update_xaxes(title="이벤트 수", dtick=1)
    st.plotly_chart(style(fig, 280), width="stretch")


def _timeline(r: pd.DataFrame) -> None:
    c = colors()
    corp = st.radio("기업", sorted(r.corp_name.unique()), horizontal=True, key="q3_tl_corp")
    d = r[r.corp_name == corp].sort_values("official_date").copy()
    d["label"] = d.event_name.str.slice(0, 26) + d.analysis_scope.map({"reference": " (참고용)"}).fillna("")
    fig = go.Figure()
    for row in d[d.first_report_date.notna()].itertuples():
        fig.add_scatter(x=[row.official_date, row.first_report_date], y=[row.label, row.label], mode="lines",
                        line=dict(color=c["grid"], width=3), hoverinfo="skip", showlegend=False)
    fig.add_scatter(x=d.official_date, y=d.label, mode="markers", name="뉴스 발표일",
                    marker=dict(symbol="circle-open", size=12, color=c["news"], line=dict(width=2)),
                    customdata=d[["event_name", "판정"]],
                    hovertemplate="%{customdata[0]}<br>뉴스 발표 %{x|%Y-%m-%d}<br>판정: %{customdata[1]}<extra></extra>")
    for v in VERDICT_ORDER + ["참고용"]:
        s = d[(d["판정"] == v) & d.first_report_date.notna()]
        if len(s):
            fig.add_scatter(x=s.first_report_date, y=s.label, mode="markers", name=f"첫 반영 공시 · {v}",
                            marker=dict(size=13, color=c["verdict"][v], line=dict(color=c["surface"], width=2)),
                            customdata=s[["event_name", "first_report", "lag_days"]],
                            hovertemplate="%{customdata[0]}<br>%{customdata[1]} · 접수 %{x|%Y-%m-%d}<br>발표 후 %{customdata[2]}일<extra></extra>")
    miss = d[d["판정"] == "미확인"]
    if len(miss):
        fig.add_scatter(x=miss.official_date, y=miss.label, mode="text", text=["   공시에서 미확인"] * len(miss),
                        textposition="middle right", textfont=dict(color=c["muted"], size=12), hoverinfo="skip", showlegend=False)
    fig.update_yaxes(categoryorder="array", categoryarray=list(d.label), autorange="reversed", title=None)
    fig.update_xaxes(title=None, range=["2023-09-01", "2026-11-30"], dtick="M6", tickformat="%Y년 %-m월")  # 'Jan 2024' 대신 한글
    st.plotly_chart(style(fig, max(260, 46 * len(d) + 90)), width="stretch")
    st.caption("○ 뉴스 발표일 → ● 그 이벤트가 처음 실린 정기보고서 접수일. 점에 마우스를 올리면 보고서 이름과 걸린 일수가 나온다.")


def _levels(corps: list[str]) -> None:
    c = colors()
    lv = dl.chunk_levels()
    lv = lv[lv.corp_name.isin(corps) & lv.level.notna()]
    t = pd.crosstab(lv.corp_name, lv.level).reindex(columns=LEVEL_ORDER, fill_value=0)
    share = t.div(t.sum(axis=1), axis=0)
    order = list(share["L3"].sort_values().index)
    st.markdown("#### 공시에 적힌 AI 는 어느 단계인가")
    st.markdown("사업보고서에서 AI 를 말한 문단을 4단계로 나눈 비중. **L3(자체 개발·상용화)가 클수록 공시로 확인되는 실행이 많다.**")
    fig = go.Figure()
    for lvl in LEVEL_ORDER:
        fig.add_bar(y=order, x=share.loc[order, lvl], name=LEVEL_NAME[lvl], orientation="h",
                    marker=dict(color=c["level"][lvl], line=dict(color=c["surface"], width=2)),
                    customdata=t.loc[order, lvl],
                    hovertemplate="%{y} · " + LEVEL_NAME[lvl] + " %{x:.0%} (%{customdata}개 문단)<extra></extra>")
    fig.update_layout(barmode="stack", bargap=0.4)
    fig.update_xaxes(tickformat=".0%", range=[0, 1], title=None)
    st.plotly_chart(style(fig, 300), width="stretch")
    top = order[::-1]
    tbl = pd.DataFrame({"기업": top, "L3 비중": [f"{share.loc[x, 'L3']:.0%}" for x in top],
                        "AI 문단 수": [int(t.loc[x].sum()) for x in top]})
    st.dataframe(tbl, hide_index=True, width="content")
    with st.expander("분류 방법과 한계"):
        st.markdown("- AI 용어가 있는 공시 문단만 LLM 으로 분류 (프롬프트 chunk_v7). 처음 보는 표본 29개를 A·B 가 합의 판정한 기준 **가중 정확도 87%**.\n"
                    "- L3·L0 은 표본에서 100% 맞았지만 **L2(외부 AI 도입) 정확도는 25%로 낮다** — L3 비중 중심으로 볼 것.\n"
                    "- KT나스미디어는 연결 자회사였던 플레이디 제품을 근거로 한 문단(2025-04 매각 전)을 뺐다.")


def _table(r: pd.DataFrame) -> None:
    # 표에서만 쓰는 기업 체크박스 (사이드바 기업 필터로 고른 기업 안에서 다시 고른다)
    corps = sorted(r.corp_name.unique())
    keys = {c: f"q3_tbl_{c}" for c in corps}
    for k in keys.values():
        st.session_state.setdefault(k, True)
    head, reset = st.columns([5, 1], vertical_alignment="bottom")
    head.caption("표에서 볼 기업")
    reset.button("초기화", key="q3_tbl_reset", width="stretch",
                 on_click=lambda: st.session_state.update({k: True for k in keys.values()}))
    on = [c for col, c in zip(st.columns(len(corps)), corps) if col.checkbox(c, key=keys[c])]
    r = r[r.corp_name.isin(on)]
    if r.empty:
        st.info("기업을 하나 이상 골라 주세요.")
        return
    t = r.sort_values(["analysis_scope", "corp_name", "official_date"])[
        ["corp_name", "event_name", "official_date", "판정", "first_report", "first_report_date", "lag_days", "dart_url", "primary_source_url"]]
    st.dataframe(t, hide_index=True, width="stretch", height=min(35 * len(t) + 40, 800), column_config={
        "corp_name": "기업", "event_name": "이벤트", "official_date": "뉴스 발표일", "판정": "판정",
        "first_report": "첫 반영 공시", "first_report_date": "접수일", "lag_days": st.column_config.NumberColumn("발표 후 일수"),
        "dart_url": st.column_config.LinkColumn("공시 원문", display_text="DART"),
        "primary_source_url": st.column_config.LinkColumn("뉴스 원문", display_text="기사")})


def render(corps: list[str], period: tuple) -> None:
    r = dl.reflection()
    r = r[r.corp_name.isin(corps) & (r.official_date >= str(period[0])) & (r.official_date <= str(period[1]))].copy()
    r["판정"] = r.verdict.map(verdict_ko)
    st.header("Q3. 뉴스에서 말한 AI, 사업보고서에도 실렸나")
    if r.empty:
        st.info("선택한 기업·기간에 이벤트가 없습니다.")
        return
    t1, t2, t3, t4 = st.tabs(["요약", "이벤트 타임라인", "공시 AI 단계", "이벤트 표"])
    with t1:
        _summary(r)
    with t2:
        _timeline(r)
    with t3:
        _levels(corps)
    with t4:
        _table(r)
