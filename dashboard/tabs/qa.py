"""
[B·D6] 공시 Q&A — 사업보고서 근거로 답하고, 근거가 없으면 '공시에서 확인되지 않습니다'

render() 를 A 의 app.py 탭에서 부르면 된다. 답변은 rag/qa_chain.py (하이브리드 검색 + LLM, 테스트 15개: 답변 10/10 · 거절 5/5).
대시보드에서 LLM 을 부르는 유일한 곳 (질문마다 답해야 하므로). 분류·수집은 화면에서 하지 않는다.
"""
import streamlit as st

EXAMPLES = ["플레이디의 AX Unit은 어떤 조직인가요?",
            "KT나스미디어가 출원하거나 등록한 AI 관련 특허는 무엇인가요?",
            "이노션의 AI 얼라이언스에는 몇 개 기업이 참여했나요?",
            "제일기획의 AI 배너 자동화 솔루션 베리에이드 도입 내용을 알려주세요."]


@st.cache_resource(show_spinner="공시 검색기를 준비하는 중…")
def _qa():
    from rag.qa_chain import QA
    return QA()


def render() -> None:
    st.subheader("공시 Q&A — 사업보고서에 뭐라고 적혀 있나")
    st.caption("5개사(제일기획·이노션·플레이디·인크로스·KT나스미디어) 정기공시의 AI 관련 내용을 근거로만 답합니다. "
               "근거가 없으면 답하지 않습니다. 답 옆의 번호는 아래 출처 공시입니다.")
    cols = st.columns(len(EXAMPLES))
    for col, q in zip(cols, EXAMPLES):
        if col.button(q, width="stretch"):
            st.session_state.qa_q = q
    q = st.text_input("질문", key="qa_q", placeholder="예: 인크로스 스텔라이즈는 언제 출시됐나요?")
    if not q:
        return
    with st.spinner("공시를 찾고 답을 쓰는 중…"):
        try:
            r = _qa().ask(q)
        except Exception as e:  # API 키 없음 등
            st.error(f"답변을 만들지 못했습니다: {type(e).__name__}. `.env` 의 OPENAI_API_KEY 를 확인하세요.")
            return
    if not r["answerable"]:
        st.info("공시에서 확인되지 않습니다. 질문한 내용이 정기공시에 적혀 있지 않거나, 분석 대상 5개사가 아닙니다.")
        return
    st.markdown(r["answer"])
    st.markdown("**출처**")
    for s in r["sources"]:
        lv = "" if s["ai_level"] is None else f" · AI 단계 L{s['ai_level']}"
        st.markdown(f"[{s['n']}] {s['corp']} · {s['report']} (접수 {s['report_date']}) · "
                    f"{s['section'].split('>')[-1].strip()}{lv} — [DART 원문]({s['url']})")
