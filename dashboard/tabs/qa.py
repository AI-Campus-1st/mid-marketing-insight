"""
[B·D6] AI 실행 확인 챗봇 (공시 Q&A) — 사업보고서 근거로 답하고, 근거가 없으면 '공시에서 확인되지 않습니다'

render() 를 A 의 app.py 탭에서 부르면 된다. 답변은 rag/qa_chain.py (하이브리드 검색 + LLM, 테스트 15개: 답변 10/10 · 거절 5/5).
대시보드에서 LLM 을 부르는 유일한 곳 (질문마다 답해야 하므로). 분류·수집은 화면에서 하지 않는다.
질문은 한 개씩 따로 답한다 (앞 대화를 기억해 이어서 답하지 않음) — 매번 기업 이름을 넣어 묻는 게 정확하다.
"""
import streamlit as st

from dashboard.theme import LEVEL_NAME

TITLE = "AI 실행 확인 챗봇"
TAGLINE = "광고대행사가 말한 AI, **공시에도 '했다'고 적혀 있을까?** 사업보고서에서 찾아 근거와 함께 답해 드려요."
WELCOME = """**제일기획 · 이노션 · 플레이디 · 인크로스 · KT나스미디어** 5곳의 사업·반기·분기보고서(2023-10 ~ 2026-10)에서 찾아봐요.

- 답마다 **어느 보고서에 적혀 있는지** 출처와 DART 원문 링크를 붙여요.
- 공시에 없는 내용은 지어내지 않고 **"공시에서 확인되지 않습니다"** 라고 답해요.
- 질문에 **회사 이름**을 넣어 주세요. 질문마다 따로 답해요."""
# 질문 종류별 예시 (첫 화면 버튼)
EXAMPLES = {"자체 솔루션": "제일기획의 AI 배너 자동화 솔루션 베리에이드 도입 내용을 알려주세요.",
            "연구개발·특허": "KT나스미디어가 출원하거나 등록한 AI 관련 특허는 무엇인가요?",
            "AI 조직": "플레이디의 AX Unit은 어떤 조직인가요?",
            "제휴·협력": "이노션의 AI 얼라이언스에는 몇 개 기업이 참여했나요?"}
REFUSE = "**공시에서 확인되지 않습니다.**\n\n질문한 내용이 정기공시에 적혀 있지 않거나, 분석 대상 5개사가 아니에요."


@st.cache_resource(show_spinner="공시 검색기를 준비하는 중…")
def _qa():
    from rag.qa_chain import QA
    return QA()


def _answer(q: str) -> dict:
    """질문 → 대화에 넣을 답 메시지 {role, content, sources}"""
    try:
        r = _qa().ask(q)
    except Exception as e:  # API 키 없음 등
        return {"role": "assistant", "content": f"답변을 만들지 못했어요 ({type(e).__name__}). `.env` 의 OPENAI_API_KEY 를 확인해 주세요.",
                "sources": []}
    if not r["answerable"]:
        return {"role": "assistant", "content": REFUSE, "sources": []}
    summary, _, points = r["answer"].partition("\n\n")
    return {"role": "assistant", "content": f"**{summary}**\n\n{points}", "sources": r["sources"]}


def _show(msg: dict) -> None:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("sources"):
            with st.expander(f"출처 공시 {len(msg['sources'])}건"):
                for s in msg["sources"]:
                    lv = "" if s["ai_level"] is None else f" · {LEVEL_NAME[f'L{s['ai_level']}']}"
                    st.markdown(f"[{s['n']}] **{s['corp']}** {s['report']} · 접수 {s['report_date']} · "
                                f"{s['section'].split('>')[-1].strip()}{lv} — [DART 원문]({s['url']})")


def render() -> None:
    msgs = st.session_state.setdefault("qa_msgs", [])
    head, clear = st.columns([5, 1], vertical_alignment="bottom")
    head.title(TITLE)
    if msgs and clear.button("대화 지우기", width="stretch"):
        msgs.clear()
        st.rerun()
    st.markdown(TAGLINE)

    picked = None
    if not msgs:  # 첫 화면: 소개 + 질문 종류별 예시
        with st.chat_message("assistant"):
            st.markdown(WELCOME)
        st.markdown("##### 이런 걸 물어볼 수 있어요")
        for col, (kind, q) in zip(st.columns(2) * 2, EXAMPLES.items()):
            with col.container(border=True):
                st.caption(kind)
                if st.button(q, key=f"ex_{kind}", width="stretch"):
                    picked = q
    for m in msgs:
        _show(m)

    q = st.chat_input("회사 이름을 넣어 물어보세요 (예: 인크로스 스텔라이즈는 언제 출시됐나요?)") or picked
    if q:
        msgs.append({"role": "user", "content": q})
        _show(msgs[-1])
        with st.chat_message("assistant"), st.spinner("공시를 찾고 답을 쓰는 중…"):
            a = _answer(q)
        msgs.append(a)
        st.rerun()
