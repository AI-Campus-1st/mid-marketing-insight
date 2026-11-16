"""제공 뉴스 본문 정제 + 정규표현식 태깅 (Day 3, 담당 A)

입력: project.db documents (제공 뉴스, news_origin = 'provided')
출력: data/processed/news_preprocessed.csv  (doc_id 마다 정제 본문 + 태그)
      --write-db 를 주면 news_company 테이블을 채운다 (사람 검수 후에만 실행)

정제 (원문 body 는 DB 에 그대로 두고, 정제본은 CSV 에만 둔다):
  제공 본문은 이미 1차 정제돼 있다 (이메일·URL·기자 바이라인·▶ 기호 0건).
  남은 잡음만 규칙으로 지운다. 규칙마다 이름이 있어 무엇을 지웠는지 검수 파일에서 확인할 수 있다.
  - 머리 지역·매체 표시  (서울 뉴스1), (평창 연합뉴스)
  - 꼬리 저작권 문구      Copyright All rights reserved. / 무단 전재… AI학습 이용 금지  ← 'AI' 오탐 원인
  - 꼬리 제보 안내        "여러분의 제보를 기다립니다" 등 매체 고정 문구
  - AI 작성 고지          "AI 매터스 제휴… 클로드·챗GPT 를 활용해 작성"            ← 'AI'·'GPT' 오탐 원인
  - 관련기사 링크·바로가기, 사진 제공 표기

태깅 (정제본 기준):
  - AI 용어: settings.AI_TERM_PATTERN (Day 3 확정)
  - 기업: CORP_ALIASES (KT나스미디어는 옛 이름 '나스미디어' 포함). 조인은 corp_code
"""

import argparse
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402
from analysis.event_review_sheet import CORP_ALIASES  # noqa: E402

OUT_CSV = settings.DATA_DIR / "processed" / "news_preprocessed.csv"
AI_RE = re.compile(settings.AI_TERM_PATTERN)

# ── 정제 규칙 ─────────────────────────────────────────
# (이름, 정규표현식, 적용 위치)  위치: 'head' = 본문 맨 앞만, 'tail' = 본문 뒤쪽 40% 안에서 시작할 때만, 'any' = 어디서나
# tail 규칙은 매칭 시작점부터 본문 끝까지 지운다 (꼬리 문구 뒤에는 기사 내용이 없음)
MEDIA_DATELINE = r"(?:연합뉴스|뉴스1|뉴시스)"
CLEAN_RULES = [
    ("머리_지역매체", re.compile(r"^\s*\((?:[가-힣]+\s)+" + MEDIA_DATELINE + r"?\s*\)|^\s*\((?:로이터|AFP|AP)\)"), "head"),
    ("머리_사진제공", re.compile(r"^\s*\([^()]{1,20}\s?제공\)"), "head"),
    ("AI작성고지", re.compile(r"이 기사는 AI 전문 매체 'AI 매터스'와 제휴를 통해 제공됩니다\.\s*"
                             r"(?:기사는 .{0,60}?활용해 작성되었습니다\.)?"), "any"),   # '클로드 3.5' 의 마침표 때문에 [^.] 대신 .{0,60}?
    ("바로가기링크", re.compile(r"\(\s*[^()]{0,20}바로가기\s*\)"), "any"),
    ("관련기사링크", re.compile(r"관련기사 보기 .{0,150}?news view \d+"), "any"),
    ("사진제공", re.compile(r"\(\s*사진\s?제공[^()]{0,40}\)"), "any"),
    ("꼬리_제보안내", re.compile(
        r"발로 뛰는|당신의 제보가 뉴스로|각종 비리와 부당대우|\S*는 (?:24시간 )?여러분의 제보|"
        r"카카오톡\s+'[^']*제보'|bbs report write|제보하기\s+전화|기자 프로필|"
        r"\S*에서는 연예인 및 연예계 종사자들과 관련된 제보|"
        # 연합뉴스TV: 본문 뒤 키워드 태그(예: 'AI마케팅  AI  데이터') + 기사문의 → 태그까지 지움
        #   태그는 단어 사이가 공백 2칸 이상 / 또는 마지막 문장 바로 뒤에 붙음
        r"(?:[^\s.!?\"'”]+\s{2,}){1,20}기사문의\s*및\s*제보|(?<=[.!?\"'”])\s+[^.!?\"'”]{0,100}?기사문의\s*및\s*제보"), "tail"),
    ("꼬리_기사목록", re.compile(r"기사 바로가기"), "tail"),   # 서울경제 지면 안내: '기사 바로가기 <다른 기사 제목>' 반복
    ("꼬리_저작권", re.compile(r"(?:한경닷컴 뉴스룸\s*)?(?:Copyright|ⓒ|©)|무단\s?전재[^.]{0,40}금지"), "tail"),
]
TAIL_FROM = 0.6   # tail 규칙: 본문 길이의 60% 지점 이후에서 시작하는 매칭만


def clean_body(body: str | None) -> tuple[str | None, list[dict]]:
    """정제 본문과 지운 구간 목록 [{rule, start, end, text}] (start·end 는 원문 기준 위치)"""
    if not body:
        return body, []
    spans = []
    for name, pat, where in CLEAN_RULES:
        if where == "head":
            m = pat.match(body)
            if m:
                spans.append((name, m.start(), m.end()))
        elif where == "tail":
            hits = [m for m in pat.finditer(body) if m.start() >= len(body) * TAIL_FROM]
            if hits:
                spans.append((name, hits[0].start(), len(body)))
        else:
            spans += [(name, m.start(), m.end()) for m in pat.finditer(body)]
    # 겹치는 구간은 합친다 (규칙 이름은 먼저 시작한 쪽)
    spans.sort(key=lambda s: (s[1], -s[2]))
    merged = []
    for name, s, e in spans:
        if merged and s < merged[-1][2]:
            merged[-1] = (merged[-1][0], merged[-1][1], max(merged[-1][2], e))
        else:
            merged.append((name, s, e))
    out, pos = [], 0
    for _, s, e in merged:
        out.append(body[pos:s])
        pos = e
    out.append(body[pos:])
    text = re.sub(r"\s+", " ", " ".join(out)).strip()
    return text, [{"rule": n, "start": s, "end": e, "text": body[s:e].strip()} for n, s, e in merged]


def in_spans(pos: int, spans: list[dict]) -> bool:
    return any(sp["start"] <= pos < sp["end"] for sp in spans)


# ── 태깅 ─────────────────────────────────────────────
def corp_codes() -> dict[str, str]:
    c = pd.read_csv(settings.COMPANIES_CSV, dtype=str, encoding="utf-8-sig")
    return dict(zip(c["corp_name"], c["corp_code"]))


CORP_RE = {corp: re.compile("|".join(sorted(aliases, key=len, reverse=True)))
           for corp, aliases in CORP_ALIASES.items()}


def tag(title: str, body_clean: str | None) -> dict:
    body_clean = body_clean or ""
    ai_terms = AI_RE.findall(title) + AI_RE.findall(body_clean)
    corps = {}
    for corp, pat in CORP_RE.items():
        n_title, n_body = len(pat.findall(title)), len(pat.findall(body_clean))
        if n_title or n_body:
            corps[corp] = {"in_title": int(n_title > 0), "count": n_title + n_body}
    return {
        "ai_in_title": int(bool(AI_RE.search(title))),
        "ai_count": len(ai_terms),
        "ai_terms": "; ".join(sorted(set(ai_terms))),
        "corps": corps,
    }


def load_news(con) -> pd.DataFrame:
    return pd.read_sql(
        "SELECT doc_id, published_date, media, title, body, is_duplicate FROM documents "
        "WHERE source_type = 'news' AND news_origin = 'provided' ORDER BY doc_id", con)


def run(df: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """정제·태깅 결과 표와 지운 구간 목록 (검수 파일용)"""
    rows, removed = [], []
    for r in df.itertuples():
        body_clean, spans = clean_body(r.body)
        t = tag(r.title, body_clean)
        for sp in spans:
            removed.append({"doc_id": r.doc_id, **sp})
        rows.append({
            "doc_id": r.doc_id, "published_date": r.published_date, "media": r.media,
            "is_duplicate": r.is_duplicate, "title": r.title, "body_clean": body_clean,
            "len_raw": len(r.body or ""), "len_clean": len(body_clean or ""),
            "removed_rules": "; ".join(sp["rule"] for sp in spans),
            "ai_in_title": t["ai_in_title"], "ai_count": t["ai_count"], "ai_terms": t["ai_terms"],
            "corps": "; ".join(t["corps"]),
            "corps_in_title": "; ".join(c for c, v in t["corps"].items() if v["in_title"]),
        })
    return pd.DataFrame(rows), removed


def write_news_company(con, out: pd.DataFrame) -> int:
    codes = corp_codes()
    rows = []
    for r in out[out["is_duplicate"] == 0].itertuples():
        titled = set(filter(None, r.corps_in_title.split("; ")))
        for corp in filter(None, r.corps.split("; ")):
            rows.append((r.doc_id, codes[corp], int(corp in titled)))
    con.execute("DELETE FROM news_company")
    con.executemany("INSERT INTO news_company (doc_id, corp_code, in_title) VALUES (?, ?, ?)", rows)
    con.commit()
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-db", action="store_true", help="news_company 테이블 채우기 (검수 후)")
    args = ap.parse_args()

    con = sqlite3.connect(settings.DB_PATH)
    out, removed = run(load_news(con))
    out.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    valid = out[out["is_duplicate"] == 0]
    print(f"정제·태깅 {len(out)}건 (집계 대상 {len(valid)}) → {OUT_CSV.relative_to(settings.ROOT)}")
    print(f"  정제로 지운 구간 {len(removed)}개, 정제된 기사 {(valid['removed_rules'] != '').sum()}건")
    print(f"  AI 용어 기사 {(valid['ai_count'] > 0).sum()}건 (제목 {valid['ai_in_title'].sum()})")
    for corp in CORP_ALIASES:
        has = valid["corps"].str.split("; ").map(lambda x: corp in x)
        ttl = valid["corps_in_title"].str.split("; ").map(lambda x: corp in x)
        print(f"  {corp}: 기사 {has.sum()} (제목 {ttl.sum()})")
    if args.write_db:
        print(f"news_company {write_news_company(con, out)}행 적재")


if __name__ == "__main__":
    main()
