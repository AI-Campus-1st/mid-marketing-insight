"""
[A·D7] 기획안 검토 화면 — 기획안을 올리면 공시·뉴스에서 비슷한 AI 활용 사례를 찾아 리포트 한 장으로 보여 준다

사용자: 5개사 밖 중견 대행사 신사업기획팀장 (메인 페르소나, 이슈 #28) — "우리 기획과 같은 일을 경쟁사가 실제로 했나?"
- 입력: 파일(txt·md·pdf·docx) 또는 붙여 넣기. 예시 기획안 버튼
- 결과: 기획 요약 → 공시로 확인된 사례(같은 영역 / 인접 영역) → 뉴스에 나온 사례 → 출처
- 챗봇과 분리했고 이어지는 질문은 받지 않는다 (rag/plan_review.py 머리말, decisions.md Day 7)
- 결과는 세션에만 둔다 (기획안은 회사 내부 문서일 수 있어 파일로 저장하지 않음)
"""
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
    from rag.plan_review import PlanReview
    return PlanReview()


def _reviewer():
    """rag/ 코드가 바뀌면(파일 수정 시각) 새로 만든다 — 서버를 켠 채 코드를 고치면 옛 객체가 남는 문제 (qa.py 와 같음)"""
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


def _section(col: dict, ref: str) -> None:
    """한 칸(공시 또는 뉴스): 요약 → 같은 영역 → 인접 영역"""
    if col["summary"]:
        st.markdown(f"**{col['summary']}**")
    for head, rows in (("✅ 같은 영역 — 기획과 같은 일에 AI 를 쓴 사례", col["same"]),
                       ("↪ 참고: 인접 영역 — 비슷한 기술을 다른 일에 쓴 사례", col["adjacent"])):
        if rows:
            st.markdown(f"**{head}**\n" + "\n".join(f"- {t} [{ref.format(n)}]" for t, n in rows))


def _report(r: dict, label: str) -> None:
    p = r["plan"]
    st.markdown(f"##### 기획안에서 읽은 내용 — {p['title'] or '(제목 없음)'}")
    st.caption(f"{label} · AI 활용 영역: {', '.join(p['areas']) or '없음'} · 검색어: {', '.join(p['queries']) or '없음'}")
    if not r["ok"]:
        st.warning(r["message"] + " AI 를 어디에 어떻게 쓰려는지가 드러나게 써 주세요.")
        return
    d, n = r["disclosure"], r["news"]
    with st.container(border=True):
        st.markdown("##### 📑 공시로 확인된 사례")
        if d["same"] or d["adjacent"]:
            _section(d, "{}")
        else:
            st.markdown(r["message"])
    if n["same"] or n["adjacent"]:
        with st.container(border=True):
            st.markdown("##### 📰 뉴스에 나온 사례")
            st.caption("언론 보도·회사 발표 — 공시보다 한 단계 약한 근거예요")
            _section(n, "뉴스 {}")
    if d["sources"] or n["sources"]:
        label = " · ".join(x for x in [f"공시 {len(d['sources'])}건" if d["sources"] else "",
                                       f"뉴스 {len(n['sources'])}건" if n["sources"] else ""] if x)
        with st.expander(f"출처 {label}"):
            for s in d["sources"]:
                lv = "" if s["ai_level"] is None else f" · {LEVEL_NAME[f'L{s['ai_level']}']}"
                st.markdown(f"[{s['n']}] **{s['corp']}** {s['report']} · 접수 {s['report_date']} · "
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
    if "pr_result" in st.session_state:
        st.divider()
        _report(*st.session_state["pr_result"])
