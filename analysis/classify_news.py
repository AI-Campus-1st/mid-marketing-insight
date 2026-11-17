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

PROMPT_VERSION = "news_v2"
DEFAULT_MODEL = "gpt-4.1-mini"   # event_classify.py 와 같은 모델. .env 의 OPENAI_MODEL 로 바꿀 수 있음
PRICE_PER_M = {"gpt-4.1-mini": (0.40, 1.60)}   # (입력, 출력) USD / 100만 토큰 — 추정용, 단가 바뀌면 수정
MAX_CHARS = 1500      # 본문 앞부분만 (중앙값 약 1,070자 → 대부분 전문)
SAMPLE_SEED = 42
CACHE = settings.DATA_DIR / "processed" / "llm_cache" / f"classify_news_{PROMPT_VERSION}.jsonl"
SAMPLE_FILE = settings.DATA_DIR / "processed" / "review" / "llm_sample_N.txt"   # N 자리에 표본 크기
SENTIMENT = {"기대": "positive", "우려": "negative", "중립": "neutral"}
# Q2 키워드 Top N 을 셀 수 있게 고정 목록에서 고른다 (v1 자유 키워드는 기사마다 표현이 달라 집계 불가)
ISSUES = ["생성형 AI 콘텐츠 제작", "AI 개인화·타겟팅", "AI 에이전트", "AI 검색·GEO", "마케팅 자동화·애드테크",
          "리테일 미디어·커머스", "AICC·고객상담", "AI 교육·인력", "AI 규제·저작권·윤리", "개인정보·데이터 규제",
          "실적·투자·상장", "제휴·협력", "행사·시상", "숏폼·인플루언서", "기타"]

# v1 → v2 변경 (표본 50건 A 확인): 감성 기대 40/50 — 출시·MOU·교육·상장 발표를 기대로 판정,
# 관련 84% — 대학 발전기금·스타트업 대회 모집·지자체 투자유치 등을 관련으로 판정, 자유 키워드는 기사마다 표현이 달라 집계 불가
# → 감성 기본값 중립 + 발표형 기사는 중립, 관련성은 '주체가 하는 일' 기준 + 0 예시 보강, 이슈는 고정 목록
SYSTEM = """당신은 광고대행사 신사업기획팀장을 위해 뉴스를 분류하는 분석가입니다.
기사 하나를 읽고 아래 기준으로 판단해 JSON 으로만 답하세요.

[is_relevant] 광고·마케팅 산업 동향으로 의미 있는 기사인가? (1/0)
  판단법: 제목과 첫 문단의 주체가 '하는 일'이 광고·마케팅 활동·서비스·산업 자체인가.
  1: 광고·마케팅 기술·솔루션·서비스 출시와 도입, 광고대행사·매체·광고 플랫폼·애드테크 기업의 사업,
     브랜드의 마케팅·광고 캠페인 사례, 광고·마케팅 시장 분석, 광고·개인정보 규제
  0: 마케팅이 지원 항목·교육 과목·나열 속 한 단어로만 나오는 기사. 예:
     정치인·지자체·공공기관 행정과 지원사업(투자유치, 수출 사절단, 전통시장·소상공인 지원 공모, 청년 인재 양성),
     교육·강좌·대회·공모전 참가자 모집이나 수료 소식, 대학·기관 소식(발전기금, 협약식),
     스포츠 후원·상품 출시, 일반 기업·금융사의 실적·투자·인사, 다른 산업 기술(통신 인프라, 반도체, 핀테크 일반)
[is_ai_related] 기사의 '주요 내용'이 AI 인가? (1/0)
  1: AI 기술·서비스·도입·규제·영향이 제목이나 첫 문단의 핵심
  0: AI 가 회사 소개, 배경 설명, 교육 과목·지원 항목 나열 속 하나로만 나옴
[sentiment] 기사가 광고·마케팅 업계에 주는 영향을 '평가'하는 논조. 기본값은 중립.
  중립: 사실 전달. 출시·도입·제휴·MOU·투자·상장·실적·행사·교육·수상 '발표' 기사는 회사의 자랑 문구가 있어도 중립
  기대: 기자·전문가·보고서가 전망·효과·시장 변화를 긍정적으로 '평가'하는 서술이 기사의 중심일 때만
  우려: 위험·규제 부담·부작용(저작권·딥페이크·허위정보·일자리·개인정보·신뢰 하락)·실패·한계를 중심으로 서술
[issue_keywords] 아래 목록에서 기사의 핵심 이슈 1~2개를 골라 그대로 쓴다. 맞는 것이 없으면 "기타".
  """ + " / ".join(ISSUES) + """
[summary] 한 줄 요약 (40자 이내)
[reason] is_relevant·sentiment 판단 근거 (50자 이내)

응답 형식:
{"is_relevant": 1|0, "is_ai_related": 1|0, "sentiment": "기대"|"우려"|"중립",
 "issue_keywords": [str, ...], "summary": str, "reason": str}"""


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
    s = t.sample(n, random_state=SAMPLE_SEED)
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
    """응답 값 정리. 형식이 틀리면 ValueError → 재시도"""
    rel, ai = int(out["is_relevant"]), int(out["is_ai_related"])
    if rel not in (0, 1) or ai not in (0, 1) or out["sentiment"] not in SENTIMENT:
        raise ValueError(f"잘못된 값: {out}")
    raw = [str(k).strip() for k in out.get("issue_keywords") or []]
    kws = [k for k in raw if k in ISSUES][:2] or ["기타"]   # 목록 밖 값은 버리고, 남는 게 없으면 기타 (버린 값은 reason 뒤에 남김)
    dropped = [k for k in raw if k and k not in ISSUES]
    if dropped:
        out["reason"] = f"{out.get('reason', '')} [목록 밖: {', '.join(dropped)}]"
    return {"is_relevant": rel, "is_ai_related": ai, "sentiment": out["sentiment"], "issue_keywords": kws,
            "summary": str(out.get("summary", ""))[:80], "reason": str(out.get("reason", ""))[:120]}


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
