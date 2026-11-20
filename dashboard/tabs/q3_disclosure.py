"""
[B·D6] Q3 공시 반영 — 뉴스에서 말한 AI 가 정기공시(사업·반기·분기보고서)에 언제·얼마나 구체적으로 실렸나

render(corps, period) 를 A 의 app.py 에서 부르면 된다. corps: 기업 목록, period: (시작일, 끝일) 이벤트 발표일 범위
화면 구성은 Q1·Q2 와 같은 형식: 제목 · 큰 요약문 · KPI 카드(판정별) → 하위 탭 3개(카드) → 아래 접기 메뉴
    탭: 요약(기업별 반영 결과 + 눈여겨볼 점) · 이벤트 타임라인(아래에 이벤트 표) · 공시 AI 단계
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config.ai_levels import LEVELS
from dashboard import data_loader as dl
from dashboard import style as ui
from dashboard.theme import LEVEL_NAME, LEVEL_ORDER, VERDICT_ORDER, colors, style, verdict_ko, verdict_label

HOW_TO_READ = """
- **판정 기준:** 뉴스 발표일 이후 처음 끝나는 분기의 정기보고서(기대 보고서)에 실렸으면 *다음 공시에 반영*, 그 뒤 보고서에서 처음이면 *늦게 반영*,
  발표 전 보고서에 이미 있었으면 *공시가 먼저*, 수집 기간 끝(2026년 10월)까지 확인되지 않으면 *공시에서 미확인*입니다.
- **'공시에서 미확인'은 실행하지 않았다는 뜻이 아닙니다.** 정기보고서에 모든 사업 활동이 실리지는 않으며, 특히 제휴·외부 솔루션 도입은 따로 적을 항목이 없어 덜 실리는 것으로 보입니다.
- 분기보고서에는 회사 연혁이 없어서, 연혁에만 실리는 이벤트는 반기·사업보고서를 기대 보고서로 봅니다.
- 2026년 7월 이후 발표된 이벤트는 다음 보고서가 아직 없어 판정하지 않습니다(참고용).
"""
ACCURACY = """
- AI 용어가 있는 공시 문단만 LLM 으로 분류했습니다 (프롬프트 chunk_v7). 처음 보는 표본 29개를 A·B 가 합의 판정한 결과 표본 일치율은 **79%(23/29)**, 전체 문단의 단계 비중으로 가중한 **추정 정확도는 87%**입니다. 표본이 작아 추정치의 불확실성이 큽니다.
- '자체 개발·상용화'·'언급'은 표본에서 모두 맞았지만 **'도입·적용'(외부 AI 도입)은 4개 중 1개만 맞아** 믿기 어렵습니다 — '자체 개발·상용화' 비중 중심으로 보십시오.
- KT나스미디어는 연결 자회사였던 플레이디의 제품을 근거로 한 문단 13개(2025년 4월 매각 전 보고서)를 뺐습니다.
"""

EVENT_TYPE_KO = {"partnership": "제휴", "adoption": "외부 솔루션 도입", "launch": "출시", "campaign": "캠페인",
                 "organization": "조직", "award": "수상", "patent": "특허"}   # 리포트 4-3 유형 이름과 같게
# KPI 카드 아래 작은 글씨 — 판정이 무슨 뜻인지 한 줄로
VERDICT_NOTE = {"제때": "발표 뒤 첫 정기보고서에 실림", "늦게": "그다음 보고서부터 실림",
                "공시가 먼저": "발표 전 보고서에 이미 있음", "미확인": "수집 끝(2026년 10월)까지 확인되지 않음"}
X_RANGE = (pd.Timestamp("2023-09-01"), pd.Timestamp("2026-11-30"))   # 타임라인 가로축 (수집 기간 + 앞뒤 여유)


def _label(v: str) -> str:
    return verdict_label(v)   # 화면 이름: 제때 → 다음 공시에 반영, 늦게 → 늦게 반영 (theme.VERDICT_LABEL)


def _judged(r: pd.DataFrame) -> pd.DataFrame:
    return r[r.analysis_scope == "judge"]


def _headline(r: pd.DataFrame) -> None:
    """Q1·Q2 와 같은 형식: 큰 요약문 한 줄 + 판정별 KPI 카드 (탭 위, 세 탭 공통)"""
    j = _judged(r)
    n = {v: int((j["판정"] == v).sum()) for v in VERDICT_ORDER}
    found = n["제때"] + n["늦게"] + n["공시가 먼저"]
    if j.empty:   # 필터에 참고용(최근 발표) 이벤트만 남은 경우
        ui.lead(f"선택한 기업·기간에는 판정 대상 이벤트가 없습니다 "
                f'<span style="font-weight:500;opacity:.6;font-size:.9rem">(참고용 {len(r)}개는 이벤트 타임라인에서 볼 수 있습니다)</span>', big=True)
    else:
        lag = j[j.lag_days.notna() & j["판정"].isin(["제때", "늦게"])].lag_days
        sub = (f'<span style="display:block;font-weight:500;opacity:.6;font-size:.9rem">'
               f'(뉴스 발표 뒤 정기보고서에 실리기까지 중앙값 {int(lag.median())}일)</span>') if len(lag) else ""
        ui.lead(f"뉴스로 발표한 AI 이벤트 {len(j)}개 중 <b>{found}개({found / len(j):.0%})</b>가 정기공시에 실렸습니다.{sub}", big=True)
    ui.kpis([(_label(v), f"{n[v]}개", VERDICT_NOTE[v]) for v in VERDICT_ORDER], key="q3")


def _points(r: pd.DataFrame) -> None:
    """눈여겨볼 점 (데이터에서 계산 — 필터에 따라 바뀜)"""
    j = _judged(r)
    pts = []
    hit = j.groupby("corp_name")["판정"].apply(lambda x: int((x != "미확인").sum()))
    zero = [c for c, v in hit.items() if v == 0]
    if zero:
        pts.append(f"<b>{ui.esc(', '.join(zero))}</b>: 뉴스로 알려진 AI 이벤트가 공시에서 하나도 확인되지 않았습니다.")
    miss = j[j["판정"] == "미확인"]
    if len(miss):
        # 유형마다 '미확인 / 그 유형의 판정 대상' — 개수만 보면 출시(9개 중 2개)와 제휴(4개 중 3개)의 차이가 안 보임
        total, kinds = j.event_type.value_counts(), miss.event_type.value_counts()
        kinds = sorted(kinds.items(), key=lambda kv: (-kv[1] / total[kv[0]], -kv[1]))   # 미확인 비율이 높은 유형부터
        txt = ", ".join(f"{EVENT_TYPE_KO.get(t, '기타')} {total[t]}개 중 {v}개" for t, v in kinds)
        hard = [EVENT_TYPE_KO[t] for t in ("partnership", "adoption") if t in dict(kinds)]
        # 해석은 필터 결과에 제휴·도입이 있을 때만, 단정하지 않고 (리포트 4-3: 분석 대상에서 관찰된 경향)
        why = (f" {'·'.join(hard)}처럼 정기보고서에 따로 적을 항목이 없는 유형이 덜 실리는 것으로 보입니다." if hard else "")
        pts.append(f"공시에서 미확인 <b>{len(miss)}개</b>: {txt}.{why} 공시에서 확인되지 않았다고 실행하지 않았다는 뜻은 아닙니다.")
    late = j[j["판정"] == "늦게"]
    if len(late):
        pts.append(f"늦게 반영 <b>{len(late)}개</b>: " + ", ".join(ui.esc(e) for e in late.event_name) + ".")
    ref = int((r.analysis_scope == "reference").sum())
    if ref:
        pts.append(f"2026년 7월 이후 발표된 <b>{ref}개</b>는 다음 보고서가 아직 없어 판정하지 않았습니다 (참고용).")
    if pts:
        st.markdown('<ul class="q3-points">' + "".join(f"<li>{p}</li>" for p in pts) + "</ul>", unsafe_allow_html=True)
    else:
        st.caption("선택한 기업·기간에 따로 짚을 점이 없습니다.")


def _hbar_layout(fig, n_rows: int):
    """가로 누적 막대 공통: 막대 끝 둥글게 · 격자 없음 · 범례 오른쪽 위 (Q2 이슈 순위와 같은 모양)"""
    fig = style(fig, max(240, 44 * n_rows + 70))
    fig.update_layout(barmode="stack", bargap=.4, barcornerradius=6,
                      legend=dict(x=1, xanchor="right", y=1.0, yanchor="bottom", orientation="h"))
    fig.update_yaxes(title=None, showgrid=False)
    return fig


def _by_corp(j: pd.DataFrame):
    c = colors()
    t = pd.crosstab(j.corp_name, j["판정"]).reindex(columns=VERDICT_ORDER, fill_value=0)
    t = t.loc[t.sum(axis=1).sort_values().index]
    fig = go.Figure()
    for v in VERDICT_ORDER:
        fig.add_bar(y=t.index, x=t[v], name=_label(v), orientation="h",
                    marker=dict(color=c["verdict"][v], line=dict(color=c["surface"], width=2)),
                    text=[str(x) if x else "" for x in t[v]], textposition="inside", insidetextanchor="middle",
                    hovertemplate="%{y} · " + _label(v) + " %{x}개<extra></extra>")
    total = t.sum(axis=1)   # 막대 끝 합계 (Q2 이슈 순위처럼 막대 밖에 숫자)
    fig.add_scatter(x=total, y=t.index, mode="text", text=[f"  {x}개" for x in total], textposition="middle right",
                    textfont=dict(color=c["text2"], size=12), hoverinfo="skip", showlegend=False, cliponaxis=False)
    fig = _hbar_layout(fig, len(t))
    fig.update_xaxes(title=None, showgrid=False, showticklabels=False, zeroline=False, range=[0, total.max() * 1.12])
    return fig


def _year_axis(fig) -> None:
    """가로축을 Q1 월별 차트와 같은 형식으로: 3개월마다 'N월' + 아래 연도 칸(해 경계에 옅은 세로선)"""
    c = colors()
    lo, hi = X_RANGE
    # 'N월' 글자는 그 달 가운데(15일)에 — 해 경계선(1월 1일)이 '1월' 글자를 가로지르지 않게 (Q1 은 막대 가운데가 달 가운데)
    ticks = [d + pd.Timedelta(days=14) for d in pd.date_range(lo, hi, freq="MS") if d.month in (1, 4, 7, 10)]
    fig.update_xaxes(title=None, range=[lo, hi], tickvals=ticks, ticktext=[f"{d.month}월" for d in ticks],
                     tickangle=0, showgrid=False, automargin=False)
    for y in range(lo.year + 1, hi.year + 1):   # 해 경계: 그림 안 + 날짜 줄 아래까지 (픽셀 단위라 차트 높이와 상관없이 같은 길이)
        d = pd.Timestamp(f"{y}-01-01")
        fig.add_shape(type="line", xref="x", yref="paper", x0=d, x1=d, y0=0, y1=1, line=dict(color=c["grid"], width=1.5), layer="below")
        fig.add_shape(type="line", xref="x", yref="paper", ysizemode="pixel", yanchor=0, x0=d, x1=d, y0=-48, y1=0,
                      line=dict(color=c["grid"], width=1.5), layer="below")
    for y in range(lo.year, hi.year + 1):
        a, b = max(lo, pd.Timestamp(f"{y}-01-01")), min(hi, pd.Timestamp(f"{y}-12-31"))
        fig.add_annotation(x=a + (b - a) / 2, xref="x", y=0, yref="paper", yshift=-30, text=f"{y}년", showarrow=False,
                           xanchor="center", yanchor="top", font=dict(color=c["text2"], size=12))


def _timeline_chart(d: pd.DataFrame):
    c = colors()
    fig = go.Figure()
    for row in d[d.first_report_date.notna()].itertuples():
        fig.add_scatter(x=[row.official_date, row.first_report_date], y=[row.label, row.label], mode="lines",
                        line=dict(color=c["grid"], width=3), hoverinfo="skip", showlegend=False)
    fig.add_scatter(x=d.official_date, y=d.label, mode="markers", name="뉴스 발표일",
                    marker=dict(symbol="circle-open", size=12, color=c["news"], line=dict(width=2)),
                    customdata=d[["event_name", "판정명"]],
                    hovertemplate="%{customdata[0]}<br>뉴스 발표 %{x|%Y년 %m월 %d일}<br>판정: %{customdata[1]}<extra></extra>")
    for v in VERDICT_ORDER + ["참고용"]:
        s = d[(d["판정"] == v) & d.first_report_date.notna()]
        if len(s):
            fig.add_scatter(x=s.first_report_date, y=s.label, mode="markers", name=_label(v),
                            marker=dict(size=13, color=c["verdict"][v], line=dict(color=c["surface"], width=2)),
                            customdata=s[["event_name", "first_report", "lag_days"]],
                            hovertemplate="%{customdata[0]}<br>%{customdata[1]} · 접수 %{x|%Y년 %m월 %d일}<br>발표 후 %{customdata[2]}일<extra></extra>")
    miss = d[d["판정"] == "미확인"]
    if len(miss):
        fig.add_scatter(x=miss.official_date, y=miss.label, mode="text", text=["   공시에서 미확인"] * len(miss),
                        textposition="middle right", textfont=dict(color=c["muted"], size=12), hoverinfo="skip", showlegend=False)
    fig.update_yaxes(categoryorder="array", categoryarray=list(d.label), autorange="reversed", title=None, showgrid=False)
    fig = style(fig, max(260, 46 * len(d) + 110))
    _year_axis(fig)
    fig.update_layout(legend=dict(x=1, xanchor="right"), margin=dict(l=10, r=10, t=40, b=72))
    return fig


def _timeline(r: pd.DataFrame) -> None:
    with st.container(border=True, key="card_q3_timeline"):
        ui.section("이벤트 타임라인", ui.ICON_CLOCK)
        st.caption("기업을 고르면 그 기업의 이벤트가 보입니다. 점에 마우스를 올리면 보고서 이름과 걸린 일수가 보입니다.")
        corp = st.radio("기업", sorted(r.corp_name.unique()), horizontal=True, key="q3_tl_corp", label_visibility="collapsed")
        d = r[r.corp_name == corp].sort_values("official_date").copy()
        d["label"] = d.event_name.str.slice(0, 26) + d.analysis_scope.map({"reference": " (참고용)"}).fillna("")
        st.plotly_chart(_timeline_chart(d), width="stretch", config={"displayModeBar": False})
        st.caption("* ○ 뉴스 발표일 → ● 그 이벤트가 처음 실린 정기보고서 접수일 (색은 판정)\n"
                   "* 공시에서 미확인 : 수집 기간 끝(2026년 10월)까지 정기보고서에서 확인되지 않음\n"
                   "* (참고용) : 2026년 7월 이후 발표돼 다음 보고서가 아직 없음")


def _levels(corps: list[str]) -> None:
    c = colors()
    lv = dl.chunk_levels()
    lv = lv[lv.corp_name.isin(corps) & lv.level.notna()]
    t = pd.crosstab(lv.corp_name, lv.level).reindex(columns=LEVEL_ORDER, fill_value=0)
    share = t.div(t.sum(axis=1), axis=0)
    order = list(share["L3"].sort_values().index)
    with st.container(border=True, key="card_q3_levels"):
        ui.section("기업별 공시 AI 단계 비중", ui.ICON_LAYERS)
        st.caption("정기공시에서 AI 를 말한 문단을 4단계로 나눈 비중입니다. 공시에 적힌 AI 서술의 차이이며, 실제 AI 기술력이나 사업 성과 순위는 아닙니다. "
                   "막대에 마우스를 올리면 비중과 문단 수가 보입니다.")
        fig = go.Figure()
        for lvl in LEVEL_ORDER:
            fig.add_bar(y=order, x=share.loc[order, lvl], name=LEVEL_NAME[lvl], orientation="h",
                        marker=dict(color=c["level"][lvl], line=dict(color=c["surface"], width=2)),
                        customdata=t.loc[order, lvl],
                        hovertemplate="%{y} · " + LEVEL_NAME[lvl] + " %{x:.0%} (%{customdata}개 문단)<extra></extra>")
        fig = _hbar_layout(fig, len(order))
        fig.update_xaxes(tickformat=".0%", range=[0, 1], title=None, showgrid=False)
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})   # 기업별 L3 비중 표는 뺐다 — 마우스를 올리면 비중·문단 수
        st.caption("\n".join(f"* {LEVEL_NAME[f'L{k}']} : {desc}" for k, (_, desc) in LEVELS.items()))


def _table(r: pd.DataFrame) -> None:
    with st.container(border=True, key="card_q3_table"):
        ui.section("이벤트 표", ui.ICON_TABLE)
        st.caption("표에서 볼 기업을 고를 수 있습니다. 'DART'·'기사'를 누르면 원문이 열립니다.")
        # 표에서만 쓰는 기업 체크박스 (사이드바 기업 필터로 고른 기업 안에서 다시 고른다)
        corps = sorted(r.corp_name.unique())
        keys = {c: f"q3_tbl_{c}" for c in corps}
        for k in keys.values():
            st.session_state.setdefault(k, True)
        boxes, reset = st.columns([6, 1], vertical_alignment="center")
        with boxes:
            on = [c for col, c in zip(st.columns(len(corps)), corps) if col.checkbox(c, key=keys[c])]
        reset.button("초기화", key="q3_tbl_reset", width="stretch",
                     on_click=lambda: st.session_state.update({k: True for k in keys.values()}))
        r = r[r.corp_name.isin(on)]
        if r.empty:
            st.info("기업을 하나 이상 골라 주십시오.")
            return
        t = r.sort_values(["analysis_scope", "corp_name", "official_date"])[
            ["corp_name", "event_name", "official_date", "판정명", "first_report", "first_report_date", "lag_days", "dart_url", "primary_source_url"]].copy()
        # 빈 값이 표에 'None' 으로 보이지 않게 (공시에서 미확인·참고용 이벤트는 첫 반영 공시가 없음 — A 리뷰 PR #26)
        t[["first_report", "first_report_date"]] = t[["first_report", "first_report_date"]].fillna("—")
        t["lag_days"] = t.lag_days.map(lambda x: "—" if pd.isna(x) else f"{int(x)}일")
        t[["dart_url", "primary_source_url"]] = t[["dart_url", "primary_source_url"]].fillna("")   # 링크 칸은 빈칸
        st.dataframe(t, hide_index=True, width="stretch", height=min(35 * len(t) + 40, 800), column_config={
            "corp_name": "기업", "event_name": "이벤트", "official_date": "뉴스 발표일", "판정명": "판정",
            "first_report": "첫 반영 공시", "first_report_date": "접수일", "lag_days": "발표 후 일수",
            "dart_url": st.column_config.LinkColumn("공시 원문", display_text="DART"),
            "primary_source_url": st.column_config.LinkColumn("뉴스 원문", display_text="기사")})


def render(corps: list[str], period: tuple) -> None:
    ui.page_title("Q3. 뉴스에서 말한 AI, 정기공시에도 실렸나",
                  "뉴스로 발표한 AI 이벤트가 정기공시(사업·반기·분기보고서)에 실렸는지, 공시 속 AI 는 어느 단계인지 정리했습니다.")
    r = dl.reflection()
    r = r[r.corp_name.isin(corps) & (r.official_date >= str(period[0])) & (r.official_date <= str(period[1]))].copy()
    r["판정"] = r.verdict.map(verdict_ko)          # 안쪽 이름 (색·계산용)
    r["판정명"] = r["판정"].map(verdict_label)   # 화면 이름
    if r.empty:
        st.info("선택한 기업·기간에 이벤트가 없습니다.")
        return
    _headline(r)
    t1, t2, t3 = st.tabs(["요약", "이벤트 타임라인", "공시 AI 단계"])
    with t1:
        j = _judged(r)
        left, right = st.columns([5, 2], gap="medium")   # Q1 처럼 왼쪽 차트 카드 + 오른쪽 좁은 카드
        with left, st.container(border=True, key="card_q3_corp"):
            ui.section("기업별 반영 결과", ui.ICON_BARS)
            if j.empty:
                st.caption("판정 대상 이벤트가 없습니다.")
            else:
                st.plotly_chart(_by_corp(j), width="stretch", config={"displayModeBar": False})
                st.caption("* 판정 대상 : 2026년 6월까지 발표돼 다음 정기보고서가 나온 이벤트 (막대 끝 숫자 = 기업별 합계)")
        with right, st.container(border=True, key="card_q3_points"):
            ui.section("눈여겨볼 점", ui.ICON_ALERT)
            _points(r)
    with t2:
        _timeline(r)   # 타임라인에서 본 이벤트를 바로 아래 표에서 공시·기사 원문 링크로 확인
        _table(r)
    with t3:
        _levels(corps)

    with st.container(key="q3_more"):   # 아래 접기 메뉴: Q1·Q2 와 같은 제목 글꼴 (style.py)
        with st.expander("판정 기준"):
            st.markdown(HOW_TO_READ)
        with st.expander("분류 정확도"):
            st.markdown(ACCURACY)
