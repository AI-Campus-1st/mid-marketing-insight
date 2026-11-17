"""
[B·D4] 앵커 이벤트 ↔ 공시 청크 매칭 후보 생성과 사람 검증 반영 (decisions.md '공시 매칭 방식', A·B 합의)

make  : 이벤트마다 후보를 뽑아 event_dart_mapping 에 넣고, 검증자별 엑셀 시트를 만든다.
        - 범위: 같은 기업 청크 중 원본 접수일(report_date)이 기준일 −1년 ~ 수집 종료
        - 키워드 후보: config/event_search_terms.csv 검색어가 하나라도 들어간 청크 **전부**
        - 임베딩 후보: 키워드에 안 걸린 청크 중 이벤트 설명과 코사인 유사도 상위 5개 (rag/embeddings.py)
        - 실행할 때 쓴 검색어를 ai_events.search_terms 에 기록 (원본은 CSV 하나)
        - 이미 사람이 검증한 행(verified)은 그대로 둔다
        - 시트: 공시 담당 B 가 5개사 전부 검증. 이벤트별로 보고서 날짜순.
          같은 이벤트 안에서 다음 보고서에 같은 문장이 또 나오면 한 행으로 합치고 날짜만 적는다 (판정은 처음 행에만)
apply : 채운 시트의 판정(같음 / 다름)을 event_dart_mapping.verified 에 반영한다. 같음 = 1, 다름 = 0.
        합쳐 둔 같은 문장의 다른 보고서에도 같은 판정을 넣는다 (verify_note 에 '반복'). 판정 기준(실행 설계서 4장): 같은 기업 / 같은 대상(이름이 같거나 기능·대상이 같음) /
        시간 정합(기준일 이후 기간이거나, 이전이면 계획 맥락)

실행
    python rag/match_candidates.py make
    python rag/match_candidates.py apply
출력: data/processed/review/event_match_B.xlsx
"""
import argparse
import json
import re
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402
from rag.embeddings import chunk_embeddings, embed_texts  # noqa: E402

TERMS_CSV = settings.CONFIG_DIR / "event_search_terms.csv"
ANCHOR_CSV = settings.DATA_DIR / "processed" / "anchor_events.csv"
OUT = settings.DATA_DIR / "processed" / "review"
DRAFT_COLS = ["Claude 초안", "초안 근거"]   # 시트에 있으면 다시 만들 때 이어 받는 참고 초안 칸
TOP_K = 5
# 검증은 공시 담당 B 가 전부 (Day 4 변경, decisions.md). preceded·not_found 판정은 B 가 검증 후 한 번 더 확인
REVIEWER = {"이노션": "B", "KT나스미디어": "B", "인크로스": "B", "플레이디": "B", "제일기획": "B"}
TYPE_KO = {"launch": "출시·공개", "organization": "조직 신설", "adoption": "도입", "investment": "투자",
           "partnership": "제휴·협약", "patent": "특허", "campaign": "캠페인", "award": "수상"}
CHOICES = ["같음", "다름"]
VERIFIED = {"같음": 1, "다름": 0}


def report_kind(title: str) -> str:
    for k, v in (("사업보고서", "annual"), ("반기보고서", "half"), ("분기보고서", "quarter")):
        if k in title:
            return v
    return "other"


def snippet(text: str, terms: list[str], width: int = 120) -> str:
    """걸린 검색어 앞뒤, 없으면 앞부분"""
    pos = [text.find(t) for t in terms if t in text]
    i = min(pos) if pos else 0
    return text[max(0, i - width):i + width * 2].replace("\n", " ")


def norm(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", s)


def load(con) -> tuple[pd.DataFrame, pd.DataFrame]:
    ev = pd.read_sql("""SELECT e.event_id, e.corp_code, c.corp_name, e.event_name, e.event_type,
                               e.official_date, e.analysis_scope, e.ai_level_claimed, e.primary_source_url
                        FROM ai_events e JOIN companies c USING(corp_code) ORDER BY e.event_id""", con)
    terms = pd.read_csv(TERMS_CSV, dtype=str)[["event_id", "search_terms", "note"]].rename(columns={"note": "term_note"})
    names = pd.read_csv(ANCHOR_CSV, dtype=str)[["event_id", "all_names", "verification"]]
    ev = ev.merge(terms, on="event_id", how="left").merge(names, on="event_id", how="left")
    missing = ev[ev.search_terms.isna()].event_id.tolist()
    if missing:
        sys.exit(f"검색어가 없는 이벤트: {missing} (config/event_search_terms.csv 확인)")
    ev["terms"] = ev.search_terms.map(json.loads)
    ch = pd.read_sql("""SELECT k.chunk_id, k.corp_code, k.rcept_no, k.report_date, k.section, k.text, d.title
                        FROM disclosure_chunks k JOIN documents d USING(doc_id)""", con)
    return ev, ch


def event_desc(e) -> str:
    """유형 · 뉴스가 주장한 단계 · 다른 이름 (검증할 때 이벤트가 무엇인지 한 줄로)"""
    lv = "" if pd.isna(e.ai_level_claimed) else f" · 뉴스 주장 L{int(float(e.ai_level_claimed))}"
    names = "" if pd.isna(e.all_names) or e.all_names == e.event_name else f" · 다른 이름: {e.all_names}"
    return f"{TYPE_KO.get(e.event_type, e.event_type)}{lv}{names}"


def query_text(e) -> str:
    names = "" if pd.isna(e.all_names) else str(e.all_names)
    return f"{e.corp_name} {e.event_name} ({TYPE_KO.get(e.event_type, e.event_type)}). 관련 이름: {names}"


def make() -> None:
    con = sqlite3.connect(settings.DB_PATH)
    con.executescript((settings.ROOT / "schema.sql").read_text(encoding="utf-8"))  # 새 테이블만 만들어짐
    ev, ch = load(con)
    ids, mat = chunk_embeddings(con)
    row_of = {c: i for i, c in enumerate(ids)}
    qv = embed_texts([query_text(e) for e in ev.itertuples()])

    rows = []
    for e, q in zip(ev.itertuples(), qv):
        start = (date.fromisoformat(e.official_date) - timedelta(days=365)).isoformat()
        pool = ch[(ch.corp_code == e.corp_code) & (ch.report_date >= start)].copy()
        pool["score"] = mat[[row_of[c] for c in pool.chunk_id]] @ q
        pool["hit"] = pool.text.map(lambda t: [w for w in e.terms if w in t])
        kw = pool[pool.hit.str.len() > 0].assign(method="keyword")
        emb = pool[pool.hit.str.len() == 0].nlargest(TOP_K, "score").assign(method="embedding")
        for c in pd.concat([kw, emb]).itertuples():
            rows.append({"event_id": e.event_id, "chunk_id": c.chunk_id, "rcept_no": c.rcept_no,
                         "report_date": c.report_date, "report_kind": report_kind(c.title),
                         "match_method": c.method, "matched_terms": json.dumps(c.hit, ensure_ascii=False) if c.hit else None,
                         "match_score": round(float(c.score), 4)})
    cand = pd.DataFrame(rows)

    # DB 반영: 사람 검증 칸은 건드리지 않고 후보 정보만 갱신, 이번에 안 나온 미검증 자동 후보는 삭제
    con.executemany(
        """INSERT INTO event_dart_mapping (event_id, chunk_id, rcept_no, report_date, report_kind,
                                           match_method, matched_terms, match_score)
           VALUES (:event_id, :chunk_id, :rcept_no, :report_date, :report_kind, :match_method, :matched_terms, :match_score)
           ON CONFLICT(event_id, chunk_id) DO UPDATE SET rcept_no = excluded.rcept_no, report_date = excluded.report_date,
               report_kind = excluded.report_kind, match_method = excluded.match_method,
               matched_terms = excluded.matched_terms, match_score = excluded.match_score""",
        cand.to_dict("records"))
    keep = set(zip(cand.event_id, cand.chunk_id))
    old = con.execute("""SELECT event_id, chunk_id FROM event_dart_mapping
                         WHERE verified IS NULL AND match_method != 'manual'""").fetchall()
    con.executemany("DELETE FROM event_dart_mapping WHERE event_id = ? AND chunk_id = ?", [k for k in old if k not in keep])
    con.executemany("UPDATE ai_events SET search_terms = ? WHERE event_id = ?",
                    [(json.dumps(t, ensure_ascii=False), i) for i, t in zip(ev.event_id, ev.terms)])
    con.commit()

    s = cand.groupby(["event_id", "match_method"]).size().unstack(fill_value=0)
    print(f"후보 {len(cand)}행 (키워드 {s.get('keyword', pd.Series()).sum()} · 임베딩 {s.get('embedding', pd.Series()).sum()}), "
          f"이벤트 {cand.event_id.nunique()}개")
    write_sheets(con, ev, ch)


def write_sheets(con, ev: pd.DataFrame, ch: pd.DataFrame) -> None:
    from openpyxl import load_workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    m = pd.read_sql("SELECT * FROM event_dart_mapping", con).merge(
        ch[["chunk_id", "section", "text", "title"]], on="chunk_id").merge(ev, on="event_id")
    m = m.sort_values(["event_id", "report_date", "chunk_id"])
    out = []
    for eid, g in m.groupby("event_id", sort=True):
        first = {}   # 문장 키 → 처음 나온 행 (같은 문장이 다음 보고서에 또 나오면 그 행에 합친다)
        for r in g.itertuples():
            terms = json.loads(r.matched_terms) if isinstance(r.matched_terms, str) else []
            key = norm(snippet(r.text, terms, 60)) if terms else norm(r.text)
            if key in first:
                f = first[key]
                f["반복 보고서"].append(r.report_date)
                f["반복 chunk_id"].append(r.chunk_id)
                continue
            snip = snippet(r.text, terms) if terms else r.text[:300].replace("\n", " ")
            first[key] = row = {
                "검증": REVIEWER[r.corp_name], "이벤트": eid, "기업": r.corp_name, "이벤트명": r.event_name,
                "이벤트 설명": event_desc(r),
                "발표일": r.official_date, "범위": "판정" if r.analysis_scope == "judge" else "참고용",
                "보고서": r.title, "접수일": r.report_date,
                "시점": "발표 전" if r.report_date < r.official_date else "발표 후",
                "섹션": str(r.section).split(">")[-1].strip(), "방법": "키워드" if r.match_method == "keyword" else "임베딩",
                "걸린 검색어": ", ".join(terms), "유사도": r.match_score, "근거 부분": snip,
                "판정": {1: "같음", 0: "다름"}.get(r.verified, "") if pd.notna(r.verified) else "",
                "메모": r.verify_note if isinstance(r.verify_note, str) else "", "청크 전문": r.text, "chunk_id": r.chunk_id,
                "반복 보고서": [], "반복 chunk_id": []}
            out.append(row)
        for n, row in enumerate([o for o in out if o["이벤트"] == eid], 1):
            row["후보"] = n
    for row in out:
        reps = row.pop("반복 보고서")
        row["같은 문장이 또 나온 보고서"] = f"{len(reps)}개: " + ", ".join(reps) if reps else ""
        row["반복 chunk_id"] = ";".join(row["반복 chunk_id"])
    order = ["검증", "이벤트", "기업", "이벤트명", "이벤트 설명", "발표일", "범위", "후보", "보고서", "접수일", "시점", "섹션",
             "방법", "걸린 검색어", "유사도", "같은 문장이 또 나온 보고서", "근거 부분", "판정", "메모", "청크 전문",
             "chunk_id", "반복 chunk_id"]
    out = [{k: r[k] for k in order} for r in out]
    df = pd.DataFrame(out)

    guide = [["이벤트 ↔ 공시 매칭 검증", ""],
             ["먼저", "'이벤트 요약' 시트에서 이벤트가 무엇인지(유형·다른 이름·매칭 힌트·원문 링크) 확인한다."],
             ["하는 법", "이벤트별로 보고서 날짜순으로 읽고, 노란 '판정' 칸에서 같음 / 다름 을 고른다. 애매하면 '메모'."],
             ["같은 문장", "다음 보고서에 똑같은 문장이 또 나오면 행을 따로 만들지 않고 '같은 문장이 또 나온 보고서' 칸에 날짜만 적었다. "
                         "처음 나온 행만 판정하면 apply 할 때 그 보고서들에도 같은 판정이 들어간다."],
             ["같은 사건 기준", "① 같은 기업 ② 같은 대상: 서비스·조직 이름이 같거나, 일반 표현이라도 기능·대상이 같음 "
                           "③ 시간 정합: 발표 후 기간을 다루거나, 발표 전이면 계획·개발 맥락 (→ '공시가 먼저 말함' 후보)"],
             ["주의", "청크에 적힌 내용만 본다. 판정 결과(제때·늦게·공시가 먼저·미확인)는 시트가 아니라 DB 뷰가 계산한다."],
             ["두 번 보기", "결과를 바꾸는 판정은 전체 검증을 끝낸 뒤 한 번 더 본다: '발표 전'에 같음(공시가 먼저 말함), 한 이벤트의 후보가 모두 '다름'(공시에서 미확인)."],
             ["Claude 초안", "초록 칸 'Claude 초안'·'초안 근거'는 Claude 가 쓴 참고 초안이다. 판정은 원문을 보고 노란 '판정' 칸에 직접 적는다. '⚠경계'는 판단이 갈릴 수 있는 것."],
             ["범위", "'참고용'은 2026-06-30 이후 이벤트라 타임라인 표시용. 시간이 없으면 뒤로 미룬다."]]
    cnt = m.groupby("event_id").agg(키워드=("match_method", lambda x: (x == "keyword").sum()),
                                    임베딩=("match_method", lambda x: (x == "embedding").sum()))
    summ = pd.DataFrame([{
        "이벤트": e.event_id, "기업": e.corp_name, "이벤트명": e.event_name, "발표일": e.official_date,
        "범위": "판정" if e.analysis_scope == "judge" else "참고용", "유형": TYPE_KO.get(e.event_type, e.event_type),
        "뉴스 주장 단계": "" if pd.isna(e.ai_level_claimed) else f"L{int(float(e.ai_level_claimed))}",
        "다른 이름": "" if pd.isna(e.all_names) else e.all_names, "검색어": ", ".join(e.terms),
        "매칭 힌트": "" if pd.isna(e.term_note) else e.term_note,
        "이벤트 확인 기록": "" if pd.isna(e.verification) else e.verification,
        "원문 링크": "" if pd.isna(e.primary_source_url) else e.primary_source_url,
        "후보 (키워드)": int(cnt.loc[e.event_id, "키워드"]) if e.event_id in cnt.index else 0,
        "후보 (임베딩)": int(cnt.loc[e.event_id, "임베딩"]) if e.event_id in cnt.index else 0,
    } for e in ev.itertuples()])

    OUT.mkdir(parents=True, exist_ok=True)
    for who in sorted(set(REVIEWER.values())):
        path = OUT / f"event_match_{who}.xlsx"
        part = df[df["검증"] == who].drop(columns="검증")
        if path.exists():  # 기존 시트의 참고 초안·판정·메모를 (이벤트, chunk_id) 기준으로 이어 받는다
            old_all = pd.read_excel(path, sheet_name="매칭", dtype=str)
            if set(DRAFT_COLS) <= set(old_all.columns):
                part = part.merge(old_all[["이벤트", "chunk_id"] + DRAFT_COLS], on=["이벤트", "chunk_id"], how="left")
                cols = [c for c in part.columns if c not in DRAFT_COLS]
                i = cols.index("판정")
                part = part[cols[:i] + DRAFT_COLS + cols[i:]]
            old = old_all[old_all["판정"].notna() | old_all["메모"].notna()].set_index(["이벤트", "chunk_id"])[["판정", "메모"]]
            if len(old):
                order = list(part.columns)
                part = part.set_index(["이벤트", "chunk_id"])
                part.update(old)
                part = part.reset_index()[order]
                print(f"{path.name}: 기존 판정·메모 {len(old)}행 이어 받음")
        parts = summ[summ["기업"].map(REVIEWER) == who]
        with pd.ExcelWriter(path, engine="openpyxl") as xw:
            pd.DataFrame(guide).to_excel(xw, sheet_name="안내", index=False, header=False)
            parts.to_excel(xw, sheet_name="이벤트 요약", index=False)
            part.to_excel(xw, sheet_name="매칭", index=False)
        wb = load_workbook(path)
        g = wb["안내"]
        g.column_dimensions["A"].width, g.column_dimensions["B"].width = 16, 120
        g["A1"].font = Font(bold=True, size=13)
        for row in g.iter_rows():
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
        es = wb["이벤트 요약"]
        es.freeze_panes = "D2"
        ew = {"이벤트": 7, "기업": 11, "이벤트명": 26, "발표일": 11, "범위": 7, "유형": 10, "뉴스 주장 단계": 8,
              "다른 이름": 30, "검색어": 26, "매칭 힌트": 50, "이벤트 확인 기록": 40, "원문 링크": 30,
              "후보 (키워드)": 8, "후보 (임베딩)": 8}
        for c in es[1]:
            c.font, c.fill = Font(bold=True), PatternFill("solid", fgColor="DDE5F0")
            es.column_dimensions[c.column_letter].width = ew.get(c.value, 12)
        for row in es.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
        ws = wb["매칭"]
        ws.freeze_panes = "F2"
        widths = {"이벤트": 7, "기업": 11, "이벤트명": 22, "이벤트 설명": 26, "발표일": 11, "범위": 7, "후보": 5, "보고서": 18, "접수일": 11,
                  "시점": 8, "섹션": 18, "방법": 7, "걸린 검색어": 16, "유사도": 7, "근거 부분": 60,
                  "판정": 8, "메모": 25, "청크 전문": 80, "chunk_id": 22, "Claude 초안": 9, "초안 근거": 45,
                  "같은 문장이 또 나온 보고서": 22, "반복 chunk_id": 10}
        dv = DataValidation(type="list", formula1='"' + ",".join(CHOICES) + '"', allow_blank=True)
        ws.add_data_validation(dv)
        head, judge, band = PatternFill("solid", fgColor="DDE5F0"), PatternFill("solid", fgColor="FFF2CC"), PatternFill("solid", fgColor="F2F2F2")
        cols = {c.value: c.column_letter for c in ws[1]}
        for c in ws[1]:
            c.font, c.fill = Font(bold=True), head
            ws.column_dimensions[c.column_letter].width = widths.get(c.value, 12)
        dv.add(f"{cols['판정']}2:{cols['판정']}{ws.max_row}")
        ev_col = [c.value for c in ws[cols["이벤트"]]]
        for i, row in enumerate(ws.iter_rows(min_row=2), start=2):
            shade = sorted(set(ev_col[1:])).index(ev_col[i - 1]) % 2 == 1  # 이벤트마다 줄무늬
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
                if shade:
                    c.fill = band
            ws[f"{cols['판정']}{i}"].fill = judge
            for c in ("Claude 초안", "초안 근거"):
                if c in cols:
                    ws[f"{cols[c]}{i}"].fill = PatternFill("solid", fgColor="E2EFDA")
        wb.save(path)
        print(f"{path.name}: {len(part)}행, 이벤트 {part['이벤트'].nunique()}개")


def apply() -> None:
    con = sqlite3.connect(settings.DB_PATH)
    n = 0
    for who in ("A", "B"):
        path = OUT / f"event_match_{who}.xlsx"
        if not path.exists():
            continue
        d = pd.read_excel(path, sheet_name="매칭", dtype=str)
        d = d[d["판정"].notna() & (d["판정"].str.strip() != "")]
        bad = d[~d["판정"].str.strip().isin(CHOICES)]
        if len(bad):
            sys.exit(f"{path.name}: 판정 값이 이상한 행 {bad['후보'].tolist()} (같음/다름 중 하나)")
        for _, r in d.iterrows():
            j = r["판정"].strip()
            note = r["메모"] if isinstance(r["메모"], str) else None
            reps = r["반복 chunk_id"].split(";") if isinstance(r.get("반복 chunk_id"), str) and r["반복 chunk_id"] else []
            for cid, nt in [(r["chunk_id"], note)] + [(c, f"반복 (원본 {r['chunk_id']})") for c in reps]:
                con.execute("""UPDATE event_dart_mapping SET verified = ?, verified_by = ?, verify_note = ?
                               WHERE event_id = ? AND chunk_id = ?""", (VERIFIED[j], who, nt, r["이벤트"], cid))
                n += 1
    con.commit()
    print(f"반영 {n}행")
    print(pd.read_sql("""SELECT event_id, COALESCE(SUM(verified = 1), 0) AS 같음, COALESCE(SUM(verified = 0), 0) AS 다름, SUM(verified IS NULL) AS 미검증
                         FROM event_dart_mapping GROUP BY event_id""", con).to_string(index=False))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="이벤트 ↔ 공시 매칭 후보")
    p.add_argument("action", choices=["make", "apply"])
    {"make": make, "apply": apply}[p.parse_args().action]()
