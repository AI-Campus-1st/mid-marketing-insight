"""
[B·D7] 상단 결론 카드의 공시 쪽 문장 — 공통 필터(기업·기간)에 맞춰 다시 계산

app.py 의 conclusion() TODO 중 B 몫 (decisions.md Day 6 '결론 카드 범위'):
    - 공시의 실행: reflection() 을 기업·이벤트 발표일로 걸러 판정 대상 수·반영 수
    - 판단: 필터 기업의 L3 비중 순위 (chunk_levels, Q3 '공시 AI 단계'와 같은 기준 — 플레이디 제품 근거 문단 제외, 기간은 걸지 않음)

    disclosure_rows(corps, period) -> [("공시", 문장), ("판단", 문장)]   # 문장은 app.py 카드처럼 <b> 쓰는 HTML
    disclosure_facts(corps, period) -> 숫자 dict (문장 없이 값만, 리포트·시험용)

'뉴스의 말'(A·D7)도 같은 꼴: news_rows(news, period, corps) -> [("뉴스", 문장)]
    - news = data_loader.filter_news(dl.news(), 전체 기간, corps, categories, …) — 기업·카테고리만 거른 전체 기간 기사
      (급증 기준선은 Q1 처럼 전체 기간으로 계산하고, 문장에는 선택 기간의 급증만)
    - 급증: Q1 과 같은 규칙 (trend.detect_spikes, 월평균 AI 기사 MIN_MONTHLY_AI 미만이면 판정 안 함)
    - 화제 이동: 관련 기사(news_v4)의 해마다 이슈 비중 — 첫해는 비중 1위, 다음 해부터는 전년보다 비중이 가장 많이 오른 이슈
      (전체 기준 2024 생성형 AI 콘텐츠 제작 → 2025 AI 에이전트 +19%p → 2026 AI 검색·GEO +10%p, 기존 고정 문장과 같음)
    - 기업을 고르면 (C안, 기업 제목 기사는 5개사 합쳐 137건이라 급증·화제 이동을 판정할 수 없음):
      그 기업 보도량 · 가장 많이 보도된 해(한 해 안이면 달) · 관련 기사의 주요 이슈 1~2개 (company_news_facts)
    scope_text(period, corps, categories) -> 결론 카드 제목 옆 범위 문구
app.py 의 conclusion() 은 news_rows + disclosure_rows 를 이어 붙인다.
"""
import sqlite3

import pandas as pd

from dashboard import data_loader as dl

MIN_YEAR_N = 50       # 화제 이동: 그해 관련 기사가 이보다 적으면 비중이 흔들려 뺀다
MIN_YEAR_MONTHS = 6   # 그해 기사가 있는 달이 이보다 적은 해(2023 은 10-19 부터 2~3개월)도 뺀다

ALL_CORPS = ["제일기획", "이노션", "플레이디", "인크로스", "KT나스미디어"]


def _corp_text(corps: list[str]) -> str:
    return "5개사" if set(corps) >= set(ALL_CORPS) else "·".join(c for c in ALL_CORPS if c in corps)


def _ga(word: str) -> str:
    """주격 조사: 받침 있으면 '이', 없으면 '가' (제일기획이 · 이노션이 · 인크로스가)"""
    ch = word[-1]
    return "이" if "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 else "가"


def disclosure_facts(corps: list[str], period: tuple) -> dict:
    """필터 기업·기간(이벤트 발표일)의 반영 판정 개수 + 기업별 L3 비중 (L3 비중은 5개사 모두 계산해 순위를 낸다)"""
    r = dl.reflection()
    r = r[r.corp_name.isin(corps) & (r.official_date >= str(period[0])) & (r.official_date <= str(period[1]))]
    j = r[r.analysis_scope == "judge"]
    v = j.verdict.fillna("")
    lag = j[v.eq("on_time") | v.str.startswith("delayed")].lag_days.dropna()
    lv = dl.chunk_levels()
    lv = lv[lv.level.notna()]
    l3 = lv.groupby("corp_name")["level"].apply(lambda s: (s == "L3").mean()).reindex(ALL_CORPS).dropna().sort_values(ascending=False)
    return {
        "judge": len(j), "reference": int((r.analysis_scope == "reference").sum()),
        "found": int((v != "not_found").sum()), "on_time": int((v == "on_time").sum()),
        "delayed": int(v.str.startswith("delayed").sum()), "preceded": int((v == "preceded").sum()),
        "not_found": int((v == "not_found").sum()),
        "median_lag": None if lag.empty else int(lag.median()),
        "l3_share": l3.to_dict(),                                   # 5개사 모두 (순위용)
        "l3_rank": {c: i + 1 for i, c in enumerate(l3.index)},
        "corps": [c for c in ALL_CORPS if c in corps],
    }


def disclosure_rows(corps: list[str], period: tuple) -> list[tuple[str, str]]:
    """결론 카드의 ('공시의 실행', 문장), ('판단', 문장). 공시 데이터가 없으면 안내 문장"""
    try:
        f = disclosure_facts(corps, period)
    except (sqlite3.Error, pd.errors.DatabaseError):  # 공시 테이블이 없는 DB (다른 오류는 숨기지 않음)
        return [("공시", "공시 데이터가 이 DB 에 없어 표시하지 않습니다.")]
    who = _corp_text(f["corps"])

    if f["judge"] == 0:
        ref = f" (발표가 최근이라 아직 판정하지 않은 참고용 이벤트 {f['reference']}개)" if f["reference"] else ""
        execution = f"선택한 기업·기간에는 반영을 판정할 AI 이벤트가 없습니다{ref}."
    else:
        head = f"{who}{_ga(who)} 발표한 AI 이벤트 {f['judge']}개 중"
        if f["found"] == 0:
            execution = f"{head} 정기공시(사업·반기·분기보고서)에서 확인된 것은 <b>없습니다</b>"
        else:
            parts = [f"다음 공시에 반영 {f['on_time']}"] + ([f"늦게 반영 {f['delayed']}"] if f["delayed"] else []) \
                + ([f"공시가 먼저 {f['preceded']}"] if f["preceded"] else [])
            found = f"{head} <b>{f['found']}개({f['found'] / f['judge']:.0%})</b>가 정기공시(사업·반기·분기보고서)에서 확인"
            if f["median_lag"] is not None:
                execution = f"{found}되고 ({' · '.join(parts)}), 실리기까지 보통 {f['median_lag']}일이 걸립니다"
            else:
                execution = f"{found}됩니다 ({' · '.join(parts)})"
        if f["not_found"]:
            execution += (f". 공시에서 미확인 {f['not_found']}개 — 제휴·외부 솔루션 도입은 공시에 실을 칸이 마땅치 않아, "
                          "미확인이 곧 실행 안 함은 아닙니다")
        execution += "."

    share, rank = f["l3_share"], f["l3_rank"]
    picked = [c for c in f["corps"] if c in share]
    # 형식: '~를 기준으로 비교했을 때, ~다.' (A 요청 — 기준과 결과를 한 문장으로)
    base = "대행사의 AI 경쟁력을 <b>정기공시로 확인되는 자체 개발·상용화</b>를 기준으로 비교했을 때"
    if not picked:
        judgement = f"{base}, 선택한 기업에는 비교할 공시 속 AI 문단이 없습니다."
    elif len(picked) == 1:
        c = picked[0]
        judgement = (f"{base}, {c}의 공시 속 AI 문단 중 '자체 개발·상용화' 비중은 <b>{share[c]:.0%}</b>"
                     f"(5개사 중 {rank[c]}위)입니다.")
    else:
        top, low = max(picked, key=share.get), min(picked, key=share.get)
        judgement = (f"{base}, 선택한 기업 중 '자체 개발·상용화' 비중이 가장 높은 곳은 <b>{top}({share[top]:.0%})</b>, "
                     f"가장 낮은 곳은 {low}({share[low]:.0%})입니다.")
    return [("공시", execution), ("결론", judgement)]


def news_facts(news: pd.DataFrame, period: tuple) -> dict:
    """첫 급증 월·배율·이후 최저 월 건수 + 해마다 화제 이슈 (news 는 기업·카테고리만 거른 전체 기간 기사)"""
    from analysis import trend
    from dashboard.tabs.q1_issue_trend import MIN_MONTHLY_AI
    from dashboard.tabs.q2_issues import TREND_ISSUES
    m = trend.monthly(news) if len(news) else pd.DataFrame(columns=["ai", "partial"])
    full_all = m[~m["partial"]] if len(m) else m
    judge = len(full_all) >= 7 and full_all["ai"].mean() >= MIN_MONTHLY_AI   # Q1 과 같은 규칙
    spike = None
    if judge:
        sp = m.join(trend.detect_spikes(m["ai"], m["partial"])[["ratio", "spike"]])
        lo, hi = period[0].strftime("%Y-%m"), period[1].strftime("%Y-%m")
        sp = sp[(sp.index >= lo) & (sp.index <= hi)]
        hits = list(sp.index[sp["spike"]])
        if hits:
            after = sp.loc[hits[0]:]
            after = after[~after["partial"]]
            spike = {"month": hits[0], "ratio": float(sp.loc[hits[0], "ratio"]), "floor": int(after["ai"].min())}
    rel = news[(news["is_relevant"] == 1) & (news["date"] >= period[0]) & (news["date"] <= period[1])].copy()
    path = []
    if len(rel):
        rel["year"] = pd.to_datetime(rel["date"]).dt.year
        n = rel.groupby("year").size()
        months = rel.groupby("year")["month"].nunique()
        n = n[(n >= MIN_YEAR_N) & (months.reindex(n.index) >= MIN_YEAR_MONTHS)]
        e = rel[rel["year"].isin(n.index)][["year", "issues"]].explode("issues")
        share = (e.groupby(["year", "issues"]).size().unstack(fill_value=0)
                 .reindex(index=n.index, columns=TREND_ISSUES, fill_value=0).div(n, axis=0) * 100)
        for i, y in enumerate(share.index):
            if i == 0:
                iss = share.loc[y].idxmax()
                path.append({"year": int(y), "issue": iss, "share": float(share.loc[y, iss]), "rise": None})
            else:
                d = share.loc[y] - share.iloc[i - 1]
                iss = d.idxmax()
                path.append({"year": int(y), "issue": iss, "share": float(share.loc[y, iss]), "rise": float(d[iss])})
    return {"judge_spike": judge, "spike": spike, "path": path, "articles": int(len(rel))}


def company_news_facts(news: pd.DataFrame, period: tuple) -> dict:
    """기업 필터일 때: 선택 기간 기사 수, 가장 많이 보도된 해(기간이 한 해 안이면 달), 관련 기사의 주요 이슈 (Q2 내용 이슈 기준)"""
    from dashboard.tabs.q2_issues import TREND_ISSUES
    d = news[(news["date"] >= period[0]) & (news["date"] <= period[1])].copy()
    out = {"articles": int(len(d)), "peak": None, "peak_n": 0, "issues": [], "relevant": 0}
    if d.empty:
        return out
    d["year"] = pd.to_datetime(d["date"]).dt.year
    if d["year"].nunique() > 1:
        c = d.groupby("year").size()
        out["peak"], out["peak_n"] = f"{int(c.idxmax())}년", int(c.max())
    else:
        c = d.groupby("month").size()
        y, mo = str(c.idxmax()).split("-")
        out["peak"], out["peak_n"] = f"{y}년 {int(mo)}월", int(c.max())   # '2025-11가' 대신 '2025년 11월이
    rel = d[d["is_relevant"] == 1]
    out["relevant"] = int(len(rel))
    if len(rel):
        share = rel["issues"].explode().value_counts().reindex(TREND_ISSUES).dropna() / len(rel) * 100
        out["issues"] = [(i, float(v)) for i, v in share.sort_values(ascending=False).head(2).items() if v > 0]
    return out


def news_rows(news: pd.DataFrame, period: tuple, corps: list[str]) -> list[tuple[str, str]]:
    """결론 카드의 ('뉴스의 말', 문장). 뉴스 결과가 없는 DB 면 안내 문장
    5개사 전체 = 급증 + 화제 이동 / 기업을 고르면 = 그 기업 보도량 · 많이 보도된 때 · 주요 이슈 (C안)"""
    if not set(corps) >= set(ALL_CORPS):
        try:
            g = company_news_facts(news, period)
        except (KeyError, sqlite3.Error, pd.errors.DatabaseError):
            return [("뉴스", "뉴스 데이터가 이 DB 에 없어 표시하지 않습니다.")]
        who = _corp_text(corps)
        if not g["articles"]:
            return [("뉴스", f"선택한 기간에는 제목에 {who}{_ga(who)} 나온 기사가 없습니다.")]
        text = f"제목에 {who}{_ga(who)} 나온 기사 <b>{g['articles']}건</b> 중 {g['peak']}{_ga(g['peak'])} {g['peak_n']}건으로 가장 많았"
        if g["issues"]:
            text += "고, 주로 " + "·".join(f"<b>{i}</b>({v:.0f}%)" for i, v in g["issues"]) + " 이슈를 다뤘습니다"
        else:
            text += "습니다"
        return [("뉴스", text + ".")]
    try:
        f = news_facts(news, period)
    except (KeyError, sqlite3.Error, pd.errors.DatabaseError):  # 뉴스 결과(llm_results 등)가 없는 DB
        return [("뉴스", "뉴스 데이터가 이 DB 에 없어 표시하지 않습니다.")]
    subj = "제공 뉴스의 AI 마케팅 기사"
    if f["spike"]:
        s = f["spike"]
        first = (f"{subj}는 <b>{s['month']}</b>에 직전 반년 평균의 {s['ratio']:.1f}배로 급증한 뒤 "
                 f"월 {s['floor']}건 아래로 내려가지 않았고")
    elif not f["judge_spike"]:
        first = f"{subj}는 수가 적어 급증 시점을 판정하지 않고"
    else:
        first = f"{subj}는 선택한 기간에 급증한 달이 없고"
    p = f["path"]
    if len(p) >= 2:
        steps = [f"{x['issue']}({x['year']})" if x["rise"] is None
                 else f"<b>{x['issue']}</b>({x['year']}, 비중 {x['rise']:+.0f}%p)" for x in p]
        second = "화제는 " + " → ".join(steps) + " 로 옮겨 가고 있습니다"
    elif len(p) == 1:
        second = f"관련 기사에서 가장 많이 다룬 이슈는 <b>{p[0]['issue']}</b>({p[0]['year']}, {p[0]['share']:.0f}%)입니다"
    elif f["articles"]:
        second = f"선택한 기간이 {MIN_YEAR_MONTHS}개월보다 짧거나 관련 기사가 적어 화제 이동은 판정하지 않습니다"
    else:
        second = "관련 기사가 없어 화제 이동은 판정하지 않습니다"
    return [("뉴스", f"{first}, {second}.")]


def scope_text(period: tuple, corps: list[str], categories: list[str] | None = None, all_categories: list[str] | None = None) -> str:
    """결론 카드 제목 옆 범위 문구 (예: '2025-01 – 2026-10 · 이노션·인크로스 기준')"""
    out = f"{period[0]:%Y-%m} – {period[1]:%Y-%m} · {_corp_text([c for c in ALL_CORPS if c in corps])} 기준"
    if categories is not None and all_categories is not None and set(categories) != set(all_categories):
        out += " · 뉴스 카테고리 " + "·".join(categories)
    return out


if __name__ == "__main__":   # 확인용: python -m dashboard.conclusion
    from datetime import date
    full = (date(2023, 10, 19), date(2026, 10, 19))
    for corps, period in [(ALL_CORPS, full), (["이노션", "인크로스"], full), (["제일기획"], full),
                          (ALL_CORPS, (date(2025, 1, 1), date(2025, 12, 31))), (ALL_CORPS, (date(2026, 8, 1), date(2026, 10, 19)))]:
        print(f"\n[{', '.join(corps)} · {period[0]}~{period[1]}]")
        news = dl.filter_news(dl.news(), full, corps, ["기술 및 트렌드", "시장 및 경쟁", "공급망 및 인프라", "규제 및 정책"], ALL_CORPS)
        for k, s in news_rows(news, period, corps) + disclosure_rows(corps, period):
            print(f"  {k}: {s}")
