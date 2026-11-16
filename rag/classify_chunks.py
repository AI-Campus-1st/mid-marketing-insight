"""
[B·D3] 공시 청크 AI 단계 분류 (LLM) → disclosure_chunks.ai_level 등

하는 일
    AI 용어가 있는 청크(ai_term_count > 0)만 LLM 에 보내 아래 4개 칸을 채운다.
        ai_level           : 0 언급 / 1 계획 / 2 도입·적용 / 3 자체 개발·상용화, null = 회사 AI 와 무관
        ai_domain          : creative / operation / analytics / new_biz (ai_level 이 1 이상일 때만)
        has_outcome_figure : 1 = 회사 AI 의 매출·고객 수·건수 같은 운영 성과 숫자가 있음
        reason             : 근거 구절과 판단 이유
    model·prompt_version 도 같이 저장한다 → model 이 채워져 있으면 '분류됨' (decisions.md 분류 상태 규칙)

프롬프트
    - 단계 정의·경계 규칙·공시 추가 규칙은 config/ai_levels.prompt_block("disclosure") 를 그대로 쓴다 (A·B 공통 기준).
    - 한 청크에 AI 문장이 여러 개면 '회사 자신의 AI' 중 가장 높은 단계를 고른다.
      (말-실행 격차 = 이벤트 주장 수준 − 매칭 청크의 최고 단계 이므로)
    - 프롬프트를 바꾸면 PROMPT_VERSION 을 올리고 바뀐 내용을 decisions.md 에 적는다.

재실행
    - LLM 응답은 캐시(data/processed/llm_cache/chunk_classify_{PROMPT_VERSION}.jsonl)에 저장한다.
      키 = chunk_id + 본문 해시 → 다시 돌려도 API 를 다시 부르지 않고, 본문이 바뀐 청크만 새로 분류한다.
    - 실패한 청크는 건너뛰고, 다시 실행하면 이어서 분류한다.

실행
    python rag/classify_chunks.py --dry-run          # API 호출 없이 대상 수와 프롬프트 예시만
    python rag/classify_chunks.py --pilot 4          # 기업별 4개씩(20개) 시험 분류 → 결과 확인 후 프롬프트 수정
    python rag/classify_chunks.py                    # 전체 (AI 청크 459개)
필요: .env 의 OPENAI_API_KEY (선택: OPENAI_MODEL, 기본 gpt-4.1-mini — A 의 event_classify.py 와 같은 모델)
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402
from config.ai_levels import prompt_block  # noqa: E402

PROMPT_VERSION = "chunk_v1"
DEFAULT_MODEL = "gpt-4.1-mini"
CACHE = settings.DATA_DIR / "processed" / "llm_cache" / f"chunk_classify_{PROMPT_VERSION}.jsonl"
DOMAINS = ("creative", "operation", "analytics", "new_biz")

SYSTEM = f"""당신은 광고대행사 정기공시(사업·반기·분기보고서)의 AI 관련 문장을 분류하는 분석가입니다.
공시 청크 하나를 읽고, '대상 기업 자신의 AI'가 어느 단계까지 확인되는지 판정해 JSON 으로만 답하세요.

[판정 순서]
1. 청크에서 AI 가 나오는 문장을 모두 찾는다.
2. 각 문장이 '대상 기업 자신의 AI'인지 본다. 광고주 상품명·캠페인명·수상작 이름 속 AI, 영문 단어 속 글자는 회사의 AI 가 아니다.
3. 회사의 AI 문장마다 아래 단계를 매기고, 그중 가장 높은 단계를 ai_level 로 답한다.
   회사의 AI 문장이 하나도 없으면 ai_level 은 null.

{prompt_block("disclosure")}

[AI 영역 ai_domain] ai_level 이 1 이상일 때만, 가장 높은 단계 문장의 영역 하나. 0 이거나 null 이면 null.
  creative  : 광고 소재·영상·이미지·카피 제작
  operation : 캠페인 운영·집행 자동화, 업무 자동화 플랫폼
  analytics : 데이터 분석·타겟팅·매칭·추천·성과 측정
  new_biz   : 위 셋에 들지 않는 새 서비스·사업 (광고 안전성, 검색 최적화(GEO), AI 조직·투자 등)

[운영 성과 숫자 has_outcome_figure]
  1 = 회사 AI 서비스의 매출·고객 수·이용 건수·처리량 등 운영 성과 숫자가 있다.
  0 = 없다. (연도·날짜·특허 번호·연구 인원·일반 시장 규모는 성과 숫자가 아니다)

[reason] 근거가 된 구절을 따옴표로 짧게 인용하고 판단을 덧붙인다. 80자 이내.

응답 형식:
{{"ai_level": 0|1|2|3|null, "ai_domain": "creative"|"operation"|"analytics"|"new_biz"|null,
 "has_outcome_figure": 0|1, "reason": str}}"""


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def build_prompt(r) -> str:
    return (f"대상 기업: {r.corp_name}\n보고서: {r.report_name} (접수일 {r.report_date})\n"
            f"섹션: {r.section}\n\n[청크]\n{r.text}")


def load_targets(con) -> pd.DataFrame:
    return pd.read_sql(
        """SELECT k.chunk_id, c.corp_name, d.title AS report_name, k.report_date, k.section, k.text, k.model
           FROM disclosure_chunks k
           JOIN companies c ON c.corp_code = k.corp_code
           JOIN documents d ON d.doc_id = k.doc_id
           WHERE k.ai_term_count > 0
           ORDER BY k.chunk_id""", con)


def load_cache() -> dict[str, dict]:
    if not CACHE.exists():
        return {}
    return {r["key"]: r for r in map(json.loads, CACHE.read_text(encoding="utf-8").splitlines())}


def clean(out: dict) -> dict:
    """LLM 응답을 스키마 CHECK 에 맞게 정리 (규칙을 코드로 한 번 더 강제)"""
    level = out.get("ai_level")
    level = int(level) if str(level) in ("0", "1", "2", "3") else None
    domain = out.get("ai_domain") if out.get("ai_domain") in DOMAINS else None
    if level is None or level == 0:  # 영역은 회사 AI 가 1단계 이상일 때만
        domain = None
    figure = 1 if str(out.get("has_outcome_figure")) == "1" else 0
    if level is None:
        figure = 0
    return {"ai_level": level, "ai_domain": domain, "has_outcome_figure": figure,
            "reason": str(out.get("reason") or "")[:200]}


def main() -> None:
    parser = argparse.ArgumentParser(description="공시 청크 AI 단계 분류")
    parser.add_argument("--dry-run", action="store_true", help="API 호출 없이 대상 수와 프롬프트 1개만 출력")
    parser.add_argument("--pilot", type=int, metavar="N", help="기업별 N개씩만 분류 (시험용)")
    parser.add_argument("--redo", action="store_true", help="이미 분류된 청크도 현재 프롬프트 버전으로 다시 분류")
    args = parser.parse_args()

    con = sqlite3.connect(settings.DB_PATH)
    targets = load_targets(con)
    todo = targets if args.redo else targets[targets.model.isna()]
    if args.pilot:
        todo = todo.sample(frac=1, random_state=42).groupby("corp_name").head(args.pilot).sort_values("chunk_id")
    print(f"AI 청크 {len(targets)}개 중 분류 대상 {len(todo)}개 (이미 분류됨 {targets.model.notna().sum()}개)")
    print(todo.corp_name.value_counts().to_string())

    if args.dry_run or todo.empty:
        if len(todo):
            print("\n--- 시스템 프롬프트 ---\n" + SYSTEM)
            print("\n--- 청크 예시 ---\n" + build_prompt(todo.iloc[0])[:1200])
        return

    load_dotenv(settings.ROOT / ".env")
    if not os.getenv("OPENAI_API_KEY"):
        sys.exit(".env 에 OPENAI_API_KEY 가 없습니다")
    from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError
    # A 의 event_classify.py 와 같은 이유로 gzip 만 받는다 (httpx 압축 패키지 버전 충돌)
    client = OpenAI(default_headers={"Accept-Encoding": "gzip, deflate"})
    model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL)

    cache = load_cache()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    n_call = n_fail = 0
    with CACHE.open("a", encoding="utf-8") as cache_file:
        for r in todo.itertuples():
            key = f"{r.chunk_id}:{text_hash(r.text)}"
            if key not in cache:
                out = None
                for attempt in range(3):  # 연결 오류는 재시도, 그래도 실패하면 이 청크만 건너뜀
                    try:
                        res = client.chat.completions.create(
                            model=model, temperature=0, response_format={"type": "json_object"},
                            messages=[{"role": "system", "content": SYSTEM},
                                      {"role": "user", "content": build_prompt(r)}])
                        out = json.loads(res.choices[0].message.content)
                        break
                    except (APIConnectionError, APITimeoutError, RateLimitError, json.JSONDecodeError) as e:
                        print(f"  재시도 {attempt + 1}/3 ({type(e).__name__}): {r.chunk_id}")
                        time.sleep(5 * (attempt + 1))
                if out is None:
                    n_fail += 1
                    continue
                cache[key] = {"key": key, "model": model, "prompt_version": PROMPT_VERSION, **out}
                cache_file.write(json.dumps(cache[key], ensure_ascii=False) + "\n")
                n_call += 1
            c = cache[key]
            v = clean(c)
            con.execute(
                """UPDATE disclosure_chunks SET ai_level = ?, ai_domain = ?, has_outcome_figure = ?,
                   reason = ?, model = ?, prompt_version = ? WHERE chunk_id = ?""",
                (v["ai_level"], v["ai_domain"], v["has_outcome_figure"], v["reason"],
                 c["model"], c["prompt_version"], r.chunk_id))
    con.commit()

    print(f"\nAPI 호출 {n_call}회, 실패 {n_fail}개 (다시 실행하면 이어서 분류)")
    done = pd.read_sql(
        """SELECT c.corp_name, k.ai_level FROM disclosure_chunks k JOIN companies c USING(corp_code)
           WHERE k.model IS NOT NULL""", con)
    done["단계"] = done.ai_level.map(lambda x: "무관" if pd.isna(x) else f"L{int(x)}")
    print(pd.crosstab(done.corp_name, done["단계"], margins=True, margins_name="합계").to_string())


if __name__ == "__main__":
    main()
