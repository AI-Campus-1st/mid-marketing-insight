"""
[B·D5 → A·D7] 공시 Q&A — 사업보고서 근거로 답하고, 뉴스는 보조 근거로 따로 보여 준다

D7 추가 (qa_v4)
    - 뉴스 보조 근거: rag/news_retriever.py 로 관련 기사를 함께 찾아 '공시로 확인된 것'과 '뉴스에 나온 것'을 나눠 답한다
      공시가 우선: answerable·points 는 공시 근거만, 뉴스는 news_points 로 따로 (공시에 없으면 뉴스만 보여 줌)
    - 기획안 사례 찾기: ask_plan(기획안 글) → LLM 이 기획의 AI 활용 영역·검색어를 뽑고, 공시·뉴스에서 관련 사례를 모아 답한다

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

실행: python rag/qa_chain.py "플레이디의 AX Unit은 어떤 조직인가요?"   (기획안: python rag/qa_chain.py --plan 파일.txt)
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

PROMPT_VERSION = "qa_v4"
PLAN_PROMPT_VERSION = "plan_v1"
# v1 → v2 (테스트 15개): 1차 MOU 연혁("10개 AI 전문 기업과 손잡고")을 보고 "따라서 AI 얼라이언스는 10개"로 추론한 환각,
#   정답 청크가 검색 9~22위라 근거 5개에 못 들어감, 여러 가지를 묻는 질문에 일부만 답함
#   → 고유 이름이 근거에 없으면 답하지 않기·추론 금지, 묻는 것 모두 답하기, 검색 30개 → 근거 8개
#   + 같은 본문 합치기를 앞 300자 비교 → 본문 전체 비교로 (연혁 청크를 잘못 합쳐 '아이앤서베이'를 놓친 버그)
# v2 → v3 (대시보드 챗봇 화면): 답이 한 덩어리 긴 문단, "근거에 ~라는 내용이 있고" 같은 말투, 원문 기호(**) 섞임,
#   분기마다 같은 문장이라 출처가 5~8개 → 결론(summary)·핵심 줄(points, 줄마다 근거 1개)을 따로 받아 코드가 목록으로 조립,
#   원문 옮겨 쓰기 대신 쉬운 말로, 반복 내용은 최신 보고서 하나만 인용, 출처 번호는 1부터 다시 매김, JSON 끊기면 한 번 재시도
# v3 → v4 (A·D7, 공시 + 뉴스): 근거에 관련 뉴스 기사(NEWS_N 개)를 더하고 답을 두 칸으로 — 공시(summary·points, 공시 근거만)와
#   뉴스(news_summary·news_points, 뉴스 근거만). answerable 은 그대로 '공시에 답이 있나'라서 테스트 15개 판정 기준이 같다.
#   5개사가 아닌 회사(매드업·HSAD 등)는 공시는 거절, 뉴스에 있으면 뉴스 칸만 답한다
DEFAULT_MODEL = "gpt-4.1-mini"
SEARCH_K, CONTEXT_N, CONTEXT_CHARS = 30, 8, 1000
NEWS_K, NEWS_N, NEWS_CHARS = 12, 4, 700        # 뉴스: 12개 찾아 같은 기사(매체만 다른 재배포)를 뺀 뒤 4개
PLAN_CHARS, PLAN_CONTEXT_N, PLAN_NEWS_N = 6000, 8, 6
PLAN_SEARCH_K = 80  # 기획안: 넓게 찾은 뒤 AI 청크만 남긴다 (30개면 KT나스미디어 긴 연구개발 표가 차지해 이노션 GEO 청크가 밖으로 밀림)
# 기획 영역 중 고유 용어가 있는 것은 그 용어가 들어간 AI 청크를 검색 결과에 직접 섞는다
#   (이노션 GEO 수주 문장은 긴 '기타 참고사항' 안 한 줄이라 의미 검색으로는 AI 청크 중 37위)
#   '자동화'·'타겟팅'처럼 흔한 말은 너무 많이 걸려서 넣지 않는다
AREA_TERMS = {"AI 검색·GEO": r"GEO|생성형 엔진|AI 검색|AEO", "AI 에이전트": r"에이전트|에이전틱",
              "숏폼·인플루언서": r"인플루언서|크리에이터|숏폼", "AICC·고객상담": r"AICC|컨택센터|고객상담",
              "리테일 미디어·커머스": r"리테일 ?미디어"}
PLAN_PER_CORP = 2   # 기획안 사례: 한 회사(공시)·한 제목 앞부분(뉴스)이 근거를 독차지하지 않게 (첫 시험에서 KT나스미디어 연구개발 표가 8개 중 5개)
REFUSAL = "공시에서 확인되지 않습니다."
DART_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"
CORPS_TEXT = "광고대행사 5개사(제일기획, 이노션, 플레이디, 인크로스, KT나스미디어)"

SYSTEM = f"""당신은 {CORPS_TEXT}의 DART 정기공시(사업·반기·분기보고서)를 우선 근거로, 광고·마케팅 뉴스 기사를 보조 근거로
질문에 답하는 분석가입니다. 아래 [근거]에 적힌 내용만 사용해 한국어로 답하고 JSON 으로만 답하세요.
[근거]는 두 종류입니다: '공시' 근거(회사가 법적 책임을 지고 낸 보고서)와 '뉴스' 근거(언론 보도·회사 발표 인용).

규칙 (무엇을 답하나)
1. 공시 칸(answerable·summary·points)은 '공시' 근거만 쓴다. 공시 근거에 질문의 답이 직접 적혀 있을 때만 answerable=true.
   비슷한 주제만 있고 질문한 내용(그 서비스·제휴·조직·숫자)이 없으면 false.
   "현황", "무엇을 하나"처럼 넓게 묻는 질문은 그 회사의 관련 내용이 공시 근거에 있으면 true 로 답한다.
2. 뉴스 칸(news_summary·news_points)은 '뉴스' 근거만 쓴다. 질문과 직접 관련된 기사 내용이 있을 때만 채우고, 없으면 "" 와 [].
   공시에 이미 있는 내용을 되풀이하지 말고, 공시에 없는 사실(발표 시점·수치·고객·반응)을 보충한다.
3. 근거에 없는 사실, 배경지식, 추측을 덧붙이지 않는다. 날짜·숫자·이름은 근거에 적힌 그대로 쓴다.
4. 질문한 회사와 근거의 회사가 다르면 그 근거는 쓰지 않는다. 5개사가 아닌 회사에 대한 질문은 공시 칸을 false 로 하고,
   뉴스 근거에 그 회사 내용이 있으면 뉴스 칸만 답한다.
5. 질문이 가리키는 고유 이름(서비스·조직·제휴·특허 이름, 예: 'AI 얼라이언스')이 근거에 그대로 나오지 않으면,
   비슷한 다른 문장으로 그 이름에 대한 답을 만들지 않는다. "따라서", "~로 보인다" 같은 추론으로 잇지 않는다.
6. 질문이 여러 가지(언제·무엇·몇 개)를 물으면 근거에 있는 것은 모두 답한다. 목록을 묻는 질문(특허·서비스 등)은 근거에 나온 것을 모두.
   공시 근거에 없는 부분은 points 에 "OO: 공시에서 확인되지 않습니다" 한 줄로.

규칙 (어떻게 쓰나 — 화면에 그대로 보인다)
7. summary: 공시 기준으로 질문에 바로 답하는 결론 한 문장 (근거 번호 없이). "~라고 공시했습니다", "~하고 있습니다"처럼 끝맺는다.
   news_summary: 뉴스 기준 한 문장. "~라고 보도됐습니다", "~를 발표했습니다"처럼 보도·발표임이 드러나게 끝맺는다.
8. points(2~5줄)·news_points(0~3줄): 한 줄에 한 가지, 40자 안팎, "항목: 내용" 꼴이면 좋다. 각 줄의 근거 번호를 cite 에.
9. 원문을 길게 옮겨 쓰지 말고 쉬운 말로 줄인다. "근거에 ~라는 내용이 있고", "공시에 따르면 ~라고 기재" 같은 말은 쓰지 않는다.
   원문의 표 기호·별표·괄호 번호는 옮기지 않는다. 진행 상태·연도는 "(진행 중, 2025년)"처럼 괄호로 짧게.
10. 같은 내용이 여러 보고서·기사에 반복되면 가장 최근 것 하나만 cite 한다.
11. answerable=false 이면 summary 는 "{REFUSAL}", points 는 [].

응답 형식: {{"answerable": true|false, "summary": str, "points": [{{"text": str, "cite": int}}],
 "news_summary": str, "news_points": [{{"text": str, "cite": int}}]}}"""

PLAN_EXTRACT = """광고·마케팅 기획안(또는 AI 신사업 계획)을 읽고, 관련 사례를 찾기 위한 정보를 JSON 으로만 뽑으세요.
- title: 기획 내용을 한 문장으로 (30자 안팎)
- areas: 이 기획이 AI 를 쓰려는 마케팅 업무 1~3개. 다음 중에서 고른다:
  ["생성형 AI 콘텐츠 제작", "AI 개인화·타겟팅", "마케팅 자동화·애드테크", "AI 에이전트", "AI 검색·GEO", "숏폼·인플루언서",
   "리테일 미디어·커머스", "AICC·고객상담", "AI 교육·인력", "기타"]
- queries: 사례 검색어 3~4개. 기획에서 AI 를 쓰는 방식을 짧은 명사구로 (예: "AI 배너 광고 소재 자동 생성", "생성형 AI 검색 노출 최적화").
  회사 이름이나 '기획안' 같은 일반어는 넣지 않는다.
기획안에 AI·마케팅 활용 내용이 없으면 areas 는 [], queries 는 [].
응답 형식: {"title": str, "areas": [str], "queries": [str]}"""

PLAN_SYSTEM = f"""당신은 {CORPS_TEXT}의 DART 정기공시와 광고·마케팅 뉴스에서, 사용자가 올린 기획안과 비슷한 AI 활용 사례를 찾아 주는 분석가입니다.
아래 [기획안 요약]과 [근거]만 사용해 한국어로 답하고 JSON 으로만 답하세요. 근거에 없는 사례·사실·추측은 쓰지 않습니다.

1. points: '공시' 근거에 나온 사례 0~5줄. 기획안과 같은 업무에 AI 를 쓴 것만 고른다 (AI 가 아닌 일반 자동화·시스템 개발은 뺀다).
   한 줄 = 한 회사의 한 사례,
   "회사: 무엇을 했나 (단계·연도)" 꼴로 50자 안팎. 근거 번호를 cite 에.
2. news_points: '뉴스' 근거에 나온 사례 0~5줄. 같은 꼴. 공시 사례를 되풀이하지 않는다.
   5개사가 아닌 회사·브랜드 사례도 된다. 보도·발표 내용이므로 "~를 발표", "~로 보도"처럼 쓴다.
   기획안과 같은 상품·서비스를 실제로 사업화한 회사 사례를 먼저, 업계 동향 기사는 그다음에 쓴다.
   줄 맨 앞 이름은 사례의 주체(회사·브랜드)다. 매체 이름을 쓰지 않는다. 주체가 특정되지 않으면 "업계:"로 쓴다.
   줄 안에 근거 번호를 쓰지 않는다 (번호는 cite 에만).
3. summary: 공시 사례 중 기획안과 가장 가까운 것이 무엇인지 한 문장 (근거 번호 없이). 공시 사례가 없으면 "".
   news_summary: 뉴스 사례 중 기획안과 가장 가까운 것이 무엇인지 60자 안팎 한 문장. 뉴스 사례가 없으면 "".
4. 한 회사는 한 줄만 (사례가 여럿이면 한 줄에 묶는다). 여러 회사의 사례를 고루 보여 준다.
   summary·news_summary 에 이름을 쓴 회사는 반드시 points·news_points 에도 한 줄이 있어야 한다. 기획안과 관련 없는 근거는 쓰지 않는다.
응답 형식: {{"summary": str, "points": [{{"text": str, "cite": int}}], "news_summary": str, "news_points": [{{"text": str, "cite": int}}]}}"""


def _norm(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", s)


def _dedupe_chunks(hits, n: int):
    """본문 전체가 같은 청크만 하나로 (앞부분만 비교하면 연혁처럼 뒤에 항목이 추가된 청크를 잘못 합침)"""
    hits = hits.copy()
    hits["key"] = hits.text.map(_norm)
    hits = hits.sort_values("report_date").drop_duplicates("key")  # 같은 본문은 가장 이른 보고서 하나
    return hits.sort_index().head(n).reset_index(drop=True)  # 검색 순위대로


def _dedupe_news(hits, n: int):
    """매체만 다른 같은 보도자료는 하나로 (제목 앞 15자 또는 본문 앞 150자가 같으면 같은 기사)"""
    hits = hits.copy()
    hits["k1"], hits["k2"] = hits.title.map(lambda t: _norm(t)[:15]), hits.text.map(lambda t: _norm(t)[:150])
    hits = hits.drop_duplicates("k1").drop_duplicates("k2")
    return hits.head(n).reset_index(drop=True)


def _one_per_name(raw) -> list:
    """기획안 사례: 줄 맨 앞 '이름:' 이 같은 줄은 처음 것만 (LLM 이 '한 회사 한 줄' 규칙을 가끔 어김)"""
    seen, out = set(), []
    for pt in raw or []:
        name = str(pt.get("text", "")).split(":")[0].strip() if isinstance(pt, dict) else ""
        if name and name in seen:
            continue
        seen.add(name)
        out.append(pt)
    return out


def _points(raw, lo: int, hi: int) -> list[tuple[str, int]]:
    """LLM 의 [{text, cite}] → [(text, cite)] — cite 가 lo~hi 범위(그 칸의 근거 종류)인 줄만"""
    out = []
    for pt in raw or []:
        c = pt.get("cite") if isinstance(pt, dict) else None
        c = c[0] if isinstance(c, list) and c else c
        if isinstance(pt, dict) and pt.get("text") and isinstance(c, int) and lo <= c <= hi:
            text = re.sub(r"\s*\(\d{1,2}(\s*,\s*\d{1,2})*\)", "", pt["text"].replace("**", "")).strip()  # 줄 안 근거 번호 '(1)' '(4,6)'
            out.append((text, c))
    return out


class QA:
    def __init__(self, retriever: Retriever | None = None, news: bool = True):
        from dotenv import load_dotenv
        from openai import OpenAI
        load_dotenv(settings.ROOT / ".env")
        self.retriever = retriever or Retriever()
        self.news = None
        if news:
            from rag.news_retriever import NewsRetriever
            self.news = NewsRetriever()
        self.client = OpenAI(default_headers={"Accept-Encoding": "gzip, deflate"})
        self.model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL)

    def context(self, question: str):
        return _dedupe_chunks(self.retriever.search(question, k=SEARCH_K), CONTEXT_N)

    def news_context(self, question: str):
        if self.news is None:
            return None
        return _dedupe_news(self.news.search(question, k=NEWS_K), NEWS_N)

    # ---------- 공통: 근거 글 · LLM 호출 · 답 조립 ----------
    @staticmethod
    def _blocks(ctx, nctx) -> str:
        blocks = [f"[{i + 1}] 공시 | {r.corp_name} | {r.report_name} (접수 {r.report_date}) | {r.section}\n"
                  f"{r.text[:CONTEXT_CHARS].replace('**', '')}" for i, r in enumerate(ctx.itertuples())]
        if nctx is not None:
            blocks += [f"[{len(ctx) + i + 1}] 뉴스 | {r.media} ({r.date}) | {r.title}\n{r.text[:NEWS_CHARS]}"
                       for i, r in enumerate(nctx.itertuples())]
        return "\n\n".join(blocks)

    def _llm(self, system: str, user: str, max_tokens: int = 1100) -> dict:
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        for _ in range(2):  # JSON 이 중간에 끊기면 한 번 더
            res = self.client.chat.completions.create(model=self.model, temperature=0, max_tokens=max_tokens,
                                                      response_format={"type": "json_object"}, messages=msgs)
            try:
                return json.loads(res.choices[0].message.content)
            except json.JSONDecodeError:
                continue
        return {}

    @staticmethod
    def _assemble(out: dict, ctx, nctx) -> dict:
        """LLM 답 → 공시 칸(answer·sources)과 뉴스 칸(news_answer·news_sources). 출처 번호는 칸마다 1부터"""
        nd = len(ctx)
        nn = 0 if nctx is None else len(nctx)
        points = _points(out.get("points"), 1, nd)
        npoints = _points(out.get("news_points"), nd + 1, nd + nn)
        order = list(dict.fromkeys(c for _, c in points))
        new = {c: i + 1 for i, c in enumerate(order)}
        norder = list(dict.fromkeys(c for _, c in npoints))
        nnew = {c: i + 1 for i, c in enumerate(norder)}
        answerable = bool(points) and out.get("answerable", True) is not False  # 인용 없는 답은 거절로
        answer = REFUSAL
        if answerable:
            sm = (out.get("summary") or "").replace("**", "").strip()
            answer = (sm + "\n\n" if sm else "") + "\n".join(f"- {t} [{new[c]}]" for t, c in points)
        sources = [] if not answerable else [{
            "n": new[c], "corp": ctx.corp_name[c - 1], "report": ctx.report_name[c - 1], "report_date": ctx.report_date[c - 1],
            "section": ctx.section[c - 1], "ai_level": None if ctx.ai_level.isna()[c - 1] else int(ctx.ai_level[c - 1]),
            "url": DART_URL.format(ctx.rcept_no[c - 1]), "chunk_id": ctx.chunk_id[c - 1]} for c in order]
        news_answer = None
        if npoints:
            ns = (out.get("news_summary") or "").replace("**", "").strip()
            news_answer = (ns + "\n\n" if ns else "") + "\n".join(f"- {t} [뉴스 {nnew[c]}]" for t, c in npoints)
        news_sources = [{"n": nnew[c], "media": nctx.media[c - nd - 1], "date": nctx.date[c - nd - 1],
                         "title": nctx.title[c - nd - 1], "url": nctx.url[c - nd - 1], "doc_id": nctx.doc_id[c - nd - 1]}
                        for c in norder]
        return {"answerable": answerable, "answer": answer, "sources": sources,
                "news_answer": news_answer, "news_sources": news_sources}

    # ---------- 질문 ----------
    def ask(self, question: str) -> dict:
        ctx, nctx = self.context(question), self.news_context(question)
        out = self._llm(SYSTEM, f"[질문]\n{question}\n\n[근거]\n" + self._blocks(ctx, nctx))
        r = self._assemble(out, ctx, nctx)
        return {"question": question, **r, "context_chunk_ids": ctx.chunk_id.tolist(),
                "context_news_ids": [] if nctx is None else nctx.doc_id.tolist(),
                "corp_filter": ctx.corp_filter.iloc[0] if len(ctx) else None,
                "model": self.model, "prompt_version": PROMPT_VERSION}

    # ---------- 기획안 → 관련 사례 ----------
    def read_plan(self, plan: str) -> dict:
        out = self._llm(PLAN_EXTRACT, plan[:PLAN_CHARS], max_tokens=400)
        queries = [q.strip() for q in out.get("queries") or [] if isinstance(q, str) and q.strip()][:4]
        areas = [a for a in out.get("areas") or [] if isinstance(a, str)][:3]
        return {"title": str(out.get("title") or "").strip(), "areas": areas, "queries": queries}

    def ask_plan(self, plan: str) -> dict:
        """기획안 글 → 기획 요약(plan) + 공시·뉴스 사례. 검색어마다 찾은 결과를 순위대로 번갈아 합친다"""
        p = self.read_plan(plan)
        base = {"question": p["title"] or plan[:40], "plan": p, "model": self.model, "prompt_version": PLAN_PROMPT_VERSION}
        if not p["queries"]:
            return {**base, "answerable": False, "answer": "기획안에서 AI 활용 내용을 찾지 못했습니다.", "sources": [],
                    "news_answer": None, "news_sources": []}
        import pandas as pd
        hits = [self.retriever.search(q, k=PLAN_SEARCH_K, corp=None) for q in p["queries"]]
        pats = [AREA_TERMS[a] for a in p["areas"] if a in AREA_TERMS]
        if pats:
            m = self.retriever.meta
            ah = m[m.text.str.contains("|".join(pats), regex=True) & (m.ai_level.fillna(-1) >= 1)]
            hits.insert(0, ah.sort_values("report_date", ascending=False).head(PLAN_SEARCH_K).reset_index(drop=True))
        merged = pd.concat([h.assign(_r=h.index) for h in hits]).sort_values("_r", kind="stable")
        merged = merged[merged.ai_level.fillna(-1) >= 1]  # 회사가 AI 를 계획·도입·개발한 청크만 (L0 업계 전망·AI 아닌 자동화 제외)
        merged = _dedupe_chunks(merged.drop_duplicates("chunk_id").reset_index(drop=True), len(merged))
        ctx = merged[merged.groupby("corp_name").cumcount() < PLAN_PER_CORP].head(PLAN_CONTEXT_N).reset_index(drop=True)
        nctx = None
        if self.news is not None:
            nh = [self.news.search(q, k=NEWS_K, corp=None) for q in p["queries"]]
            if pats:  # 공시와 같이: 제목에 영역 고유 용어가 있는 기사를 먼저 (최신순)
                nmeta = self.news.meta
                nh.insert(0, nmeta[nmeta.title.str.contains("|".join(pats), regex=True)]
                          .sort_values("date", ascending=False).head(NEWS_K).reset_index(drop=True))
            nm = pd.concat([h.assign(_r=h.index) for h in nh]).sort_values("_r", kind="stable")
            nm = _dedupe_news(nm.drop_duplicates("doc_id").reset_index(drop=True), len(nm))
            # 같은 회사 기사 몰림 방지: 제목 첫 단어(대개 회사·서비스 이름)당 PLAN_PER_CORP 개까지
            head = nm.title.str.replace(r"^\[[^\]]*\]\s*", "", regex=True).str.split(r"[\s,·…]", regex=True).str[0]
            nctx = nm[nm.groupby(head).cumcount() < PLAN_PER_CORP].head(PLAN_NEWS_N).reset_index(drop=True)
        summary = f"제목: {p['title']}\nAI 활용 영역: {', '.join(p['areas'])}\n검색어: {', '.join(p['queries'])}"
        out = self._llm(PLAN_SYSTEM, f"[기획안 요약]\n{summary}\n\n[기획안 원문 일부]\n{plan[:1500]}\n\n[근거]\n"
                        + self._blocks(ctx, nctx), max_tokens=1300)
        out["answerable"] = True
        out["points"], out["news_points"] = _one_per_name(out.get("points")), _one_per_name(out.get("news_points"))
        for key in ("summary", "news_summary"):  # 요약은 한 문장만, 줄 안 근거 번호 제거
            t = re.sub(r"\s*\(\d{1,2}(\s*,\s*\d{1,2})*\)", "", str(out.get(key) or ""))
            out[key] = re.split(r"(?<=[.다함])\s", t.strip(), maxsplit=1)[0]
        r = self._assemble(out, ctx, nctx)
        if not r["answerable"]:
            r["answer"] = "공시에서 비슷한 사례를 찾지 못했습니다."
        return {**base, **r, "context_chunk_ids": ctx.chunk_id.tolist(),
                "context_news_ids": [] if nctx is None else nctx.doc_id.tolist()}


def format_answer(r: dict) -> str:
    lines = []
    if r.get("plan"):
        p = r["plan"]
        lines.append(f"[기획안] {p['title']} · 영역: {', '.join(p['areas'])} · 검색어: {', '.join(p['queries'])}\n")
    lines.append(r["answer"])
    for s in r["sources"]:
        lv = "" if s["ai_level"] is None else f" · AI 단계 L{s['ai_level']}"
        lines.append(f"  [{s['n']}] {s['corp']} {s['report']} (접수 {s['report_date']}) · {s['section'].split('>')[-1].strip()}{lv}\n      {s['url']}")
    if r.get("news_answer"):
        lines.append("\n[뉴스] " + r["news_answer"])
        for s in r["news_sources"]:
            lines.append(f"  [뉴스 {s['n']}] {s['media']} ({s['date']}) {s['title']}\n      {s['url']}")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--plan":
        print(format_answer(QA().ask_plan(Path(sys.argv[2]).read_text(encoding="utf-8"))))
    else:
        q = " ".join(sys.argv[1:]) or "플레이디의 AX Unit은 어떤 조직인가요?"
        print(format_answer(QA().ask(q)))
