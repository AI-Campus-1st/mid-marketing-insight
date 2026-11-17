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
from dashboard.theme import colors, mode, style

# 내용 이슈 (형식 이슈 '제휴·협력'·'실적·투자·상장'·'행사·시상'·'기타'는 추이 차트에서 뺌). 색은 이슈에 고정 (순위가 아니라)
TREND_ISSUES = ["생성형 AI 콘텐츠 제작", "AI 개인화·타겟팅", "마케팅 자동화·애드테크", "AI 에이전트", "AI 검색·GEO"]
CATEGORICAL = {   # dataviz 기본 범주 팔레트 슬롯 1~5 (라이트·다크 각각 검증된 값)
    "light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"],
    "dark": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"],
}
PARTIAL_Q = {"2023Q4", "2026Q4"}   # 수집 기간이 분기 중간에 시작·끝남
MIN_QUARTER_N = 20                  # 분기 관련 기사가 이보다 적으면 비중이 흔들려 점을 흐리게

ACCURACY = """
- **관련 기사** = LLM(`news_v4`)이 '광고·마케팅 산업 주체의 산업 활동'으로 분류한 기사. 새 표본 30건(A·B 합의 판정) 기준 관련 정확도 77%, 관련으로 고른 기사의 정밀도 약 73%.
- **이슈** = 고정 목록 15개 중 기사마다 1~3개. 새 표본 기준 사람이 고른 이슈와 하나 이상 겹침 90%, LLM 이 고른 이슈의 정밀도 63% · 재현율 64%.
- 비중 = 그 분기 관련 기사 중 그 이슈가 붙은 기사 비율 (한 기사에 이슈가 여럿이라 합이 100% 를 넘음).
- 2023Q4(10-19부터)·2026Q4(10-19까지)는 일부 기간만 들어간 분기라 빈 점으로 표시.
"""


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
                        marker=dict(size=7, color=col), customdata=n.loc[full],
                        hovertemplate=f"%{{x}} {iss} %{{y:.0f}}% (관련 기사 %{{customdata}}건)<extra></extra>")
        part = [q for q in share.index if q in PARTIAL_Q]
        if part:
            fig.add_scatter(x=part, y=share.loc[part, iss], mode="markers", showlegend=False,
                            marker=dict(size=8, color="rgba(0,0,0,0)", line=dict(color=col, width=2)),
                            hovertemplate=f"%{{x}} {iss} %{{y:.0f}}% (일부 기간)<extra></extra>")
    # 선이 5개라 끝 라벨 대신 범례(위쪽) + 마우스 올리면 이슈 이름 (끝 값이 비슷해 라벨이 겹침)
    fig.update_yaxes(title=None, ticksuffix="%", rangemode="tozero")
    fig.update_xaxes(title=None, categoryorder="array", categoryarray=list(share.index), showgrid=False)
    return style(fig, height=420)


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
    fig.update_layout(showlegend=False, height=height - 40, margin=dict(l=10, r=10, t=10, b=10),
                      font=dict(family="Pretendard, Apple SD Gothic Neo, Malgun Gothic, sans-serif"))
    plot = fig.to_html(full_html=False, include_plotlyjs="cdn", div_id="q2trend",
                       config={"displayModeBar": False, "responsive": True}, default_width="100%")
    chips = "".join(f'<span class="chip" data-i="{k}"><i style="background:{ic[iss]}"></i>{iss}</span>'
                    for k, iss in enumerate(TREND_ISSUES))
    return f"""
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css">
<style>
  body {{ margin: 0; font-family: Pretendard, 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif; background: transparent; }}
  .legend {{ display: flex; flex-wrap: wrap; gap: .4rem; margin: 0 0 4px 4px; }}
  .chip {{ display: inline-flex; align-items: center; gap: .4rem; font-size: 12.5px; color: {c["text2"]}; cursor: default;
          padding: .25rem .6rem; border-radius: 999px; border: 1px solid transparent; opacity: .55; transition: all .12s; }}
  .chip i {{ width: 9px; height: 9px; border-radius: 50%; display: inline-block; }}
  .chip.on {{ opacity: 1; color: {c["text"]}; font-weight: 700; border-color: {c["grid"]}; }}
  .hint {{ font-size: 11.5px; color: {c["muted"]}; margin-left: auto; align-self: center; }}
</style>
<div class="legend">{chips}<span class="hint">범례나 선에 마우스를 올리면 그 이슈가 보여요</span></div>
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


FORMAT_ISSUES = {"제휴·협력", "실적·투자·상장", "행사·시상"}   # 형식 이슈 (내용 이슈와 구분해 옅게)
LIGHT_BAR = {"light": "#d6e2f3", "dark": "#2b3a52"}


def _topn_chart(rel: pd.DataFrame):
    """이슈 순위 — 내용 이슈는 파랑, 형식 이슈(제휴·실적·행사)는 옅게 (레퍼런스: 강조만 진하게)"""
    c = colors()
    cnt = rel["issues"].explode().value_counts().drop("기타", errors="ignore").sort_values()
    col = [LIGHT_BAR[mode()] if i in FORMAT_ISSUES else c["verdict"]["제때"] for i in cnt.index]
    fig = go.Figure(go.Bar(x=cnt.values, y=cnt.index, orientation="h", marker=dict(color=col, cornerradius=6),
                           text=cnt.values, textposition="outside", textfont=dict(color=c["text2"]), cliponaxis=False,
                           hovertemplate="%{y} %{x}건<extra></extra>"))
    fig.update_xaxes(title=None, showgrid=False, showticklabels=False, range=[0, cnt.max() * 1.15])   # 막대 끝 숫자 자리
    fig.update_yaxes(title=None, showgrid=False)
    fig.update_layout(bargap=.35)
    return style(fig, height=max(260, 26 * len(cnt) + 40))


def _concerns(period) -> None:
    ui.section("우려 기사", "!", badge="사람 검수", badge_kind="orange", sub="카드를 누르면 원문")
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
    ui.mini_cards([{"tag": f"같은 사건 기사 {r.n}건" if r.n > 1 else "우려 논조", "title": r.title,
                    "meta": f"{r.media} · {r.date}", "url": r.url} for r in g.itertuples()])


def render(df: pd.DataFrame, period) -> None:
    ui.page_title("Q2. 무엇이 화제인가 — AI 마케팅 이슈의 이동", "LLM 이 광고·마케팅 관련으로 분류한 기사의 이슈 (news_v4)")
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
        ui.lead(f'{ui.pill("NEXT", "line")} 광고·마케팅 관련 기사에서 <b>{ui.esc(top)}</b> 비중이 '
                f'{early[top]:.0f}% → <b>{late[top]:.0f}%</b> 로 가장 많이 늘었다 '
                f'<span style="font-weight:500;opacity:.6;font-size:.9rem">({full[0]}–{full[2]} 평균 → {full[-3]}–{full[-1]} 평균)</span>')
    neutral = (rel["sentiment"] == "neutral").mean()
    ui.kpis([("관련 기사", f"{len(rel):,}건", f"전체 기사의 {len(rel) / len(df):.0%}"),
             ("논조 중립 (사실 전달)", f"{neutral:.0%}", "출시·제휴·실적 발표 위주"),
             ("우려 논조 (LLM)", f"{int((rel['sentiment'] == 'negative').sum())}건", "사람이 거른 목록은 아래")], key="q2")

    with st.container(border=True, key="card_q2_trend"):
        ui.section("이슈별 비중 (분기)", "◔", badge="내용 이슈 5개", badge_kind="blue", sub="빈 점 = 일부 기간만 들어간 분기")
        import streamlit.components.v1 as components
        components.html(_trend_html(share, n), height=440)
        st.caption("관련 기사 중 각 이슈가 붙은 기사 비율 (한 기사에 이슈가 여럿이라 합이 100% 를 넘음)")

    left, right = st.columns([2, 3], gap="large")
    with left, st.container(border=True, key="card_q2_rank"):
        ui.section("이슈 순위", "▤", sub="파랑 = 내용 · 옅음 = 형식")
        st.plotly_chart(_topn_chart(rel), width="stretch", config={"displayModeBar": False})
    with right:
        _concerns(period)

    with st.expander("읽는 법 · 정확도"):
        st.markdown(ACCURACY)
    with st.expander("표로 보기 (분기별 이슈 비중 %)"):
        st.dataframe(share.round(0).assign(관련기사=n), width="stretch")
