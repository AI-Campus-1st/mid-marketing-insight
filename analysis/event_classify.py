"""
[A·D3] 이벤트 검토 시트 LLM 1차 판정 — 네이버 외 출처만 사용

event_review_sheet.py 가 만든 시트에서, 묶음 안에 네이버가 아닌 기사(제공 뉴스·뉴스룸·웹 원문)가 있는 행만
그 기사들의 본문으로 LLM 이 판정한다. 네이버 기사는 프롬프트에 넣지 않는다.
(NAVER API HUB 약관: 검색 결과·파생물의 AI 입력 금지 → 네이버만 있는 묶음은 사람이 판정)

- 사람이 이미 입력한 행은 건드리지 않는다 (judged_by = human 또는 is_event 가 채워진 행)
- 결과는 추천값: judged_by = llm 으로 표시하고, 표본 20행을 사람이 검수해 정확도를 기록한다
- LLM 응답은 캐시(data/processed/llm_cache/event_classify_{PROMPT_VERSION}.jsonl)에 저장해 재실행 시 재호출하지 않는다

실행: python analysis/event_classify.py [--limit N] [--dry-run]
필요: .env 의 OPENAI_API_KEY (선택: OPENAI_MODEL)
"""

import argparse
import json
import os
import sys
import time
import unicodedata
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402
from analysis.event_review_sheet import MEMBERS, SHEET  # noqa: E402

PROMPT_VERSION = "event_v2"
DEFAULT_MODEL = "gpt-4.1-mini"  # .env 의 OPENAI_MODEL 로 바꿀 수 있음
# 입력 출처가 바뀌면 같은 프롬프트라도 결과가 달라지므로 캐시를 분리한다
# (2026-11-16 뉴스룸 미사용 결정 → 제공 뉴스·웹 원문만 입력)
INPUT_VERSION = "provided_web"
CACHE = settings.DATA_DIR / "processed" / "llm_cache" / f"event_classify_{PROMPT_VERSION}_{INPUT_VERSION}.jsonl"
MAX_DOCS = 3          # 묶음당 프롬프트에 넣을 기사 수
MAX_CHARS = 1500      # 기사당 본문 길이

# v1 → v2 변경 (AI 2차 검토 20행: LLM Y 정밀도 3/10, N 정확도 10/10)
# 오류 유형: AI 배경 언급을 이벤트로 판정 4, AI 무관 캠페인·수상 2, 행사를 출시로 오인 1
# → 'AI 가 행동의 핵심' 조건, 비해당 목록 강화, 0단계면 N, 판정 순서 명시
SYSTEM = """당신은 광고대행사의 AI 관련 '앵커 이벤트'를 판정하는 분석가입니다.
기사들은 같은 사건을 다룬 묶음입니다. 아래 순서대로 판단하고 JSON 으로만 답하세요.

[판정 순서]
1단계 — 행동이 무엇인가? 대상 기업이 주어인, 날짜가 특정되는 사업 행동 하나를 찾는다.
        아래 '항상 N' 유형이면 AI 가 아무리 언급돼도 N.
2단계 — AI 가 그 행동의 핵심인가? 제목 또는 첫 문단에서 AI 가 행동의 대상·수단 자체여야 한다.
        AI 가 회사 소개, 성장 배경, 향후 방향, 업계 트렌드로만 나오면 N.
3단계 — 1·2단계를 모두 통과하면 Y.

[항상 N]
- 실적·주가·배당 발표 ("AI 기반 성장" 같은 설명이 있어도 N)
- 인사, 대표 선임, 조직 개편·본부 재편 (AI 전담 조직을 '새로 만든' 경우만 예외)
- 세미나·컨퍼런스·쇼케이스 개최나 강연 (그 자리에서 새 AI 서비스를 '처음 공개'한 경우만 예외)
- 시장 전망·트렌드 보고서 발간
- 파트너사 선정·광고 판매 계약 중 AI 와 무관한 것
- 캠페인·수상 중 AI 를 실제로 사용했다는 서술이 없는 것
- 증권사 의견, 다른 회사의 행동

[Y 예시] AI 솔루션 출시·공개, AI 전담 조직 신설, 외부 AI 솔루션 도입, AI 기업 투자·인수,
         AI 기업과의 제휴·협약, AI 특허 출원·등록, AI 로 제작한 캠페인 집행, AI 기술·AI 활용 작품의 수상

[event_type] launch(출시·공개) / organization(조직 신설) / adoption(도입·적용) / investment(투자·인수) /
             partnership(제휴·협약) / patent(특허) / campaign(캠페인) / award(수상)
[ai_level_claimed] 기사가 '주장하는' 수준 (실제 검증 여부와 무관)
  0 언급: AI 가 트렌드·환경으로만 등장
  1 계획: 회사가 주어, 미래형("~할 예정", "추진")
  2 도입·적용: 외부 AI 를 실제 업무·캠페인에 사용 (과거·현재형)
  3 자체 개발·상용화: 자체 개발, 상품·서비스로 출시·판매, 전담 조직, 매출·투자 금액 등 숫자
  경계 규칙: 미래형이면 2 이상 불가. 고유 서비스명이나 숫자가 있으면 한 단계 위로.
  일관성 규칙: is_event 가 Y 이면 ai_level_claimed 는 1 이상이어야 한다 (0 이면 N 으로 판정).
[official_date] 본문의 "○일 밝혔다" 등과 기사 날짜로 추정한 회사 발표일 (YYYY-MM-DD, 모르면 null)
               행사에서 공개한 경우 기사 작성일이 아니라 행사일.

응답 형식:
{"is_event": "Y"|"N", "event_type": str|null, "event_name": str|null (예: "SCV 출시", 20자 이내),
 "ai_level_claimed": 0|1|2|3|null, "official_date": str|null, "reason": str (50자 이내)}"""


def load_bodies() -> dict[str, str]:
    """URL → 본문 (네이버 외 출처만)"""
    bodies = {}
    for f in settings.RAW_NEWS_DIR.glob("01_*.csv"):
        if "묶음" in unicodedata.normalize("NFC", f.name):
            p = pd.read_csv(f, usecols=["원문URL", "본문전문"]).dropna()
            bodies.update(dict(zip(p["원문URL"], p["본문전문"])))
    web = settings.SUPPLEMENT_DIR / "anchor_supplement.csv"
    if web.exists():
        w = pd.read_csv(web, usecols=["원문URL", "본문요약"]).dropna()
        bodies.update(dict(zip(w["원문URL"], w["본문요약"])))
    return bodies


def load_cache() -> dict[str, dict]:
    if not CACHE.exists():
        return {}
    return {r["key"]: r for r in map(json.loads, CACHE.read_text(encoding="utf-8").splitlines())}


def build_prompt(corp: str, docs: pd.DataFrame, bodies: dict[str, str]) -> str:
    parts = [f"대상 기업: {corp}"]
    for i, d in enumerate(docs.itertuples(), 1):
        body = str(bodies.get(d.url, ""))[:MAX_CHARS]
        parts.append(f"\n[기사 {i}] 출처: {d.source} / 날짜: {str(d.date)[:10]}\n제목: {d.title}\n본문: {body}")
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="앞에서 N개 묶음만 (테스트용)")
    parser.add_argument("--dry-run", action="store_true", help="API 호출 없이 대상 건수와 프롬프트 1개만 확인")
    parser.add_argument("--rejudge-llm", action="store_true",
                        help="이전 프롬프트로 LLM 이 판정한 행을 현재 버전으로 다시 판정 (사람 판정은 그대로)")
    args = parser.parse_args()

    load_dotenv(settings.ROOT / ".env")
    sheet = pd.read_csv(SHEET, dtype=str)
    members = pd.read_csv(MEMBERS, dtype=str)
    members = members[members["source"] != "naver"]  # 약관: 네이버 기사는 AI 에 넣지 않음

    not_human = ~sheet["judged_by"].fillna("").eq("human")
    empty = sheet["is_event"].isna()
    if args.rejudge_llm:
        empty = empty | sheet["judged_by"].fillna("").eq("llm")
        # 이전 버전 결과는 비교용으로 남김
        prev = sheet.loc[sheet["judged_by"].eq("llm"), ["key", "is_event", "event_type", "event_name", "ai_level_claimed"]]
        prev.to_csv(SHEET.with_name("llm_previous_judgments.csv"), index=False, encoding="utf-8-sig")
    todo = sheet[sheet["key"].isin(members["key"]) & empty & not_human]
    if args.limit:
        todo = todo.head(args.limit)
    print(f"LLM 판정 대상 {len(todo)}행 (네이버만 있는 행 {int(sheet['only_naver'].astype(int).sum())}행은 사람 판정)")

    bodies = load_bodies()
    cache = load_cache()
    rank = {"web": 0, "provided": 1}

    if todo.empty:
        print("판정할 행이 없습니다 (이미 모두 판정됨)")
        return
    if args.dry_run:
        k = todo.iloc[0]["key"]
        docs = members[members["key"] == k].assign(r=lambda x: x["source"].map(rank)).sort_values("r").head(MAX_DOCS)
        print("\n--- 프롬프트 예시 ---\n" + build_prompt(todo.iloc[0]["corp"], docs, bodies)[:1500])
        return

    # brotli/zstd 응답 해제 시 httpx 와 압축 패키지 버전 충돌이 있어 gzip 만 받는다
    client = OpenAI(default_headers={"Accept-Encoding": "gzip, deflate"})
    model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    n_call = n_fail = 0
    with CACHE.open("a", encoding="utf-8") as cache_file:
        for row in todo.itertuples():
            if row.key not in cache:
                docs = members[members["key"] == row.key].assign(
                    r=lambda x: x["source"].map(rank)).sort_values("r").head(MAX_DOCS)
                out = None
                for attempt in range(3):  # 연결 오류는 재시도, 그래도 실패하면 이 행만 건너뜀
                    try:
                        res = client.chat.completions.create(
                            model=model,
                            temperature=0,
                            response_format={"type": "json_object"},
                            messages=[{"role": "system", "content": SYSTEM},
                                      {"role": "user", "content": build_prompt(row.corp, docs, bodies)}],
                        )
                        out = json.loads(res.choices[0].message.content)
                        break
                    except (APIConnectionError, APITimeoutError, RateLimitError, json.JSONDecodeError) as e:
                        print(f"  재시도 {attempt + 1}/3 ({type(e).__name__}): {row.key}")
                        time.sleep(5 * (attempt + 1))
                if out is None:
                    n_fail += 1
                    continue
                cache[row.key] = {"key": row.key, "model": model, "prompt_version": PROMPT_VERSION, **out}
                cache_file.write(json.dumps(cache[row.key], ensure_ascii=False) + "\n")
                n_call += 1
            r = cache[row.key]
            idx = sheet.index[sheet["key"] == row.key][0]
            # 일관성 규칙을 코드로 한 번 더 강제: Y 인데 0단계(또는 단계 없음)면 N
            if r.get("is_event") == "Y" and r.get("ai_level_claimed") in (0, "0", None):
                r = {**r, "is_event": "N", "reason": f"[규칙] 0단계라 N 처리 / {r.get('reason')}"}
            sheet.loc[idx, "is_event"] = r.get("is_event")
            sheet.loc[idx, "event_type"] = r.get("event_type")
            sheet.loc[idx, "event_name"] = r.get("event_name")
            sheet.loc[idx, "ai_level_claimed"] = r.get("ai_level_claimed")
            sheet.loc[idx, "official_date"] = r.get("official_date") or sheet.loc[idx, "suggested_official_date"]
            sheet.loc[idx, "judged_by"] = "llm"
            sheet.loc[idx, "llm_reason"] = r.get("reason")
            if sheet.loc[idx, "rep_source"] != "naver":  # 1차 출처 추천 (네이버 링크는 넣지 않음)
                sheet.loc[idx, "primary_source_url"] = sheet.loc[idx, "rep_url"]

    sheet.to_csv(SHEET, index=False, encoding="utf-8-sig")
    done = sheet[sheet["judged_by"] == "llm"]
    print(f"API 호출 {n_call}회 (실패 {n_fail}행 — 다시 실행하면 이어서 판정), "
          f"LLM 판정 {len(done)}행 → 이벤트 Y {int((done['is_event'] == 'Y').sum())}행")
    print(f"남은 사람 판정: 네이버만 있는 1·2순위 "
          f"{int(((sheet['only_naver'] == '1') & (sheet['priority'].astype(int) <= 2) & sheet['is_event'].isna()).sum())}행")
    print(f"저장: {SHEET}")


if __name__ == "__main__":
    main()
