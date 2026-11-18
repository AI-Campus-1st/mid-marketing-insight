"""
[A·D7] 기획안 검토 화면 — 기획안을 올리면 공시·뉴스에서 비슷한 AI 활용 사례를 찾아 리포트 한 장으로 보여 준다

사용자: 5개사 밖 중견 대행사 신사업기획팀장 (메인 페르소나, 이슈 #28) — "우리 기획과 같은 일을 경쟁사가 실제로 했나?"
- 입력: 파일(txt·md·pdf·docx) 또는 붙여 넣기. 예시 기획안 버튼
- 결과: 요약 숫자(같은/인접 영역 · 공시/뉴스) → 왼쪽 기획안 문서(사례가 붙은 문단을 칠함) · 오른쪽 그 문단의 사례 카드 → 출처
  (문단은 rag/plan_review.split_paras, 사례 ↔ 문단 연결은 LLM 의 para — plan_v4)
- 챗봇과 분리했고 이어지는 질문은 받지 않는다 (rag/plan_review.py 머리말, decisions.md Day 7)
- 결과는 세션에만 둔다 (기획안은 회사 내부 문서일 수 있어 파일로 저장하지 않음)
"""
import html
import io
import re
import zipfile

import streamlit as st

from config import settings
from dashboard.theme import LEVEL_NAME

TITLE = "기획안 검토"
TAGLINE = ("AI 신사업 계획이나 마케팅 기획안을 올리면, **같은 일을 이미 한 곳이 있는지** 사업보고서와 뉴스에서 찾아 드려요. "
           "공시로 확인된 사례와 뉴스에만 나온 사례를 나눠 보여 줘요.")
GUIDE = """- **같은 영역**: 기획과 같은 일(같은 상품·서비스·마케팅 업무)에 AI 를 쓴 사례예요.
- **인접 영역**: 비슷한 기술을 다른 일에 썼거나, 조직 신설·제휴·투자처럼 준비 단계인 사례예요.
- 공시는 5개사(제일기획 · 이노션 · 플레이디 · 인크로스 · KT나스미디어) 사업·반기·분기보고서, 뉴스는 제공 뉴스 중 광고·마케팅 관련 2,492건이에요.
- 기획의 결과(효과·성공 여부)를 예측하지 않아요. 근거에 적힌 사례만 보여 줘요."""
EXAMPLE = ("[신사업 제안] AI 검색 최적화(GEO) 컨설팅 상품 출시\n"
           "배경: 소비자가 챗GPT 같은 생성형 AI 검색으로 브랜드를 찾기 시작하면서 기존 검색광고만으로는 노출이 줄고 있음.\n"
           "제안: 광고주 브랜드가 생성형 AI 답변에 우선 노출되도록 진단·콘텐츠 최적화·모니터링을 묶은 GEO 컨설팅 상품을 출시.\n"
           "필요 역량: AI 답변 노출 진단 도구(자체 개발 또는 외부 솔루션 제휴), 콘텐츠 제작 인력.")
FILE_TYPES = ["txt", "md", "pdf", "docx"]
MIN_CHARS = 30


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
.pr-para.hl-adj { background: #f1f4f8; border-left-color: #9aa8bb; }      /* 인접 영역 사례만 붙은 문단 */
.pr-card { background: #ffffff; border: 1px solid #b4d0f1; border-radius: 12px; padding: .65rem .85rem; margin: .1rem 0 .55rem;
  box-shadow: 0 1px 2px rgba(16,24,40,.04), 0 4px 12px rgba(16,24,40,.05); position: relative; font-size: .86rem; line-height: 1.5; }
.pr-card::before { content: ""; position: absolute; left: -1.05rem; top: 1.1rem; width: 1.05rem; border-top: 2px solid #2a78d6; }  /* 문단과 잇는 선 */
.pr-card.adj { border-color: #d5dbe5; } .pr-card.adj::before { border-top-color: #9aa8bb; }
.pr-card .head { font-size: .74rem; font-weight: 700; color: #2a78d6; margin-bottom: .3rem; }
.pr-card.adj .head { color: #6b7686; }
.pr-item { margin: .3rem 0; }
.pr-item .tag { display: inline-block; font-size: .7rem; font-weight: 700; border-radius: 5px; padding: 0 .35rem; margin-right: .3rem; }
.pr-item .tag.d { background: #e3eefc; color: #1d5fae; } .pr-item .tag.n { background: #fdf0e1; color: #a35a00; }
.pr-item .tag.adj { background: #eef1f5; color: #5b6676; }
.pr-item a { font-size: .74rem; color: #6b7686; text-decoration: none; margin-left: .25rem; }
.pr-col-head { font-size: .8rem; font-weight: 700; color: #6b7686; letter-spacing: .02em; margin-bottom: .6rem; }
</style>"""


def _items(r: dict) -> dict[int, list[dict]]:
    """문단 번호 → 그 문단의 사례 [{text, n, same, kind(d|n), url}] (0 = 기획 전체). 같은 영역 먼저, 공시 먼저"""
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
    tag = f'<span class="tag {x["kind"]}">{src}</span>' + ("" if x["same"] else '<span class="tag adj">인접</span>')
    link = f'<a href="{html.escape(x["url"])}" target="_blank">{src} {x["n"]} ↗</a>' if x["url"] else ""
    return f'<div class="pr-item">{tag}{body}{link}</div>'


def _card(items: list[dict]) -> str:
    same = sum(x["same"] for x in items)
    head = (f"같은 영역 {same}건" + (f" · 인접 {len(items) - same}건" if len(items) > same else "")) if same else f"인접 영역 {len(items)}건"
    return f'<div class="pr-card{"" if same else " adj"}"><div class="head">{head}</div>{"".join(_item_html(x) for x in items)}</div>'


def _summary(r: dict) -> None:
    d, n = r["disclosure"], r["news"]
    corps = lambda col: len({t.split(":")[0].strip() for t, *_ in col})  # noqa: E731
    k = st.columns(4)
    k[0].metric("같은 영역 · 공시", f"{corps(d['same'])}곳", help="기획과 같은 일에 AI 를 쓴 5개사 사례 (사업보고서 기준)")
    k[1].metric("같은 영역 · 뉴스", f"{len(n['same'])}건", help="보도·회사 발표 — 5개사 밖 회사 포함")
    k[2].metric("인접 영역 · 공시", f"{corps(d['adjacent'])}곳", help="비슷한 기술을 다른 일에 썼거나 조직·제휴 단계")
    k[3].metric("인접 영역 · 뉴스", f"{len(n['adjacent'])}건")
    for label, col in (("공시", d), ("뉴스", n)):
        if col["summary"]:
            st.markdown(f"**{label}** · {col['summary']}")


def _report(r: dict, label: str) -> None:
    p = r["plan"]
    st.markdown(f"##### {p['title'] or '(제목 없음)'}")
    st.caption(f"{label} · AI 활용 영역: {', '.join(p['areas']) or '없음'}")
    if not r["ok"]:
        st.warning(r["message"] + " AI 를 어디에 어떻게 쓰려는지가 드러나게 써 주세요.")
        return
    _summary(r)
    if r["message"]:
        st.caption(r["message"])
    by = _items(r)
    st.markdown(DOC_CSS, unsafe_allow_html=True)
    with st.container(key="pr_doc", gap=None):
        head_l, head_r = st.columns([3, 2], gap="large")
        head_l.markdown('<div class="pr-col-head">📄 기획안</div>', unsafe_allow_html=True)
        head_r.markdown('<div class="pr-col-head">🔎 비슷한 사례</div>', unsafe_allow_html=True)
        if by.get(0):  # 특정 문단이 아니라 기획 전체와 관련된 사례는 문서 위에
            l0, r0 = st.columns([3, 2], gap="large")
            l0.markdown('<div class="pr-whole">기획 전체와 관련된 사례 (특정 문단과 맞지 않음) →</div>', unsafe_allow_html=True)
            r0.markdown(_card(by[0]), unsafe_allow_html=True)
        n = len(r["paras"])
        for i, text in enumerate(r["paras"], 1):
            key = "pr_row_" + ("only" if n == 1 else "first" if i == 1 else "last" if i == n else str(i))
            with st.container(key=key, gap=None):
                left, right = st.columns([3, 2], gap="large")
                items = by.get(i, [])
                cls = "hl" if any(x["same"] for x in items) else "hl-adj" if items else ""
                left.markdown(f'<div class="pr-para {cls}">{html.escape(text)}</div>', unsafe_allow_html=True)
                if items:
                    right.markdown(_card(items), unsafe_allow_html=True)
    st.caption("파란 문단 = 같은 영역 사례가 있는 부분 · 회색 문단 = 인접 영역 사례만 있는 부분 · "
               "문서는 올린 파일에서 읽은 글자로 다시 그린 것이라 원본의 표·그림·글꼴은 보이지 않아요.")
    d, n = r["disclosure"], r["news"]
    if d["sources"] or n["sources"]:
        lbl = " · ".join(x for x in [f"공시 {len(d['sources'])}건" if d["sources"] else "",
                                     f"뉴스 {len(n['sources'])}건" if n["sources"] else ""] if x)
        with st.expander(f"출처 {lbl}"):
            for s in d["sources"]:
                lv = "" if s["ai_level"] is None else f" · {LEVEL_NAME[f'L{s['ai_level']}']}"
                st.markdown(f"[공시 {s['n']}] **{s['corp']}** {s['report']} · 접수 {s['report_date']} · "
                            f"{s['section'].split('>')[-1].strip()}{lv} — [DART 원문]({s['url']})")
            for s in n["sources"]:
                st.markdown(f"[뉴스 {s['n']}] **{s['media']}** {s['date']} · {s['title']} — [기사 원문]({s['url']})")


def render() -> None:
    st.title(TITLE)
    st.markdown(TAGLINE)
    with st.expander("결과 읽는 법", icon=":material/info:"):
        st.markdown(GUIDE)

    up = st.file_uploader("기획안 파일 (txt · md · pdf · docx)", type=FILE_TYPES, key="pr_file")
    text = st.text_area("또는 기획 내용을 붙여 넣으세요", key="pr_text", height=160,
                        placeholder="예: [신사업 제안] AI 검색 최적화(GEO) 컨설팅 상품 출시 — 배경, 제안, 필요 역량…")
    go, ex, _ = st.columns([1, 1.4, 3])
    run = go.button("사례 찾기", type="primary", width="stretch", icon=":material/search:")
    if ex.button("예시 기획안으로 해 보기", width="stretch"):
        body, label = EXAMPLE, "예시: GEO 컨설팅 상품 기획안"
    elif run:
        body, label = text.strip(), "붙여 넣은 글"
        if up is not None:
            try:
                body = _file_text(up).strip()
            except Exception as e:  # 깨진 파일 등
                st.error(f"'{up.name}' 을(를) 읽지 못했어요 ({type(e).__name__}). txt·md·pdf·docx 파일인지 확인해 주세요.")
                return
            if not body:
                st.error(f"'{up.name}' 에서 글자를 찾지 못했어요. 스캔한 그림 PDF 는 읽을 수 없어요.")
                return
            label = f"📎 {up.name}"
        if len(body) < MIN_CHARS:
            st.info("기획안 파일을 올리거나 기획 내용을 몇 줄 이상 붙여 넣어 주세요.")
            return
    else:
        body = None
    if body:
        with st.spinner("기획안을 읽고 공시·뉴스에서 비슷한 사례를 찾는 중…"):
            try:
                st.session_state["pr_result"] = (_reviewer().review(body), label)
            except Exception as e:  # API 키 없음 등
                st.error(f"사례를 찾지 못했어요 ({type(e).__name__}). `.env` 의 OPENAI_API_KEY 를 확인해 주세요.")
                return
    if "pr_result" in st.session_state and "paras" not in st.session_state["pr_result"][0]:
        del st.session_state["pr_result"]   # 옛 버전 코드로 만든 결과 (문단 정보 없음) — 다시 찾아야 함
    if "pr_result" in st.session_state:
        st.divider()
        try:
            _report(*st.session_state["pr_result"])
        except Exception as e:  # 결과 모양이 화면 코드와 다름 등 — app.py 의 '공시 데이터 없음' 안내로 새지 않게
            del st.session_state["pr_result"]
            st.error(f"결과를 그리지 못했어요 ({type(e).__name__}: {e}). '사례 찾기'를 다시 눌러 주세요.")
