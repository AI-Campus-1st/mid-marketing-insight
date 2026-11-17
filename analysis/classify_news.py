"""
[A·D4] 제공 뉴스 LLM 분류 → llm_results (Q2 감성·이슈)

대상: 제공 뉴스 집계 대상(is_duplicate = 0) 중 본문 있는 기사 (6,443건)
입력: 제목 + 정제 본문(analysis/preprocess.py 의 body_clean, 앞 MAX_CHARS 자)
출력: llm_results (doc_id, prompt_version) — is_relevant, is_ai_related, sentiment, issue_keywords, summary, reason, model

- 네이버 보강 뉴스는 대상이 아니다 (약관: AI 입력 금지). 제공 뉴스만 사용
- 응답은 캐시(data/processed/llm_cache/classify_news_{PROMPT_VERSION}.jsonl)에 저장 → 재실행 시 재호출 없음, 중단돼도 이어서
- 토큰 사용량과 추정 비용을 출력한다 (단가는 PRICE_PER_M, 실제 청구액은 OpenAI 대시보드에서 확인)

실행:
  python analysis/classify_news.py --dry-run            # 대상 건수, 프롬프트 1개, 예상 비용만
  python analysis/classify_news.py --sample 50          # 고정 표본 50건 시험 (비용 확인)
  python analysis/classify_news.py                      # 전체
필요: .env 의 OPENAI_API_KEY (선택: OPENAI_MODEL)
"""

import argparse
import json
import os
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402
from analysis.preprocess import OUT_CSV  # noqa: E402

PROMPT_VERSION = "news_v4"
DEFAULT_MODEL = "gpt-4.1-mini"   # event_classify.py 와 같은 모델. .env 의 OPENAI_MODEL 로 바꿀 수 있음
PRICE_PER_M = {"gpt-4.1-mini": (0.40, 1.60)}   # (입력, 출력) USD / 100만 토큰 — 추정용, 단가 바뀌면 수정
MAX_CHARS = 1500      # 본문 앞부분만 (중앙값 약 1,070자 → 대부분 전문)
SAMPLE_SEED = 42
CACHE = settings.DATA_DIR / "processed" / "llm_cache" / f"classify_news_{PROMPT_VERSION}.jsonl"
SAMPLE_FILE = settings.DATA_DIR / "processed" / "review" / "llm_sample_N.txt"   # N 자리에 표본 크기
# v4: 관련·AI중심은 LLM 이 고른 사실(주체 유형·행위 유형·AI 역할)로 코드가 정한다
#   (v3 까지 '0 으로 보라'고 적은 유형도 LLM 이 1 로 판정 — 관련 오류가 모두 LLM 1/사람 0 쪽)
INDUSTRY_SUBJECTS = {"광고대행사", "매체·광고플랫폼", "애드테크·마케팅솔루션", "브랜드", "업계전반"}
INDUSTRY_ACTIONS = {"마케팅서비스", "캠페인", "사업", "분석", "규제"}
SUBJECT_TYPES = INDUSTRY_SUBJECTS | {"공공기관", "교육기관", "다른산업", "기타"}
ACTION_TYPES = INDUSTRY_ACTIONS | {"모집안내", "지원사업", "인사", "기타"}


# action_type 자리에 이슈·주체 이름을 쓰는 경우가 있어 가까운 행위로 바꾼다 (v4 시험 50건 중 11건)
ACTION_ALIAS = {"실적·투자·상장": "사업", "제휴·협력": "사업", "행사·시상": "모집안내", "교육기관": "모집안내",
                "공공기관": "지원사업", "AI 교육·인력": "모집안내"}


def decide(subject_type: str, action_type: str, ai_role: str) -> tuple[int, int]:
    """(is_relevant, is_ai_related). 관련 = 광고·마케팅 산업 주체가 산업 활동을 함, AI중심 = AI 역할이 핵심"""
    if subject_type == "브랜드":   # 일반 기업은 마케팅 활동일 때만 (상품 출시·사옥 이전·IP 사업 같은 '사업'은 제외)
        rel = action_type in {"캠페인", "마케팅서비스", "분석"}
    else:
        rel = subject_type in INDUSTRY_SUBJECTS and action_type in INDUSTRY_ACTIONS
    return int(rel), int(ai_role == "핵심")


SENTIMENT = {"기대": "positive", "우려": "negative", "중립": "neutral"}
# Q2 키워드 Top N 을 셀 수 있게 고정 목록에서 고른다 (v1 자유 키워드는 기사마다 표현이 달라 집계 불가)
ISSUES = ["생성형 AI 콘텐츠 제작", "AI 개인화·타겟팅", "AI 에이전트", "AI 검색·GEO", "마케팅 자동화·애드테크",
          "리테일 미디어·커머스", "AICC·고객상담", "AI 교육·인력", "AI 규제·저작권·윤리", "개인정보·데이터 규제",
          "실적·투자·상장", "제휴·협력", "행사·시상", "숏폼·인플루언서", "기타"]

# 이슈마다 한 줄 정의 (v3) — LLM 이 넓은 항목(애드테크·생성형)을 기본값처럼 쓰지 않도록
ISSUE_DESC = {
    "생성형 AI 콘텐츠 제작": "광고 소재·영상·이미지·카피를 AI 로 생성·제작 (본문에 생성·제작 이야기가 있을 때만)",
    "AI 개인화·타겟팅": "고객·오디언스 데이터 분석, 예측, 추천, 맞춤 타겟팅",
    "AI 에이전트": "AI 에이전트·에이전틱 AI 가 업무를 스스로 수행",
    "AI 검색·GEO": "AI 검색·답변 엔진 최적화(GEO·AEO), 검색 광고의 AI 변화",
    "마케팅 자동화·애드테크": "광고 운영·집행·입찰 자동화 플랫폼, DSP·SSP 등 애드테크 기술 (마케팅 기술이면 무조건 붙이지 않음)",
    "리테일 미디어·커머스": "리테일 미디어, 커머스 광고, 라이브커머스·쇼핑",
    "AICC·고객상담": "AI 컨택센터·챗봇 상담",
    "AI 교육·인력": "AI 교육·인재 양성·조직 역량",
    "AI 규제·저작권·윤리": "AI 관련 규제·저작권·딥페이크·윤리 논란",
    "개인정보·데이터 규제": "개인정보·쿠키·데이터 활용 규제",
    "실적·투자·상장": "실적 발표, 투자 유치, 상장",
    "제휴·협력": "MOU·제휴·공동 개발·합작 법인",
    "행사·시상": "세미나·콘퍼런스·광고제·전시·시상",
    "숏폼·인플루언서": "숏폼 영상, 인플루언서·크리에이터 마케팅",
    "기타": "위 목록에 맞는 것이 없음",
}

# v1 → v2 변경 (표본 50건 A 확인): 감성 기대 40/50 — 출시·MOU·교육·상장 발표를 기대로 판정,
# 관련 84% — 대학 발전기금·스타트업 대회 모집·지자체 투자유치 등을 관련으로 판정, 자유 키워드는 기사마다 표현이 달라 집계 불가
# → 감성 기본값 중립 + 발표형 기사는 중립, 관련성은 '주체가 하는 일' 기준 + 0 예시 보강, 이슈는 고정 목록
# v2 → v3 변경 (표본 50건 A 사람 검수: 관련 72%·AI중심 82%·감성 86%, 이슈 관련 기사 겹침 78%·정밀도 60%):
#   관련 오류 14건·AI중심 9건이 모두 LLM 1/사람 0 → 다른 산업 AI·IT, 공공·교육·창업 사업, 행사 모집·위촉을 0 으로 명시,
#   세미나(내용 소개 있으면 1)·교육(모집·수료 0) 기준, 관련과 AI중심은 따로 판단
#   감성: 사람 판정 기대 0/50 → 기대 더 엄격, 회의적 질문·한계 지적은 우려
#   이슈: 애드테크 LLM 19/사람 11·생성형 11/6 과다, 행사·시상 3/8 부족, MOU 에 제휴·협력 누락, 사람이 3개 고른 기사 있음
#   → 항목 정의 추가, 행사·MOU 기사 규칙, 최대 3개
# v3 → v4 (같은 50건: v3 관련 78%·AI중심 82%·감성 92%): '0 으로 보라'고 적은 유형도 여전히 관련 1 로 판정(10건)
#   → LLM 은 주체 유형·행위 유형·AI 역할만 고르고, 관련·AI중심은 decide() 가 정함 (B 의 공시 분류 v5 와 같은 방식)
SYSTEM = """당신은 광고대행사 신사업기획팀장을 위해 뉴스를 분류하는 분석가입니다.
기사 하나를 읽고 아래 항목을 골라 JSON 으로만 답하세요. 관련 여부는 고른 값으로 코드가 정하므로, 주체·행위·AI 역할을 있는 그대로 고르세요.

[subject_type] 제목과 첫 문단에서 '무언가를 하는' 주체의 유형 하나 (주체가 여럿이면 기사의 중심 주체)
  광고대행사 / 매체·광고플랫폼 (광고를 파는 매체, 포털·SNS·커머스의 광고 사업) / 애드테크·마케팅솔루션 (광고·마케팅 기술·솔루션 회사) /
  브랜드 (자사 제품의 마케팅·광고를 하는 일반 기업) / 업계전반 (특정 회사 없이 광고·마케팅 시장·트렌드를 다룸) /
  공공기관 (정부·지자체·공공기관·협회·진흥원) / 교육기관 (대학·교육 업체·교육 프로그램 운영 주체) /
  다른산업 (통신·클라우드·IT·금융·핀테크·제조·유통 등 광고·마케팅 외 산업의 회사가 그 산업의 일을 함) / 기타
[action_type] 그 주체가 하는 일 하나 (아래 9개 이름 중에서만. 주체 유형이나 이슈 목록의 이름을 쓰지 않는다)
  마케팅서비스 (광고·마케팅 솔루션·서비스 출시·도입·고도화) / 캠페인 (광고·마케팅 캠페인 집행·사례) /
  사업 (광고·마케팅 사업의 실적·투자·상장·조직·제휴) / 분석 (광고·마케팅 시장·트렌드·전략 분석, 세미나에서 발표된 내용 소개 포함) /
  규제 (광고·개인정보·AI 규제) / 모집안내 (행사·교육·대회·공모전 개최·참가 모집·수료·성료 안내) /
  지원사업 (지원사업·육성 프로그램 선정·운영) / 인사 (인물 영입·위촉·선임) / 기타
[ai_role] 기사에서 AI 의 역할 하나
  핵심 (AI 기술·서비스·도입·규제·영향이 제목이나 첫 문단의 핵심. AI 가 주제인 교육·세미나·공모전 포함) /
  배경 (회사 소개·배경 설명·교육 과목·지원 항목·사업 분야 나열 속 하나) / 없음
[sentiment] 기사 논조. 기본값은 중립이며, 대부분의 기사는 중립이다.
  중립: 사실 전달. 출시·도입·제휴·MOU·투자·상장·실적·행사·교육·수상 '발표' 기사는 회사의 자랑 문구나
        회사 관계자의 기대 발언이 있어도 중립. 기대와 우려를 함께 다룬 기사도 중립
  기대: 기자·외부 전문가·보고서가 광고·마케팅 업계에 대한 긍정적 전망·효과를 '평가'하는 것이 기사 전체의 중심일 때만
  우려: 위험·규제 부담·부작용(저작권·딥페이크·허위정보·일자리·개인정보·신뢰 하락)·실패·한계가 기사의 중심.
        회의적인 질문형 제목('~할 수 있을까?')이나 '성과가 안 난다', '과장됐다' 같은 한계 지적이 중심이면 우려
[issue_keywords] 아래 목록에서 기사의 핵심 이슈를 1~3개 골라 이름 그대로 쓴다. 넓은 항목을 기본값처럼 붙이지 않는다.
  규칙: 세미나·콘퍼런스·광고제·전시 기사는 '행사·시상'을 반드시 포함 / MOU·제휴·공동 개발 기사는 '제휴·협력'을 반드시 포함 /
        실적·투자·상장 기사는 '실적·투자·상장'을 포함 / 맞는 것이 없으면 "기타"
""" + "\n".join(f"  - {k}: {v}" for k, v in ISSUE_DESC.items()) + """
[summary] 한 줄 요약 (40자 이내)
[reason] subject_type·action_type·sentiment 판단 근거 (50자 이내)

응답 형식:
{"subject": str (주체 이름), "subject_type": str, "action_type": str, "ai_role": "핵심"|"배경"|"없음",
 "sentiment": "기대"|"우려"|"중립", "issue_keywords": [str, ...], "summary": str, "reason": str}"""


def load_targets() -> pd.DataFrame:
    df = pd.read_csv(OUT_CSV)
    return df[(df["is_duplicate"] == 0) & df["body_clean"].notna()].reset_index(drop=True)


def sample_targets(n: int) -> pd.DataFrame:
    """고정 표본 n건. 처음 뽑을 때 doc_id 를 파일에 저장하고, 이후엔 파일을 그대로 쓴다
    (대상 목록이 바뀌어도 — 예: 중복 규칙 변경 — 같은 기사로 시험·검수하도록)"""
    t = load_targets()
    f = SAMPLE_FILE.with_name(f"llm_sample_{n}.txt")
    if f.exists():
        ids = f.read_text(encoding="utf-8").split()
        return t.set_index("doc_id").loc[ids].reset_index()
    used = {d for g in f.parent.glob("llm_sample_*.txt") for d in g.read_text(encoding="utf-8").split()}
    s = t[~t["doc_id"].isin(used)].sample(n, random_state=SAMPLE_SEED)   # 다른 표본(프롬프트 개선용)에 쓴 기사는 빼고
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\n".join(s["doc_id"]), encoding="utf-8")
    return s


def build_prompt(r) -> str:
    return f"날짜: {r.published_date[:10]} / 매체: {r.media}\n제목: {r.title}\n본문: {str(r.body_clean)[:MAX_CHARS]}"


def load_cache() -> dict[str, dict]:
    if not CACHE.exists():
        return {}
    return {r["doc_id"]: r for r in map(json.loads, CACHE.read_text(encoding="utf-8").splitlines())}


def validate(out: dict) -> dict:
    """응답 값 정리. 유형 값이 목록 밖이면 가까운 값 또는 '기타'로 바꾸고(원래 값은 reason 에 남김), 감성이 틀리면 ValueError → 재시도"""
    if out.get("sentiment") not in SENTIMENT:
        raise ValueError(f"잘못된 감성: {out.get('sentiment')}")
    note = []
    st, at, ai = out.get("subject_type"), out.get("action_type"), out.get("ai_role")
    if st not in SUBJECT_TYPES:
        note.append(f"subject_type={st}"); st = "기타"
    if at not in ACTION_TYPES:
        note.append(f"action_type={at}"); at = ACTION_ALIAS.get(at, "기타")
    if ai not in ("핵심", "배경", "없음"):
        note.append(f"ai_role={ai}"); ai = "배경"
    if note:
        out["reason"] = f"{out.get('reason', '')} [값 보정: {', '.join(note)}]"
    rel, is_ai = decide(st, at, ai)
    raw = [str(k).strip() for k in out.get("issue_keywords") or []]
    kws = [k for k in raw if k in ISSUES][:3] or ["기타"]   # 목록 밖 값은 버리고, 남는 게 없으면 기타 (버린 값은 reason 뒤에 남김)
    dropped = [k for k in raw if k and k not in ISSUES]
    reason = str(out.get("reason", ""))
    if dropped:
        reason += f" [목록 밖: {', '.join(dropped)}]"
    return {"is_relevant": rel, "is_ai_related": is_ai, "sentiment": out["sentiment"], "issue_keywords": kws,
            "summary": str(out.get("summary", ""))[:80],
            "reason": f"[{out.get('subject', '')}·{st}·{at}·AI {ai}] {reason}"[:160],
            "subject": str(out.get("subject", ""))[:40], "subject_type": st, "action_type": at, "ai_role": ai}


def classify_one(client, model, r) -> dict | None:
    for attempt in range(3):   # 연결 오류·형식 오류는 재시도, 그래도 실패하면 건너뜀 (다시 실행하면 이어서)
        try:
            res = client.chat.completions.create(
                model=model, temperature=0, response_format={"type": "json_object"},
                messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": build_prompt(r)}])
            out = validate(json.loads(res.choices[0].message.content))
            u = res.usage
            return {"doc_id": r.doc_id, "model": model, "prompt_version": PROMPT_VERSION, **out,
                    "tokens_in": u.prompt_tokens, "tokens_out": u.completion_tokens}
        except (APIConnectionError, APITimeoutError, RateLimitError, json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            print(f"  재시도 {attempt + 1}/3 ({type(e).__name__}): {r.doc_id}")
            time.sleep(5 * (attempt + 1))
    return None


def cost(rows: list[dict], model: str) -> tuple[int, int, float | None]:
    tin = sum(r.get("tokens_in", 0) for r in rows)
    tout = sum(r.get("tokens_out", 0) for r in rows)
    p = PRICE_PER_M.get(model)
    return tin, tout, (tin * p[0] + tout * p[1]) / 1e6 if p else None


def save_db(rows: list[dict]) -> None:
    with sqlite3.connect(settings.DB_PATH) as con:
        con.executemany(
            "INSERT OR REPLACE INTO llm_results (doc_id, prompt_version, is_relevant, is_ai_related, sentiment, "
            "issue_keywords, summary, reason, model) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(r["doc_id"], r["prompt_version"], r["is_relevant"], r["is_ai_related"], SENTIMENT[r["sentiment"]],
              json.dumps(r["issue_keywords"], ensure_ascii=False), r["summary"], r["reason"], r["model"]) for r in rows])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, help="고정 표본 N건만 (시험용, data/processed/review/llm_sample_N.txt 에 고정)")
    ap.add_argument("--dry-run", action="store_true", help="API 호출 없이 대상 건수·프롬프트·예상 비용만")
    ap.add_argument("--workers", type=int, default=4, help="동시 호출 수")
    args = ap.parse_args()

    load_dotenv(settings.ROOT / ".env")
    model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
    targets = load_targets()
    if args.sample:
        targets = sample_targets(args.sample)
    cache = load_cache()
    todo = [r for r in targets.itertuples() if r.doc_id not in cache]
    print(f"대상 {len(targets)}건 (캐시 {len(targets) - len(todo)}건, 새로 호출 {len(todo)}건) / 모델 {model} / {PROMPT_VERSION}")

    if args.dry_run:
        print("\n--- 프롬프트 예시 ---\n" + build_prompt(targets.iloc[0:1].itertuples().__next__())[:800])
        done = [cache[d] for d in targets["doc_id"] if d in cache]
        if done:
            tin, tout, usd = cost(done, model)
            n = len(done)
            print(f"\n캐시 기준 건당 평균 입력 {tin / n:.0f} / 출력 {tout / n:.0f} 토큰"
                  + (f" → 남은 {len(todo)}건 예상 ${usd / n * len(todo):.2f}" if usd is not None else ""))
        return

    client = OpenAI(default_headers={"Accept-Encoding": "gzip, deflate"})   # event_classify.py 와 같은 이유(압축 패키지 충돌)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    lock, new, n_fail = threading.Lock(), [], 0
    with CACHE.open("a", encoding="utf-8") as f, ThreadPoolExecutor(args.workers) as ex:
        futs = [ex.submit(classify_one, client, model, r) for r in todo]
        for i, fut in enumerate(as_completed(futs), 1):
            res = fut.result()
            if res is None:
                n_fail += 1
                continue
            with lock:
                f.write(json.dumps(res, ensure_ascii=False) + "\n")
                f.flush()
                cache[res["doc_id"]] = res
                new.append(res)
            if i % 200 == 0:
                print(f"  {i}/{len(todo)}")

    rows = [cache[d] for d in targets["doc_id"] if d in cache]
    save_db(rows)
    tin, tout, usd = cost(new, model)
    print(f"API 호출 {len(new)}건 (실패 {n_fail}건 — 다시 실행하면 이어서), llm_results 저장 {len(rows)}건")
    print(f"이번 호출 토큰 입력 {tin:,} / 출력 {tout:,}" + (f" → 추정 ${usd:.3f}" if usd is not None else ""))
    if new and usd is not None:
        rest = len(load_targets()) - len(cache)
        print(f"  건당 ${usd / len(new):.5f} → 남은 {rest:,}건 예상 ${usd / len(new) * rest:.2f}")
    r = pd.DataFrame(rows)
    if not r.empty:
        print(f"관련 {r['is_relevant'].mean():.0%} / AI 중심 {r['is_ai_related'].mean():.0%} / 감성 {r['sentiment'].value_counts().to_dict()}")


if __name__ == "__main__":
    main()
