"""
[B·D5 → A·D7] 공시 Q&A — 사업보고서 근거로 답하고, 뉴스는 보조 근거로 따로 보여 준다

D7 추가 (qa_v4)
    - 뉴스 보조 근거: rag/news_retriever.py 로 관련 기사를 함께 찾아 '공시로 확인된 것'과 '뉴스에 나온 것'을 나눠 답한다
      공시가 우선: answerable·points 는 공시 근거만, 뉴스는 news_points 로 따로 (공시에 없으면 뉴스만 보여 줌)
    - 기획안 사례 찾기: ask_plan(기획안 글) → LLM 이 기획의 AI 활용 영역·검색어를 뽑고, 공시·뉴스에서 관련 사례를 모아 답한다
    - 기획안 후속 질문: ask_followup(질문, 기획안 상태) → 같은 기획안·앞서 찾은 근거(+ 질문으로 새로 찾은 몇 개)로 이어서 답한다
      '더 자세히'·'보강할 점'·'기대효과·우려' — 예측은 하지 않고 근거에 적힌 효과·우려만 (회사 주장 / 보도로 표시)

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
from rag.retriever import Retriever, _grams  # noqa: E402

PROMPT_VERSION = "qa_v4"
PLAN_PROMPT_VERSION = "plan_v2"
FOLLOW_PROMPT_VERSION = "follow_v2"  # v1 → v2: 기획안 검토 사항을 근거처럼 쓰지 않기, 우려 질문엔 뉴스 우려 보도 모두  # 기획안 후속 질문 (테스트에서 질문마다 따로 답해 '보강 추천'에 옥외광고·미수금 보증보험이 나옴)  # v1 → v2 (B 리뷰 PR #29): 사례를 같은 영역 / 인접 영역으로 나눔
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
# 조직·제휴·투자 사례는 '일 자체'가 아니라서 인접 영역 (프롬프트 규칙을 LLM 이 자주 어겨 코드로: 이노션 'AI 전담 TF 신설'이 same 으로 나옴)
ORG_TERMS = r"TF|조직|팀\s?(신설|구성)|신설|얼라이언스|MOU|업무협약|제휴|투자|인수"
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
4. summary: match=same 인 공시 사례 중 기획안과 가장 가까운 것을 한 문장 (근거 번호 없이). same 공시 사례가 없으면 "".
   news_summary: match=same 인 뉴스 사례 중 가장 가까운 것을 60자 안팎 한 문장. same 뉴스 사례가 없으면 "".
5. 한 회사는 한 줄만 (사례가 여럿이면 한 줄에 묶는다). 여러 회사의 사례를 고루 보여 준다.
   summary·news_summary 에 이름을 쓴 회사는 반드시 points·news_points 에도 한 줄이 있어야 한다. 기획안과 관련 없는 근거는 쓰지 않는다.
   줄 안에 근거 번호를 쓰지 않는다 (번호는 cite 에만).
응답 형식: {{"summary": str, "points": [{{"text": str, "cite": int, "match": "same"|"adjacent"}}],
 "news_summary": str, "news_points": [{{"text": str, "cite": int, "match": "same"|"adjacent"}}]}}"""


FOLLOW_SYSTEM = f"""당신은 사용자가 올린 기획안과, 그 기획안으로 앞서 찾은 {CORPS_TEXT} 공시·광고 뉴스 사례를 바탕으로
후속 질문에 답하는 분석가입니다. [기획안 요약]·[이전 답]·[근거]만 사용해 한국어로 답하고 JSON 으로만 답하세요.

질문 종류별
- 자세히·설명: 이전 답의 사례를 근거에 적힌 내용으로 풀어 설명한다. 질문이 회사를 가리키면 그 회사 근거만. kind="fact".
- 보강·추천: 기획안에 없거나 약한 요소를, 근거 사례가 실제로 한 것과 비교해 제안한다. "보강할 것: 어떤 회사가 어떻게 했나" 꼴, kind="suggestion".
  사례에 기대지 않은 일반 조언은 쓰지 않는다. 기획안의 AI 활용과 관련 없는 사업(옥외광고, 미수금 관리, 커머스 브랜드 등)은 쓰지 않는다.
- 기대효과·우려·리스크: 근거에 적힌 효과(kind="effect")와 우려·리스크·한계(kind="concern")만 쓴다.
  사용자 기획의 결과를 예측하지 않는다 (수치·성공 여부를 단정하지 않는다). 근거에 우려가 없으면 우려를 만들지 않는다.
  기획안에 적힌 검토 사항·리스크(예: 저작권, 책임 소재)를 근거에 있는 것처럼 쓰지 않는다 — 그 말이 근거 글에 있을 때만.
  우려를 묻는 질문이면 '뉴스' 근거 중 논란·불매·반발·우려 조사 같은 보도를 news_points 에 빠짐없이 쓴다.
- 그 밖: 근거에 있는 사실 (kind="fact").

공통
1. 공시 칸(summary·points)은 '공시' 근거만, 뉴스 칸(news_summary·news_points)은 '뉴스' 근거만 쓴다. 근거에 없는 사실·추측은 쓰지 않는다.
2. 줄은 40~60자, 맨 앞 이름은 사례의 주체(회사). 줄마다 근거 번호를 cite 에. 줄 안에 근거 번호를 쓰지 않는다.
3. summary·news_summary: 질문에 바로 답하는 한 문장. 없으면 "".
4. 답할 근거가 공시에 없으면 answerable=false, points=[] (뉴스 칸은 따로 채울 수 있다).
응답 형식: {{"answerable": true|false, "summary": str, "points": [{{"text": str, "cite": int, "kind": "fact"|"suggestion"|"effect"|"concern"}}],
 "news_summary": str, "news_points": [{{"text": str, "cite": int, "kind": "fact"|"suggestion"|"effect"|"concern"}}]}}"""
PREDICT_PAT = r"예측|전망|예상|될까|될지|어떻게 될"
CONCERN_PAT = r"우려|리스크|위험|부작용|문제점|한계"
EFFECT_PAT = r"효과|이점|장점|성과"
PREDICT_NOTE = "기획의 결과를 예측하지는 않아요. 비슷한 사례의 근거에 적힌 효과·우려만 보여 드려요."
KIND_TAG = {"effect": {"d": "회사 주장", "n": "보도"}, "concern": {"d": "공시 기재", "n": "보도"}}
FOLLOW_EXTRA_N, FOLLOW_EXTRA_NEWS = 4, 2
# 근거 대조: 줄의 2글자 조각 중 인용 근거에 있는 비율이 이보다 낮으면 버린다 (후속 질문 모드)
#   시험: 정상 줄 0.40~0.89 / 기획안에 적힌 리스크를 KT나스미디어 공시처럼 쓴 줄 0.00 / '기획안과 유사하게 ~ 줄일 수 있다' 예측 줄 0.33
GROUND_MIN = 0.35
# 우려 질문에는 사람이 거른 우려 기사(Q2 화면과 같은 목록, 19건 = 12사건)를 근거 후보에 더한다 — 일반 검색으로는 잘 안 걸림
CONCERN_CSV = settings.DATA_DIR / "processed" / "q2_concern_articles.csv"
CONCERN_N = 3


# 우려·효과 줄은 더 엄격하게: 줄에 나온 핵심어가 인용 근거에 모두 있어야 한다
#   (비율 검사만으로는 '이노션은 TF 신설… 저작권 등 법적 문제를 인지 (공시 기재)'처럼 앞은 근거, 뒤는 지어낸 줄이 0.42~0.50 으로 통과)
KIND_TERMS = {
    "concern": r"우려|리스크|위험|논란|불매|반발|저작권|초상권|윤리|법적|규제|부작용|한계|악용|딥페이크|허위|불신|신뢰|책임|오류",
    "effect": r"절감|향상|증대|증가|단축|개선|효율|성장|매출|수익|생산성",
}


def _terms_ok(line: str, text: str, kind: str | None) -> bool:
    """줄의 우려·효과 핵심어 중 하나 이상이 근거에 있어야 (전부를 요구하면 LLM 이 '불만'을 '반발'로 바꿔 쓴 진짜 보도 줄까지 탈락).
    우려 줄인데 핵심어가 하나도 없으면 우려 줄이 아니므로 버린다"""
    pat = KIND_TERMS.get(kind or "")
    if pat is None:
        return True
    found = set(re.findall(pat, line))
    if not found:
        return kind != "concern"
    return any(t in text for t in found)


def p_title(state: dict) -> str:
    return str(state.get("plan", {}).get("title", ""))


def _ground(line: str, text: str) -> float:
    g = set(_grams(line))
    return len(g & set(_grams(text))) / max(1, len(g))  # 질문으로 새로 찾아 더하는 근거 수 (기획안 근거 + 이만큼)


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


def _points(raw, lo: int, hi: int) -> list[tuple[str, int, str | None]]:
    """LLM 의 [{text, cite, match?}] → [(text, cite, match)] — cite 가 lo~hi 범위(그 칸의 근거 종류)인 줄만"""
    out = []
    for pt in raw or []:
        c = pt.get("cite") if isinstance(pt, dict) else None
        c = c[0] if isinstance(c, list) and c else c
        if isinstance(pt, dict) and pt.get("text") and isinstance(c, int) and lo <= c <= hi:
            text = re.sub(r"\s*\(\d{1,2}(\s*,\s*\d{1,2})*\)", "", pt["text"].replace("**", "")).strip()  # 줄 안 근거 번호 '(1)' '(4,6)'
            out.append((text, c, pt.get("match")))
    return out


SAME_HEAD, ADJ_HEAD = "✅ 같은 영역 — 기획과 같은 일에 AI 를 쓴 사례", "↪ 참고: 인접 영역 — 비슷한 기술을 다른 일에 쓴 사례"


def _lines(points, ref, grouped: bool) -> str:
    """[(text, cite, match)] → 목록 글. grouped 면 같은 영역 / 인접 영역 소제목으로 나눔 (한쪽만 있어도 소제목은 붙임)"""
    row = lambda t, c: f"- {t} {ref(c)}"  # noqa: E731
    if not grouped:
        return "\n".join(row(t, c) for t, c, _ in points)
    same = [row(t, c) for t, c, m in points if m != "adjacent"]
    adj = [row(t, c) for t, c, m in points if m == "adjacent"]
    parts = [f"**{SAME_HEAD}**\n" + "\n".join(same)] if same else []
    parts += [f"**{ADJ_HEAD}**\n" + "\n".join(adj)] if adj else []
    return "\n\n".join(parts)


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
    def _assemble(out: dict, ctx, nctx, grouped: bool = False) -> dict:
        """LLM 답 → 공시 칸(answer·sources)과 뉴스 칸(news_answer·news_sources). 출처 번호는 칸마다 1부터
        grouped=True (기획안): 줄을 '같은 영역'과 '참고: 인접 영역'으로 나눠 쓴다 (same 먼저, 번호도 그 순서)"""
        nd = len(ctx)
        nn = 0 if nctx is None else len(nctx)
        points = _points(out.get("points"), 1, nd)
        npoints = _points(out.get("news_points"), nd + 1, nd + nn)
        if grouped:  # same 을 먼저 (안정 정렬)
            points = sorted(points, key=lambda x: x[2] == "adjacent")
            npoints = sorted(npoints, key=lambda x: x[2] == "adjacent")
        order = list(dict.fromkeys(c for _, c, _ in points))
        new = {c: i + 1 for i, c in enumerate(order)}
        norder = list(dict.fromkeys(c for _, c, _ in npoints))
        nnew = {c: i + 1 for i, c in enumerate(norder)}
        answerable = bool(points) and out.get("answerable", True) is not False  # 인용 없는 답은 거절로
        answer = REFUSAL
        if answerable:
            sm = (out.get("summary") or "").replace("**", "").strip()
            answer = (sm + "\n\n" if sm else "") + _lines(points, lambda c: f"[{new[c]}]", grouped)
        sources = [] if not answerable else [{
            "n": new[c], "corp": ctx.corp_name[c - 1], "report": ctx.report_name[c - 1], "report_date": ctx.report_date[c - 1],
            "section": ctx.section[c - 1], "ai_level": None if ctx.ai_level.isna()[c - 1] else int(ctx.ai_level[c - 1]),
            "url": DART_URL.format(ctx.rcept_no[c - 1]), "chunk_id": ctx.chunk_id[c - 1]} for c in order]
        news_answer = None
        if npoints:
            ns = (out.get("news_summary") or "").replace("**", "").strip()
            news_answer = (ns + "\n\n" if ns else "") + _lines(npoints, lambda c: f"[뉴스 {nnew[c]}]", grouped)
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
        # 같은 영역 / 인접 영역 (B 리뷰 PR #29): 고유 용어가 있는 영역(AREA_TERMS)은 코드가 정한다 — 인용한 근거에 그 용어가 있으면 same.
        #   기획 영역이 모두 고유 용어 영역이면(예: GEO 하나) 용어가 없는 근거는 adjacent. 그 밖에는 LLM 의 match
        #   (GEO 기획안 v1: 공시 4개사가 한 목록에 나와 '4곳이 GEO 를 한다'로 읽힘 — 실제 GEO 는 이노션·플레이디 2곳)
        nd = len(ctx)
        all_term = bool(p["areas"]) and len(pats) == len(p["areas"])
        pat = re.compile("|".join(pats)) if pats else None

        def _cited_text(c: int) -> str:
            if 1 <= c <= nd:
                return ctx.text[c - 1]
            k = c - nd - 1
            return "" if nctx is None or not 0 <= k < len(nctx) else nctx.title[k] + " " + nctx.text[k]

        medias = set() if nctx is None else set(nctx.media)
        for key in ("points", "news_points"):
            for pt in out[key]:
                if not isinstance(pt, dict):
                    continue
                c = pt.get("cite")
                c = c[0] if isinstance(c, list) and c else c
                name, sep, rest = str(pt.get("text", "")).partition(":")
                if sep and name.strip() in medias:  # 줄 맨 앞에 매체 이름을 쓴 경우(동향 기사) → '업계'
                    pt["text"] = "업계:" + rest
                hit = pat is not None and isinstance(c, int) and bool(pat.search(_cited_text(c)))
                if re.search(ORG_TERMS, str(pt.get("text", ""))):  # 먼저: 청크(1,000자)에 영역 용어가 섞여 있어도 줄이 조직·제휴 이야기면 인접
                    pt["match"] = "adjacent"
                elif hit:
                    pt["match"] = "same"
                elif all_term:
                    pt["match"] = "adjacent"
                else:
                    pt["match"] = "adjacent" if pt.get("match") == "adjacent" else "same"
        for key in ("summary", "news_summary"):  # 요약은 한 문장만, 줄 안 근거 번호 제거
            t = re.sub(r"\s*\(\d{1,2}(\s*,\s*\d{1,2})*\)", "", str(out.get(key) or ""))
            out[key] = re.split(r"(?<=[.다함])\s", t.strip(), maxsplit=1)[0]
        # 요약은 same 사례에 대해서만 — same 이 없으면 정해진 문장으로 (LLM 요약이 인접 사례를 같은 영역처럼 쓰지 않게)
        area = ", ".join(p["areas"]) or "기획"
        if not any(isinstance(x, dict) and x.get("match") == "same" for x in out["points"]):
            out["summary"] = f"공시에는 기획과 같은 영역({area})의 사례가 없고, 인접 영역 사례만 있습니다." if out["points"] else ""
        if not any(isinstance(x, dict) and x.get("match") == "same" for x in out["news_points"]):
            out["news_summary"] = f"뉴스에도 기획과 같은 영역({area})의 사례는 없고, 인접 영역 사례만 있습니다." if out["news_points"] else ""
        r = self._assemble(out, ctx, nctx, grouped=True)
        if not r["answerable"]:
            r["answer"] = "공시에서 비슷한 사례를 찾지 못했습니다."
        return {**base, **r, "context_chunk_ids": ctx.chunk_id.tolist(),
                "context_news_ids": [] if nctx is None else nctx.doc_id.tolist()}

    # ---------- 기획안 후속 질문 ----------
    def ask_followup(self, question: str, state: dict, prev_answer: str = "") -> dict:
        """state = {plan, plan_text, chunk_ids, news_ids} (ask_plan 결과에서). 기획안 근거 + 질문으로 새로 찾은 몇 개로 답한다"""
        import pandas as pd
        m = self.retriever.meta.set_index("chunk_id")
        base = m.loc[[c for c in state.get("chunk_ids", []) if c in m.index]].reset_index()
        extra = _dedupe_chunks(self.retriever.search(question, k=SEARCH_K), FOLLOW_EXTRA_N)
        ctx = pd.concat([base, extra]).drop_duplicates("chunk_id").reset_index(drop=True)
        nctx = None
        if self.news is not None:
            nm = self.news.meta.set_index("doc_id")
            nbase = nm.loc[[d for d in state.get("news_ids", []) if d in nm.index]].reset_index()
            nextra = _dedupe_news(self.news.search(question, k=NEWS_K), FOLLOW_EXTRA_NEWS)
            parts = [nbase, nextra]
            if re.search(CONCERN_PAT, question) and CONCERN_CSV.exists():
                import numpy as np
                from rag.embeddings import embed_texts
                cids = set(pd.read_csv(CONCERN_CSV, usecols=["doc_id"]).doc_id)
                idx = np.flatnonzero(self.news.meta.doc_id.isin(cids).values)
                if len(idx):
                    qv = embed_texts([f"{p_title(state)} {question}"])[0]
                    top = idx[np.argsort(-(self.news.mat[idx] @ qv))]
                    parts.append(_dedupe_news(self.news.meta.iloc[top].reset_index(drop=True), CONCERN_N))  # 같은 사건(보그 7건)은 하나로
            nctx = pd.concat(parts).drop_duplicates("doc_id").reset_index(drop=True)
        p = state.get("plan", {})
        head = f"제목: {p.get('title', '')}\nAI 활용 영역: {', '.join(p.get('areas', []))}"
        user = (f"[기획안 요약]\n{head}\n\n[기획안 원문 일부]\n{state.get('plan_text', '')[:1500]}\n\n"
                f"[이전 답]\n{prev_answer[:1200]}\n\n[질문]\n{question}\n\n[근거]\n" + self._blocks(ctx, nctx))
        out = self._llm(FOLLOW_SYSTEM, user, max_tokens=1300)
        nd, nn = len(ctx), 0 if nctx is None else len(nctx)
        kinds = []  # 출처 번호가 그 칸 범위 안인 줄의 kind 만 (화면에 실제로 나오는 줄)
        cited_all = {"d": "", "n": ""}  # 칸마다 남은 줄이 인용한 근거 글 (요약 대조용)
        # 효과·우려를 물으면 그 종류의 줄만 (우려 질문에 'KT나스미디어 DSP 연구' 같은 사실 줄이 섞여 나옴)
        want = ({"concern"} if re.search(CONCERN_PAT, question) else set()) | ({"effect"} if re.search(EFFECT_PAT, question) else set())
        for key, src, lo, hi in (("points", "d", 1, nd), ("news_points", "n", nd + 1, nd + nn)):
            pts = [pt for pt in out.get(key) or [] if isinstance(pt, dict)]
            kept = []
            for pt in pts:
                c = pt.get("cite")
                c = c[0] if isinstance(c, list) and c else c
                if not (isinstance(c, int) and lo <= c <= hi):
                    continue
                cited = ctx.text[c - 1] if src == "d" else nctx.title[c - nd - 1] + " " + nctx.text[c - nd - 1]
                if _ground(str(pt.get("text", "")), cited) < GROUND_MIN:  # 근거에 없는 말 (기획안 내용을 근거처럼 씀 등)
                    continue
                if not _terms_ok(str(pt.get("text", "")), cited, pt.get("kind")):  # 우려·효과 핵심어가 근거에 없음
                    continue
                if want and pt.get("kind") not in want:
                    continue
                kinds.append(pt.get("kind"))
                kept.append(pt)
                cited_all[src] += " " + cited
            for pt in kept:
                tag = KIND_TAG.get(pt.get("kind"), {}).get(src)
                if tag and pt.get("text"):
                    pt["text"] = f"{pt['text']} ({tag})"
                if pt.get("kind") == "suggestion" and pt.get("text") and not str(pt["text"]).startswith("보강"):
                    pt["text"] = "보강 제안 · " + pt["text"]
            out[key] = kept
        for key, src in (("summary", "d"), ("news_summary", "n")):
            out[key] = re.sub(r"\s*\(\d{1,2}(\s*,\s*\d{1,2})*\)", "", str(out.get(key) or "")).strip()
            # 요약도 근거 대조: 남은 줄의 근거에 없는 말이면 지운다 (기획안에 적힌 '저작권·초상권 우려'를 요약에 옮겨 씀)
            if out[key] and _ground(out[key], cited_all[src]) < GROUND_MIN:
                out[key] = ""
        # 예측 요청·우려 요청은 코드가 문장을 정한다 (LLM 이 '~가 기대됩니다'처럼 예측처럼 끝맺지 않게)
        if re.search(PREDICT_PAT, question):
            # 요약은 코드가 정한다: LLM 요약이 기획안에 적힌 리스크를 근거처럼 옮겨 씀 ('예산 집행 오류 리스크가 존재한다')
            out["summary"], out["news_summary"] = PREDICT_NOTE, ""
        if re.search(CONCERN_PAT, question) and "concern" not in kinds:
            # 요약도 코드가: LLM 요약이 기획안에 적힌 우려(저작권·초상권)를 옮겨 쓴 뒤 '확인된 우려 없음'과 부딪힘
            note = "앞서 찾은 사례의 근거(공시·뉴스)에는 우려·리스크가 적혀 있지 않아요."
            out["summary"] = (PREDICT_NOTE + " " + note) if re.search(PREDICT_PAT, question) else note
            out["news_summary"] = ""
        if re.search(CONCERN_PAT, question) and "concern" in kinds and out["news_points"]:
            # 뉴스 요약도 코드가: LLM 요약이 기획안의 '저작권·초상권'을 보도된 우려처럼 섞음 (줄은 근거 대조를 통과한 것만 남음)
            out["news_summary"] = "뉴스에 보도된 우려·논란이에요."
        if not out["points"] and re.search(CONCERN_PAT, question) and "concern" in kinds:  # 우려는 뉴스에만 있을 때
            out["summary"] = (PREDICT_NOTE + " " if re.search(PREDICT_PAT, question) else "") + "공시에는 이런 우려가 적혀 있지 않아요."
        r = self._assemble(out, ctx, nctx)
        if not r["answerable"] and out["summary"] and (re.search(PREDICT_PAT, question) or re.search(CONCERN_PAT, question)):
            r["answer"] = out["summary"]  # 공시 줄이 없어도 안내 문장은 보여 준다 (코드가 정한 문장만 여기 옴)
        return {"question": question, **r, "followup": True, "plan": p, "context_chunk_ids": ctx.chunk_id.tolist(),
                "context_news_ids": [] if nctx is None else nctx.doc_id.tolist(),
                "model": self.model, "prompt_version": FOLLOW_PROMPT_VERSION}


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
