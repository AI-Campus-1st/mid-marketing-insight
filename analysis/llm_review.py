"""
[A·D4] 뉴스 LLM 분류 사람 검수 (가이드 필수: 표본 검수로 분류 정확도 확인)

python analysis/llm_review.py              → data/processed/review/llm_review_{REVIEW_VERSION}.xlsx 생성 (판정·LLM결과·이슈판정 시트)
python analysis/llm_review.py --version news_v4 --n 30          → 새 표본 30건 검수 엑셀 (llm_review_news_v4_n30.xlsx)
python analysis/llm_review.py --version news_v4 --n 30 --score  → 그 엑셀 채점
python analysis/llm_review.py --score      → 판정 집계(항목별 정확도, 혼동표) + review_samples 테이블에 저장
python analysis/llm_review.py --compare news_v2 news_v3 → 사람 판정 기준 버전 비교
python analysis/llm_review.py --issue-sheet → 기존 엑셀에 '이슈판정' 시트 추가 (LLM·사람 이슈를 같은 체크 칸으로 위아래 비교)

- 표본: classify_news.py --sample N 과 같은 기사 N건 (data/processed/review/llm_sample_N.txt 에 고정, 새 표본은 기존 표본 기사 제외)
- 50건(news_v2)은 프롬프트 개선용(v3·v4 를 여기에 맞춰 고침), 리포트용 정확도는 처음 보는 새 표본(news_v4 30건)으로 잰다
- 앵커링 방지: '판정' 시트에는 LLM 값을 넣지 않는다. LLM 결과는 'LLM결과' 시트에 따로 (판정 끝난 뒤 보기)
- B 교차 칸(B_…)은 비워 두면 A·B 일치율만 건너뜀 (Day 4: 교차 대신 합의 판정으로 바꿈, decisions.md)
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402
from analysis.classify_news import CACHE, ISSUES, SENTIMENT, sample_targets  # noqa: E402

# 사람 검수를 한 프롬프트 버전 (검수 엑셀·review_samples 의 기준). classify_news 의 현재 프롬프트 버전과 다를 수 있다
# — v3 이후 프롬프트는 이 엑셀의 사람 판정으로 --compare 해서 비교한다
REVIEW_VERSION = "news_v2"

N_SAMPLE, N_CROSS = 50, 10
REVIEWER = "AB"   # review_samples.reviewer — Day 4 부터 A·B 합의 판정 (decisions.md)
XLSX = settings.DATA_DIR / "processed" / "review" / f"llm_review_{REVIEW_VERSION}.xlsx"


def configure(version: str, n: int, reviewer: str) -> None:
    """검수할 프롬프트 버전·표본 크기에 맞춰 파일 이름 등을 바꾼다 (기본 = news_v2 표본 50건)"""
    global REVIEW_VERSION, N_SAMPLE, N_CROSS, XLSX, REVIEWER
    REVIEW_VERSION, N_SAMPLE, REVIEWER = version, n, reviewer
    if (version, n) != ("news_v2", 50):
        N_CROSS = 0
        XLSX = XLSX.with_name(f"llm_review_{version}_n{n}.xlsx")
    GUIDE[0][1] = f"프롬프트 {REVIEW_VERSION} / 표본 {N_SAMPLE}건" + (f" (B 교차 {N_CROSS}건)" if N_CROSS else "")
    if not N_CROSS:
        GUIDE[:] = [g for g in GUIDE if g[0] != "B 교차"]
FIELDS = [  # (판정 칸 이름, LLM 키, review_samples.target, 선택지)
    ("관련(1/0)", "is_relevant", "relevance", ["1", "0"]),
    ("AI중심(1/0)", "is_ai_related", "ai_related", ["1", "0"]),
    ("감성", "sentiment", "sentiment", list(SENTIMENT)),
]
GUIDE = [
    ["뉴스 LLM 분류 검수", f"프롬프트 {REVIEW_VERSION} / 표본 {N_SAMPLE}건 (B 교차 {N_CROSS}건)"],
    ["판정 방식", "A·B 가 같이 보면서 합의 판정 (decisions.md)"],
    ["순서", "① '판정' 시트를 LLM 결과를 보지 않고 채운다 → ② 다 채운 뒤 'LLM결과' 시트와 비교해도 됨 (판정은 고치지 않기)"],
    ["관련(1/0)", "1 = 광고·마케팅 산업 동향으로 의미 있음: 광고대행사·매체·광고 플랫폼·애드테크·마케팅 솔루션 기업의 사업, 브랜드의 마케팅 캠페인·마케팅 솔루션 도입, "
                 "광고·마케팅 시장 분석, 광고·개인정보 규제, 세미나·광고제 기사 중 발표 내용(사례·솔루션·트렌드)이 실린 것. "
                 "0 = 다른 산업의 AI·IT 소식(통신사 전시·클라우드·핀테크), 정부·지자체·공공기관 지원사업, 교육·강좌 모집·수료·성료, 대회·공모전 참가자 모집, "
                 "세미나·행사 개최·모집 안내만 있는 기사, 인물 영입·위촉, 일반 기업의 상품 출시·사옥 이전·실적"],
    ["AI중심(1/0)", "관련과 따로 판단. 1 = AI 가 제목·첫 문단의 핵심 (AI 가 주제인 교육·세미나·공모전도 1) / 0 = AI 가 배경·교육 과목·사업 분야 나열 속 하나"],
    ["감성", "중립 = 사실 전달 (출시·제휴·MOU·상장·실적·행사·교육 '발표'는 자랑 문구가 있어도 중립) / "
            "기대 = 기자·전문가·보고서가 전망·효과를 긍정적으로 '평가'하는 서술이 중심 / 우려 = 위험·규제 부담·부작용·한계 중심"],
    ["이슈", "'판정' 시트를 다 채운 뒤 '이슈판정' 시트에서 체크 (아래 줄 참고). 목록: " + " / ".join(ISSUES)],
    ["B 교차", "B교차 = Y 인 10행은 B 도 같은 칸(B_ 로 시작)을 서로 보지 않고 채운다"],
]


def sample_rows() -> pd.DataFrame:
    t = sample_targets(N_SAMPLE).reset_index(drop=True)
    cache = {r["doc_id"]: r for r in map(json.loads, CACHE.with_name(f"classify_news_{REVIEW_VERSION}.jsonl").read_text(encoding="utf-8").splitlines())}
    missing = [d for d in t["doc_id"] if d not in cache]
    if missing:
        sys.exit(f"LLM 결과 없는 표본 {len(missing)}건 → 먼저 classify_news.py --sample {N_SAMPLE}")
    t["llm"] = t["doc_id"].map(cache)
    t["B교차"] = ["Y" if N_CROSS and i % (N_SAMPLE // N_CROSS) == 0 else "" for i in range(len(t))]   # 5건마다 1건 → 10건
    return t


def make() -> None:
    if XLSX.exists():
        sys.exit(f"{XLSX.name} 이 이미 있음 (판정 보호). 다시 만들려면 파일을 옮기고 실행")
    t = sample_rows()
    judge = pd.DataFrame({
        "#": range(1, len(t) + 1), "doc_id": t["doc_id"], **({"B교차": t["B교차"]} if N_CROSS else {}), "date": t["published_date"].str[:10],
        "media": t["media"], "제목": t["title"], "본문 앞 700자": t["body_clean"].str[:700],
        **{f: "" for f, *_ in FIELDS}, "메모": "",
        **({**{f"B_{f}": "" for f, *_ in FIELDS}, "B_메모": ""} if N_CROSS else {}),
    })
    llm = pd.DataFrame({
        "#": range(1, len(t) + 1), "doc_id": t["doc_id"], "제목": t["title"],
        **{f"LLM {f}": t["llm"].map(lambda r, k=k: r[k]) for f, k, *_ in FIELDS},
        "LLM 이슈": t["llm"].map(lambda r: ", ".join(r["issue_keywords"])),
        "LLM 근거": t["llm"].map(lambda r: r["reason"]), "LLM 요약": t["llm"].map(lambda r: r["summary"]),
    })
    XLSX.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(XLSX, engine="openpyxl") as xw:
        pd.DataFrame(GUIDE).to_excel(xw, sheet_name="안내", index=False, header=False)
        judge.to_excel(xw, sheet_name="판정", index=False)
        llm.to_excel(xw, sheet_name="LLM결과", index=False)
    style()
    print(f"→ {XLSX.relative_to(settings.ROOT)} (판정 {len(judge)}행, B 교차 {N_CROSS}행)")
    make_issue_sheet()


def style() -> None:
    wb = load_workbook(XLSX)
    g = wb["안내"]
    g.column_dimensions["A"].width, g.column_dimensions["B"].width = 14, 130
    g["A1"].font = Font(bold=True, size=13)
    for row in g.iter_rows():
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    head, fill_a, fill_b = PatternFill("solid", fgColor="DDE5F0"), PatternFill("solid", fgColor="FFF2CC"), PatternFill("solid", fgColor="E2F0D9")
    widths = {"#": 4, "doc_id": 9, "B교차": 6, "date": 11, "media": 10, "제목": 40, "본문 앞 700자": 80, "메모": 20, "B_메모": 20,
              "LLM 이슈": 30, "LLM 근거": 40, "LLM 요약": 30}
    choices = {f: opts for f, _, _, opts in FIELDS}
    for name in ["판정", "LLM결과"]:
        ws = wb[name]
        ws.freeze_panes = "D2"
        for cell in ws[1]:
            v, L = str(cell.value), cell.column_letter
            cell.font, cell.fill = Font(bold=True), head
            ws.column_dimensions[L].width = widths.get(v, 13)
            key = v[2:] if v.startswith("B_") else v
            if key in choices or v == "이슈 맞음(O/X)":
                cell.fill = fill_b if v.startswith("B_") else fill_a
                opts = choices.get(key, ["O", "X"])
                dv = DataValidation(type="list", formula1='"' + ",".join(opts) + '"', allow_blank=True)
                ws.add_data_validation(dv)
                dv.add(f"{L}2:{L}{ws.max_row}")
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(XLSX)


def norm(v) -> str:
    s = str(v).strip()
    return "" if s in ("", "nan", "None") else s.removesuffix(".0")


# ── 이슈 판정 시트 (체크 방식) ───────────────────────────
ISSUE_SHEET = "이슈판정"
ISSUE_COL0 = 7   # G 열부터 이슈 15개


def is_true(v) -> bool:
    """Numbers·엑셀을 오가며 TRUE 가 bool / 'TRUE' / 1 / '✓' 로 바뀔 수 있어 모두 받는다"""
    return str(v).strip().upper() in ("TRUE", "1", "1.0", "✓", "V", "O", "참")


def make_issue_sheet() -> None:
    """기사마다 LLM 행(이슈 체크 채움) + 사람 행(빈 체크)을 위아래로 두고, 본문·메모를 함께 보여 주는 시트를 추가한다.
    기존 시트(판정·LLM결과)는 건드리지 않는다. 이미 이 시트가 있으면 멈춘다 (판정 보호)."""
    from openpyxl.utils import get_column_letter as L
    wb = load_workbook(XLSX)
    if ISSUE_SHEET in wb.sheetnames:
        sys.exit(f"'{ISSUE_SHEET}' 시트가 이미 있음 (판정 보호)")
    t = sample_rows().set_index("doc_id")
    judge = pd.read_excel(XLSX, sheet_name="판정", dtype=str)
    ws = wb.create_sheet(ISSUE_SHEET)
    last = ISSUE_COL0 + len(ISSUES) - 1
    c_match, c_memo = last + 1, last + 2
    head = ["#", "doc_id", "구분", "사람 관련", "제목", "LLM 이 본 본문 (제목 + 정제 본문 앞 1,500자)"] + ISSUES + ["LLM 과 비교", "메모"]
    for i, h in enumerate(head, 1):
        c = ws.cell(1, i, h)
        c.font, c.fill = Font(bold=True), PatternFill("solid", fgColor="DDE5F0")
        c.alignment = Alignment(wrap_text=True, vertical="bottom", text_rotation=90 if ISSUE_COL0 <= i <= last else 0)
    fill_llm, fill_me = PatternFill("solid", fgColor="EEF3FA"), PatternFill("solid", fgColor="FFF2CC")
    for k, r in enumerate(judge.itertuples()):
        a, b = 2 + 2 * k, 3 + 2 * k            # a = LLM 행, b = 사람 행
        tr = t.loc[r.doc_id]
        llm_issues = set(tr["llm"]["issue_keywords"])
        body = f"{tr['title']}\n\n{str(tr['body_clean'])[:1500]}"
        for col, val in [(1, int(r._1)), (2, r.doc_id), (4, norm(r._8)), (5, tr["title"]), (6, body)]:
            ws.cell(a, col, val)
            ws.merge_cells(start_row=a, start_column=col, end_row=b, end_column=col)
        ws.cell(a, 3, "LLM").fill = fill_llm
        ws.cell(b, 3, "사람").fill = fill_me
        for j, iss in enumerate(ISSUES):
            ws.cell(a, ISSUE_COL0 + j, iss in llm_issues).fill = fill_llm
            ws.cell(b, ISSUE_COL0 + j, False).fill = fill_me
        g0, g1 = L(ISSUE_COL0), L(last)
        ws.cell(b, c_match, f'=IF(COUNTIF({g0}{b}:{g1}{b},TRUE)=0,"",IF(SUMPRODUCT(--({g0}{a}:{g1}{a}={g0}{b}:{g1}{b}))={len(ISSUES)},"같음","다름"))')
        ws.merge_cells(start_row=a, start_column=c_memo, end_row=b, end_column=c_memo)
        ws.row_dimensions[a].height = ws.row_dimensions[b].height = 110
    widths = {1: 4, 2: 9, 3: 6, 4: 6, 5: 28, 6: 75, c_match: 8, c_memo: 30}
    for col in range(1, c_memo + 1):
        ws.column_dimensions[L(col)].width = widths.get(col, 4.5)
    ws.row_dimensions[1].height = 150
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top", horizontal="center" if ISSUE_COL0 <= c.column <= c_match else None)
    ws.freeze_panes = ws.cell(2, ISSUE_COL0)
    g = wb["안내"]
    g.append(["이슈판정 시트", "기사마다 위 행 = LLM 이 고른 이슈(TRUE), 아래 노란 행 = 사람이 맞다고 보는 이슈를 TRUE 로. 1~2개, 목록에 맞는 게 없으면 '기타'. "
              "Numbers: 이슈 15열을 선택 → 포맷 > 셀 > 데이터 포맷 '체크상자' 로 바꾸면 체크박스가 됨. 'LLM 과 비교' 는 사람 행을 채우면 같음/다름 표시. "
              "Q2 는 관련 기사만 쓰므로 '사람 관련' = 1 인 행만 채워도 됨"])
    wb.save(XLSX)
    print(f"'{ISSUE_SHEET}' 시트 추가 → {XLSX.relative_to(settings.ROOT)} ({len(judge)}건 × LLM·사람 2행)")


def score_issues() -> None:
    try:
        x = pd.read_excel(XLSX, sheet_name=ISSUE_SHEET, header=0)
    except ValueError:
        return
    x["doc_id"] = x["doc_id"].ffill()
    llm, me = x[x["구분"] == "LLM"].set_index("doc_id"), x[x["구분"] == "사람"].set_index("doc_id")
    pick = lambda r: {i for i in ISSUES if i in r.index and is_true(r[i])}
    pairs = [(pick(llm.loc[d]), pick(me.loc[d])) for d in me.index if pick(me.loc[d])]
    if not pairs:
        print("이슈       판정 0")
        return
    exact = sum(l == h for l, h in pairs) / len(pairs)
    any_hit = sum(bool(l & h) for l, h in pairs) / len(pairs)
    tp = sum(len(l & h) for l, h in pairs); fp = sum(len(l - h) for l, h in pairs); fn = sum(len(h - l) for l, h in pairs)
    print(f"이슈       판정 {len(pairs)}건  완전 일치 {exact:.0%} / 하나 이상 겹침 {any_hit:.0%} / "
          f"LLM 이 고른 이슈 정밀도 {tp / (tp + fp):.0%} / 사람 이슈 재현율 {tp / (tp + fn):.0%}")
    per = pd.DataFrame([{"이슈": i, "LLM": sum(i in l for l, _ in pairs), "사람": sum(i in h for _, h in pairs),
                         "둘 다": sum(i in l and i in h for l, h in pairs)} for i in ISSUES])
    print(per[(per["LLM"] + per["사람"]) > 0].to_string(index=False))


def score() -> None:
    judge = pd.read_excel(XLSX, sheet_name="판정", dtype=str)
    llm = pd.read_excel(XLSX, sheet_name="LLM결과", dtype=str).set_index("doc_id")
    rows = []
    print(f"=== {REVIEW_VERSION} 검수 ===")
    for f, key, target, _ in FIELDS:
        j = judge.assign(h=judge[f].map(norm), l=judge["doc_id"].map(llm[f"LLM {f}"]).map(norm))
        done = j[j["h"] != ""]
        acc = (done["h"] == done["l"]).mean() if len(done) else float("nan")
        print(f"{f:10s} 판정 {len(done)}/{len(j)}  정확도 {acc:.0%}" if len(done) else f"{f:10s} 판정 0")
        if len(done):
            print(pd.crosstab(done["h"].rename("사람"), done["l"].rename("LLM")).to_string(), "\n")
        b = j.assign(bh=j[f"B_{f}"].map(norm) if f"B_{f}" in j else "")
        both = b[(b["bh"] != "") & (b["h"] != "")]
        if len(both):
            print(f"  A·B 일치 {(both['h'] == both['bh']).mean():.0%} ({len(both)}건), B 정확도 {(both['bh'] == both['l']).mean():.0%}")
        for reviewer, col in [(REVIEWER, "h"), ("B", "bh")]:
            src = b if reviewer == "B" else j
            for r in src[src[col] != ""].itertuples():
                hv, lv = getattr(r, col), r.l
                if target == "sentiment":
                    hv, lv = SENTIMENT.get(hv, hv), SENTIMENT.get(lv, lv)
                note = r.메모 if reviewer == REVIEWER else getattr(r, "B_메모", None)
                rows.append((r.doc_id, target, REVIEW_VERSION, lv, hv, int(hv == lv), reviewer, None if pd.isna(note) else note))
    score_issues()   # 이슈는 '이슈판정' 시트(체크 방식)로 채점. review_samples.target 에 'issue' 가 없어 DB 저장은 안 함
    if rows:
        with sqlite3.connect(settings.DB_PATH) as con:
            con.execute("DELETE FROM review_samples WHERE prompt_version = ? AND doc_id IS NOT NULL", (REVIEW_VERSION,))
            con.executemany("INSERT INTO review_samples (doc_id, target, prompt_version, llm_label, human_label, is_correct, "
                            "reviewer, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
        print(f"review_samples 저장 {len(rows)}행")


def compare(versions: list[str]) -> None:
    """사람 판정(판정·이슈판정 시트)을 정답으로, 여러 프롬프트 버전의 캐시 결과를 같은 표본에서 비교 (DB 저장 없음)"""
    judge = pd.read_excel(XLSX, sheet_name="판정", dtype=str).set_index("doc_id")
    x = pd.read_excel(XLSX, sheet_name=ISSUE_SHEET)
    x["doc_id"] = x["doc_id"].ffill()
    me = x[x["구분"] == "사람"].set_index("doc_id")
    human_issue = {d: {i for i in ISSUES if is_true(me.loc[d, i])} for d in me.index}
    rel = judge["관련(1/0)"].map(norm)
    rows = []
    for v in versions:
        f = CACHE.with_name(f"classify_news_{v}.jsonl")
        c = {r["doc_id"]: r for r in map(json.loads, f.read_text(encoding="utf-8").splitlines())}
        ids = [d for d in judge.index if d in c]
        row = {"버전": v, "n": len(ids)}
        for name, key in [("관련(1/0)", "is_relevant"), ("AI중심(1/0)", "is_ai_related"), ("감성", "sentiment")]:
            row[name.split("(")[0]] = sum(norm(judge.loc[d, name]) == norm(c[d][key]) for d in ids) / len(ids)
        for scope, sel in [("이슈", ids), ("이슈(관련)", [d for d in ids if rel[d] == "1"])]:
            pairs = [(set(c[d]["issue_keywords"]), human_issue[d]) for d in sel if human_issue.get(d)]
            tp = sum(len(l & h) for l, h in pairs); fp = sum(len(l - h) for l, h in pairs); fn = sum(len(h - l) for l, h in pairs)
            row[f"{scope} 겹침"] = sum(bool(l & h) for l, h in pairs) / len(pairs)
            row[f"{scope} 정밀도"] = tp / (tp + fp)
            row[f"{scope} 재현율"] = tp / (tp + fn)
        row["기대/우려/중립"] = "/".join(str(sum(c[d]["sentiment"] == s for d in ids)) for s in ["기대", "우려", "중립"])
        rows.append(row)
    out = pd.DataFrame(rows).set_index("버전")
    num = out.select_dtypes("number").columns.drop("n")
    print(out.assign(**{k: out[k].map("{:.0%}".format) for k in num}).T.to_string())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--issue-sheet", action="store_true", help="기존 엑셀에 '이슈판정' 시트(체크 방식) 추가")
    ap.add_argument("--compare", nargs="+", metavar="VERSION", help="사람 판정 기준으로 프롬프트 버전 비교 (예: news_v2 news_v3)")
    ap.add_argument("--version", default="news_v2", help="검수할 프롬프트 버전 (엑셀·review_samples 기준)")
    ap.add_argument("--n", type=int, default=50, help="표본 크기")
    ap.add_argument("--reviewer", default="AB", help="review_samples.reviewer (기본 'AB' = A·B 합의 판정)")
    args = ap.parse_args()
    configure(args.version, args.n, args.reviewer)
    if args.compare:
        compare(args.compare)
    else:
        score() if args.score else make_issue_sheet() if args.issue_sheet else make()
