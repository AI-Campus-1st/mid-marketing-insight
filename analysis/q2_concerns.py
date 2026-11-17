"""
[A·D5] Q2 보조: '우려' 기사 목록 사람 검수 → 화면용 목록

python analysis/q2_concerns.py           → data/processed/review/q2_concern_review.xlsx (관련 = 1 이고 감성 = 우려인 기사)
python analysis/q2_concerns.py --apply   → 판정 O 인 기사만 data/processed/q2_concern_articles.csv (대시보드가 읽음)

왜 사람이 거르나: LLM(news_v4) 우려 판정 42건에 관련 판정이 틀린 기사(담배 규제·증시·밈 투자 등)가 섞여 있음 (decisions.md Q2 확정 행)
판정 기준: O = 광고·마케팅 업계의 우려를 다룬 기사(AI 광고·모델 논란, 과장 마케팅, 규제 부담 등) / X = 광고·마케팅과 무관하거나 우려 기사가 아님
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
from analysis.preprocess import OUT_CSV  # noqa: E402

PROMPT_VERSION = "news_v4"
XLSX = settings.DATA_DIR / "processed" / "review" / "q2_concern_review.xlsx"
OUT = settings.DATA_DIR / "processed" / "q2_concern_articles.csv"
JUDGE = "판정(O/X)"
GUIDE = [
    ["Q2 우려 기사 검수", f"관련 = 1 이고 감성 = 우려인 기사 ({PROMPT_VERSION}). 판정 O 인 기사만 Q2 화면의 '우려 기사 목록'에 나온다"],
    ["O", "광고·마케팅 업계의 우려를 다룬 기사 — AI 광고·AI 모델 논란, 과장·허위 마케팅('AI 워싱'), 광고·개인정보·AI 규제 부담, AI 로 인한 일자리·신뢰 문제 등"],
    ["X", "광고·마케팅과 무관(담배·주류 제품 규제 자체, 증시·밈 투자, 정치) 이거나 실제로는 우려 기사가 아님"],
    ["대표 기사", "같은 사건 기사가 여럿이면(예: 보그 AI 모델) 모두 O 로 두고 '같은 사건' 칸에 같은 이름을 적으면 화면에서는 하나로 묶어 보여 줌"],
]


def load() -> pd.DataFrame:
    with sqlite3.connect(settings.DB_PATH) as con:
        r = pd.read_sql("SELECT doc_id, is_ai_related, issue_keywords, summary, reason FROM llm_results "
                        "WHERE prompt_version = ? AND is_relevant = 1 AND sentiment = 'negative'", con, params=(PROMPT_VERSION,))
    news = pd.read_csv(OUT_CSV, usecols=["doc_id", "published_date", "media", "title", "body_clean"])
    return r.merge(news, on="doc_id").sort_values("published_date").reset_index(drop=True)


def make() -> None:
    if XLSX.exists():
        sys.exit(f"{XLSX.name} 이 이미 있음 (판정 보호). 다시 만들려면 파일을 옮기고 실행")
    d = load()
    sheet = pd.DataFrame({
        "#": range(1, len(d) + 1), "doc_id": d["doc_id"], "date": d["published_date"].str[:10], "media": d["media"],
        "제목": d["title"], "본문 앞 500자": d["body_clean"].str[:500],
        "LLM 이슈": d["issue_keywords"].map(lambda s: ", ".join(json.loads(s))), "LLM 요약": d["summary"], "LLM 근거": d["reason"],
        JUDGE: "", "같은 사건": "", "메모": "",
    })
    XLSX.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(XLSX, engine="openpyxl") as xw:
        pd.DataFrame(GUIDE).to_excel(xw, sheet_name="안내", index=False, header=False)
        sheet.to_excel(xw, sheet_name="판정", index=False)
    wb = load_workbook(XLSX)
    g = wb["안내"]
    g.column_dimensions["A"].width, g.column_dimensions["B"].width = 14, 120
    g["A1"].font = Font(bold=True, size=13)
    ws = wb["판정"]
    widths = {"#": 4, "doc_id": 9, "date": 11, "media": 11, "제목": 42, "본문 앞 500자": 70, "LLM 이슈": 22,
              "LLM 요약": 30, "LLM 근거": 30, JUDGE: 10, "같은 사건": 14, "메모": 24}
    for c in ws[1]:
        c.font, c.fill = Font(bold=True), PatternFill("solid", fgColor="DDE5F0")
        ws.column_dimensions[c.column_letter].width = widths.get(c.value, 12)
        if c.value in (JUDGE, "같은 사건", "메모"):
            c.fill = PatternFill("solid", fgColor="FFF2CC")
        if c.value == JUDGE:
            dv = DataValidation(type="list", formula1='"O,X"', allow_blank=True)
            ws.add_data_validation(dv)
            dv.add(f"{c.column_letter}2:{c.column_letter}{ws.max_row}")
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "F2"
    wb.save(XLSX)
    print(f"→ {XLSX.relative_to(settings.ROOT)} ({len(sheet)}건)")


def apply() -> None:
    j = pd.read_excel(XLSX, sheet_name="판정", dtype=str)
    judged = j[JUDGE].fillna("").str.strip().str.upper()
    if (judged == "").any():
        print(f"아직 판정 안 한 행 {(judged == '').sum()}건 — 판정한 행만 반영")
    keep = j[judged == "O"]
    d = load().set_index("doc_id").loc[keep["doc_id"]].reset_index()
    out = pd.DataFrame({"doc_id": d["doc_id"], "published_date": d["published_date"], "media": d["media"], "title": d["title"],
                        "issues": d["issue_keywords"], "summary": d["summary"],
                        "event_group": keep["같은 사건"].fillna("").values})
    out.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"O {len(keep)} / X {(judged == 'X').sum()} → {OUT.relative_to(settings.ROOT)} ({len(out)}건, 같은 사건 묶음 {out['event_group'].replace('', pd.NA).nunique()}개)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="판정 O 인 기사만 화면용 CSV 로")
    apply() if ap.parse_args().apply else make()
