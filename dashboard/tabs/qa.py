"""
[B·D6] 공시 Q&A (챗봇 화면) — 사업보고서 근거로 답하고, 근거가 없으면 '공시에서 확인되지 않습니다'

render() 를 A 의 app.py 탭에서 부르면 된다. 답변은 rag/qa_chain.py (하이브리드 검색 + LLM, 테스트 15개: 답변 10/10 · 거절 5/5).
대시보드에서 LLM 을 부르는 유일한 곳 (질문마다 답해야 하므로). 분류·수집은 화면에서 하지 않는다.
질문은 한 개씩 따로 답한다 (앞 대화를 기억해 이어서 답하지 않음) — 매번 기업 이름을 넣어 묻는 게 정확하다.
"""
import streamlit as st

EXAMPLES = ["플레이디의 AX Unit은 어떤 조직인가요?",
            "KT나스미디어가 출원하거나 등록한 AI 관련 특허는 무엇인가요?",
            "이노션의 AI 얼라이언스에는 몇 개 기업이 참여했나요?",
            "제일기획의 AI 배너 자동화 솔루션 베리에이드 도입 내용을 알려주세요."]
WELCOME = ("안녕하세요! 광고대행사 5곳(제일기획·이노션·플레이디·인크로스·KT나스미디어)의 **사업보고서·분기보고서**에서 "
           "AI 관련 내용을 찾아 답해 드려요.\n\n공시에 적힌 내용으로만 답하고, 적혀 있지 않으면 모른다고 말씀드려요. "
           "질문할 때 **기업 이름**을 넣어 주시면 더 정확해요.")
REFUSE = "공시에서 확인되지 않습니다. 질문한 내용이 정기공시에 적혀 있지 않거나, 분석 대상 5개사가 아니에요."


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
    return {"role": "assistant", "content": r["answer"], "sources": r["sources"]}


def _show(msg: dict) -> None:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("sources"):
            with st.expander(f"출처 공시 {len(msg['sources'])}건"):
                for s in msg["sources"]:
                    lv = "" if s["ai_level"] is None else f" · AI 단계 L{s['ai_level']}"
                    st.markdown(f"[{s['n']}] {s['corp']} · {s['report']} (접수 {s['report_date']}) · "
                                f"{s['section'].split('>')[-1].strip()}{lv} — [DART 원문]({s['url']})")


def render() -> None:
    msgs = st.session_state.setdefault("qa_msgs", [])
    head, clear = st.columns([5, 1], vertical_alignment="center")
    head.subheader("공시 Q&A")
    if msgs and clear.button("대화 지우기", width="stretch"):
        msgs.clear()
        st.rerun()

    with st.chat_message("assistant"):
        st.markdown(WELCOME)
    picked = None
    if not msgs:  # 첫 화면: 예시 질문
        st.caption("이런 걸 물어볼 수 있어요")
        for col, q in zip(st.columns(2) * 2, EXAMPLES):
            if col.button(q, width="stretch"):
                picked = q
    for m in msgs:
        _show(m)

    q = st.chat_input("공시에 대해 물어보세요 (예: 인크로스 스텔라이즈는 언제 출시됐나요?)") or picked
    if q:
        msgs.append({"role": "user", "content": q})
        _show(msgs[-1])
        with st.chat_message("assistant"), st.spinner("공시를 찾고 답을 쓰는 중…"):
            a = _answer(q)
        msgs.append(a)
        st.rerun()
