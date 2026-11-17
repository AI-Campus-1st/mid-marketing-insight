"""
[B·D5] 공시 Q&A — 사업보고서 근거로만 답하고, 근거에 답이 없으면 거절

흐름
    1. 검색: rag/retriever.py 하이브리드(임베딩 + BM25) + 기업 필터, 상위 SEARCH_K 개
    2. 정리: 같은 문장이 분기마다 반복되므로 본문이 같은 청크는 하나만 남기고(가장 이른 보고서), 최대 CONTEXT_N 개
    3. 답변: LLM 에 근거 청크만 주고 JSON 으로 답하게 한다
       {"answerable": true|false, "answer": str, "citations": [근거 번호]}
       - 근거 안에 답이 없으면 answerable=false → "공시에서 확인되지 않습니다"
       - answerable=true 인데 인용이 없으면 코드가 거절로 바꾼다 (근거 없는 답 방지)
    4. 출처: 인용한 근거마다 공시명·접수일·DART 링크·섹션·AI 단계

거절을 유사도 경계값으로 하지 않는 이유: 답이 있는 질문과 없는 질문의 검색 유사도가 겹침 (rag/eval_retrieval.py, decisions.md)
LangChain 대신 OpenAI SDK 를 직접 쓴다: 검색기를 직접 만들었고 체인이 한 단계라 의존성을 늘릴 이유가 없음

실행: python rag/qa_chain.py "플레이디의 AX Unit은 어떤 조직인가요?"
필요: .env 의 OPENAI_API_KEY (선택: OPENAI_MODEL, 기본 gpt-4.1-mini)
"""
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402
from rag.retriever import Retriever  # noqa: E402

PROMPT_VERSION = "qa_v2"
# v1 → v2 (테스트 15개): 1차 MOU 연혁("10개 AI 전문 기업과 손잡고")을 보고 "따라서 AI 얼라이언스는 10개"로 추론한 환각,
#   정답 청크가 검색 9~22위라 근거 5개에 못 들어감, 여러 가지를 묻는 질문에 일부만 답함
#   → 고유 이름이 근거에 없으면 답하지 않기·추론 금지, 묻는 것 모두 답하기, 검색 30개 → 근거 8개
#   + 같은 본문 합치기를 앞 300자 비교 → 본문 전체 비교로 (연혁 청크를 잘못 합쳐 '아이앤서베이'를 놓친 버그)
DEFAULT_MODEL = "gpt-4.1-mini"
SEARCH_K, CONTEXT_N, CONTEXT_CHARS = 30, 8, 1000
REFUSAL = "공시에서 확인되지 않습니다."
DART_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"

SYSTEM = f"""당신은 광고대행사 5개사(제일기획, 이노션, 플레이디, 인크로스, KT나스미디어)의 DART 정기공시(사업·반기·분기보고서)를 근거로
질문에 답하는 분석가입니다. 아래 [근거]에 적힌 내용만 사용해 한국어로 답하고 JSON 으로만 답하세요.

규칙
1. [근거]에 질문의 답이 직접 적혀 있을 때만 answerable=true. 비슷한 주제만 있고 질문한 내용(그 서비스·제휴·조직·숫자)이 없으면 false.
2. 근거에 없는 사실, 배경지식, 추측을 덧붙이지 않는다. 날짜·숫자·이름은 근거에 적힌 그대로 쓴다.
3. 답의 문장마다 근거 번호를 [1]처럼 붙이고, 쓴 근거 번호를 citations 에 넣는다.
4. 질문한 회사와 근거의 회사가 다르면 그 근거는 쓰지 않는다. 분석 대상 5개사가 아닌 회사에 대한 질문은 false.
5. answerable=false 이면 answer 는 "{REFUSAL}" 한 문장만.
6. 질문이 가리키는 고유 이름(서비스·조직·제휴·특허 이름, 예: 'AI 얼라이언스')이 근거에 그대로 나오지 않으면,
   비슷한 다른 문장으로 그 이름에 대한 답을 만들지 않는다. "따라서", "~로 보인다" 같은 추론으로 잇지 않는다.
7. 질문이 여러 가지(언제·무엇·몇 개)를 물으면 근거에 있는 것은 모두 답한다. 목록을 묻는 질문(특허·서비스 등)은 근거에 나온 것을 모두.
   근거에 없는 부분만 "공시에서 확인되지 않습니다"라고 덧붙인다.
8. 답은 4문장 이내로.

응답 형식: {{"answerable": true|false, "answer": str, "citations": [int]}}"""


def _norm(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", s)


class QA:
    def __init__(self, retriever: Retriever | None = None):
        from dotenv import load_dotenv
        from openai import OpenAI
        load_dotenv(settings.ROOT / ".env")
        self.retriever = retriever or Retriever()
        self.client = OpenAI(default_headers={"Accept-Encoding": "gzip, deflate"})
        self.model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL)

    def context(self, question: str):
        hits = self.retriever.search(question, k=SEARCH_K)
        # 본문 전체가 같은 청크만 하나로 (앞부분만 비교하면 연혁처럼 뒤에 항목이 추가된 청크를 잘못 합침)
        hits["key"] = hits.text.map(_norm)
        hits = hits.sort_values("report_date").drop_duplicates("key")  # 같은 본문은 가장 이른 보고서 하나
        return hits.sort_index().head(CONTEXT_N).reset_index(drop=True)  # 검색 순위대로

    def ask(self, question: str) -> dict:
        ctx = self.context(question)
        blocks = [f"[{i + 1}] {r.corp_name} | {r.report_name} (접수 {r.report_date}) | {r.section}\n{r.text[:CONTEXT_CHARS]}"
                  for i, r in enumerate(ctx.itertuples())]
        res = self.client.chat.completions.create(
            model=self.model, temperature=0, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": f"[질문]\n{question}\n\n[근거]\n" + "\n\n".join(blocks)}])
        out = json.loads(res.choices[0].message.content)
        cites = [c for c in out.get("citations") or [] if isinstance(c, int) and 1 <= c <= len(ctx)]
        answerable = bool(out.get("answerable")) and bool(cites)  # 인용 없는 답은 거절로
        sources = [] if not answerable else [{
            "n": c, "corp": ctx.corp_name[c - 1], "report": ctx.report_name[c - 1], "report_date": ctx.report_date[c - 1],
            "section": ctx.section[c - 1], "ai_level": None if ctx.ai_level.isna()[c - 1] else int(ctx.ai_level[c - 1]),
            "url": DART_URL.format(ctx.rcept_no[c - 1]), "chunk_id": ctx.chunk_id[c - 1]} for c in sorted(set(cites))]
        return {"question": question, "answerable": answerable,
                "answer": out.get("answer", "") if answerable else REFUSAL, "sources": sources,
                "context_chunk_ids": ctx.chunk_id.tolist(), "corp_filter": ctx.corp_filter.iloc[0] if len(ctx) else None,
                "model": self.model, "prompt_version": PROMPT_VERSION}


def format_answer(r: dict) -> str:
    lines = [r["answer"]]
    for s in r["sources"]:
        lv = "" if s["ai_level"] is None else f" · AI 단계 L{s['ai_level']}"
        lines.append(f"  [{s['n']}] {s['corp']} {s['report']} (접수 {s['report_date']}) · {s['section'].split('>')[-1].strip()}{lv}\n      {s['url']}")
    return "\n".join(lines)


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "플레이디의 AX Unit은 어떤 조직인가요?"
    print(format_answer(QA().ask(q)))
