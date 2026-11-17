"""정제·태깅 사람 검수용 엑셀 만들기 (Day 3, 담당 A)

python analysis/preprocess_review.py           → data/processed/review/preprocess_review.xlsx
python analysis/preprocess_review.py --score   → 판정이 채워진 엑셀을 읽어 시트별 정밀도 출력

시트
  1_정제_지운문구     정제 규칙이 지운 문구 (같은 문구는 한 줄로 묶고 건수 표시) → 지워도 되는 잡음인가
  2_정제로_빠진매칭   원문에선 AI 용어·기업명이 잡혔는데 정제로 지워진 것 → 지운 게 맞나
  3_기업태그          기사↔기업 태그 전수 (집계 대상) → 그 기업을 가리키는 게 맞나
  4_AI용어_표본       AI 용어가 잡힌 기사 표본 50 (무작위 30 + 1회만 잡힌 기사 20) → 인공지능 뜻이 맞나
  5_AI미태그_표본     AI 용어가 안 잡힌 기사 표본 25 → 놓친 AI 언급이 있나 (재현율 확인)
  6_중복검수          제공 뉴스 중복 표시 24건과 남긴 기사의 짝 → 같은 기사가 맞나
판정 칸은 O / X (드롭다운), 메모 칸은 자유
"""

import argparse
import difflib
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402
from analysis.preprocess import AI_RE, CORP_RE, OUT_CSV, as_text, clean_body, in_spans, load_news  # noqa: E402

REVIEW_XLSX = settings.DATA_DIR / "processed" / "review" / "preprocess_review.xlsx"
SEED = 42
JUDGE, MEMO = "판정(O/X)", "메모"


def snippet(text: str, start: int, end: int, w: int = 40) -> str:
    return ("…" + text[max(0, start - w):start] + "【" + text[start:end] + "】" + text[end:end + w] + "…")


def snippets(text: str, pat: re.Pattern, k: int = 3) -> str:
    return "\n".join(snippet(text, m.start(), m.end()) for m in list(pat.finditer(text))[:k])


# ── 시트 만들기 ───────────────────────────────────────
def sheet_removed(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, lost = [], []
    for r in raw[raw["is_duplicate"] == 0].itertuples():
        _, spans = clean_body(r.body)
        for sp in spans:
            rows.append({"rule": sp["rule"], "text": re.sub(r"\s+", " ", sp["text"]), "doc_id": r.doc_id,
                         "앞 문맥": "…" + re.sub(r"\s+", " ", r.body[max(0, sp["start"] - 50):sp["start"]])})
        if not spans:
            continue
        pats = [("AI용어", AI_RE)] + [(c, p) for c, p in CORP_RE.items()]
        for kind, pat in pats:
            for m in pat.finditer(r.body):
                if in_spans(m.start(), spans):
                    lost.append({"doc_id": r.doc_id, "media": r.media, "종류": kind, "매칭": m.group(),
                                 "문맥": snippet(r.body, m.start(), m.end(), 60), JUDGE: "", MEMO: ""})
    df = pd.DataFrame(rows)
    grouped = (df.groupby(["rule", "text"], sort=False)
                 .agg(건수=("doc_id", "size"), 예시_doc_id=("doc_id", lambda s: ", ".join(s[:3])),
                      예시_앞문맥=("앞 문맥", "first"))
                 .reset_index().sort_values(["rule", "건수"], ascending=[True, False]))
    grouped = grouped.rename(columns={"rule": "규칙", "text": "지운 문구"})
    grouped[JUDGE], grouped[MEMO] = "", ""
    return grouped, pd.DataFrame(lost)


def sheet_corps(out: pd.DataFrame) -> pd.DataFrame:
    rows = []
    v = out[out["is_duplicate"] == 0].fillna({"body_clean": "", "corps": "", "corps_in_title": ""})
    for r in v[v["corps"] != ""].itertuples():
        for corp in r.corps.split("; "):
            pat = CORP_RE[corp]
            rows.append({"doc_id": r.doc_id, "date": r.published_date[:10], "media": r.media, "기업": corp,
                         "제목에": "Y" if corp in r.corps_in_title.split("; ") else "",
                         "본문 횟수": len(pat.findall(r.body_clean)), "제목": r.title,
                         "본문 문맥(최대 3곳)": snippets(r.body_clean, pat),
                         JUDGE: "", "중심기사(Y/N, 선택)": "", MEMO: ""})
    return pd.DataFrame(rows).sort_values(["기업", "date"])


def sheet_ai_sample(out: pd.DataFrame) -> pd.DataFrame:
    v = out[(out["is_duplicate"] == 0) & (out["ai_count"] > 0)].fillna({"body_clean": ""})
    rand = v.sample(30, random_state=SEED).assign(표본="무작위")
    once = v[(v["ai_count"] == 1) & ~v["doc_id"].isin(rand["doc_id"])].sample(20, random_state=SEED).assign(표본="1회만")
    rows = []
    for r in pd.concat([rand, once]).itertuples():
        text = r.title + " ‖ " + r.body_clean
        rows.append({"doc_id": r.doc_id, "date": r.published_date[:10], "media": r.media, "표본": r.표본,
                     "AI 횟수": r.ai_count, "잡힌 용어": r.ai_terms, "제목": r.title,
                     "문맥(최대 3곳, ‖ 앞은 제목)": snippets(text, AI_RE), JUDGE: "", MEMO: ""})
    return pd.DataFrame(rows)


def sheet_untagged(out: pd.DataFrame) -> pd.DataFrame:
    v = out[(out["is_duplicate"] == 0) & (out["ai_count"] == 0) & out["body_clean"].notna()]
    hint = re.compile(r"챗봇|머신러닝|딥러닝|에이전트|알고리즘|빅데이터|자동화|에이아이|오픈에이아이|챗지피티|지피티|ML|AX")
    rows = []
    for r in v.sample(25, random_state=SEED).itertuples():
        h = sorted(set(hint.findall(r.title + " " + r.body_clean)))
        rows.append({"doc_id": r.doc_id, "date": r.published_date[:10], "media": r.media, "제목": r.title,
                     "본문 앞 500자": r.body_clean[:500], "참고 단어": ", ".join(h),
                     "판정(O=AI 언급 없음 / X=놓침)": "", MEMO: ""})
    return pd.DataFrame(rows)


def sheet_dups(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.sort_values(["published_date", "doc_id"])
    keep = df[df["is_duplicate"] == 0]
    rows = []
    for d in df[df["is_duplicate"] == 1].itertuples():
        k = keep[(keep["title_norm"] == d.title_norm) & (keep["body_hash"] == d.body_hash)] if d.body_hash else keep.iloc[0:0]
        rule = "①"
        if k.empty:
            k, rule = keep[(keep["title_norm"] == d.title_norm) & (keep["media"] == d.media)], "②"
        k = k.iloc[0]
        b1, b2 = as_text(k.body), as_text(d.body)
        sim = difflib.SequenceMatcher(None, b1, b2, autojunk=False).ratio() if b1 and b2 else None
        rows.append({"규칙": rule, "남긴 기사": k.doc_id, "중복 기사": d.doc_id,
                     "남긴 날짜": k.published_date[:16], "중복 날짜": d.published_date[:16],
                     "남긴 매체": k.media, "중복 매체": d.media, "남긴 제목": k.title, "중복 제목": d.title,
                     "본문 유사도": None if sim is None else round(sim, 3), "본문 길이(남김/중복)": f"{len(b1)} / {len(b2)}",
                     "남긴 본문 앞 300자": b1[:300], "중복 본문 앞 300자": b2[:300],
                     "판정(O=같은 기사)": "", MEMO: ""})
    out = pd.DataFrame(rows).sort_values(["규칙", "남긴 날짜"]).reset_index(drop=True)
    out.insert(0, "#", range(1, len(out) + 1))
    return out


GUIDE = [
    ["정제·태깅 검수 파일", ""],
    ["만든 스크립트", "analysis/preprocess.py (정제·태깅), analysis/preprocess_review.py (이 파일)"],
    ["판정", "각 시트의 '판정' 칸에 O 또는 X (드롭다운). 애매하면 메모에 적기"],
    ["1_정제_지운문구", "O = 지워도 되는 잡음(저작권·제보 안내·지역 표시 등) / X = 기사 내용이 지워짐"],
    ["2_정제로_빠진매칭", "O = 잡음 속 용어라 빠지는 게 맞음 / X = 기사 내용 속 용어라 남겨야 함"],
    ["3_기업태그", "O = 그 기업(5개사)을 가리킴 / X = 다른 것을 가리킴. '중심기사'는 선택: 기사가 그 기업 이야기인지(Y) 지나가는 언급인지(N)"],
    ["4_AI용어_표본", "O = 잡힌 용어 중 하나 이상이 인공지능 뜻 / X = 전부 다른 뜻(예: 사람 이름 약자). 기사 주제가 AI 인지는 Q2 LLM 이 판정하므로 여기선 용어 뜻만"],
    ["5_AI미태그_표본", "O = AI 언급 없음(태그 안 된 게 맞음) / X = AI 를 다른 표현으로 언급했는데 놓침(참고 단어 칸 확인)"],
    ["6_중복검수", "O = 같은 기사(중복 표시 맞음) / X = 다른 기사(둘 다 집계해야 함)"],
]


def write_xlsx(sheets: dict[str, pd.DataFrame]) -> None:
    REVIEW_XLSX.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(REVIEW_XLSX, engine="openpyxl") as xw:
        pd.DataFrame(GUIDE).to_excel(xw, sheet_name="안내", index=False, header=False)
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name, index=False)
    wb = load_workbook(REVIEW_XLSX)
    wb["안내"].column_dimensions["A"].width = 22
    wb["안내"].column_dimensions["B"].width = 120
    wb["안내"]["A1"].font = Font(bold=True, size=13)
    head_fill, judge_fill = PatternFill("solid", fgColor="DDE5F0"), PatternFill("solid", fgColor="FFF2CC")
    for name in sheets:
        ws = wb[name]
        ws.freeze_panes = "B2"
        ws.auto_filter.ref = ws.dimensions
        dv = DataValidation(type="list", formula1='"O,X"', allow_blank=True)
        ws.add_data_validation(dv)
        for cell in ws[1]:
            cell.font, cell.fill = Font(bold=True), head_fill
            letter = cell.column_letter
            width = max(len(str(cell.value)) * 2, *(min(len(str(c.value or "")), 60) for c in ws[letter][1:200]), 6)
            ws.column_dimensions[letter].width = min(width + 2, 70)
            if str(cell.value).startswith("판정") or str(cell.value).startswith("중심기사"):
                ws.column_dimensions[letter].width = 12
                cell.fill = judge_fill
                if str(cell.value).startswith("판정"):
                    dv.add(f"{letter}2:{letter}{ws.max_row}")
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(REVIEW_XLSX)


def has_judgments() -> bool:
    for df in pd.read_excel(REVIEW_XLSX, sheet_name=None).values():
        col = next((c for c in df.columns if str(c).startswith("판정")), None)
        if col is not None and df[col].notna().any():
            return True
    return False


def score() -> None:
    xl = pd.read_excel(REVIEW_XLSX, sheet_name=None)
    for name, df in xl.items():
        col = next((c for c in df.columns if str(c).startswith("판정")), None)
        if col is None:
            continue
        j = df[col].astype(str).str.strip().str.upper()
        o, x = (j == "O").sum(), (j == "X").sum()
        rate = f"{o / (o + x):.1%}" if o + x else "-"
        extra = ""
        if name == "4_AI용어_표본":
            extra = "  " + ", ".join(f"{g}: {(j[df['표본'] == g] == 'O').sum()}/{j[df['표본'] == g].isin(['O', 'X']).sum()}"
                                     for g in ["무작위", "1회만"])
        print(f"{name:14s} 판정 {o + x}/{len(df)}  O {o}  X {x}  O비율 {rate}{extra}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--score", action="store_true", help="채운 엑셀의 판정 집계")
    ap.add_argument("--force", action="store_true", help="판정이 들어 있는 엑셀도 덮어쓰기")
    args = ap.parse_args()
    if args.score:
        return score()
    if REVIEW_XLSX.exists() and not args.force and has_judgments():
        sys.exit(f"{REVIEW_XLSX.name} 에 사람 판정이 들어 있어 덮어쓰지 않음. 새로 만들려면 파일을 옮기고 실행하거나 --force")
    con = sqlite3.connect(settings.DB_PATH)
    raw = load_news(con).merge(
        pd.read_sql("SELECT doc_id, title_norm, body_hash FROM documents WHERE news_origin = 'provided'", con), on="doc_id")
    out = pd.read_csv(OUT_CSV)
    removed, lost = sheet_removed(raw)
    sheets = {"1_정제_지운문구": removed, "2_정제로_빠진매칭": lost, "3_기업태그": sheet_corps(out),
              "4_AI용어_표본": sheet_ai_sample(out), "5_AI미태그_표본": sheet_untagged(out), "6_중복검수": sheet_dups(raw)}
    write_xlsx(sheets)
    print(f"→ {REVIEW_XLSX.relative_to(settings.ROOT)}")
    for name, df in sheets.items():
        print(f"  {name}: {len(df)}행")


if __name__ == "__main__":
    main()
