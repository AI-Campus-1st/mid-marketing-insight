"""
[B·D7] 예전 DB 의 disclosure_chunks 를 schema.sql 정의대로 다시 만들기 (분류 결과는 그대로 옮김)

왜
    Day 1 에 만든 DB 의 disclosure_chunks 에는 Day 2 에 ai_level(L0~L3)로 바꾸기 전 컬럼 ai_stage(declare/plan/execute)가
    남아 있다 (값은 모두 NULL, 읽는 코드 없음). schema.sql 은 CREATE TABLE IF NOT EXISTS 라 이미 있는 테이블을 바꾸지 않고,
    rag/chunk.py 의 sync_columns 는 없는 컬럼을 맨 끝에 ADD COLUMN 으로 붙이기만 해서
      - 안 쓰는 ai_stage 가 남고
      - report_date · ai_level · has_outcome_figure 가 created_at 뒤에 붙고
      - ai_level 의 CHECK (0~3) 제약이 빠졌다.
    schema.sql 로 새로 만든 DB 는 처음부터 정의대로라, 예전 DB 를 쓰는 사람만 이 스크립트로 맞추면 된다.

하는 일
    1. 지금 테이블 정의가 schema.sql 과 같으면 아무것도 하지 않는다.
    2. --apply 일 때만: DB 를 data/processed/backup/ 에 백업한 뒤, 한 트랜잭션 안에서
       schema.sql 정의로 새 테이블을 만들고 → 같은 이름의 컬럼만 옮기고(ai_stage 버림) → 예전 테이블을 지우고 이름을 바꾸고
       → 인덱스와 event_reflection 뷰를 schema.sql 대로 다시 만든다.
    3. 옮기기 전후로 행 수 · 단계별 개수 · 청크별 분류 결과 · Q3 반영 판정이 같은지 확인하고, 다르면 되돌린다.

실행
    python rag/rebuild_chunk_table.py            # 확인만 (DB 를 바꾸지 않음)
    python rag/rebuild_chunk_table.py --apply    # 백업 후 다시 만들기
"""
import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402

TABLE = "disclosure_chunks"


def _reference() -> sqlite3.Connection:
    ref = sqlite3.connect(":memory:")
    ref.executescript((settings.ROOT / "schema.sql").read_text(encoding="utf-8"))
    return ref


def _cols(con: sqlite3.Connection) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({TABLE})")]


def _sql(con: sqlite3.Connection, name: str) -> str | None:
    r = con.execute("SELECT sql FROM sqlite_master WHERE name = ?", (name,)).fetchone()
    return r[0].replace(f'CREATE TABLE "{name}"', f"CREATE TABLE {name}", 1) if r else None   # RENAME 뒤에는 이름에 따옴표가 붙음


def _snapshot(con: sqlite3.Connection, keep: list[str]) -> dict:
    """옮기기 전후 비교용: 행 수 · 단계별 개수 · 청크별 분류 결과 · Q3 반영 판정"""
    cols = ", ".join(keep)
    return {
        "rows": con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0],
        "levels": con.execute(f"SELECT ai_level, COUNT(*) FROM {TABLE} GROUP BY 1 ORDER BY 1").fetchall(),
        "data": con.execute(f"SELECT {cols} FROM {TABLE} ORDER BY chunk_id").fetchall(),
        "verdicts": con.execute("SELECT verdict, COUNT(*) FROM event_reflection GROUP BY 1 ORDER BY 1").fetchall(),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--apply", action="store_true", help="백업 후 실제로 다시 만든다 (없으면 확인만)")
    ap.add_argument("--db", type=Path, default=settings.DB_PATH)
    args = ap.parse_args()

    ref = _reference()
    want, want_sql = _cols(ref), _sql(ref, TABLE)
    con = sqlite3.connect(args.db, isolation_level=None)
    have = _cols(con)
    if not have:
        sys.exit(f"{args.db} 에 {TABLE} 가 없습니다.")
    if have == want and _sql(con, TABLE) == want_sql:
        print("이미 schema.sql 정의와 같습니다. 바꿀 것이 없습니다.")
        return

    extra = [c for c in have if c not in want]
    missing = [c for c in want if c not in have]
    print(f"지금 컬럼: {have}")
    print(f"schema.sql: {want}")
    print(f"DB 에만 있는 컬럼(버림): {extra} · schema.sql 에만 있는 컬럼(빈 값으로 생김): {missing}")
    for c in extra:   # 버릴 컬럼에 값이 있으면 멈춘다 — 쓰고 있는 데이터일 수 있음
        n = con.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE {c} IS NOT NULL").fetchone()[0]
        if n:
            sys.exit(f"버릴 컬럼 {c} 에 값이 {n}개 있어 멈춥니다. 먼저 확인해 주십시오.")
    bad = con.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE ai_level NOT BETWEEN 0 AND 3").fetchone()[0] \
        if "ai_level" in have else 0
    if bad:
        sys.exit(f"ai_level 이 0~3 밖인 행이 {bad}개 있어 CHECK 제약을 넣을 수 없습니다.")
    if not args.apply:
        print("확인만 했습니다. 다시 만들려면 --apply 를 붙여 실행하십시오.")
        return

    backup = args.db.parent / "backup" / f"{args.db.stem}_before_rebuild_{datetime.now():%Y%m%d_%H%M%S}.db"
    backup.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"VACUUM INTO '{backup}'")   # 일관된 복사본 (열려 있는 DB 를 그냥 복사하지 않음)
    print(f"백업: {backup}")

    keep = [c for c in want if c in have]
    before = _snapshot(con, keep)
    con.execute("PRAGMA foreign_keys = OFF")   # 트랜잭션 밖에서만 바뀜 — 테이블을 바꾸는 동안 event_dart_mapping 외래키 검사를 멈춤
    try:
        con.execute("BEGIN")
        for (view,) in ref.execute("SELECT name FROM sqlite_master WHERE type = 'view'").fetchall():
            con.execute(f"DROP VIEW IF EXISTS {view}")   # 뷰가 이 테이블을 읽어 먼저 지우고 마지막에 다시 만든다
        con.execute(want_sql.replace(f"CREATE TABLE {TABLE}", f"CREATE TABLE {TABLE}_new", 1))
        con.execute(f"INSERT INTO {TABLE}_new ({', '.join(keep)}) SELECT {', '.join(keep)} FROM {TABLE}")
        con.execute(f"DROP TABLE {TABLE}")
        con.execute(f"ALTER TABLE {TABLE}_new RENAME TO {TABLE}")
        for (sql,) in ref.execute("SELECT sql FROM sqlite_master WHERE type IN ('index', 'view') AND sql IS NOT NULL "
                                  "ORDER BY type = 'view'"):   # 인덱스 먼저, 뷰 나중
            if sql.upper().startswith("CREATE VIEW") or f" ON {TABLE}(" in sql:
                con.execute(sql)
        after = _snapshot(con, keep)
        if con.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("외래키 검사 실패")
        for k in before:
            if before[k] != after[k]:
                raise RuntimeError(f"옮기기 전후 '{k}' 가 다릅니다")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.execute("PRAGMA foreign_keys = ON")

    print(f"완료: {_cols(con)}")
    print(f"행 {after['rows']}개 · 단계별 {after['levels']} · Q3 판정 {after['verdicts']} — 옮기기 전과 같음")
    print("integrity_check:", con.execute("PRAGMA integrity_check").fetchone()[0])


if __name__ == "__main__":
    main()
