"""
[B·D6 → A·D7] 광고대행사 AI 공시 챗봇 (공시 Q&A) — 사업보고서 근거로 답하고, 근거가 없으면 '공시에서 확인되지 않습니다'

D7 추가: ① 뉴스 보조 근거 — 공시 답 아래에 '뉴스에서는' 칸을 따로 (공시가 우선, rag/qa_chain.py qa_v4)
         ② 기획안 사례 찾기 — 입력창에 기획안 파일(txt·md·pdf·docx)을 붙이거나 긴 글(PLAN_MIN_CHARS 자 이상)을 넣으면
            기획의 AI 활용 영역을 읽고 공시·뉴스에서 비슷한 사례를 찾아 준다 (QA.ask_plan)
         ③ 기획안 후속 질문 — 기획안을 올린 대화의 다음 질문은 그 기획안·앞서 찾은 근거로 이어서 답한다 (QA.ask_followup)
            기획 상태는 기획안 답 메시지의 plan_state 에 저장 → 지난 대화를 다시 열어도 이어진다. 새 주제는 '＋ 새 대화'

render() 를 A 의 app.py 탭에서 부르면 된다. 답변은 rag/qa_chain.py (하이브리드 검색 + LLM, 테스트 15개: 답변 10/10 · 거절 5/5).
대시보드에서 LLM 을 부르는 유일한 곳 (질문마다 답해야 하므로). 분류·수집은 화면에서 하지 않는다.
질문은 한 개씩 따로 답한다 (앞 대화를 기억해 이어서 답하지 않음) — 매번 기업 이름을 넣어 묻는 게 정확하다.
대화 기록: 답이 나올 때마다 data/processed/chat_history/<대화id>.json 에 저장(저장소 제외), 사이드바 '지난 대화'에서 다시 연다.
"""
import io
import json
import re
import zipfile
from datetime import datetime

import streamlit as st

from config import settings

from dashboard.theme import LEVEL_NAME

TITLE = "광고대행사 AI 공시 챗봇"
TAGLINE = ("광고대행사가 말한 AI, **공시에도 '했다'고 적혀 있을까?** 사업보고서에서 먼저 찾고, 관련 뉴스도 함께 보여 드려요. "
           "**기획안을 올리면** 비슷한 AI 활용 사례를 찾아 드려요.")
WELCOME = """**제일기획 · 이노션 · 플레이디 · 인크로스 · KT나스미디어** 5곳의 사업·반기·분기보고서(2023-10 ~ 2026-10)에서 찾아봐요.

- 답마다 **어느 보고서에 적혀 있는지** 출처와 DART 원문 링크를 붙여요.
- 공시에 없는 내용은 지어내지 않고 **"공시에서 확인되지 않습니다"** 라고 답해요.
- 관련 **뉴스 기사**(제공 뉴스 중 광고·마케팅 관련 2,492건)는 공시 답 아래 **'뉴스에서는'** 칸에 따로 보여 줘요. 보도·회사 발표라서 공시보다 한 단계 약한 근거예요.
- 입력창의 📎 로 **기획안**(txt·md·pdf·docx)을 붙이거나 기획 내용을 길게 붙여 넣으면, 비슷한 AI 활용 사례를 공시·뉴스에서 찾아 드려요.
- 질문에 **회사 이름**을 넣어 주세요. 질문마다 따로 답해요."""
# 질문 종류별 예시 (첫 화면 버튼)
EXAMPLES = {"자체 솔루션": "제일기획의 AI 배너 자동화 솔루션 베리에이드 도입 내용을 알려주세요.",
            "연구개발·특허": "KT나스미디어가 출원하거나 등록한 AI 관련 특허는 무엇인가요?",
            "AI 조직": "플레이디의 AX Unit은 어떤 조직인가요?",
            "제휴·협력": "이노션의 AI 얼라이언스에는 몇 개 기업이 참여했나요?",
            "5개사 밖 (뉴스)": "HSAD의 GEO 사업은 어떤가요?"}
# 기획안 사례 찾기 예시 (첫 화면 버튼) — 신사업기획팀장(메인 사용자) 관점
PLAN_EXAMPLE = ("[신사업 제안] AI 검색 최적화(GEO) 컨설팅 상품 출시\n"
                "배경: 소비자가 챗GPT 같은 생성형 AI 검색으로 브랜드를 찾기 시작하면서 기존 검색광고만으로는 노출이 줄고 있음.\n"
                "제안: 광고주 브랜드가 생성형 AI 답변에 우선 노출되도록 진단·콘텐츠 최적화·모니터링을 묶은 GEO 컨설팅 상품을 출시.\n"
                "필요 역량: AI 답변 노출 진단 도구(자체 개발 또는 외부 솔루션 제휴), 콘텐츠 제작 인력.")
PLAN_LABEL = "AI 검색 최적화(GEO) 컨설팅 상품 출시 기획안 — 비슷한 사례 찾기"
PLAN_MIN_CHARS = 200          # 이보다 긴 입력은 질문이 아니라 기획안으로 본다
FILE_TYPES = ["txt", "md", "pdf", "docx"]
HIST_DIR = settings.DATA_DIR / "processed" / "chat_history"
HIST_SHOW = 30  # 사이드바에 보여 줄 지난 대화 수
REFUSE = "**공시에서 확인되지 않습니다.**\n\n질문한 내용이 정기공시에 적혀 있지 않거나, 분석 대상 5개사가 아니에요."
REFUSE_SHORT = "**공시에서 확인되지 않습니다.** 질문한 내용이 정기공시에 적혀 있지 않거나, 분석 대상 5개사가 아니에요."


@st.cache_resource(show_spinner="공시·뉴스 검색기를 준비하는 중…", max_entries=1)  # 최신 코드의 검색기 하나만 (B 리뷰 PR #29)
def _qa_cached(code_version: str):
    from rag.qa_chain import QA
    return QA()


def _qa():
    """검색기는 한 번만 만든다. 단 rag/ 코드가 바뀌면(파일 수정 시각) 새로 만든다
    — 서버를 켠 채 코드를 고치면 옛 객체가 남아 새 메서드(ask_plan)가 없다는 AttributeError 가 났음"""
    rag = settings.ROOT / "rag"
    version = "|".join(str((rag / f).stat().st_mtime_ns) for f in ("qa_chain.py", "news_retriever.py", "retriever.py"))
    return _qa_cached(version)


def _bold_first(text: str) -> str:
    """'결론\n\n- 줄…' → 결론만 굵게 (결론이 없으면 목록만)"""
    head, sep, rest = text.partition("\n\n")
    if head.startswith("**"):  # 결론 없이 소제목(같은 영역 등)부터 시작
        return text
    if not sep:
        return text if text.startswith("- ") else f"**{text}**"
    return f"**{head}**\n\n{rest}"


def _plan_state(msgs: list) -> tuple[dict | None, str]:
    """이 대화에서 가장 최근 기획안 상태와 직전 답 글 (후속 질문용)"""
    state = next((m["plan_state"] for m in reversed(msgs) if m.get("plan_state")), None)
    prev = next((m["content"] for m in reversed(msgs) if m["role"] == "assistant"), "")
    return state, prev


def _answer(q: str, plan: bool = False, state: dict | None = None, prev: str = "") -> dict:
    """질문(또는 기획안 글) → 대화에 넣을 답 메시지 {role, content, sources, news_sources[, plan_state]}"""
    try:
        if state is not None:
            r = _qa().ask_followup(q, state, prev)
        else:
            r = _qa().ask_plan(q) if plan else _qa().ask(q)
    except Exception as e:  # API 키 없음 등
        return {"role": "assistant", "content": f"답변을 만들지 못했어요 ({type(e).__name__}). `.env` 의 OPENAI_API_KEY 를 확인해 주세요.",
                "sources": []}
    parts = []
    if state is not None:
        parts.append(f":gray[📎 기획안 '{state['plan'].get('title', '')}' 에 이어서 답해요 · 예측은 하지 않고 근거에 적힌 내용만 · 새 주제는 ＋ 새 대화]")
        if r["answerable"] or r["answer"] != "공시에서 확인되지 않습니다.":  # 거절이 아니면 (예측·우려 안내 문장 포함)
            parts.append(_bold_first(r["answer"]))
        elif r.get("news_answer"):
            parts.append("**공시 근거에서는 찾지 못했어요.**")
        else:
            parts.append("**앞서 찾은 사례와 공시·뉴스에서 답할 근거를 찾지 못했어요.** 질문을 바꾸거나 회사 이름을 넣어 보세요.")
    elif plan:
        p = r["plan"]
        parts.append(f"**기획안에서 읽은 내용** — {p['title'] or '(제목 없음)'}  \n"
                     f":gray[AI 활용 영역: {', '.join(p['areas']) or '없음'} · 검색어: {', '.join(p['queries']) or '없음'}]")
        parts.append("##### 📑 공시로 확인된 사례\n" + (_bold_first(r["answer"]) if r["answerable"] else r["answer"]))
    elif r["answerable"]:
        parts.append(_bold_first(r["answer"]))
    elif r.get("news_answer"):
        parts.append(REFUSE_SHORT)
    else:
        return {"role": "assistant", "content": REFUSE, "sources": []}
    if r.get("news_answer"):
        title = "📰 뉴스에 나온 사례" if plan else "📰 뉴스에서는"
        parts.append(f"##### {title}\n:gray[언론 보도·회사 발표 — 공시보다 한 단계 약한 근거예요]\n\n" + _bold_first(r["news_answer"]))
    msg = {"role": "assistant", "content": "\n\n".join(parts), "sources": r["sources"], "news_sources": r.get("news_sources", [])}
    if plan and r.get("plan", {}).get("queries"):  # 후속 질문이 이어 쓸 기획 상태
        msg["plan_state"] = {"plan": r["plan"], "plan_text": q[:1500], "chunk_ids": r.get("context_chunk_ids", []),
                             "news_ids": r.get("context_news_ids", [])}
    return msg


def _file_text(f) -> str:
    """입력창에 붙인 파일 → 글. txt·md 는 그대로, pdf 는 pypdf, docx 는 압축 안 word/document.xml 의 글자만"""
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


def _show(msg: dict) -> None:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        src, news = msg.get("sources") or [], msg.get("news_sources") or []
        if src or news:
            label = " · ".join(x for x in [f"공시 {len(src)}건" if src else "", f"뉴스 {len(news)}건" if news else ""] if x)
            with st.expander(f"출처 {label}"):
                for s in src:
                    lv = "" if s["ai_level"] is None else f" · {LEVEL_NAME[f'L{s['ai_level']}']}"
                    st.markdown(f"[{s['n']}] **{s['corp']}** {s['report']} · 접수 {s['report_date']} · "
                                f"{s['section'].split('>')[-1].strip()}{lv} — [DART 원문]({s['url']})")
                for s in news:
                    st.markdown(f"[뉴스 {s['n']}] **{s['media']}** {s['date']} · {s['title']} — [기사 원문]({s['url']})")


# ---------- 대화 기록 (파일 하나 = 대화 하나) ----------
def _save() -> None:
    msgs = st.session_state.qa_msgs
    if not msgs:
        return
    cid = st.session_state.setdefault("qa_id", datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    HIST_DIR.mkdir(parents=True, exist_ok=True)
    first = next(m["content"] for m in msgs if m["role"] == "user").replace("**[기획안]**\n\n", "[기획안] ")
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
            st.caption("아직 없어요. 질문하면 여기에 저장돼요.")
        cur = st.session_state.get("qa_id")
        for c in convs[:HIST_SHOW]:
            title = c["title"] if len(c["title"]) <= 24 else c["title"][:24] + "…"
            mark = "▶ " if c["id"] == cur else ""  # 지금 보고 있는 대화
            st.button(f"{mark}{title}  \n{c['updated']}", key=f"hist_{c['id']}", width="stretch",
                      on_click=_open, args=(c,), help=c["title"])


def render() -> None:
    msgs = st.session_state.setdefault("qa_msgs", [])
    _sidebar()
    head, clear = st.columns([5, 1], vertical_alignment="bottom")
    head.title(TITLE)
    if msgs:
        clear.button("이 대화 지우기", width="stretch", on_click=_delete)
    st.markdown(TAGLINE)

    picked = None
    if not msgs:  # 첫 화면: 소개 + 질문 종류별 예시
        with st.chat_message("assistant"):
            st.markdown(WELCOME)
        st.markdown("##### 이런 걸 물어볼 수 있어요")
        cards = list(EXAMPLES.items()) + [("기획안 사례 찾기 · 📎 파일도 가능", PLAN_LABEL)]
        for col, (kind, q) in zip(st.columns(2) * 3, cards):  # 2열 × 3줄: 질문 예시 5개 + 기획안 예시 1개
            with col.container(border=True):
                st.caption(kind)
                if st.button(q, key="ex_plan" if q == PLAN_LABEL else f"ex_{kind}", width="stretch"):
                    picked = ("plan", PLAN_EXAMPLE) if q == PLAN_LABEL else q
    for m in msgs:
        _show(m)

    has_plan = _plan_state(msgs)[0] is not None
    hint = ("이 기획안에 이어서 물어보세요 (더 자세히 · 보강할 점 · 기대효과·우려)" if has_plan
            else "회사 이름을 넣어 물어보세요 · 📎 기획안을 붙이면 비슷한 사례를 찾아요")
    v = st.chat_input(hint,
                      accept_file=True, file_type=FILE_TYPES, key="qa_input")  # key 고정: 안내 문구가 바뀌어도 같은 입력창
    text, fname = "", None
    if v:
        text = (v.text or "").strip()
        if v.files:
            fname = v.files[0].name
            try:
                body = _file_text(v.files[0]).strip()
            except Exception as e:  # 깨진 파일 등
                st.error(f"'{fname}' 을(를) 읽지 못했어요 ({type(e).__name__}). txt·md·pdf·docx 파일인지 확인해 주세요.")
                return
            if not body:
                st.error(f"'{fname}' 에서 글자를 찾지 못했어요. 스캔한 그림 PDF 는 읽을 수 없어요.")
                return
            text = (text + "\n\n" + body).strip()
    elif isinstance(picked, tuple):
        text = picked[1]
    elif picked:
        text = picked
    if text:
        plan = bool(fname) or isinstance(picked, tuple) or len(text) >= PLAN_MIN_CHARS
        state, prev = (None, "") if plan else _plan_state(msgs)
        shown = f"📎 {fname}" if fname else (f"**[기획안]**\n\n{text}" if plan else text)
        if fname and v.text:
            shown += f"\n\n{v.text.strip()}"
        msgs.append({"role": "user", "content": shown})
        _show(msgs[-1])
        spin = ("기획안을 읽고 공시·뉴스에서 비슷한 사례를 찾는 중…" if plan else
                "기획안과 앞서 찾은 사례로 답을 쓰는 중…" if state else "공시와 뉴스를 찾고 답을 쓰는 중…")
        with st.chat_message("assistant"), st.spinner(spin):
            a = _answer(text, plan=plan, state=state, prev=prev)
        msgs.append(a)
        _save()
        st.rerun()
