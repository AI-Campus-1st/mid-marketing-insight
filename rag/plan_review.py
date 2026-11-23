"""
[A·D7] 기획안 검토 — 기획안(AI 신사업 계획·마케팅 기획)을 넣으면 공시·뉴스에서 비슷한 AI 활용 사례를 찾아 준다

챗봇(rag/qa_chain.py)과 분리한 이유 (PR #29 리뷰): 한 입력창에서 기획안과 질문을 같이 받으니
  기획안을 올린 뒤의 회사 질문까지 기획안 근거로 답해 공시 답을 놓쳤다 (인크로스 스텔라이즈 출시 → '공시에 없음').
  챗봇은 공시 질문만, 기획안은 이 화면에서 한 번에 리포트로 — 이어지는 질문은 받지 않는다 (예측·환각 위험, decisions.md)

흐름
    1. 기획안 읽기: LLM 이 제목·AI 활용 영역(정해진 목록)·검색어 3~4개를 뽑는다 (PLAN_EXTRACT)
    2. 공시 사례: 검색어마다 넓게(80개) 찾고, 고유 용어 영역(GEO 등)은 그 용어가 든 AI 청크를 먼저 섞는다
       → AI 단계 L1 이상만, 같은 본문 하나로, 한 회사 PLAN_PER_CORP 개까지, PLAN_CONTEXT_N 개
    3. 뉴스 사례: 제공 뉴스 중 관련 기사(rag/news_retriever.py)에서 같은 방식 (제목 첫 단어당 PLAN_PER_CORP 개)
    4. 답: LLM 이 사례 줄을 쓰고, 같은 영역 / 인접 영역은 코드가 먼저 정한다 (조직·제휴 → 인접, 고유 용어가 근거에 있으면 같은 영역)
    5. 문단 연결 (plan_v4): 기획안을 문단으로 나눠(split_paras) 번호를 붙이고, 사례 줄마다 기획안의 어느 문단과 맞는지(para)를 받는다
       → 화면은 왼쪽에 기획안 문서, 오른쪽에 그 문단의 사례 카드 (dashboard/tabs/plan_review.py)

실행: python rag/plan_review.py 기획안.txt
필요: .env 의 OPENAI_API_KEY (선택: OPENAI_MODEL, 기본 gpt-4.1-mini)
"""
import json
import os
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402
from rag.retriever import Retriever  # noqa: E402

PROMPT_VERSION = "plan_v6"
# v1 (A·D7): 기획안 → 공시·뉴스 사례 한 목록
# v1 → v2 (B 리뷰 PR #29): GEO 기획안에 공시 4개사가 한 목록으로 나와 '4곳이 GEO 를 한다'로 읽힘 → 같은 영역 / 인접 영역으로 나눔
# v2 → v3 (B 리뷰 PR #29): '한 회사 한 줄' 이라 플레이디 '스파이더'(GEO)와 '크레파스'(광고 소재)가 같은 영역 한 줄에 묶임
#   → 한 회사는 '영역마다' 한 줄 (같은 영역 줄과 인접 영역 줄을 따로), 같은 영역 줄에는 기획과 같은 일 사례만
# v3 → v4 (화면 개편): 리포트가 목록뿐이라 기획안의 어느 부분에 대한 사례인지 안 보임 → 문단 번호를 붙여 넣고
#   검색어·사례 줄마다 para(문단 번호)를 받는다. 번호가 없거나 범위 밖이면 0(기획 전체)
# v4 → v5 (화면 피드백): 사례만 나열돼 '그래서 우리 기획은?'이 안 보임 → 같은 호출에서 결론(conclusion) 2~3문장
#   (예측·권고 없이 '이미 한 곳 / 보도만 / 비슷한 기술' 위치만). 예측 표현이 든 문장·근거와 겹침이 적은 문장은 코드가 지우고,
#   사례가 하나도 안 붙은 실행 방안 문단(검색어를 뽑은 문단)은 코드가 gaps 로 따로 계산한다
# v5 → v6 (화면 피드백): 결론이 '어느 회사가 했다'만 말하고 기획의 어느 부분이 그 회사의 어떤 사업과 같은지 안 보임
#   → 사례 줄마다 keyword(사업 이름 짧게), 사례가 붙은 문단마다 para_labels(기획 부분 이름 짧게)를 받고,
#   '기획 부분 ↔ 회사 · 사업 키워드' 짝 목록과 결론 문장은 이미 문단과 이어 둔 사례 줄로 화면 코드가 만든다.
#   LLM 결론 문장(v5)은 뺐다: 근거 대조를 통과해도 '플레이디·이노션 등도 뉴스에서'처럼 공시 사례를 뉴스로 바꿔 씀 (출처 혼동은 단어 겹침으로 못 잡음)
DEFAULT_MODEL = "gpt-4.1-mini"
CONTEXT_CHARS, NEWS_CHARS = 1000, 700
NEWS_K = 12
PLAN_CHARS, PLAN_CONTEXT_N, PLAN_NEWS_N = 6000, 8, 6
PLAN_SEARCH_K = 80  # 넓게 찾은 뒤 AI 청크만 남긴다 (30개면 KT나스미디어 긴 연구개발 표가 차지해 이노션 GEO 청크가 밖으로 밀림)
PLAN_PER_CORP = 2   # 한 회사(공시)·한 제목 앞부분(뉴스)이 근거를 독차지하지 않게 (첫 시험에서 KT나스미디어 연구개발 표가 8개 중 5개)
# 기획 영역 중 고유 용어가 있는 것은 그 용어가 들어간 AI 청크를 검색 결과에 직접 섞는다
#   (이노션 GEO 수주 문장은 긴 '기타 참고사항' 안 한 줄이라 의미 검색으로는 AI 청크 중 37위)
#   '자동화'·'타겟팅'처럼 흔한 말은 너무 많이 걸려서 넣지 않는다
AREA_TERMS = {"AI 검색·GEO": r"GEO|생성형 엔진|AI 검색|AEO", "AI 에이전트": r"에이전트|에이전틱",
              "숏폼·인플루언서": r"인플루언서|크리에이터|숏폼", "AICC·고객상담": r"AICC|컨택센터|고객상담",
              "리테일 미디어·커머스": r"리테일 ?미디어"}
# 조직·제휴·투자 사례는 '일 자체'가 아니라서 인접 영역 (프롬프트 규칙을 LLM 이 자주 어겨 코드로: 이노션 'AI 전담 TF 신설'이 same 으로 나옴)
ORG_TERMS = r"TF|조직|팀\s?(신설|구성)|신설|얼라이언스|MOU|업무협약|제휴|투자|인수"
DART_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"
CORPS_TEXT = "광고대행사 5개사(제일기획, 이노션, 플레이디, 인크로스, KT나스미디어)"
NO_PLAN = "기획안에서 AI 활용 내용을 찾지 못했습니다."
NO_CASE = "공시에서 비슷한 사례를 찾지 못했습니다."

PLAN_EXTRACT = """광고·마케팅 기획안(또는 AI 신사업 계획)을 읽고, 관련 사례를 찾기 위한 정보를 JSON 으로만 뽑으세요.
- title: 기획 내용을 한 문장으로 (30자 안팎)
- areas: 이 기획이 AI 를 쓰려는 마케팅 업무 1~3개. 다음 중에서 고른다:
  ["생성형 AI 콘텐츠 제작", "AI 개인화·타겟팅", "마케팅 자동화·애드테크", "AI 에이전트", "AI 검색·GEO", "숏폼·인플루언서",
   "리테일 미디어·커머스", "AICC·고객상담", "AI 교육·인력", "기타"]
- queries: 사례 검색어 3~4개. 기획에서 AI 를 쓰는 방식을 짧은 명사구로 (예: "AI 배너 광고 소재 자동 생성", "생성형 AI 검색 노출 최적화").
  회사 이름이나 '기획안' 같은 일반어는 넣지 않는다. 기획안은 [P번호] 문단으로 나뉘어 있다 — 검색어마다 그 방식이 적힌 문단 번호를 para 에.
  실행 방안이 여럿이면 방안마다 하나씩 (배경·목표·리스크 문단이 아니라 AI 를 쓰는 방식이 적힌 문단에서).
기획안에 AI·마케팅 활용 내용이 없으면 areas 는 [], queries 는 [].
응답 형식: {"title": str, "areas": [str], "queries": [{"query": str, "para": int}]}"""

PLAN_SYSTEM = f"""당신은 {CORPS_TEXT}의 DART 정기공시와 광고·마케팅 뉴스에서, 사용자가 올린 기획안과 비슷한 AI 활용 사례를 찾아 주는 분석가입니다.
아래 [기획안 요약]·[기획안 문단]과 [근거]만 사용해 한국어로 답하고 JSON 으로만 답하세요. 근거에 없는 사례·사실·추측은 쓰지 않습니다.
[근거]는 두 종류입니다: '공시' 근거(회사가 법적 책임을 지고 낸 보고서)와 '뉴스' 근거(언론 보도·회사 발표 인용).

1. points: '공시' 근거에 나온 사례 0~5줄. 회사가 AI 를 쓴 사례만 (AI 가 아닌 일반 자동화·시스템 개발은 뺀다).
   한 줄 = 한 회사의 한 사례, "회사: 무엇을 했나 (단계·연도)" 꼴로 50자 안팎. 근거 번호를 cite 에.
2. news_points: '뉴스' 근거에 나온 사례 0~5줄. 같은 꼴. 공시 사례를 되풀이하지 않는다.
   5개사가 아닌 회사·브랜드 사례도 된다. 보도·발표 내용이므로 "~를 발표", "~로 보도"처럼 쓴다.
   줄 맨 앞 이름은 사례의 주체(회사·브랜드)다. 매체 이름을 쓰지 않는다. 주체가 특정되지 않으면 "업계:"로 쓴다.
3. 줄마다 match 를 단다.
   - "same": 기획안의 실행 방안 중 하나와 같은 일(같은 상품·서비스, 같은 마케팅 업무)에 AI 를 쓴 사례.
     예: GEO 기획안 ↔ GEO 솔루션 출시 / 광고 소재 자동 제작 기획안 ↔ AI 로 광고 이미지·영상 소재 생성
   - "adjacent": 비슷한 기술이지만 다른 일에 쓴 사례, 또는 조직 신설·제휴·투자처럼 일 자체가 아닌 사례.
     예: GEO 기획안 ↔ 검색광고 문구 자동 생성 / 소재 제작 기획안 ↔ AI 전담 조직 신설
   판단은 사례가 '무슨 일을 했나'로 한다 (AI 라는 공통점만으로 same 이 아니다). same 을 먼저, adjacent 는 칸마다 2줄까지.
4. 한 회사는 match 마다 한 줄만. 같은 회사에 same 사례와 adjacent 사례가 함께 있으면 두 줄로 나눠 쓴다
   (예: "플레이디: GEO 컨설팅 '스파이더' 출시" same 한 줄 + "플레이디: 광고 소재 제작 '크레파스' 출시" adjacent 한 줄).
   same 줄에는 기획과 같은 일 사례만 쓰고, 그 회사의 다른 사례를 묶어 넣지 않는다. 여러 회사의 사례를 고루 보여 준다.
5. summary: match=same 인 공시 사례 중 기획안과 가장 가까운 것을 한 문장 (근거 번호 없이). same 공시 사례가 없으면 "".
   news_summary: match=same 인 뉴스 사례 중 가장 가까운 것을 60자 안팎 한 문장. same 뉴스 사례가 없으면 "".
   summary·news_summary 는 화면에 그대로 보이므로 "~했습니다", "~하고 있습니다"처럼 "-습니다"로 끝맺는다.
   summary·news_summary 에 이름을 쓴 회사는 반드시 points·news_points 에도 한 줄이 있어야 한다. 기획안과 관련 없는 근거는 쓰지 않는다.
   줄 안에 근거 번호를 쓰지 않는다 (번호는 cite 에만).
6. 줄마다 para 에 그 사례와 가장 맞는 [기획안 문단] 번호(P 뒤 숫자)를 단다. 사례가 한 일과 같은·비슷한 일이 적힌 문단
   (보통 실행 방안·서비스 개요 문단). 특정 문단과 맞지 않고 기획 전체와 관련되면 0.
7. 줄마다 keyword: 그 회사가 한 사업을 15자 안팎 명사구로, 줄에 적힌 말 그대로 (서비스 이름이 있으면 넣는다. 예: "GEO 솔루션 '스파이더'", "영상 광고 소재 생성 서비스").
8. para_labels: 사례가 붙은 문단마다 기획안의 그 부분을 12자 안팎 명사구로 ({{"3": "GEO 컨설팅 상품"}} 꼴, 기획안 문단에 적힌 말로).
응답 형식: {{"para_labels": {{"문단 번호": str}}, "summary": str,
 "points": [{{"text": str, "cite": int, "match": "same"|"adjacent", "para": int, "keyword": str}}],
 "news_summary": str, "news_points": [{{"text": str, "cite": int, "match": "same"|"adjacent", "para": int, "keyword": str}}]}}"""

CITE_IN_TEXT = r"\s*\(\d{1,2}(\s*,\s*\d{1,2})*\)"  # 줄 안 근거 번호 '(1)' '(4,6)'


MARKER = re.compile(r"^\s*(■|□|▶|●|◆|#{1,6}\s|\[|\d{1,2}[.)]\s*\S|\(\d{1,2}\)|[가-하][.)]\s)")   # 제목·번호 줄
BULLET = re.compile(r"^\s*[-•·*○◦▪]\s")
LABEL = re.compile(r"^\s*[^\s:：\-•·*][^:：]{0,11}[:：]\s*\S")   # '배경: …'·'필요 역량: …' 처럼 짧은 이름표로 시작하는 줄


def split_paras(text: str, max_paras: int = 60) -> list[str]:
    """기획안 글 → 문단 목록. 빈 줄, 제목·번호 줄(■·1.·(1)·[…]), 이름표 줄('배경: …')에서 새 문단을 시작하고,
    글머리표(-) 줄과 표시 없는 줄은 앞 문단에 붙인다 (PDF 는 빈 줄이 없고 긴 줄이 둘로 끊김)"""
    paras, cur = [], []
    for raw in text.replace("\r", "").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            if cur:
                paras.append("\n".join(cur))
            cur = []
            continue
        if cur and (MARKER.match(line) or LABEL.match(line)):
            paras.append("\n".join(cur))
            cur = []
        cur.append(line)
    if cur:
        paras.append("\n".join(cur))
    if len(paras) > max_paras:  # 너무 잘게 나뉘면 뒤쪽을 합친다
        paras = paras[:max_paras - 1] + ["\n".join(paras[max_paras - 1:])]
    return paras


def _para(pt, n: int) -> int:
    v = pt.get("para") if isinstance(pt, dict) else None
    v = v[0] if isinstance(v, list) and v else v
    if isinstance(v, str):
        v = re.sub(r"\D", "", v)
        v = int(v) if v else 0
    return v if isinstance(v, int) and 1 <= v <= n else 0


def _keyword(kw, line: str, cut: int = 24) -> str:
    """LLM 의 짧은 이름이 줄(또는 문단)에 적힌 말과 충분히 겹치면 그대로, 아니면 줄 앞부분을 잘라 쓴다 (지어낸 이름 방지)"""
    from rag.retriever import _grams
    kw, line = str(kw or "").strip(), str(line).strip()
    g = set(_grams(kw))
    if kw and g and len(g & set(_grams(line))) / len(g) >= 0.6:
        return kw
    line = re.sub(r"\s*\(\d{4}[^)]*\)\s*$", "", line.splitlines()[0] if line else "")
    return line if len(line) <= cut else line[:cut].rstrip() + "…"


def _norm(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", s)


def _dedupe_chunks(hits: pd.DataFrame) -> pd.DataFrame:
    """본문 전체가 같은 청크만 하나로 (분기마다 같은 문장 반복 — 가장 이른 보고서), 검색 순위대로"""
    hits = hits.copy()
    hits["key"] = hits.text.map(_norm)
    return hits.sort_values("report_date").drop_duplicates("key").sort_index().reset_index(drop=True)


def _dedupe_news(hits: pd.DataFrame) -> pd.DataFrame:
    """매체만 다른 같은 보도자료는 하나로 (제목 앞 15자 또는 본문 앞 150자가 같으면 같은 기사)"""
    hits = hits.copy()
    hits["k1"], hits["k2"] = hits.title.map(lambda t: _norm(t)[:15]), hits.text.map(lambda t: _norm(t)[:150])
    return hits.drop_duplicates("k1").drop_duplicates("k2").reset_index(drop=True)


def _cite(pt) -> int | None:
    c = pt.get("cite") if isinstance(pt, dict) else None
    c = c[0] if isinstance(c, list) and c else c
    return c if isinstance(c, int) else None


class PlanReview:
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

    def _llm(self, system: str, user: str, max_tokens: int) -> dict:
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
    def numbered(paras: list[str]) -> str:
        return "\n".join(f"[P{i + 1}] {t}" for i, t in enumerate(paras))

    def read_plan(self, paras: list[str]) -> dict:
        out = self._llm(PLAN_EXTRACT, self.numbered(paras)[:PLAN_CHARS], max_tokens=500)
        queries, qparas = [], []
        for q in out.get("queries") or []:
            text = q.get("query") if isinstance(q, dict) else q
            if isinstance(text, str) and text.strip():
                queries.append(text.strip())
                qparas.append(_para(q, len(paras)))
        areas = [a for a in out.get("areas") or [] if isinstance(a, str)][:3]
        return {"title": str(out.get("title") or "").strip(), "areas": areas, "queries": queries[:4], "query_paras": qparas[:4]}

    # ---------- 근거 모으기 ----------
    def _disclosure(self, p: dict, pats: list[str]) -> pd.DataFrame:
        """검색어마다 찾은 결과를 순위대로 번갈아 합친다. 고유 용어 영역은 그 용어가 든 AI 청크(최신순)를 맨 앞 목록으로"""
        hits = [self.retriever.search(q, k=PLAN_SEARCH_K, corp=None) for q in p["queries"]]
        if pats:
            m = self.retriever.meta
            ah = m[m.text.str.contains("|".join(pats), regex=True) & (m.ai_level.fillna(-1) >= 1)]
            hits.insert(0, ah.sort_values("report_date", ascending=False).head(PLAN_SEARCH_K).reset_index(drop=True))
        merged = pd.concat([h.assign(_r=h.index) for h in hits]).sort_values("_r", kind="stable")
        merged = merged[merged.ai_level.fillna(-1) >= 1]  # 회사가 AI 를 계획·도입·개발한 청크만 (L0 업계 전망·AI 아닌 자동화 제외)
        merged = _dedupe_chunks(merged.drop_duplicates("chunk_id").reset_index(drop=True))
        return merged[merged.groupby("corp_name").cumcount() < PLAN_PER_CORP].head(PLAN_CONTEXT_N).reset_index(drop=True)

    def _news(self, p: dict, pats: list[str]) -> pd.DataFrame | None:
        if self.news is None:
            return None
        nh = [self.news.search(q, k=NEWS_K, corp=None) for q in p["queries"]]
        if pats:  # 공시와 같이: 제목에 영역 고유 용어가 있는 기사를 먼저 (최신순)
            meta = self.news.meta
            nh.insert(0, meta[meta.title.str.contains("|".join(pats), regex=True)]
                      .sort_values("date", ascending=False).head(NEWS_K).reset_index(drop=True))
        nm = pd.concat([h.assign(_r=h.index) for h in nh]).sort_values("_r", kind="stable")
        nm = _dedupe_news(nm.drop_duplicates("doc_id").reset_index(drop=True))
        # 같은 회사 기사 몰림 방지: 제목 첫 단어(대개 회사·서비스 이름)당 PLAN_PER_CORP 개까지
        head = nm.title.str.replace(r"^\[[^\]]*\]\s*", "", regex=True).str.split(r"[\s,·…]", regex=True).str[0]
        return nm[nm.groupby(head).cumcount() < PLAN_PER_CORP].head(PLAN_NEWS_N).reset_index(drop=True)

    @staticmethod
    def _blocks(ctx, nctx) -> str:
        blocks = [f"[{i + 1}] 공시 | {r.corp_name} | {r.report_name} (접수 {r.report_date}) | {r.section}\n"
                  f"{r.text[:CONTEXT_CHARS].replace('**', '')}" for i, r in enumerate(ctx.itertuples())]
        if nctx is not None:
            blocks += [f"[{len(ctx) + i + 1}] 뉴스 | {r.media} ({r.date}) | {r.title}\n{r.text[:NEWS_CHARS]}"
                       for i, r in enumerate(nctx.itertuples())]
        return "\n\n".join(blocks)

    # ---------- 기획안 → 사례 ----------
    def review(self, plan: str) -> dict:
        """기획안 글 → {plan, disclosure: {summary, same, adjacent, sources}, news: {...}}
        same·adjacent 는 [(줄, 출처 번호, 문단 번호)], 출처 번호는 칸마다 1부터 (same 먼저), 문단 번호는 paras 의 1부터 (0 = 기획 전체)"""
        paras = split_paras(plan[:PLAN_CHARS])
        p = self.read_plan(paras)
        base = {"plan": p, "paras": paras, "model": self.model, "prompt_version": PROMPT_VERSION}
        empty = {"summary": "", "same": [], "adjacent": [], "sources": []}
        if not p["queries"]:
            return {**base, "ok": False, "message": NO_PLAN, "disclosure": empty, "news": empty}
        pats = [AREA_TERMS[a] for a in p["areas"] if a in AREA_TERMS]
        ctx, nctx = self._disclosure(p, pats), self._news(p, pats)
        qs = ", ".join(f"{q} (P{n})" if n else q for q, n in zip(p["queries"], p["query_paras"]))
        head = f"제목: {p['title']}\nAI 활용 영역: {', '.join(p['areas'])}\n검색어: {qs}"
        out = self._llm(PLAN_SYSTEM, f"[기획안 요약]\n{head}\n\n[기획안 문단]\n{self.numbered(paras)}\n\n[근거]\n"
                        + self._blocks(ctx, nctx), max_tokens=1600)
        nd, nn = len(ctx), 0 if nctx is None else len(nctx)
        # 같은 영역 / 인접 영역 (B 리뷰 PR #29): 고유 용어가 있는 영역(AREA_TERMS)은 코드가 정한다 — 인용한 근거에 그 용어가 있으면 same.
        #   기획 영역이 모두 고유 용어 영역이면(예: GEO 하나) 용어가 없는 근거는 adjacent. 그 밖에는 LLM 의 match
        all_term = bool(p["areas"]) and len(pats) == len(p["areas"])
        pat = re.compile("|".join(pats)) if pats else None
        medias = set() if nctx is None else set(nctx.media)

        kws: dict[int, str] = {}  # 근거 번호 → 사업 키워드 (줄과 겹침이 충분할 때만)

        def _col(key: str, lo: int, hi: int, text_of) -> list[tuple[str, int, str, int]]:
            rows, seen = [], set()
            for pt in out.get(key) or []:
                c = _cite(pt)
                if c is None or not lo <= c <= hi or not pt.get("text"):  # 그 칸의 근거 종류가 아닌 번호는 버림
                    continue
                text = re.sub(CITE_IN_TEXT, "", str(pt["text"]).replace("**", "")).strip()
                name, sep, rest = text.partition(":")
                if sep and name.strip() in medias:  # 줄 맨 앞에 매체 이름을 쓴 경우(동향 기사) → '업계'
                    text, name = "업계:" + rest, "업계"
                if re.search(ORG_TERMS, text):  # 먼저: 청크(1,000자)에 영역 용어가 섞여 있어도 줄이 조직·제휴 이야기면 인접
                    match = "adjacent"
                elif pat is not None and pat.search(text_of(c)):
                    match = "same"
                elif all_term:
                    match = "adjacent"
                else:
                    match = "adjacent" if pt.get("match") == "adjacent" else "same"
                if (name.strip(), match) in seen:  # 한 회사는 영역(같은/인접)마다 한 줄 (LLM 이 규칙을 가끔 어김)
                    continue
                seen.add((name.strip(), match))
                rows.append((text, c, match, _para(pt, len(paras))))
                kws[c] = _keyword(pt.get("keyword"), rest if sep else text)
            return sorted(rows, key=lambda x: x[2] == "adjacent")  # same 먼저 (안정 정렬)

        def _summary(key: str, rows) -> str:
            t = re.sub(CITE_IN_TEXT, "", str(out.get(key) or "")).replace("**", "").strip()
            t = re.split(r"(?<=[.다함])\s", t, maxsplit=1)[0]  # 한 문장만
            if any(r[2] == "same" for r in rows):
                return t
            # same 이 없으면 정해진 문장 (LLM 요약이 인접 사례를 같은 영역처럼 쓰지 않게)
            src = "공시" if key == "summary" else "뉴스"
            return f"{src}에는 기획과 같은 영역({', '.join(p['areas']) or '기획'})의 사례가 없고, 인접 영역 사례만 있습니다." if rows else ""

        def _pack(rows, summary, source_of) -> dict:
            order = list(dict.fromkeys(r[1] for r in rows))
            new = {c: i + 1 for i, c in enumerate(order)}
            return {"summary": summary,
                    "same": [(t, new[c], pa) for t, c, m, pa in rows if m == "same"],
                    "adjacent": [(t, new[c], pa) for t, c, m, pa in rows if m == "adjacent"],
                    "sources": [{"n": new[c], **source_of(c)} for c in order],
                    "keywords": {new[c]: kws.get(c, "") for c in order}}

        drows = _col("points", 1, nd, lambda c: ctx.text[c - 1])
        nrows = _col("news_points", nd + 1, nd + nn, lambda c: nctx.title[c - nd - 1] + " " + nctx.text[c - nd - 1])
        disc = _pack(drows, _summary("summary", drows), lambda c: {
            "corp": ctx.corp_name[c - 1], "report": ctx.report_name[c - 1], "report_date": ctx.report_date[c - 1],
            "section": ctx.section[c - 1], "ai_level": None if pd.isna(ctx.ai_level[c - 1]) else int(ctx.ai_level[c - 1]),
            "url": DART_URL.format(ctx.rcept_no[c - 1]), "chunk_id": ctx.chunk_id[c - 1]})
        news = _pack(nrows, _summary("news_summary", nrows), lambda c: {
            "media": nctx.media[c - nd - 1], "date": nctx.date[c - nd - 1], "title": nctx.title[c - nd - 1],
            "url": nctx.url[c - nd - 1], "doc_id": nctx.doc_id[c - nd - 1]})
        hit = {pa for *_, pa in drows + nrows}
        gaps = sorted({n for n in p["query_paras"] if n and n not in hit})  # AI 를 쓰는 방식이 적혔는데 사례가 하나도 안 붙은 문단
        labels = {}
        for k, v in (out.get("para_labels") or {}).items():
            n = _para({"para": k}, len(paras))
            if n and n in hit and isinstance(v, str) and v.strip():
                labels[n] = _keyword(v, paras[n - 1], 20)
        return {**base, "ok": True, "message": "" if drows else NO_CASE, "disclosure": disc, "news": news,
                "gaps": gaps, "plan_paras": sorted((hit | set(gaps)) - {0}), "para_labels": labels,
                "context_chunk_ids": ctx.chunk_id.tolist(), "context_news_ids": [] if nctx is None else nctx.doc_id.tolist()}


def format_review(r: dict) -> str:
    p = r["plan"]
    lines = [f"[기획안] {p['title']} · 영역: {', '.join(p['areas'])} · 검색어: {', '.join(p['queries'])}"]
    lines += [f"  P{i + 1} {t.splitlines()[0][:50]}" for i, t in enumerate(r.get("paras", []))]
    if r["message"]:
        lines.append(r["message"])
    if r.get("gaps"):
        lines.append("[사례 없는 실행 방안] " + ", ".join(f"P{n}" for n in r["gaps"]))
    for label, col, ref in (("공시", r["disclosure"], "{}"), ("뉴스", r["news"], "뉴스 {}")):
        if not (col["same"] or col["adjacent"]):
            continue
        lines.append(f"\n[{label}] {col['summary']}")
        for head, rows in (("같은 영역", col["same"]), ("인접 영역", col["adjacent"])):
            lines += [f"  ({head}) P{pa} {t} [{ref.format(n)}]" for t, n, pa in rows]
        for s in col["sources"]:
            name = f"{s['corp']} {s['report']} (접수 {s['report_date']})" if "corp" in s else f"{s['media']} ({s['date']}) {s['title']}"
            lines.append(f"    [{ref.format(s['n'])}] {name}\n      {s['url']}")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("사용법: python rag/plan_review.py 기획안.txt")
    print(format_review(PlanReview().review(Path(sys.argv[1]).read_text(encoding="utf-8"))))
