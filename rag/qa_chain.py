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

PROMPT_VERSION = "qa_v3"
# v1 → v2 (테스트 15개): 1차 MOU 연혁("10개 AI 전문 기업과 손잡고")을 보고 "따라서 AI 얼라이언스는 10개"로 추론한 환각,
#   정답 청크가 검색 9~22위라 근거 5개에 못 들어감, 여러 가지를 묻는 질문에 일부만 답함
#   → 고유 이름이 근거에 없으면 답하지 않기·추론 금지, 묻는 것 모두 답하기, 검색 30개 → 근거 8개
#   + 같은 본문 합치기를 앞 300자 비교 → 본문 전체 비교로 (연혁 청크를 잘못 합쳐 '아이앤서베이'를 놓친 버그)
# v2 → v3 (대시보드 챗봇 화면): 답이 한 덩어리 긴 문단, "근거에 ~라는 내용이 있고" 같은 말투, 원문 기호(**) 섞임,
#   분기마다 같은 문장이라 출처가 5~8개 → 결론(summary)·핵심 줄(points, 줄마다 근거 1개)을 따로 받아 코드가 목록으로 조립,
#   원문 옮겨 쓰기 대신 쉬운 말로, 반복 내용은 최신 보고서 하나만 인용, 출처 번호는 1부터 다시 매김, JSON 끊기면 한 번 재시도
DEFAULT_MODEL = "gpt-4.1-mini"
SEARCH_K, CONTEXT_N, CONTEXT_CHARS = 30, 8, 1000
REFUSAL = "공시에서 확인되지 않습니다."
DART_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"

SYSTEM = f"""당신은 광고대행사 5개사(제일기획, 이노션, 플레이디, 인크로스, KT나스미디어)의 DART 정기공시(사업·반기·분기보고서)를 근거로
질문에 답하는 분석가입니다. 아래 [근거]에 적힌 내용만 사용해 한국어로 답하고 JSON 으로만 답하세요.

규칙 (무엇을 답하나)
1. [근거]에 질문의 답이 직접 적혀 있을 때만 answerable=true. 비슷한 주제만 있고 질문한 내용(그 서비스·제휴·조직·숫자)이 없으면 false.
   "현황", "무엇을 하나"처럼 넓게 묻는 질문은 그 회사의 관련 내용이 근거에 있으면 true 로 답한다.
2. 근거에 없는 사실, 배경지식, 추측을 덧붙이지 않는다. 날짜·숫자·이름은 근거에 적힌 그대로 쓴다.
3. 질문한 회사와 근거의 회사가 다르면 그 근거는 쓰지 않는다. 분석 대상 5개사가 아닌 회사에 대한 질문은 false.
4. 질문이 가리키는 고유 이름(서비스·조직·제휴·특허 이름, 예: 'AI 얼라이언스')이 근거에 그대로 나오지 않으면,
   비슷한 다른 문장으로 그 이름에 대한 답을 만들지 않는다. "따라서", "~로 보인다" 같은 추론으로 잇지 않는다.
5. 질문이 여러 가지(언제·무엇·몇 개)를 물으면 근거에 있는 것은 모두 답한다. 목록을 묻는 질문(특허·서비스 등)은 근거에 나온 것을 모두.
   근거에 없는 부분은 points 에 "OO: 공시에서 확인되지 않습니다" 한 줄로.

규칙 (어떻게 쓰나 — 화면에 그대로 보인다)
6. summary: 질문에 바로 답하는 결론 한 문장 (근거 번호 없이). "~라고 공시했습니다", "~하고 있습니다"처럼 끝맺는다.
7. points: 핵심 내용 2~5줄. 한 줄에 한 가지, 40자 안팎, "항목: 내용" 꼴이면 좋다. 각 줄의 근거 번호를 cite 에.
8. 원문을 길게 옮겨 쓰지 말고 쉬운 말로 줄인다. "근거에 ~라는 내용이 있고", "공시에 따르면 ~라고 기재" 같은 말은 쓰지 않는다.
   원문의 표 기호·별표·괄호 번호는 옮기지 않는다. 진행 상태·연도는 "(진행 중, 2025년)"처럼 괄호로 짧게.
9. 같은 내용이 여러 보고서에 반복되면 가장 최근 보고서 하나만 cite 한다.
10. answerable=false 이면 summary 는 "{REFUSAL}", points 는 [].

응답 형식: {{"answerable": true|false, "summary": str, "points": [{{"text": str, "cite": int}}]}}"""


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
        blocks = [f"[{i + 1}] {r.corp_name} | {r.report_name} (접수 {r.report_date}) | {r.section}\n{r.text[:CONTEXT_CHARS].replace('**', '')}"
                  for i, r in enumerate(ctx.itertuples())]
        msgs = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"[질문]\n{question}\n\n[근거]\n" + "\n\n".join(blocks)}]
        out = {}
        for _ in range(2):  # JSON 이 중간에 끊기면 한 번 더
            res = self.client.chat.completions.create(model=self.model, temperature=0, max_tokens=900,
                                                      response_format={"type": "json_object"}, messages=msgs)
            try:
                out = json.loads(res.choices[0].message.content)
                break
            except json.JSONDecodeError:
                continue
        points = []
        for pt in out.get("points") or []:
            c = pt.get("cite") if isinstance(pt, dict) else None
            c = c[0] if isinstance(c, list) and c else c
            if isinstance(pt, dict) and pt.get("text") and isinstance(c, int) and 1 <= c <= len(ctx):
                points.append((pt["text"].replace("**", "").strip(), c))
        answerable = bool(out.get("answerable")) and bool(points)  # 인용 없는 답은 거절로
        # 출처 번호를 화면에 나오는 순서대로 1, 2, 3… 으로 다시 매긴다
        order = list(dict.fromkeys(c for _, c in points))
        new = {c: i + 1 for i, c in enumerate(order)}
        answer = REFUSAL
        if answerable:
            answer = (out.get("summary") or "").replace("**", "").strip() + "\n\n" + \
                "\n".join(f"- {t} [{new[c]}]" for t, c in points)
        sources = [] if not answerable else [{
            "n": new[c], "corp": ctx.corp_name[c - 1], "report": ctx.report_name[c - 1], "report_date": ctx.report_date[c - 1],
            "section": ctx.section[c - 1], "ai_level": None if ctx.ai_level.isna()[c - 1] else int(ctx.ai_level[c - 1]),
            "url": DART_URL.format(ctx.rcept_no[c - 1]), "chunk_id": ctx.chunk_id[c - 1]} for c in order]
        return {"question": question, "answerable": answerable, "answer": answer, "sources": sources,
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
