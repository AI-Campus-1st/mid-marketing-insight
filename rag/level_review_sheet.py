"""
[B·D4] 공시 청크 AI 단계 검수 — 표본 50개 A·B 독립 판정 (docs/ai_level_guide.md 검수 방법)

하는 일
    make  : LLM 이 분류한 청크에서 기업당 10개씩 50개를 뽑아 A·B 각자 판정할 CSV 를 만든다.
            - 프롬프트를 고칠 때 본 시험 20개(--exclude-pilot 캐시)는 빼서 과적합 여부를 확인한다.
            - 기업마다 LLM 단계(무관·L0~L3)가 고르게 섞이도록 단계별로 먼저 하나씩 뽑고 나머지를 무작위로 채운다 (층화 표본).
              LLM 이 AI 문장을 하나도 못 찾은 청크('무관(항목 없음)')는 전부 넣는다.
            - 층화 표본이라 드문 단계가 많이 들어간다 → score 는 표본 정확도와 함께
              전체 459개의 LLM 단계 비중으로 가중한 정확도를 같이 낸다 (리포트에는 가중값 + '층화 표본' 표기).
            - 판정 시트에는 LLM 결과를 넣지 않는다 (서로의 판정·LLM 값을 보지 않고 판정). 정답 대조용 key 파일은 따로.
    score : 두 사람이 채운 시트를 읽어 일치율(A·B 사이, 각자와 LLM)을 출력하고 review_samples 에 저장한다.

판정 시트 입력 (human_level 칸): 0 / 1 / 2 / 3 / 무관 (빈칸은 아직 판정 안 함 → 집계 제외)

실행
    python rag/level_review_sheet.py make
    python rag/level_review_sheet.py score
출력: data/processed/review/ai_level_review_{A,B}.csv, ai_level_review_key.csv
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

OUT = settings.DATA_DIR / "processed" / "review"
PILOT_CACHE = settings.DATA_DIR / "processed" / "llm_cache" / "chunk_classify_chunk_v1.jsonl"  # 시험 20개
PER_CORP = 10
SEED = 42
REVIEWERS = ("A", "B")
AI_RE = re.compile(settings.AI_TERM_PATTERN)


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
    sheet = s[["sample_no", "chunk_id", "corp_name", "report_name", "report_date", "section", "ai_parts", "text"]].assign(
        human_level="", note="")
    for r in REVIEWERS:
        path = OUT / f"ai_level_review_{r}.csv"
        if path.exists():
            sys.exit(f"{path} 가 이미 있습니다. 판정 내용을 지우지 않도록 멈춥니다 (다시 만들려면 파일을 옮기고 실행).")
        sheet.to_csv(path, index=False, encoding="utf-8-sig")
    s[["sample_no", "chunk_id", "corp_name", "llm_level", "ai_domain", "reason", "prompt_version"]].to_csv(
        OUT / "ai_level_review_key.csv", index=False, encoding="utf-8-sig")
    print(f"표본 {len(s)}개 (시험 20개 제외) → {OUT}")
    print(pd.crosstab(s.corp_name, s.llm_level).to_string())


def score() -> None:
    key = pd.read_csv(OUT / "ai_level_review_key.csv", dtype=str)
    key["llm"] = key.llm_level.map(label)
    sheets = {}
    for r in REVIEWERS:
        f = OUT / f"ai_level_review_{r}.csv"
        if f.exists():
            d = pd.read_csv(f, dtype=str)
            sheets[r] = d.set_index("chunk_id").human_level.map(label)
    if not sheets:
        sys.exit("판정 시트가 없습니다")

    m = key.set_index("chunk_id")
    for r, v in sheets.items():
        m[r] = v
    con = sqlite3.connect(settings.DB_PATH)
    con.execute(f"DELETE FROM review_samples WHERE target = 'ai_level' AND chunk_id IN ({','.join('?' * len(m))})",
                list(m.index))
    for r in sheets:
        done = m[m[r] != ""]
        acc = (done[r] == done.llm).mean() if len(done) else float("nan")
        print(f"{r}: 판정 {len(done)}/{len(m)}, LLM 과 일치 {acc:.0%}")
        notes = pd.read_csv(OUT / f"ai_level_review_{r}.csv", dtype=str).set_index("chunk_id").note
        con.executemany(
            """INSERT INTO review_samples (chunk_id, target, prompt_version, llm_label, human_label, is_correct, reviewer, note)
               VALUES (?, 'ai_level', ?, ?, ?, ?, ?, ?)""",
            [(cid, x.prompt_version, x.llm, x[r], int(x[r] == x.llm), r,
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
            print(f"{r}: 전체 단계 비중으로 가중한 LLM 정확도 {(by_level * w).sum() / w.sum():.0%} "
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
