"""
[A·D4] 뉴스 LLM 분류 사람 검수 (가이드 필수: 표본 검수로 분류 정확도 확인)

python analysis/llm_review.py              → data/processed/review/llm_review_{PROMPT_VERSION}.xlsx 생성
python analysis/llm_review.py --score      → 판정 집계(항목별 정확도, 혼동표) + review_samples 테이블에 저장

- 표본: classify_news.py --sample 50 과 같은 기사 50건 (data/processed/review/llm_sample_50.txt 에 고정)
- 앵커링 방지: '판정' 시트에는 LLM 값을 넣지 않는다. LLM 결과는 'LLM결과' 시트에 따로 (판정 끝난 뒤 보기)
- 50건 중 10건은 B 교차 검수 (B 판정 칸이 따로 있음) → A·B 일치율도 계산
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
from analysis.classify_news import CACHE, ISSUES, PROMPT_VERSION, SENTIMENT, sample_targets  # noqa: E402

N_SAMPLE, N_CROSS = 50, 10
XLSX = settings.DATA_DIR / "processed" / "review" / f"llm_review_{PROMPT_VERSION}.xlsx"
FIELDS = [  # (판정 칸 이름, LLM 키, review_samples.target, 선택지)
    ("관련(1/0)", "is_relevant", "relevance", ["1", "0"]),
    ("AI중심(1/0)", "is_ai_related", "ai_related", ["1", "0"]),
    ("감성", "sentiment", "sentiment", list(SENTIMENT)),
]
GUIDE = [
    ["뉴스 LLM 분류 검수", f"프롬프트 {PROMPT_VERSION} / 표본 {N_SAMPLE}건 (B 교차 {N_CROSS}건)"],
    ["순서", "① '판정' 시트를 LLM 결과를 보지 않고 채운다 → ② 다 채운 뒤 'LLM결과' 시트와 비교해도 됨 (판정은 고치지 않기)"],
    ["관련(1/0)", "1 = 광고·마케팅 산업 동향으로 의미 있음 (광고·마케팅 기술·서비스·캠페인·시장·규제, 대행사·매체·애드테크 사업). "
                 "0 = 마케팅이 지원 항목·교육 과목·나열 속 단어로만 나옴 (지자체 지원사업, 교육생 모집, 대학 소식, 다른 산업 기술 등)"],
    ["AI중심(1/0)", "1 = AI 가 제목·첫 문단의 핵심 / 0 = AI 가 배경·나열 속 하나"],
    ["감성", "중립 = 사실 전달 (출시·제휴·MOU·상장·실적·행사·교육 '발표'는 자랑 문구가 있어도 중립) / "
            "기대 = 기자·전문가·보고서가 전망·효과를 긍정적으로 '평가'하는 서술이 중심 / 우려 = 위험·규제 부담·부작용·한계 중심"],
    ["이슈", "LLM 이 고른 이슈는 'LLM결과' 시트에 있음 → 판정을 다 한 뒤 '이슈 맞음(O/X)' 칸만 채운다. 목록: " + " / ".join(ISSUES)],
    ["B 교차", "B교차 = Y 인 10행은 B 도 같은 칸(B_ 로 시작)을 서로 보지 않고 채운다"],
]


def sample_rows() -> pd.DataFrame:
    t = sample_targets(N_SAMPLE).reset_index(drop=True)
    cache = {r["doc_id"]: r for r in map(json.loads, CACHE.read_text(encoding="utf-8").splitlines())}
    missing = [d for d in t["doc_id"] if d not in cache]
    if missing:
        sys.exit(f"LLM 결과 없는 표본 {len(missing)}건 → 먼저 classify_news.py --sample {N_SAMPLE}")
    t["llm"] = t["doc_id"].map(cache)
    t["B교차"] = ["Y" if i % (N_SAMPLE // N_CROSS) == 0 else "" for i in range(len(t))]   # 5건마다 1건 → 10건
    return t


def make() -> None:
    if XLSX.exists():
        sys.exit(f"{XLSX.name} 이 이미 있음 (판정 보호). 다시 만들려면 파일을 옮기고 실행")
    t = sample_rows()
    judge = pd.DataFrame({
        "#": range(1, len(t) + 1), "doc_id": t["doc_id"], "B교차": t["B교차"], "date": t["published_date"].str[:10],
        "media": t["media"], "제목": t["title"], "본문 앞 700자": t["body_clean"].str[:700],
        **{f: "" for f, *_ in FIELDS}, "메모": "",
        **{f"B_{f}": "" for f, *_ in FIELDS}, "B_메모": "",
    })
    llm = pd.DataFrame({
        "#": range(1, len(t) + 1), "doc_id": t["doc_id"], "제목": t["title"],
        **{f"LLM {f}": t["llm"].map(lambda r, k=k: r[k]) for f, k, *_ in FIELDS},
        "LLM 이슈": t["llm"].map(lambda r: ", ".join(r["issue_keywords"])),
        "이슈 맞음(O/X)": "", "LLM 근거": t["llm"].map(lambda r: r["reason"]), "LLM 요약": t["llm"].map(lambda r: r["summary"]),
    })
    XLSX.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(XLSX, engine="openpyxl") as xw:
        pd.DataFrame(GUIDE).to_excel(xw, sheet_name="안내", index=False, header=False)
        judge.to_excel(xw, sheet_name="판정", index=False)
        llm.to_excel(xw, sheet_name="LLM결과", index=False)
    style()
    print(f"→ {XLSX.relative_to(settings.ROOT)} (판정 {len(judge)}행, B 교차 {(judge['B교차'] == 'Y').sum()}행)")


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


def score() -> None:
    judge = pd.read_excel(XLSX, sheet_name="판정", dtype=str)
    llm = pd.read_excel(XLSX, sheet_name="LLM결과", dtype=str).set_index("doc_id")
    rows = []
    print(f"=== {PROMPT_VERSION} 검수 ===")
    for f, key, target, _ in FIELDS:
        j = judge.assign(h=judge[f].map(norm), l=judge["doc_id"].map(llm[f"LLM {f}"]).map(norm))
        done = j[j["h"] != ""]
        acc = (done["h"] == done["l"]).mean() if len(done) else float("nan")
        print(f"{f:10s} 판정 {len(done)}/{len(j)}  정확도 {acc:.0%}" if len(done) else f"{f:10s} 판정 0")
        if len(done):
            print(pd.crosstab(done["h"].rename("사람"), done["l"].rename("LLM")).to_string(), "\n")
        b = j.assign(bh=j[f"B_{f}"].map(norm))
        both = b[(b["bh"] != "") & (b["h"] != "")]
        if len(both):
            print(f"  A·B 일치 {(both['h'] == both['bh']).mean():.0%} ({len(both)}건), B 정확도 {(both['bh'] == both['l']).mean():.0%}")
        for reviewer, col in [("A", "h"), ("B", "bh")]:
            src = b if reviewer == "B" else j
            for r in src[src[col] != ""].itertuples():
                hv, lv = getattr(r, col), r.l
                if target == "sentiment":
                    hv, lv = SENTIMENT.get(hv, hv), SENTIMENT.get(lv, lv)
                note = r.메모 if reviewer == "A" else r.B_메모
                rows.append((r.doc_id, target, PROMPT_VERSION, lv, hv, int(hv == lv), reviewer, None if pd.isna(note) else note))
    issue = llm["이슈 맞음(O/X)"].map(norm)
    if (issue != "").any():
        print(f"이슈       판정 {(issue != '').sum()}/{len(issue)}  O 비율 {(issue == 'O').mean() / (issue != '').mean():.0%}")
    if rows:
        with sqlite3.connect(settings.DB_PATH) as con:
            con.execute("DELETE FROM review_samples WHERE prompt_version = ? AND doc_id IS NOT NULL", (PROMPT_VERSION,))
            con.executemany("INSERT INTO review_samples (doc_id, target, prompt_version, llm_label, human_label, is_correct, "
                            "reviewer, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
        print(f"review_samples 저장 {len(rows)}행")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--score", action="store_true")
    score() if ap.parse_args().score else make()
