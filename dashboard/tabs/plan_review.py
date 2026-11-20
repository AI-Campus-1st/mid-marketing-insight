"""
[A·D7] 기획안 검토 화면 — 기획안을 올리면 공시·뉴스에서 비슷한 AI 활용 사례를 찾아 리포트 한 장으로 보여 준다

사용자: 5개사 밖 중견 대행사 신사업기획팀장 (메인 페르소나, 이슈 #28) — "우리 기획과 같은 일을 경쟁사가 실제로 했나?"
- 입력: 파일(txt·md·pdf·docx) 또는 직접 입력 → '검토 시작'. 결과가 나오면 입력 칸은 숨긴다 ('＋ 새 기획안'으로 다시)
- 결과: 결과 요약(실행 부분 몇 곳에 같은 일 사례가 있는지 + 기획 부분 ↔ 회사 · 사업 키워드 짝) → 왼쪽 기획안 문서(사례가 붙은 문단을 칠함) ·
  오른쪽 그 문단의 사례 카드('같은 일을 이미 한 곳' / '비슷한 기술을 다른 일에 쓴 곳', 공시·뉴스 배지)
  (문단은 rag/plan_review.split_paras, 사례 ↔ 문단 연결은 LLM 의 para, 결론은 코드가 사례로 만든 짝 목록 — plan_v6)
- 사례 없는 문단이 COLLAPSE_MIN 개 이상 이어지면 앞뒤만 두고 가운데는 '⋯ 중략'으로 접는다 (눌러서 펼침)
- 챗봇과 분리했고 이어지는 질문은 받지 않는다 (rag/plan_review.py 머리말, decisions.md Day 7)
- 기록: 챗봇처럼 사이드바 '＋ 새 기획안' · '지난 기획안'. data/processed/plan_history/<id>.json (저장소 제외, 기획안 글은 문단으로만)
"""
import html
import io
import json
import re
import zipfile
from datetime import datetime

import streamlit as st

from config import settings
from dashboard import style as ui

TITLE = "기획안 검토"
TAGLINE = "AI 신사업 계획이나 마케팅 기획안을 올리면, 비슷한 사례를 정기공시(사업·반기·분기보고서)와 뉴스에서 찾아 드립니다."
FILE_TYPES = ["txt", "md", "pdf", "docx"]
MIN_CHARS = 30
HIST_DIR = settings.DATA_DIR / "processed" / "plan_history"
HIST_SHOW = 30
SAME_HEAD = ("일치",   # 기획과 같은 일을 이미 한 곳
             "같은 상품·서비스·업무에 AI 를 쓴 사례")
ADJ_HEAD = ("유사",    # 비슷한 기술을 다른 일에 쓴 곳
            "다른 업무에 썼거나, 전담 조직·제휴처럼 준비 단계인 사례")


@st.cache_resource(show_spinner="공시·뉴스 검색기를 준비하는 중…", max_entries=1)
def _reviewer_cached(code_version: str):
    # 모듈도 다시 읽는다: Streamlit 은 바뀐 dashboard/ 코드만 다시 읽고 rag/ 는 처음 읽은 옛 모듈을 계속 써서,
    #   객체만 새로 만들면 옛 코드로 만들어짐 → 새 화면 코드와 결과 모양이 달라 'expected 3, got 2' (plan_v3 → v4 때)
    import importlib

    import rag.news_retriever
    import rag.plan_review
    import rag.retriever
    for m in (rag.retriever, rag.news_retriever, rag.plan_review):  # 의존 순서대로
        importlib.reload(m)
    return rag.plan_review.PlanReview()


def _reviewer():
    """rag/ 코드가 바뀌면(파일 수정 시각) 모듈을 다시 읽고 새로 만든다 — 서버를 켠 채 코드를 고쳐도 새 코드로"""
    rag = settings.ROOT / "rag"
    return _reviewer_cached("|".join(str((rag / f).stat().st_mtime_ns)
                                     for f in ("plan_review.py", "news_retriever.py", "retriever.py")))


def _file_text(f) -> str:
    """올린 파일 → 글. txt·md 는 그대로, pdf 는 pypdf, docx 는 압축 안 word/document.xml 의 글자만"""
    name, data = f.name.lower(), f.getvalue()
    if name.endswith(".pdf"):
        from pypdf import PdfReader
        return "\n".join(pg.extract_text() or "" for pg in PdfReader(io.BytesIO(data)).pages)
    if name.endswith(".docx"):
        xml = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode("utf-8")
        paras = re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S)
        return "\n".join("".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", p)) for p in paras)
    for enc in ("utf-8", "cp949"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return ""


DOC_CSS = """<style>
/* 기획안 문서 (왼쪽 칸): 줄(st-key-pr_row_*)마다 첫 칸을 흰 종이처럼 이어 붙인다 — 칸은 줄 높이만큼 늘어나 오른쪽 카드가 길어도 종이가 끊기지 않음 */
.st-key-pr_doc { background: #f4f6fa; border-radius: 16px; padding: 1.2rem; }
div[class*="st-key-pr_row_"] [data-testid="stColumn"]:first-child {
  background: #ffffff; border-left: 1px solid #d5dbe5; border-right: 1px solid #d5dbe5; padding: .05rem 1.6rem;
}
.st-key-pr_row_first [data-testid="stColumn"]:first-child { border-top: 1px solid #d5dbe5; border-radius: 12px 12px 0 0; padding-top: 1.3rem; }
.st-key-pr_row_last [data-testid="stColumn"]:first-child { border-bottom: 1px solid #d5dbe5; border-radius: 0 0 12px 12px; padding-bottom: 1.3rem; }
.st-key-pr_row_only [data-testid="stColumn"]:first-child { border: 1px solid #d5dbe5; border-radius: 12px; padding: 1.3rem 1.6rem; }
.st-key-pr_doc [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }   /* Streamlit 기본 -1rem 이 줄을 겹치게 함 */
.pr-whole { font-size: .82rem; color: #6b7686; padding: .7rem 0 0; }
.pr-para { white-space: pre-wrap; font-size: .93rem; line-height: 1.65; color: #1f2733; padding: .25rem .6rem; margin: .15rem -.6rem .35rem;
  border-radius: 6px; border-left: 3px solid transparent; }
.pr-para.hl { background: #e3eefc; border-left-color: #2a78d6; }          /* 같은 영역 사례가 붙은 문단 */
.pr-para.hl-adj { background: #fdf5ea; border-left-color: #e0a458; }      /* 인접 영역(비슷한 기술) 사례만 붙은 문단 = 주황 */
.pr-card { background: #ffffff; border: 1px solid #b4d0f1; border-radius: 12px; padding: .65rem .85rem; margin: .1rem 0 .55rem;
  box-shadow: 0 1px 2px rgba(16,24,40,.04), 0 4px 12px rgba(16,24,40,.05); position: relative; font-size: .86rem; line-height: 1.5; }
.pr-card::before { content: ""; position: absolute; left: -1.05rem; top: 1.1rem; width: 1.05rem; border-top: 2px solid #2a78d6; }  /* 문단과 잇는 선 */
.pr-card.adj { border-color: #f0d3ad; } .pr-card.adj::before { border-top-color: #e0a458; }   /* 비슷한 기술만 있는 카드 = 주황 */
.pr-sec + .pr-sec { border-top: 1px solid #dfe4ec; margin-top: .9rem; padding-top: .85rem; }   /* 일치 / 유사 나누는 선 (위아래 여백) */
/* 일치·유사 제목: 검정 굵은 글자 (사례 글자보다 크게) */
.pr-sec .head { font-size: .95rem; font-weight: 800; color: #1f2733; }
.pr-sec .sub { font-size: .7rem; color: #8592a3; margin-bottom: .25rem; }
.pr-card.gap { border: 1px dashed #c9d2df; box-shadow: none; background: transparent; color: #6b7686; font-size: .8rem; }
.pr-card.gap::before { border-top: 2px dashed #c9d2df; }
.pr-item { margin: .3rem 0; }
.pr-item .tag { display: inline-block; font-size: .7rem; font-weight: 700; border-radius: 5px; padding: 0 .35rem; margin-right: .3rem; }
.pr-item .tag.d { background: #e3eefc; color: #1d5fae; } .pr-item .tag.n { background: #fce8f1; color: #b0336f; }   /* 뉴스 = 핑크 */
.pr-item a { font-size: .74rem; color: #6b7686; text-decoration: none; margin-left: .25rem; }
.pr-col-head { font-size: .8rem; font-weight: 700; color: #6b7686; letter-spacing: .02em; margin-bottom: .6rem; }
.pr-concl { font-size: .95rem; line-height: 1.7; color: #1f2733; }
.pr-concl .map { display: flex; gap: .9rem; padding: .45rem 0; border-top: 1px solid #d9e6f6; font-size: .87rem; line-height: 1.6; }
.pr-concl .map:first-of-type { margin-top: .6rem; }
.pr-concl .part { flex: 0 0 11rem; font-weight: 800; color: #1d5fae; }
.pr-concl .cases { flex: 1; }
.pr-concl .tag { display: inline-block; font-size: .7rem; font-weight: 700; border-radius: 5px; padding: 0 .35rem; margin-right: .35rem; }
.pr-concl .tag.d { background: #e3eefc; color: #1d5fae; } .pr-concl .tag.n { background: #fce8f1; color: #b0336f; }
.pr-concl .sim { display: inline-block; font-size: .7rem; font-weight: 700; color: #a35a00; background: #fdf0e1;   /* 비슷한 기술 = 주황 (사례 카드의 비슷한 기술 구역과 같은 색) */ border-radius: 5px; padding: 0 .35rem; margin-right: .35rem; }
.pr-concl .yr { color: #8592a3; font-size: .78rem; }
.pr-concl .none { color: #8592a3; }
.pr-concl .note { margin-top: .5rem; font-size: .75rem; color: #8592a3; }
.st-key-hl_pr_concl [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }   /* 안내 문구가 상자 밖으로 나가지 않게 */
/* '매칭 결과'·'구체적인 사례 물어보기' 소제목: 다른 소제목(16.8px)보다 크게 */
.st-key-pr_h_match h5, .st-key-pr_h_ask h5 { font-size: 1.3rem !important; }
.st-key-pr_h_match, .st-key-pr_h_ask { margin-top: 2rem; }   /* 섹션 사이를 넉넉히 띄워 구분 (결과 요약 ↔ 매칭 결과 ↔ 사례 물어보기) */
.pr-ask-sub { font-size: .875rem; color: #1f2733; margin-bottom: 1.1rem; }   /* 챗봇 버튼 설명: 검정. 아래 버튼과 띄움 */
/* 챗봇 물어보기 버튼: 말풍선 모양 (왼쪽 아래 꼬리). 기본 흰 바탕 + 회색 테두리 → 마우스를 대면 옅은 파랑 + 파랑 테두리('＋ 새 기획안'과 같은 색) */
div[class*="st-key-pr_ask_"] button { background: #ffffff !important; border: 1px solid #d5dbe5 !important;
  border-radius: 18px 18px 18px 0 !important; padding: .5rem 1.1rem !important; position: relative; margin-left: 10px; overflow: visible;
  filter: drop-shadow(0 1px 1.5px rgba(16, 24, 40, .06)) drop-shadow(0 3px 6px rgba(16, 24, 40, .06)); }   /* 옅은 그림자 (꼬리까지 같이 — box-shadow 는 꼬리에 안 생김) */
/* 꼬리: 버튼 왼쪽 아래 바깥에 작은 그림(바탕·테두리 색 같음). 오른쪽 1px 를 버튼 테두리 위에 겹쳐 이어 보이게 */
div[class*="st-key-pr_ask_"] button::before { content: ""; position: absolute; left: -10px; bottom: -1px; width: 12px; height: 14px;
  background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='14' viewBox='0 0 12 14'%3E%3Cpath d='M11 0 L11 13.5 L0.5 13.5 Q8 12 11 0 Z' fill='%23ffffff'/%3E%3Cpath d='M11 0 Q8 12 0.5 13.5 L12 13.5' fill='none' stroke='%23d5dbe5' stroke-width='1'/%3E%3C/svg%3E") no-repeat; }
div[class*="st-key-pr_ask_"] button:hover { background: #eaf2fb !important; border-color: #aac9ef !important; }
div[class*="st-key-pr_ask_"] button:hover::before { background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='14' viewBox='0 0 12 14'%3E%3Cpath d='M11 0 L11 13.5 L0.5 13.5 Q8 12 11 0 Z' fill='%23eaf2fb'/%3E%3Cpath d='M11 0 Q8 12 0.5 13.5 L12 13.5' fill='none' stroke='%23aac9ef' stroke-width='1'/%3E%3C/svg%3E") no-repeat; }
div[class*="st-key-pr_ask_"] button p { font-size: .875rem !important; font-weight: 800 !important; letter-spacing: normal; color: #1f2733 !important; }   /* 글자 검정, 본문보다 작게 */
/* 결과 요약 상자 안 여백: 양옆과 아래를 위(상자 위 → '결과 요약' 글자, 약 29px)와 같게 */
div.st-key-hl_pr_concl { padding: 5.6px 28px 28px 28px !important; }
</style>"""


# '검토 시작' 버튼: 기존 강조 파랑 (입력 화면에서도 보이도록 render() 가 매번 넣는다)
BTN_CSS = """<style>
.st-key-pr_go button { background: #2a78d6 !important; border-color: #2a78d6 !important; }
.st-key-pr_go button:hover { background: #1f66bd !important; border-color: #1f66bd !important; }
.st-key-pr_go button p, .st-key-pr_go button span { color: #ffffff !important; font-weight: 700; }
/* '검토 시작' 아래 경고 알림: 토스트처럼 떴다가 4초 뒤 사라짐. 상자 색은 사이드바 '＋ 새 기획안' 버튼과 같은 옅은 파랑 */
.pr-toast { display: inline-flex; align-items: center; gap: .55rem; padding: .7rem 1rem; border-radius: 10px;
  background: #eaf2fb; border: 1.5px solid #aac9ef; box-shadow: 0 4px 14px rgba(16, 24, 40, .10); font-size: .9rem; color: #1f2733;
  animation: pr-toast-a 4.5s ease forwards; }
.pr-toast.b { animation-name: pr-toast-b; }   /* 누를 때마다 a·b 를 번갈아 → 애니메이션 이름이 바뀌어 처음부터 다시 재생 */
.pr-toast .ic { color: #d97706; font-size: 1.05rem; }
@keyframes pr-toast-a { 0% { opacity: 0; transform: translateY(-4px); } 6% { opacity: 1; transform: none; }
  85% { opacity: 1; } 100% { opacity: 0; visibility: hidden; } }
@keyframes pr-toast-b { 0% { opacity: 0; transform: translateY(-4px); } 6% { opacity: 1; transform: none; }
  85% { opacity: 1; } 100% { opacity: 0; visibility: hidden; } }
/* '이 기획안 지우기': 기획안 제목 첫 줄과 같은 높이로 (칸 위쪽 맞춤 + 제목 h1 위 여백만큼 내림) */
.st-key-pr_del button { transform: translateY(20px); }
/* 결과 화면 맨 위: 첨부 파일(작은 회색) · AI 활용 영역 해시태그 필(사이드바 '＋ 새 기획안'과 같은 옅은 파랑) */
.pr-meta { font-size: .9rem; color: #6b7686; }
.pr-tags { display: flex; flex-wrap: wrap; gap: .4rem; margin-bottom: .55rem; }
.pr-tag { display: inline-block; padding: .28rem .75rem; border-radius: 999px; background: #eaf2fb; border: 1px solid #aac9ef;
  color: #2a78d6; font-size: .85rem; font-weight: 600; }
/* '직접 입력' 소제목 옆 작고 흐린 보충 글 */
.pr-hint { font-size: .82rem; font-weight: 400; color: #8a93a3; margin-left: .35rem; }
/* 제목과 설명 사이를 조금 띄움 */
.st-key-pr_tagline { margin-top: .6rem; }
/* 파일 첨부 칸: Streamlit 은 이 글자들을 바꾸는 설정이 없어서, 영어 글자를 숨기고(font-size: 0) 같은 자리에 한국어를 덧씌움 */
[data-testid="stFileUploaderDropzoneInstructions"] > div > span { font-size: 0 !important; }
[data-testid="stFileUploaderDropzoneInstructions"] > div > span:first-child::after { content: "파일을 여기로 끌어다 놓으십시오"; font-size: 1rem; }
[data-testid="stFileUploaderDropzoneInstructions"] > div > span:last-child::after { content: "파일 1개당 200MB까지 · TXT, MD, PDF, DOCX"; font-size: .875rem; }   /* 칸 이름(파일 첨부)과 같은 14px */
[data-testid="stFileUploaderDropzone"] button[data-testid="stBaseButton-secondary"] { font-size: 0 !important; }
[data-testid="stFileUploaderDropzone"] button[data-testid="stBaseButton-secondary"]::after { content: "파일 선택"; font-size: 1rem; }   /* 옆 버튼들(검토 시작·예시)과 같은 16px */
</style>"""


def _items(r: dict) -> dict[int, list[dict]]:
    """문단 번호 → 그 문단의 사례 [{text, n, same, kind(d|n), url}] (0 = 기획 전체). 같은 일 먼저, 공시 먼저"""
    by: dict[int, list[dict]] = {}
    for kind, col in (("d", r["disclosure"]), ("n", r["news"])):
        url = {s["n"]: s["url"] for s in col["sources"]}
        for same, rows in ((True, col["same"]), (False, col["adjacent"])):
            for t, n, pa in rows:
                by.setdefault(pa, []).append({"text": t, "n": n, "same": same, "kind": kind, "url": url.get(n, "")})
    for v in by.values():
        v.sort(key=lambda x: (not x["same"], x["kind"] != "d"))
    return by


def _item_html(x: dict) -> str:
    name, sep, rest = x["text"].partition(":")
    body = f"<b>{html.escape(name.strip())}</b> {html.escape(rest.strip())}" if sep else html.escape(x["text"])
    src = "공시" if x["kind"] == "d" else "뉴스"
    link = f'<a href="{html.escape(x["url"])}" target="_blank">원문 ↗</a>' if x["url"] else ""
    return f'<div class="pr-item"><span class="tag {x["kind"]}">{src}</span>{body}{link}</div>'


def _card(items: list[dict]) -> str:
    """사례 카드: '같은 일을 이미 한 곳'과 '비슷한 기술을 다른 일에 쓴 곳'을 선으로 나눈다"""
    secs = []
    for same, (head, sub) in ((True, SAME_HEAD), (False, ADJ_HEAD)):
        xs = [x for x in items if x["same"] == same]
        if xs:
            secs.append(f'<div class="pr-sec{"" if same else " adj"}"><div class="head">{head} {len(xs)}</div>'
                        f'<div class="sub">{sub}</div>{"".join(_item_html(x) for x in xs)}</div>')
    adj = not any(x["same"] for x in items)
    return f'<div class="pr-card{" adj" if adj else ""}">{"".join(secs)}</div>'


COLLAPSE_MIN = 4   # 사례 없는 문단이 이만큼 이어지면 앞뒤 하나씩만 두고 가운데(2개 이상)를 접는다


def _collapsed(n: int, marked: set) -> set:
    """접을 문단 번호: 사례·사례없음 표시가 없는 문단이 COLLAPSE_MIN 개 이상 이어지면 그 묶음의 첫·끝을 뺀 가운데"""
    out, run = set(), []
    for i in list(range(1, n + 1)) + [None]:
        if i is not None and i not in marked:
            run.append(i)
            continue
        if len(run) >= COLLAPSE_MIN:
            out |= set(run[1:-1])
        run = []
    return out


def _join(bits: list[tuple[str, str, str]]) -> str:
    return ", ".join(stem + (end if i == len(bits) - 1 else mid) for i, (stem, mid, end) in enumerate(bits))


GAP_CARD = '<div class="pr-card gap">공시·뉴스에서 이 부분과 같은 일을 한 사례를 찾지 못했습니다.</div>'


def _year(text: str) -> str:
    m = re.search(r"\((\d{4})[^)]*\)\s*$", text)
    return m.group(1) if m else ""


def _label(r: dict, n: int) -> str:
    lab = (r.get("para_labels") or {}).get(n) or (r.get("para_labels") or {}).get(str(n))  # 기록 파일(JSON)은 키가 글자
    if lab:
        return lab
    first = re.sub(r"^(■|□|\(\d{1,2}\)|\d{1,2}[.)]|[가-하][.)])\s*", "", r["paras"][n - 1].splitlines()[0].strip())
    return first if len(first) <= 24 else first[:24] + "…"


def _conclusion(r: dict) -> None:
    """결과 요약(옛 이름 검토 결론) — 모두 코드가 만든다 (LLM 은 기획 부분 이름·사업 키워드만):
    한 줄 요약(실행 방안 몇 개 중 몇 개에 같은 일 사례) + 기획 부분마다 '회사 · 사업 키워드'"""
    by, gaps = _items(r), set(r.get("gaps") or [])
    kw = {"d": r["disclosure"].get("keywords") or {}, "n": r["news"].get("keywords") or {}}
    parts = [n for n in (r.get("plan_paras") or sorted(set(by) | gaps)) if n]
    if not parts:
        return

    def case(x: dict) -> str:
        k = kw[x["kind"]].get(x["n"]) or kw[x["kind"]].get(str(x["n"])) or ""
        name = x["text"].split(":")[0].strip()
        y = _year(x["text"])
        return f'<b>{html.escape(name)}</b> {html.escape(k)}' + (f' <span class="yr">{y}</span>' if y else "")

    d_same = [n for n in parts if any(x["same"] and x["kind"] == "d" for x in by.get(n, []))]
    n_only = [n for n in parts if n not in d_same and any(x["same"] for x in by.get(n, []))]
    adj_only = [n for n in parts if n not in d_same and n not in n_only and by.get(n)]
    lead = f"기획의 실행 부분 {len(parts)}곳 중 "
    bits = []   # (줄기, 중간 어미, 끝 어미) — 중간은 '~고'로 잇고 마지막만 '-습니다' (예: '…있고, 1곳은 …못했습니다')
    if d_same:
        bits.append((f"<b>{len(d_same)}곳</b>은 공시로 같은 일을 이미 한 회사가 있", "고", "습니다"))
    if n_only:
        bits.append((f"<b>{len(n_only)}곳</b>은 뉴스 보도로만 같은 사례가 보", "이고", "입니다"))
    if adj_only:
        bits.append((f"<b>{len(adj_only)}곳</b>은 비슷한 기술 사례만 있", "고", "습니다"))
    if gaps:
        bits.append((f"<b>{len(gaps)}곳</b>은 사례를 찾지 못했", "고", "습니다"))
    rows = []
    for n in parts:
        xs = by.get(n, [])
        same = [x for x in xs if x["same"]]
        adj = [x for x in xs if not x["same"]]
        cells = []
        for kind, src in (("d", "공시"), ("n", "뉴스")):
            ys = [case(x) for x in same if x["kind"] == kind]
            if ys:
                cells.append(f'<span class="tag {kind}">{src}</span>' + " · ".join(ys))
        if adj:
            cells.append('<span class="sim">유사 기술</span>' + " · ".join(case(x) for x in adj))
        if not xs:
            cells.append('<span class="none">공시·뉴스에서 같은 사례를 찾지 못했습니다</span>')
        rows.append(f'<div class="map"><div class="part">{html.escape(_label(r, n))}</div><div class="cases">{"<br>".join(cells)}</div></div>')
    note = '<div class="note">기획 부분 이름과 사업 키워드만 AI 가 줄였고, 짝은 찾은 사례(공시·뉴스) 그대로입니다. 기획의 결과는 예측하지 않습니다.</div>'
    with st.container(key="hl_pr_concl"):
        st.markdown("##### 🧭 결과 요약")
        st.markdown(f'<div class="pr-concl">{lead + _join(bits)}.{"".join(rows)}{note}</div>', unsafe_allow_html=True)


def _to_chatbot(q: str, chunk_ids: list[str] | None = None) -> None:
    """버튼 콜백: 공시 챗봇 새 대화로 넘어가 이 질문에 바로 답하게 (qa.render 가 qa_pending 을 받음). 기획안 결과는 그대로 남음.
    chunk_ids: 기획안 검토가 이미 찾은 그 사례의 공시 문단 — 챗봇이 다시 검색하다 놓치지 않게 근거로 함께 넘김"""
    st.session_state.pop("qa_id", None)
    st.session_state.qa_msgs = []
    st.session_state.qa_pending = {"q": q, "chunk_ids": chunk_ids or []}
    st.session_state.page_sel = "공시 챗봇"


def _ask_chatbot(r: dict) -> None:
    """공시 사례마다 '공시 챗봇에 물어보기' 버튼 (B 제안 ①) — 이미 검증한 공시 챗봇이 답한다.
    뉴스 사례는 넣지 않음: 챗봇은 공시로만 답해서 5개사 밖 회사는 거절됨"""
    kw = r["disclosure"].get("keywords") or {}
    cid = {s["n"]: s.get("chunk_id") for s in r["disclosure"]["sources"]}   # 사례 번호 → 근거 문단 (기록 파일은 키가 글자)
    cid.update({str(k): v for k, v in list(cid.items())})
    seen, qs = set(), []
    for rows in (r["disclosure"]["same"], r["disclosure"]["adjacent"]):
        for t, n, _ in rows:
            corp = t.split(":")[0].strip()
            k = (kw.get(n) or kw.get(str(n)) or "").strip()
            if not corp or not k or (corp, k) in seen:
                continue
            seen.add((corp, k))
            qs.append((corp, k, f"{corp}의 {k}에 대해 알려주세요.", [c for c in [cid.get(n) or cid.get(str(n))] if c]))
    if not qs:
        return
    with st.container(key="pr_h_ask"):   # key: 소제목 크게 (DOC_CSS)
        st.markdown("##### 💬 구체적인 사례 물어보기")
    st.markdown('<div class="pr-ask-sub">누르면 <b>공시 챗봇 새 대화에서 바로 답합니다.</b> 기획안 검토 결과는 그대로 남습니다.</div>',
                unsafe_allow_html=True)
    for i, (corp, k, q, ids) in enumerate(qs):   # 한 줄에 하나씩, 글 길이만큼의 말풍선 (DOC_CSS st-key-pr_ask_)
        st.button(q, key=f"pr_ask_{i}", width="content", on_click=_to_chatbot, args=(q, ids))   # 버튼 글 = 챗봇에 보낼 질문 그대로


def _report(r: dict, label: str) -> None:
    # 기획안 제목·첨부 파일·AI 활용 영역은 화면 맨 위(render)에서 그림
    if not r["ok"]:
        st.warning(r["message"] + " AI 를 어디에 어떻게 쓰려는지가 드러나게 써 주십시오.")
        return
    st.markdown(DOC_CSS, unsafe_allow_html=True)
    _conclusion(r)
    by, gaps = _items(r), set(r.get("gaps") or [])
    with st.container(key="pr_h_match"):   # 왼쪽 기획안 ↔ 오른쪽 매칭 사례. key: 소제목 크게 (DOC_CSS)
        st.markdown("##### 🔍 매칭 결과")
    with st.container(key="pr_doc", gap=None):
        head_l, head_r = st.columns([3, 2], gap="large")
        head_l.markdown('<div class="pr-col-head">📄 기획안</div>', unsafe_allow_html=True)
        head_r.markdown('<div class="pr-col-head">🔎 매칭 사례</div>', unsafe_allow_html=True)
        if by.get(0):  # 특정 문단이 아니라 기획 전체와 관련된 사례는 문서 위에
            l0, r0 = st.columns([3, 2], gap="large")
            l0.markdown('<div class="pr-whole">기획 전체와 관련된 사례 →</div>', unsafe_allow_html=True)
            r0.markdown(_card(by[0]), unsafe_allow_html=True)
        n = len(r["paras"])
        hidden = _collapsed(n, set(by) | gaps)
        for i, text in enumerate(r["paras"], 1):
            if i in hidden and i - 1 in hidden:
                continue   # 접힌 묶음의 첫 문단에서 한 번에 그림
            key = "pr_row_" + ("only" if n == 1 else "first" if i == 1 else "last" if i == n else str(i))
            with st.container(key=key, gap=None):
                left, right = st.columns([3, 2], gap="large")
                if i in hidden:   # 사례 없는 문단이 길게 이어지는 곳: '중략'으로 접고 눌러서 펼침
                    run = [j for j in range(i, n + 1) if all(k in hidden for k in range(i, j + 1))]
                    with left.expander("⋯ 중략 ⋯"):
                        st.markdown("".join(f'<div class="pr-para">{html.escape(r["paras"][j - 1])}</div>' for j in run),
                                    unsafe_allow_html=True)
                    continue
                items = by.get(i, [])
                cls = "hl" if any(x["same"] for x in items) else "hl-adj" if items else ""
                left.markdown(f'<div class="pr-para {cls}">{html.escape(text)}</div>', unsafe_allow_html=True)
                if items:
                    right.markdown(_card(items), unsafe_allow_html=True)
                elif i in gaps:
                    right.markdown(GAP_CARD, unsafe_allow_html=True)
    st.caption("문서는 올린 파일에서 읽은 글자로 다시 그린 것이라 원본의 표·그림·글꼴은 보이지 않습니다.")
    _ask_chatbot(r)   # 맨 아래: 공시 사례를 챗봇에 물어보는 버튼


# ---------- 기록 (파일 하나 = 기획안 하나, 챗봇 대화 기록과 같은 방식) ----------
def _save(result: dict, label: str) -> str:
    rid = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    HIST_DIR.mkdir(parents=True, exist_ok=True)
    title = result["plan"].get("title") or label
    (HIST_DIR / f"{rid}.json").write_text(json.dumps(
        {"id": rid, "updated": datetime.now().strftime("%Y-%m-%d %H:%M"), "title": title, "label": label, "result": result},
        ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return rid


def _history() -> list[dict]:
    if not HIST_DIR.exists():
        return []
    out = []
    for f in HIST_DIR.glob("*.json"):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(out, key=lambda c: c["id"], reverse=True)


def _open(c: dict) -> None:
    st.session_state.pr_id, st.session_state.pr_result = c["id"], (c["result"], c["label"])


def _new() -> None:
    for k in ("pr_id", "pr_result", "pr_text"):
        st.session_state.pop(k, None)
    st.session_state.pr_nonce = st.session_state.get("pr_nonce", 0) + 1   # 파일 올리기 칸도 비운다 (key 를 바꿔 새 위젯으로)


def _delete() -> None:
    rid = st.session_state.get("pr_id")
    if rid:
        (HIST_DIR / f"{rid}.json").unlink(missing_ok=True)
    _new()


def _sidebar() -> None:
    with st.sidebar:
        st.button("＋ 새 기획안", width="stretch", type="primary", on_click=_new)
        items = _history()
        st.markdown("**지난 기획안**")
        if not items:
            st.caption("아직 없습니다. 검토하면 여기에 저장됩니다.")
        cur = st.session_state.get("pr_id")
        tips = []   # 잘린 제목에 마우스를 대면 전체 제목 — 메뉴 설명과 같은 말풍선·정렬 (style.hover_tips)
        for c in items[:HIST_SHOW]:
            title = c["title"] if len(c["title"]) <= 24 else c["title"][:24] + "…"
            now = "cur_" if c["id"] == cur else ""   # 지금 보고 있는 기획안: key 에 cur_ → 둥근 파란 테두리 (style.py)
            st.button(f"{title}  \n{c['updated']}", key=f"hist_pr_{now}{c['id']}", width="stretch",
                      on_click=_open, args=(c,))
            if title != c["title"]:   # 제목이 잘렸을 때만 전체 제목 말풍선
                tips.append((f"div.st-key-hist_pr_{now}{c['id']}", c["title"]))
        ui.hover_tips(tips, shift=".5rem, 4px")


def _input() -> tuple[str, str] | None:
    """입력 칸 (결과가 없을 때만 보임) → (기획안 글, 표시 이름) 또는 None"""
    nonce = st.session_state.get("pr_nonce", 0)
    # 칸 이름: 공시 챗봇 '이런 걸 물어볼 수 있습니다'와 같은 굵은 소제목(#####). 위젯 자체 이름표는 숨김(화면 읽기용으로는 남음)
    st.markdown("##### 파일 첨부")
    up = st.file_uploader("파일 첨부", type=FILE_TYPES, key=f"pr_file_{nonce}", label_visibility="collapsed")
    st.markdown('##### 직접 입력 <span class="pr-hint">(기획안 파일이 없는 경우)</span>', unsafe_allow_html=True)
    text = st.text_area("직접 입력", key="pr_text", height=160, label_visibility="collapsed",
                        placeholder="예: [신사업 제안] AI 검색 최적화(GEO) 컨설팅 상품 출시 — 배경, 제안, 필요 역량…")
    go, _ = st.columns([1, 4.4])
    run = go.button("검토 시작", key="pr_go", width="stretch", icon=":material/search:")
    if not run:
        return None
    if up is not None and text.strip():   # 둘 다 넣었으면 어느 쪽을 검토할지 모르므로 시작하지 않음
        # st.toast 는 화면 오른쪽 위에만 떠서, 같은 모양의 알림을 '검토 시작' 바로 아래에 직접 그림 (몇 초 뒤 사라짐, BTN_CSS .pr-toast)
        # 누를 때마다 다시 뜨게: Streamlit 은 같은 자리의 요소를 다시 써서 애니메이션이 처음부터 돌지 않으므로 a·b 를 번갈아 씀
        n = st.session_state["pr_toast_n"] = st.session_state.get("pr_toast_n", 0) + 1
        st.markdown(f'<div class="pr-toast {"ab"[n % 2]}"><span class="ic">⚠</span>파일 첨부와 직접 입력 중 하나만 해 주십시오.</div>',
                    unsafe_allow_html=True)
        return None
    body, label = text.strip(), "붙여 넣은 글"
    if up is not None:
        try:
            body = _file_text(up).strip()
        except Exception as e:  # 깨진 파일 등
            st.error(f"'{up.name}' 을(를) 읽지 못했습니다 ({type(e).__name__}). txt·md·pdf·docx 파일인지 확인해 주십시오.")
            return None
        if not body:
            st.error(f"'{up.name}' 에서 글자를 찾지 못했습니다. 스캔한 그림 PDF 는 읽을 수 없습니다.")
            return None
        label = f"📎 {up.name}"
    if len(body) < MIN_CHARS:
        st.info("파일을 첨부하거나 기획 내용을 몇 줄 이상 적어 주십시오.")
        return None
    return body, label


def render() -> None:
    st.markdown(BTN_CSS, unsafe_allow_html=True)
    _sidebar()
    res = st.session_state.get("pr_result")
    if res is not None and "paras" not in res[0]:
        st.session_state.pop("pr_result")   # 옛 버전 코드로 만든 결과 (문단 정보 없음) — 다시 검토해야 함
        res = None
    head, clear = st.columns([5, 1], vertical_alignment="top")   # top: 제목이 두 줄이어도 버튼은 첫 줄 높이 (BTN_CSS .st-key-pr_del)
    # 맨 위: 입력 화면은 '기획안 검토' + 설명, 결과 화면은 그 자리에 기획안 제목 + 첨부 파일 + AI 활용 영역 해시태그
    plan = res[0].get("plan", {}) if res is not None else {}
    head.title(TITLE if res is None else (plan.get("title") or "(제목 없음)"))
    if res is not None and st.session_state.get("pr_id"):
        clear.button("이 기획안 지우기", width="stretch", on_click=_delete, key="pr_del")
    with st.container(key="pr_tagline"):   # key: 제목과의 간격 (BTN_CSS)
        if res is None:
            st.markdown(TAGLINE)
        else:
            # 해시태그는 띄어쓰기 없이 (#AI에이전트), 영역 하나 = 필 하나
            tags = "".join(f'<span class="pr-tag">#{html.escape(a.replace(" ", ""))}</span>' for a in plan.get("areas") or [])
            tags = tags or '<span class="pr-meta">AI 활용 영역 없음</span>'
            st.markdown(f'<div class="pr-tags">{tags}</div><div class="pr-meta">{html.escape(res[1])}</div>',
                        unsafe_allow_html=True)

    if res is None:  # 결과가 나오면 입력 칸은 숨긴다 (다시 하려면 '＋ 새 기획안')
        got = _input()
        if got:
            body, label = got
            with st.spinner("기획안을 읽고 공시·뉴스에서 비슷한 사례를 찾는 중…"):
                try:
                    result = _reviewer().review(body)
                except Exception as e:  # API 키 없음 등
                    st.error(f"검토하지 못했습니다 ({type(e).__name__}). `.env` 의 OPENAI_API_KEY 를 확인해 주십시오.")
                    return
            st.session_state.pr_result = (result, label)
            st.session_state.pr_id = _save(result, label) if result.get("ok") else None
            st.rerun()
        return
    st.divider()
    try:
        _report(*res)
    except Exception as e:  # 결과 모양이 화면 코드와 다름 등 — app.py 의 '공시 데이터 없음' 안내로 새지 않게
        st.error(f"결과를 그리지 못했습니다 ({type(e).__name__}: {e}). '＋ 새 기획안'으로 다시 검토해 주십시오.")
