"""
[B·D6] 광고대행사 AI 공시 챗봇 (공시 Q&A) — 정기공시(사업·반기·분기보고서) 근거로 답하고, 근거가 없으면 '공시에서 확인되지 않습니다'

render() 를 A 의 app.py 탭에서 부르면 된다. 답변은 rag/qa_chain.py (하이브리드 검색 + LLM, 테스트 15개: 답변 10/10 · 거절 5/5).
대시보드에서 LLM 을 부르는 두 곳 중 하나 (질문마다 답해야 하므로, 다른 하나는 기획안 검토). 분류·수집·임베딩 만들기는 화면에서 하지 않는다 (rag/build_index.py 로 미리).
질문은 한 개씩 따로 답한다 (앞 대화를 기억해 이어서 답하지 않음) — 매번 기업 이름을 넣어 묻는 게 정확하다.
대화 기록: 답이 나올 때마다 data/processed/chat_history/<대화id>.json 에 저장(저장소 제외), 사이드바 '지난 대화'에서 다시 연다.
"""
import json
from datetime import datetime

import streamlit as st

from config import settings

from dashboard import style as ui
from dashboard.theme import LEVEL_NAME

TITLE = "광고대행사 AI 공시 챗봇"
TAGLINE = "광고대행사가 말한 AI, **공시에도 '했다'고 적혀 있을까?** 정기공시(사업·반기·분기보고서)에서 찾아 근거와 함께 답해 드립니다."
WELCOME = """**제일기획 · 이노션 · 플레이디 · 인크로스 · KT나스미디어** 5곳의 사업·반기·분기보고서(2023-10 ~ 2026-10)에서 찾아봅니다.

- 답마다 **어느 보고서에 적혀 있는지** 출처와 DART 원문 링크를 붙입니다.
- 공시에 없는 내용은 지어내지 않고 **"공시에서 확인되지 않습니다"** 라고 답합니다.
- 질문에 **회사 이름**을 넣어 주십시오. 질문마다 따로 답합니다."""
# 질문 종류별 예시 (첫 화면 버튼)
EXAMPLES = {"자체 솔루션": "제일기획의 AI 배너 자동화 솔루션 베리에이드 도입 내용을 알려주세요.",
            "연구개발·특허": "KT나스미디어가 출원하거나 등록한 AI 관련 특허는 무엇인가요?",
            "AI 조직": "플레이디의 AX Unit은 어떤 조직인가요?",
            "제휴·협력": "이노션의 AI 얼라이언스에는 몇 개 기업이 참여했나요?"}
HIST_DIR = settings.DATA_DIR / "processed" / "chat_history"
HIST_SHOW = 30  # 사이드바에 보여 줄 지난 대화 수
REFUSE = "**공시에서 확인되지 않습니다.**\n\n질문한 내용이 정기공시에 적혀 있지 않거나, 분석 대상 5개사가 아닙니다."


@st.cache_resource(show_spinner="공시 검색기를 준비하는 중…", max_entries=1)  # 최신 코드의 검색기 하나만 (B 리뷰 PR #29)
def _qa_cached(code_version: str):
    # 모듈도 다시 읽는다 (Streamlit 은 rag/ 의 바뀐 코드를 다시 읽지 않음 — dashboard/tabs/plan_review.py 와 같음)
    import importlib

    import rag.qa_chain
    import rag.retriever
    for m in (rag.retriever, rag.qa_chain):
        importlib.reload(m)
    return rag.qa_chain.QA()


def _qa():
    """검색기는 한 번만 만든다. 단 rag/ 코드가 바뀌면(파일 수정 시각) 새로 만든다
    — 서버를 켠 채 코드를 고치면 옛 객체가 남아 새 메서드가 없다는 AttributeError 가 났음"""
    rag = settings.ROOT / "rag"
    return _qa_cached("|".join(str((rag / f).stat().st_mtime_ns) for f in ("qa_chain.py", "retriever.py")))


def _answer(q: str, chunk_ids: list[str] | None = None) -> dict:
    """질문 → 대화에 넣을 답 메시지 {role, content, sources}. chunk_ids: 다른 화면이 이미 찾은 근거 문단 (기획안 검토 버튼)"""
    try:
        r = _qa().ask_with_chunks(q, chunk_ids) if chunk_ids else _qa().ask(q)
    except Exception as e:  # API 키 없음 등
        return {"role": "assistant", "content": f"답변을 만들지 못했습니다 ({type(e).__name__}). `.env` 의 OPENAI_API_KEY 를 확인해 주십시오.",
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
                    lv = "" if s["ai_level"] is None else f" · AI 단계: {LEVEL_NAME[f'L{s['ai_level']}']}"
                    st.markdown(f"[{s['n']}] **{s['corp']}** {s['report']} · 접수 {s['report_date']} · "
                                f"{s['section'].split('>')[-1].strip()}{lv} — [DART 원문]({s['url']})")


# ---------- 대화 기록 (파일 하나 = 대화 하나) ----------
def _save() -> None:
    msgs = st.session_state.qa_msgs
    if not msgs:
        return
    cid = st.session_state.setdefault("qa_id", datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    HIST_DIR.mkdir(parents=True, exist_ok=True)
    first = next(m["content"] for m in msgs if m["role"] == "user")
    (HIST_DIR / f"{cid}.json").write_text(json.dumps(
        {"id": cid, "updated": datetime.now().strftime("%Y-%m-%d %H:%M"), "title": first, "msgs": msgs},
        ensure_ascii=False, indent=1), encoding="utf-8")


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
    st.session_state.qa_id, st.session_state.qa_msgs = c["id"], list(c["msgs"])


def _new() -> None:
    st.session_state.pop("qa_id", None)
    st.session_state.qa_msgs = []


def _delete() -> None:
    cid = st.session_state.get("qa_id")
    if cid:
        (HIST_DIR / f"{cid}.json").unlink(missing_ok=True)
    _new()


def _sidebar() -> None:
    with st.sidebar:
        st.button("＋ 새 대화", width="stretch", type="primary", on_click=_new)
        convs = _history()
        st.markdown("**지난 대화**")
        if not convs:
            st.caption("아직 없습니다. 질문하면 여기에 저장됩니다.")
        cur = st.session_state.get("qa_id")
        tips = []   # 잘린 제목에 마우스를 대면 전체 제목 — 메뉴 설명과 같은 말풍선·정렬 (style.hover_tips)
        for c in convs[:HIST_SHOW]:
            title = c["title"] if len(c["title"]) <= 24 else c["title"][:24] + "…"
            now = "cur_" if c["id"] == cur else ""   # 지금 보고 있는 대화: key 에 cur_ → 둥근 파란 테두리 (style.py)
            st.button(f"{title}  \n{c['updated']}", key=f"hist_{now}{c['id']}", width="stretch",
                      on_click=_open, args=(c,))
            if title != c["title"]:   # 제목이 잘렸을 때만 전체 제목 말풍선
                tips.append((f"div.st-key-hist_{now}{c['id']}", c["title"]))
        ui.hover_tips(tips, shift=".5rem, 4px")


def render() -> None:
    msgs = st.session_state.setdefault("qa_msgs", [])
    _sidebar()
    head, clear = st.columns([5, 1], vertical_alignment="bottom")
    head.title(TITLE)
    if msgs:
        clear.button("이 대화 지우기", width="stretch", on_click=_delete)
    st.markdown(TAGLINE)

    pending = st.session_state.pop("qa_pending", None)   # 다른 화면(기획안 검토)에서 넘어온 질문 — 바로 답한다
    picked, picked_ids = (pending.get("q"), pending.get("chunk_ids")) if isinstance(pending, dict) else (pending, None)
    if not msgs and not picked:  # 첫 화면: 소개 + 질문 종류별 예시
        with st.chat_message("assistant"):
            st.markdown(WELCOME)
        st.markdown("##### 이런 걸 물어볼 수 있습니다")
        for col, (kind, q) in zip(st.columns(2) * 2, EXAMPLES.items()):
            with col.container(border=True):
                st.caption(kind)
                if st.button(q, key=f"ex_{kind}", width="stretch"):
                    picked = q
    for m in msgs:
        _show(m)

    q = st.chat_input("회사 이름을 넣어 물어보십시오 (예: 인크로스 스텔라이즈는 언제 출시됐나요?)") or picked
    if q:
        msgs.append({"role": "user", "content": q})
        _show(msgs[-1])
        with st.chat_message("assistant"), st.spinner("공시를 찾고 답을 쓰는 중…"):
            a = _answer(q, picked_ids if q == picked else None)
        msgs.append(a)
        _save()
        st.rerun()
