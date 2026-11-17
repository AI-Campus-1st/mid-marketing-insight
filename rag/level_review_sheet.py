"""
[B·D4] 공시 청크 AI 단계 검수 — 표본 50개 A·B 독립 판정 (docs/ai_level_guide.md 검수 방법)

하는 일
    make  : LLM 이 분류한 청크에서 기업당 10개씩 50개를 뽑아 A·B 각자 판정할 엑셀을 만든다.
            - 시트: '안내'(판정 기준 = config/ai_levels.py + 공시 판정 보충) / '판정'(판정 칸 드롭다운, 긴 글은 셀 안 줄바꿈)
            - 프롬프트를 고칠 때 본 시험 20개(--exclude-pilot 캐시)는 빼서 과적합 여부를 확인한다.
            - 기업마다 LLM 단계(무관·L0~L3)가 고르게 섞이도록 단계별로 먼저 하나씩 뽑고 나머지를 무작위로 채운다 (층화 표본).
              LLM 이 AI 문장을 하나도 못 찾은 청크('무관(항목 없음)')는 전부 넣는다.
            - 층화 표본이라 드문 단계가 많이 들어간다 → score 는 표본 정확도와 함께
              전체 459개의 LLM 단계 비중으로 가중한 정확도를 같이 낸다 (리포트에는 가중값 + '층화 표본' 표기).
            - 판정 시트에는 LLM 결과를 넣지 않는다 (서로의 판정·LLM 값을 보지 않고 판정). 정답 대조용 key 파일은 따로.
    score : 채운 시트를 읽어 LLM 과의 일치율을 출력하고 review_samples 에 저장한다.
            A·B 시트가 둘 다 있으면 A·B 일치율도 낸다. B 시트 하나뿐이면 A·B 가 상의해 채운 '합의 판정'으로 보고
            reviewer = 'AB' 로 저장한다 (Day 4: 독립 판정 2부 대신 합의 판정 1부, decisions.md).

판정 입력 ('판정' 칸 드롭다운): 0 / 1 / 2 / 3 / 무관 (빈칸은 아직 판정 안 함 → 집계 제외)

실행
    python rag/level_review_sheet.py make
    python rag/level_review_sheet.py score
출력: data/processed/review/ai_level_review_{A,B}.xlsx (판정용), ai_level_review_key.csv (LLM 정답, 판정자에게 보내지 않음)
"""
import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402
from config.ai_levels import BOUNDARY_RULES, DISCLOSURE_RULES, LEVELS  # noqa: E402

OUT = settings.DATA_DIR / "processed" / "review"
PILOT_CACHE = settings.DATA_DIR / "processed" / "llm_cache" / "chunk_classify_chunk_v1.jsonl"  # 시험 20개
PER_CORP = 10
SEED = 42
REVIEWERS = ("A", "B")
AI_RE = re.compile(settings.AI_TERM_PATTERN)
CHOICES = ["0", "1", "2", "3", "무관"]
# 판정 시트 칸 (데이터 칸 → 엑셀 머리글)
COLS = {"sample_no": "번호", "corp_name": "기업", "report_name": "보고서", "report_date": "접수일",
        "section": "섹션", "ai_parts": "AI 나오는 부분", "text": "청크 전문", "human_level": "판정", "note": "메모",
        "chunk_id": "chunk_id"}
WIDTHS = {"번호": 6, "기업": 12, "보고서": 18, "접수일": 11, "섹션": 22, "AI 나오는 부분": 60, "청크 전문": 80,
          "판정": 8, "메모": 30, "chunk_id": 22}
# 공시 판정 보충 (rag/classify_chunks.py 프롬프트와 같은 기준, decisions.md)
SUPPLEMENT = [
    "청크 안에 AI 문장이 여러 개면 '대상 기업 자신의 AI' 중 가장 높은 단계로 판정한다 (연혁·표는 항목마다 따로 본다).",
    "3 은 조건 하나만 맞아도 3: 자체 개발, AI 서비스·기능 출시, 전담 조직, 특허 (숫자가 없어도).",
    "2 와 3 은 'AI 가 들어간 결과물이 누구의 제품인가'로 가른다. 외부 모델(ChatGPT 등)을 써도 자사 제품·서비스로 출시·제공하면 3, "
    "외부 완성 솔루션을 쓰기만 하거나 다른 회사(모회사 포함) AI 서비스를 운영 대행하면 2.",
    "'당사의 ○○ 솔루션'처럼 회사(또는 연결 자회사)가 이름 붙인 솔루션은 자체 솔루션이다.",
    "시장·업계 전망은 0. 무관은 광고주 상품·캠페인·수상작 이름 속 AI / 영문 단어 속 글자 / AI 가 실제로 없음, 세 경우뿐.",
]


def guide_rows() -> list[list[str]]:
    rows = [["공시 청크 AI 단계 검수", ""],
            ["하는 법", "'판정' 시트의 '판정' 칸(노란색)에서 0·1·2·3·무관 중 하나를 고른다. 애매하면 '메모'에 이유를 적는다. "
                     "'AI 나오는 부분'은 AI 용어 앞뒤만 모은 것이고, 전체 문맥은 '청크 전문'에서 본다."],
            ["주의", "상대 판정이나 LLM 결과를 보지 않고 혼자 판정한다 (일치율을 재기 위해). 이 시트에는 LLM 결과가 없다."],
            ["", ""], ["[단계]", ""]]
    rows += [[f"{k} {name}", desc] for k, (name, desc) in LEVELS.items()]
    rows += [["무관", "회사의 AI 가 아니다 (아래 공시 추가 규칙 참고)"], ["", ""], ["[경계 규칙]", ""]]
    rows += [["", r] for r in BOUNDARY_RULES]
    rows += [["", ""], ["[공시 추가 규칙]", ""]] + [["", r] for r in DISCLOSURE_RULES]
    rows += [["", ""], ["[공시 판정 보충]", ""]] + [["", r] for r in SUPPLEMENT]
    return rows


def write_sheet(path: Path, sheet: pd.DataFrame) -> None:
    from openpyxl import load_workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        pd.DataFrame(guide_rows()).to_excel(xw, sheet_name="안내", index=False, header=False)
        sheet.rename(columns=COLS).to_excel(xw, sheet_name="판정", index=False)
    wb = load_workbook(path)
    g = wb["안내"]
    g.column_dimensions["A"].width, g.column_dimensions["B"].width = 20, 120
    g["A1"].font = Font(bold=True, size=13)
    for row in g.iter_rows():
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
            if str(c.value or "").startswith("["):
                c.font = Font(bold=True)
    ws = wb["판정"]
    ws.freeze_panes = "C2"
    head, judge = PatternFill("solid", fgColor="DDE5F0"), PatternFill("solid", fgColor="FFF2CC")
    dv = DataValidation(type="list", formula1='"' + ",".join(CHOICES) + '"', allow_blank=True)
    ws.add_data_validation(dv)
    for cell in ws[1]:
        cell.font, cell.fill = Font(bold=True), head
        ws.column_dimensions[cell.column_letter].width = WIDTHS.get(cell.value, 12)
        if cell.value == "판정":
            cell.fill = judge
            dv.add(f"{cell.column_letter}2:{cell.column_letter}{ws.max_row}")
            for c in ws[cell.column_letter][1:]:
                c.fill = judge
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(path)


def read_sheet(path: Path) -> pd.DataFrame:
    d = pd.read_excel(path, sheet_name="판정", dtype=str)
    return d.rename(columns={v: k for k, v in COLS.items()})


def label(x) -> str:
    """DB·시트 값을 '0'~'3' 또는 '무관' 으로 통일 (빈칸은 '')"""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    s = str(x).strip().replace("L", "").replace(".0", "")
    if s in ("무관", "null", "None", "-", "없음"):
        return "무관"
    return s if s in ("0", "1", "2", "3") else ""


def ai_parts(text: str, width: int = 60) -> str:
    """AI 용어 앞뒤 width 자씩 잘라 ' / ' 로 잇는다 (겹치는 구간은 합침)"""
    spans = []
    for m in AI_RE.finditer(text):
        a, b = max(0, m.start() - width), min(len(text), m.end() + width)
        if spans and a <= spans[-1][1]:
            spans[-1][1] = b
        else:
            spans.append([a, b])
    return " / ".join(text[a:b].replace("\n", " ") for a, b in spans)


def make() -> None:
    con = sqlite3.connect(settings.DB_PATH)
    df = pd.read_sql(
        """SELECT k.chunk_id, c.corp_name, d.title AS report_name, k.report_date, k.section, k.text,
                  k.ai_level, k.ai_domain, k.reason, k.prompt_version
           FROM disclosure_chunks k
           JOIN companies c ON c.corp_code = k.corp_code
           JOIN documents d ON d.doc_id = k.doc_id
           WHERE k.model IS NOT NULL""", con)
    if PILOT_CACHE.exists():
        pilot = {json.loads(line)["key"].split(":")[0] for line in PILOT_CACHE.read_text(encoding="utf-8").splitlines()}
        df = df[~df.chunk_id.isin(pilot)]
    df["llm_level"] = df.ai_level.map(lambda x: "무관" if pd.isna(x) else str(int(x)))

    picks = []
    for corp, g in df.groupby("corp_name"):
        g = g.sample(frac=1, random_state=SEED)
        no_mention = g[g.reason.str.startswith("무관(항목 없음)", na=False)]  # LLM 이 AI 문장을 못 찾은 청크는 모두 넣음
        first = pd.concat([no_mention, g.drop(no_mention.index).groupby("llm_level").head(1)])  # 단계마다 하나씩 먼저
        rest = g.drop(first.index)
        picks.append(pd.concat([first, rest]).head(PER_CORP))
    s = pd.concat(picks).sample(frac=1, random_state=SEED).reset_index(drop=True)  # 기업·단계 순서를 섞음
    s.insert(0, "sample_no", range(1, len(s) + 1))

    OUT.mkdir(parents=True, exist_ok=True)
    s["ai_parts"] = s.text.map(ai_parts)  # AI 용어가 나오는 부분만 (판정 보조, LLM 결과 아님)
    sheet = s[["sample_no", "corp_name", "report_name", "report_date", "section", "ai_parts", "text"]].assign(
        human_level="", note="", chunk_id=s.chunk_id)  # chunk_id 는 맨 오른쪽 (채점용)
    for r in REVIEWERS:
        path = OUT / f"ai_level_review_{r}.xlsx"
        if path.exists():
            sys.exit(f"{path} 가 이미 있습니다. 판정 내용을 지우지 않도록 멈춥니다 (다시 만들려면 파일을 옮기고 실행).")
        write_sheet(path, sheet)
    s[["sample_no", "chunk_id", "corp_name", "llm_level", "ai_domain", "reason", "prompt_version"]].to_csv(
        OUT / "ai_level_review_key.csv", index=False, encoding="utf-8-sig")
    print(f"표본 {len(s)}개 (시험 20개 제외) → {OUT}")
    print(pd.crosstab(s.corp_name, s.llm_level).to_string())


def score() -> None:
    key = pd.read_csv(OUT / "ai_level_review_key.csv", dtype=str)
    key["llm"] = key.llm_level.map(label)
    sheets = {}
    for r in REVIEWERS:
        f = OUT / f"ai_level_review_{r}.xlsx"
        if f.exists():
            d = read_sheet(f)
            sheets[r] = d.set_index("chunk_id").human_level.map(label)
    if not sheets:
        sys.exit("판정 시트가 없습니다")

    m = key.set_index("chunk_id")
    for r, v in sheets.items():
        m[r] = v
    con = sqlite3.connect(settings.DB_PATH)
    con.execute(f"DELETE FROM review_samples WHERE target = 'ai_level' AND chunk_id IN ({','.join('?' * len(m))})",
                list(m.index))
    consensus = list(sheets) == ["B"]  # B 시트 하나 = A·B 합의 판정
    for r in sheets:
        who = "AB" if consensus else r
        done = m[m[r] != ""]
        acc = (done[r] == done.llm).mean() if len(done) else float("nan")
        print(f"{'A·B 합의' if consensus else r}: 판정 {len(done)}/{len(m)}, LLM 과 일치 {acc:.0%}")
        notes = read_sheet(OUT / f"ai_level_review_{r}.xlsx").set_index("chunk_id").note
        con.executemany(
            """INSERT INTO review_samples (chunk_id, target, prompt_version, llm_label, human_label, is_correct, reviewer, note)
               VALUES (?, 'ai_level', ?, ?, ?, ?, ?, ?)""",
            [(cid, x.prompt_version, x.llm, x[r], int(x[r] == x.llm), who,
              None if pd.isna(notes.get(cid)) else notes.get(cid)) for cid, x in done.iterrows()])
    con.commit()

    # 층화 표본 보정: LLM 단계별 정확도 × 전체 분류 결과에서 그 단계의 비중
    full = pd.read_sql("SELECT ai_level FROM disclosure_chunks WHERE model IS NOT NULL", con).ai_level.map(
        lambda x: "무관" if pd.isna(x) else str(int(x))).value_counts(normalize=True)
    for r in sheets:
        done = m[m[r] != ""]
        by_level = (done[r] == done.llm).groupby(done.llm).mean()
        w = full.reindex(by_level.index).fillna(0)
        if w.sum():
            print(f"{'A·B 합의' if consensus else r}: 전체 단계 비중으로 가중한 LLM 정확도 {(by_level * w).sum() / w.sum():.0%} "
                  f"(단계별 {by_level.round(2).to_dict()})")

    if all(r in sheets for r in REVIEWERS):
        both = m[(m.A != "") & (m.B != "")]
        print(f"A·B 일치 {(both.A == both.B).mean():.0%} ({len(both)}개 중 {(both.A == both.B).sum()}개)")
        agreed = both[both.A == both.B]
        print(f"A·B 가 같게 본 {len(agreed)}개 기준 LLM 정확도 {(agreed.A == agreed.llm).mean():.0%}")
        diff = both[(both.A != both.B) | (both.A != both.llm)]
        if len(diff):
            print("\n[어긋난 표본] (회고·규칙 보완용)")
            print(diff[["sample_no", "corp_name", "llm", "A", "B"]].to_string())
    print("\n저장: review_samples (target = 'ai_level')")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="공시 청크 AI 단계 검수 표본")
    p.add_argument("action", choices=["make", "score"])
    {"make": make, "score": score}[p.parse_args().action]()
