"""
[B·D3] 정기공시 섹션 청킹 + AI 용어 집계 → disclosure_chunks

하는 일
    1. documents 에서 본문이 있는 정기공시를 고른다.
       같은 보고서(회사 + 보고서명)에 정정 공시가 있으면 가장 마지막에 접수된 것 하나만 쓴다.
       (정정본은 보고서 전체를 다시 낸 것이라 원본과 함께 쓰면 AI 언급이 두 번 세어진다)
       날짜(report_date)는 원본 접수일을 쓴다. (반영 시차가 정정 날짜만큼 늘어나지 않게)
    2. 본문 파일(첨부 제외)에서 화이트리스트 섹션만 남긴다. (docs/decisions.md 참고)
         - II. 사업의 내용 (전체)
         - I. 회사의 개요 > 2. 회사의 연혁, 5. 정관에 관한 사항
    3. 소제목(## 줄) 단위로 나누고, MAX_CHARS 보다 길면 문단 경계에서 자른다.
    4. 청크마다 AI 용어 수(ai_term_count)를 세어 disclosure_chunks 에 저장한다.
    5. 화이트리스트 밖 섹션의 AI 언급 수를 기업별로 출력한다. (빠뜨린 것이 없는지 확인용)

재실행
    - chunk_id = rcept_no + '_' + 순번 이라 다시 돌려도 같은 청크는 같은 id 를 갖는다.
    - 텍스트가 그대로인 청크는 LLM 분류 결과(ai_stage 등)를 유지하고,
      텍스트가 바뀐 청크만 분류 결과를 비운다.

실행: python rag/chunk.py
다음 단계: rag/classify_chunks.py 가 ai_term_count > 0 인 청크를 LLM 으로 분류한다.
"""
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402

MAX_CHARS = 1000  # 청크 최대 길이 (한국어 공시 문단 2~4개 분량)

# AI 용어 패턴. 'AI'·'LLM' 은 앞뒤가 영문자가 아닐 때만 센다. (HYUNDAI, PENGTAI 등 오탐 방지)
# 'GPT' 는 'ChatGPT' 를 살리기 위해 경계를 두지 않는다. 근거: docs/dart_ai_check.md 3절
# TODO: 팀 합의 후 settings.AI_TERM_PATTERN 을 이 패턴으로 바꾸고 여기서는 그것을 쓴다.
AI_PATTERN = re.compile(r"(?<![A-Za-z])(?:AI|LLM)(?![A-Za-z])|GPT|인공지능|생성형")

# 화이트리스트: (장 번호, 포함할 소제목 패턴). 소제목 패턴이 None 이면 장 전체.
WHITELIST = {
    "II": None,
    "I": re.compile(r"^(2\. 회사의 연혁|5\. 정관에 관한 사항)"),
}

CHAPTER = re.compile(r"^## ([IVX]+)\. (.+)")


def pick_reports(con: sqlite3.Connection) -> list[tuple]:
    """
    정기공시 중 보고서마다 가장 마지막에 접수된 판(정정본 포함) 하나씩 고른다.
    내용은 최신 판을 쓰고, 날짜는 그 보고서가 처음 접수된 날(원본 접수일)을 함께 돌려준다.
      예) KT나스미디어 분기보고서 (2025.03): 원본 2025-05-14, 정정본 2025-07-25
          → 정정본 본문 + report_date '2025-05-14'
      정정본 날짜를 쓰면 반영 시차가 실제보다 길게 계산된다. (docs/decisions.md)

    Returns:
        (doc_id, corp_code, rcept_no, title, body, report_date) 목록
    """
    rows = con.execute(
        """SELECT doc_id, corp_code, rcept_no, title, body, substr(published_date, 1, 10) FROM documents
           WHERE report_type = '정기공시' AND body IS NOT NULL
           ORDER BY corp_code, rcept_no"""
    ).fetchall()
    latest, first_date = {}, {}
    for row in rows:
        key = (row[1], re.sub(r"^\[[^\]]*\]", "", row[3]).strip())  # '[기재정정]사업보고서 (2024.12)' → '사업보고서 (2024.12)'
        latest[key] = row                       # rcept_no 오름차순이라 마지막 것(최신 판)이 남는다
        first_date.setdefault(key, row[5])      # 처음 나온 것(원본)의 접수일만 남긴다
    return [(*latest[k][:5], first_date[k]) for k in latest]


def split_sections(body: str):
    """
    본문 파일만 읽어 (장 번호, 섹션 경로, 줄 목록) 을 소제목 단위로 돌려준다.
    body 는 '# 파일명' 줄로 파일이 구분되고, 첫 파일이 본문이다. (collect/dart_document.py)
    """
    parts = re.split(r"\n# [^\n]+\n", "\n" + body)
    main = parts[1] if len(parts) > 1 else body

    chapter, chapter_title, sub, lines = None, "", "", []
    for ln in main.split("\n"):
        m = CHAPTER.match(ln)
        if m or ln.startswith("## "):
            if lines:
                yield chapter, chapter_title, sub, lines
            lines = []
            if m:
                chapter, chapter_title, sub = m.group(1), ln[3:], ""
            else:
                sub = ln[3:]
            continue
        if ln.strip():
            lines.append(ln)
    if lines:
        yield chapter, chapter_title, sub, lines


def in_whitelist(chapter: str, sub: str) -> bool:
    if chapter not in WHITELIST:
        return False
    pattern = WHITELIST[chapter]
    return pattern is None or bool(pattern.match(sub))


def pack(lines: list[str]) -> list[str]:
    """줄(문단)을 MAX_CHARS 이하 청크로 묶는다. 한 문단이 너무 길면 문장 경계에서 자른다."""
    pieces = []
    for ln in lines:
        if len(ln) <= MAX_CHARS:
            pieces.append(ln)
            continue
        buf = ""
        for sent in re.split(r"(?<=다\.)\s+|(?<=\.)\s+(?=[가-힣A-Z(])", ln):
            if buf and len(buf) + len(sent) + 1 > MAX_CHARS:
                pieces.append(buf)
                buf = ""
            buf = f"{buf} {sent}".strip()
            while len(buf) > MAX_CHARS:  # 문장 하나가 아주 긴 경우(표 한 줄 등)
                pieces.append(buf[:MAX_CHARS])
                buf = buf[MAX_CHARS:]
        if buf:
            pieces.append(buf)

    chunks, buf = [], ""
    for p in pieces:
        if buf and len(buf) + len(p) + 1 > MAX_CHARS:
            chunks.append(buf)
            buf = ""
        buf = f"{buf}\n{p}" if buf else p
    if buf:
        chunks.append(buf)
    return chunks


def chunk_report(body: str):
    """한 보고서의 청크 목록과, 화이트리스트 밖 섹션의 AI 언급 수를 돌려준다."""
    chunks, excluded_ai = [], 0
    for chapter, chapter_title, sub, lines in split_sections(body):
        if not in_whitelist(chapter, sub):
            excluded_ai += sum(len(AI_PATTERN.findall(ln)) for ln in lines)
            continue
        section = f"{chapter_title} > {sub}" if sub else chapter_title
        for text in pack(lines):
            chunks.append((section, text))
    return chunks, excluded_ai


def save(con: sqlite3.Connection, doc_id: str, corp_code: str, rcept_no: str, report_date: str,
         chunks: list) -> None:
    rows = [(f"{rcept_no}_{i:04d}", doc_id, corp_code, rcept_no, report_date, section, i, text,
             len(AI_PATTERN.findall(text)))
            for i, (section, text) in enumerate(chunks)]
    con.executemany(
        """INSERT INTO disclosure_chunks
             (chunk_id, doc_id, corp_code, rcept_no, report_date, section, chunk_index, text, ai_term_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(chunk_id) DO UPDATE SET
             report_date = excluded.report_date, section = excluded.section, text = excluded.text, ai_term_count = excluded.ai_term_count,
             ai_stage       = CASE WHEN disclosure_chunks.text = excluded.text THEN ai_stage END,
             ai_domain      = CASE WHEN disclosure_chunks.text = excluded.text THEN ai_domain END,
             reason         = CASE WHEN disclosure_chunks.text = excluded.text THEN reason END,
             model          = CASE WHEN disclosure_chunks.text = excluded.text THEN model END,
             prompt_version = CASE WHEN disclosure_chunks.text = excluded.text THEN prompt_version END""",
        rows,
    )
    # 이전 실행보다 청크 수가 줄었으면 남는 청크를 지운다.
    con.execute("DELETE FROM disclosure_chunks WHERE rcept_no = ? AND chunk_index >= ?", (rcept_no, len(rows)))


def main():
    if not settings.DB_PATH.exists():
        sys.exit("[오류] project.db 가 없습니다. collect/dart_collect.py → dart_document.py 를 먼저 실행하세요.")
    con = sqlite3.connect(settings.DB_PATH)
    con.executescript((settings.ROOT / "schema.sql").read_text(encoding="utf-8"))  # 테이블이 없으면 생성
    # 예전 스키마로 만든 DB 에는 report_date 컬럼이 없으므로 추가한다.
    cols = {r[1] for r in con.execute("PRAGMA table_info(disclosure_chunks)")}
    if "report_date" not in cols:
        con.execute("ALTER TABLE disclosure_chunks ADD COLUMN report_date TEXT")

    reports = pick_reports(con)
    used = {r[2] for r in reports}
    # 정정 공시로 대체된 판의 청크가 남아 있으면 지운다.
    stale = [r for (r,) in con.execute("SELECT DISTINCT rcept_no FROM disclosure_chunks") if r not in used]
    con.executemany("DELETE FROM disclosure_chunks WHERE rcept_no = ?", [(r,) for r in stale])

    names = dict(con.execute("SELECT corp_code, corp_name FROM companies"))
    total, ai_in, ai_out = Counter(), Counter(), Counter()
    for doc_id, corp_code, rcept_no, title, body, report_date in reports:
        chunks, excluded_ai = chunk_report(body)
        save(con, doc_id, corp_code, rcept_no, report_date, chunks)
        name = names.get(corp_code, corp_code)
        total[name] += len(chunks)
        ai_in[name] += sum(len(AI_PATTERN.findall(t)) for _, t in chunks)
        ai_out[name] += excluded_ai
    con.commit()

    print(f"[정보] 정기공시 {len(reports)}건 청킹 (정정 공시로 대체된 판 제외)")
    print(f"{'기업':<12}{'청크':>6}{'AI 언급(포함)':>14}{'AI 언급(제외 섹션)':>18}")
    for name in total:
        print(f"{name:<12}{total[name]:>6}{ai_in[name]:>14}{ai_out[name]:>18}")
    n, n_ai = con.execute("SELECT COUNT(*), SUM(ai_term_count > 0) FROM disclosure_chunks").fetchone()
    print(f"[저장] disclosure_chunks {n}개 (AI 용어 포함 {n_ai}개 → classify_chunks.py 분류 대상)")
    con.close()


if __name__ == "__main__":
    main()
