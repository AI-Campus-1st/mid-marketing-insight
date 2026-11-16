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
import re
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402
from config.ai_levels import prompt_block  # noqa: E402

PROMPT_VERSION = "chunk_v6"
# v5 → v6: AI 용어가 없는 문장을 항목으로 넣어 판정(2건: 이노션 L0→L1, 인크로스 무관 솔루션→L3)
#   → quote 에 AI 용어를 반드시 넣게 하고, 코드가 AI 용어(settings.AI_TERM_PATTERN) 없는 항목을 버린다.
# v4 → v5: v4 에서 '최고 단계 고르기'를 LLM 에 맡기자 연혁 청크에서 낮은 항목(T deal L2)을 고르고(2건),
#   같은 문장이 L0/null 로 갈림. → LLM 은 AI 항목마다 단계만 매기고(mentions), 최고 단계는 코드(clean)가 고른다.
# v3 → v4: 기준 미정 3종 결정 (B 제안, A 확인 요청 중). L2/L3 = 'AI 가 들어간 결과물이 누구의 제품인가'.
#   자사 제품에 외부 모델 탑재해 출시 = 3 (뉴스 E022 올잇 AI 광고소재 생성 = L3 와 같은 자), 외부 완성 솔루션 사용·타사 AI 운영 대행 = 2
# v2 → v3: 같은 20개에서 '당사의 AI 자동화 운영 솔루션인 센스엔을 기반으로 영업' 을 자체로 알아보고도 L2 → 운영·판매 중인 자체 솔루션 = 3, 2 는 외부 AI 만
# v1 → v2 (시험 20개 사람 검토: 단계 오류 5, 영역 오류 1, 기준 미정 3)
#   L3 를 너무 아낌: 회사 고유 솔루션('당사의 AI 자동화 운영 솔루션인 센스엔')을 외부 도입(L2)으로,
#   AI 서비스 출시·자체 기술 개발을 "숫자가 없어서" L2 로 판정 (3건). 연혁 목록에서 최고 단계 항목을 놓침 (1건).
#   시장 전망을 null 로 판정 (규칙상 L0, 1건). 브랜드 세이프티를 operation 으로 (new_biz, 1건).
#   → L3 조건은 '하나만 맞으면 됨', '당사의/고유 이름 솔루션 = 자체', 전망 = 0, null 은 3가지 경우만, 영역 예시 보강
DEFAULT_MODEL = "gpt-4.1-mini"
CACHE = settings.DATA_DIR / "processed" / "llm_cache" / f"chunk_classify_{PROMPT_VERSION}.jsonl"
DOMAINS = ("creative", "operation", "analytics", "new_biz")
AI_RE = re.compile(settings.AI_TERM_PATTERN)

SYSTEM = f"""당신은 광고대행사 정기공시(사업·반기·분기보고서)의 AI 관련 문장을 분류하는 분석가입니다.
공시 청크 하나를 읽고, AI 가 나오는 문장(또는 연혁·표의 항목)마다 단계를 매겨 JSON 으로만 답하세요.

[판정 순서]
1. 청크에서 AI 용어(AI·인공지능·생성형·LLM·GPT 등)가 직접 나오는 문장·항목만 빠짐없이 찾는다.
   AI 용어가 없는 문장은 적지 않는다. 연혁·표처럼 나열된 청크는 항목 하나하나가 따로다.
2. 각각을 mentions 에 하나씩 적는다: 근거 구절(quote), 단계(level), 영역(domain).
   같은 청크 안의 항목끼리 단계가 달라도 된다. 서로 영향을 주지 말고 항목마다 따로 판정한다.
3. 회사 AI 가 아닌 것(광고주 상품명·캠페인명 속 AI, 영문 단어 속 글자)은 level 을 null 로 적는다.

{prompt_block("disclosure")}

[공시 판정 보충]
  - 3 의 조건은 하나만 맞아도 3 이다. 자체 개발, AI 서비스·기능 출시, 전담 조직, 특허 중 하나가 있으면 숫자가 없어도 3.
  - '당사의 ○○ 솔루션', 회사(또는 연결 자회사)가 이름을 붙인 서비스·플랫폼은 자체 솔루션이다. 외부 도입(2)으로 보지 않는다.
    자체 AI 솔루션을 운영·판매·영업에 쓰고 있다는 문장은 이미 상용화된 것이므로 3 이다.
  - 2 와 3 은 'AI 가 들어간 결과물이 누구의 제품인가'로 가른다 (기반 기술이 누구 것인지는 따지지 않는다).
      3: 외부 모델·기술(ChatGPT, 모회사·협력사 기술 등)을 썼더라도 회사의 제품·서비스·플랫폼에 넣어 출시·제공하고 있다.
         예) 'ChatGPT 를 활용한 광고소재 생성 기능 오픈'(자사 솔루션의 기능), 'KT 와 협업해 AI 기술을 접목한 당사 플랫폼을 고도화하여 제공'
      2: 외부에서 완성된 AI 솔루션·서비스를 회사 업무·캠페인에 쓰기만 한다.
         다른 회사(모회사·고객사 포함)의 AI 서비스를 운영 대행하는 것도 2 이다. 예) 'SK텔레콤의 AI 기반 커머스 T deal 운영 대행'
      제공 중인지 불분명하고 '추진', '적용할 예정'이면 1 이다.
  - 연구개발활동 표의 '○○ 개발', '○○ 기술 개발'(진행중 포함)은 회사가 직접 하는 개발이므로 3.
  - 시장·업계 전망, 광고비 전망, 다른 산업의 AI 성장 이야기는 null 이 아니라 0 이다.
  - null 은 세 경우뿐이다: 광고주 상품·캠페인·수상작 이름 속 AI / 영문 단어 속 글자 오탐 / 청크에 AI 가 실제로 없음.

[AI 영역 domain] level 이 1 이상인 항목만, 그 항목의 영역 하나. level 이 0 이거나 null 이면 null.
  creative  : 광고 소재·영상·이미지·카피 제작
  operation : 캠페인 운영·집행 자동화, 업무 자동화 플랫폼
  analytics : 데이터 분석·타겟팅·매칭·추천·성과 측정
  new_biz   : 위 셋에 들지 않는 새 서비스·사업 (브랜드 세이프티·유해 영상 차단, 검색 최적화(GEO),
              설문·커머스 같은 광고 외 서비스, AI 조직 신설·투자·제휴)

[운영 성과 숫자 has_outcome_figure] 청크 전체 기준
  1 = 회사 AI 서비스의 매출·고객 수·이용 건수·처리량 등 운영 성과 숫자가 있다.
  0 = 없다. (연도·날짜·특허 번호·연구 인원·일반 시장 규모는 성과 숫자가 아니다)

응답 형식 (quote 는 AI 용어를 포함한 청크의 구절을 그대로 40자 이내, why 는 판단 이유 30자 이내):
{{"mentions": [{{"quote": str, "level": 0|1|2|3|null, "domain": "creative"|"operation"|"analytics"|"new_biz"|null, "why": str}}],
 "has_outcome_figure": 0|1}}"""


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
    """LLM 의 항목별 판정(mentions)에서 청크 값을 코드로 정한다.
    ai_level = 회사 AI 항목 중 최고 단계, ai_domain = 그 항목의 영역, reason = 그 항목의 인용 + 이유"""
    ms = []
    for m in out.get("mentions") or []:
        lv = m.get("level")
        lv = int(lv) if str(lv) in ("0", "1", "2", "3") else None
        if AI_RE.search(str(m.get("quote", ""))):  # AI 용어가 없는 구절은 버린다 (v6)
            ms.append({**m, "level": lv})
    own = [m for m in ms if m["level"] is not None]
    if not own:
        why = ms[0] if ms else {}
        return {"ai_level": None, "ai_domain": None, "has_outcome_figure": 0,
                "reason": f"무관: '{why.get('quote', '')}' {why.get('why', '')}"[:200]}
    top = max(own, key=lambda m: m["level"])  # 같은 단계면 먼저 나온 항목
    domain = top.get("domain") if top["level"] >= 1 and top.get("domain") in DOMAINS else None
    figure = 1 if str(out.get("has_outcome_figure")) == "1" else 0
    return {"ai_level": top["level"], "ai_domain": domain, "has_outcome_figure": figure,
            "reason": f"L{top['level']}: '{top.get('quote', '')}' {top.get('why', '')} (AI 항목 {len(ms)}개)"[:200]}


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
