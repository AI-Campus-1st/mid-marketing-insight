# AA 팩트체커 (Ad & AI Fact-Checker) — AI 마케팅 이슈 × 공시 분석 대시보드

생성형 AI가 광고대행 산업을 어떻게 바꾸고 있는지, 뉴스 이슈와 DART 정기공시(사업·반기·분기보고서)를 비교해
**"말뿐인 AI인가, 실행하는 AI인가"** 를 판단하는 Streamlit 대시보드입니다.

- **대상:** 제일기획 · 이노션 · 플레이디 · 인크로스 · KT나스미디어 (5개사), 2023-10-19 ~ 2026-10-19
- **사용자:** 5개사 밖 중견 디지털 광고대행사 신사업기획팀장 (메인), 브랜드사 광고주 마케팅 담당자 (서브)
- **핵심 질문:** AI 마케팅 이슈는 언제 급증했고 무엇으로 옮겨 갔나? 뉴스로 발표한 AI 는 정기공시에도 실렸나, 공시 속 AI 는 어느 단계(언급 · 계획 · 도입·적용 · 자체 개발·상용화)인가?

> 기획 상세는 [기획안](docs/AI%20마케팅%20이슈%20×%20공시%20분석%20대시보드%20기획안.md), 설계 결정은 [decisions.md](docs/decisions.md), 리포트 원고는 [docs/report/](docs/report/) 참고

## 화면

| 화면 | 담당 | 내용 |
|---|---|---|
| 공시 챗봇 (첫 화면) | B | 공시 근거로만 답하고 출처(DART 링크)를 붙임. 공시에 없으면 "공시에서 확인되지 않습니다". 지난 대화 목록 |
| 기획안 검토 | A (챗봇 연결 B) | 기획안(txt·md·pdf·docx·붙여 넣기)을 넣으면 공시·뉴스에서 비슷한 AI 사례를 찾아 검토 결론 + 문단별 사례 카드. 공시 사례마다 **'공시 챗봇에 물어보기'** 버튼(찾아 둔 근거 문단을 함께 넘겨 챗봇 새 대화에서 바로 답함). 지난 기획안 목록 |
| 리포트 › Q1 기사량·급증 | A | 월별 AI 기사, 급증 기준선과 급증 월 |
| 리포트 › Q2 화제 이슈 | A | 이슈별 분기 비중 추이, 이슈 순위, 도입 전 주의할 점(사람이 거른 우려 기사) |
| 리포트 › Q3 공시 반영 | B | 뉴스로 발표한 AI 이벤트가 정기공시에 실렸나 (다음 공시에 반영 · 늦게 반영 · 공시가 먼저 · 공시에서 미확인) — 요약 · 이벤트 타임라인(아래 이벤트 표) · 공시 AI 단계(언급 · 계획 · 도입·적용 · 자체 개발·상용화 비중) |

- 사이드바 메뉴는 공시 챗봇 · 기획안 검토 · **리포트** 세 개이고, 리포트 안에서 맨 위 버튼으로 Q1·Q2·Q3 를 고릅니다.
- 리포트(Q1·Q2·Q3) 맨 위 **리포트 요약**(뉴스 · 공시 · 결론)은 사이드바 필터(기간·기업·뉴스 카테고리)에 맞춰 다시 계산됩니다 (`dashboard/conclusion.py`).

## 폴더 구조

> 파일별 담당·데이터 흐름은 [docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md) 참고

```
.
├── config/           # 공통 설정, 대상 5개사 목록, AI 단계 기준
├── collect/          # 데이터 수집·적재 (제공·보강 뉴스 적재, DART 공시 수집)
├── analysis/         # 뉴스 전처리·EDA·LLM 분류·검수, 앵커 이벤트, Q2 우려 기사, A→B 데이터 넘기기
├── rag/              # 공시 청킹, AI 단계 분류·검수, 이벤트 매칭, 임베딩 캐시·검색, 질의응답 체인·평가, 기획안 검토
├── dashboard/        # Streamlit 앱 (저장된 결과만 조회, 예외: 챗봇 질문·기획안 검토의 LLM 호출)
├── data/             # 모두 저장소 제외
│   ├── raw/          # 제공 뉴스, 보강 기사, DART 응답 원본
│   ├── processed/    # DB, 정제 뉴스 CSV, 검수 엑셀(review/), 챗봇·기획안 기록
│   └── vectordb/     # 임베딩 캐시 (공시 청크·관련 뉴스)
├── docs/             # 기획안, 설계 결정 기록, 파일 구조도, 리포트(report/)
├── schema.sql        # 뉴스·공시 통합 스키마 + 반영 판정 뷰(event_reflection)
└── .env.example      # 환경변수 예시 (실제 키는 .env 에만)
```

## 환경 설정

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt     # streamlit >= 1.51
cp .env.example .env                # .env 에 DART_API_KEY, OPENAI_API_KEY 입력
```

제공 뉴스 CSV는 `data/raw/news/`에 직접 넣습니다. (저장소에 포함하지 않음)

## 실행 순서

1. **DB 생성:** `sqlite3 data/processed/project.db < schema.sql`
   - 스키마가 바뀌면 같은 명령을 다시 실행해도 됩니다 (테이블은 없을 때만 만들고, 반영 판정 뷰는 새로 만듦)
2. **뉴스 적재 (A):** `python collect/load_news.py`
   - 보강 기사: `python collect/supplement_search.py` → `python collect/supplement_fetch.py` → `python collect/load_news_supplement.py`
   - 네이버 검색 API 약관상 네이버 기사는 LLM 에 넣지 않고, 원본은 수집 후 21일 안에 정리합니다 ([decisions.md](docs/decisions.md))
3. **공시 수집 (B):** `python collect/dart_corp_code.py` → `python collect/dart_collect.py` → `python collect/dart_document.py`
   - 이미 받은 원본은 다시 받지 않음 (`dart_corp_code.py`·`dart_collect.py` 는 `--refresh` 로 새로 받기)
4. **전처리·EDA:** `python analysis/preprocess.py` (A), 노트북 `analysis/eda.ipynb` (A) · `analysis/disclosure_eda.ipynb` (B)
5. **공시 청킹·AI 단계 분류 (B):** `python rag/chunk.py` → `python rag/classify_chunks.py`
   - 검수: `python rag/level_review_sheet.py make` → 엑셀 판정 → `score` (2차: `make2` → `score2`, 끝나면 `merge` 로 한 파일에)
6. **앵커 이벤트 (A):** `python analysis/event_review_sheet.py` → `python analysis/event_classify.py` → `python analysis/event_review_sheet.py --make-review` → to_review.csv 사람 확인 → `--merge-review` → `--export` → `python analysis/anchor_events.py`
7. **뉴스 LLM 분류 (A):** `python analysis/classify_news.py`
   - 검수: `python analysis/llm_review.py --version news_v4 --n 30` → 엑셀 판정 → `python analysis/llm_review.py --version news_v4 --score` (`--version` 기본값이 news_v2 라 채점 때도 넣어야 함)
   - Q2 우려 기사: `python analysis/q2_concerns.py` → 엑셀 판정 → `--apply`
8. **이벤트–공시 매칭 (B):** `python rag/check_search_terms.py` (검색어 점검) → `python rag/match_candidates.py make` → `data/processed/review/event_match_B.xlsx` 사람 검증 → `python rag/match_candidates.py apply`
   - 반영 판정(다음 공시에 반영 · 늦게 반영 · 공시가 먼저 · 공시에서 미확인 · 참고용)은 `schema.sql` 의 `event_reflection` 뷰가 매번 계산합니다 (따로 실행 없음)
9. **검색 파일(임베딩 캐시) 미리 만들기 (B):** `python rag/build_index.py` → `data/vectordb/` (공시 청크 + 관련 뉴스, 이미 있으면 바뀐 것만)
   - 이 단계를 하면 대시보드는 켜질 때 임베딩 API 를 부르지 않아요
10. **(선택) 공시 챗봇 평가 (B):** `python rag/eval_retrieval.py` (검색) · `python rag/eval_qa.py` (개선에 쓴 질문 15개) · `python rag/eval_qa.py --holdout` (처음 보는 질문 15개)
11. **대시보드 실행:** `streamlit run dashboard/app.py` → http://localhost:8501
    - 한 DB 에 뉴스 결과(`llm_results`·`news_company`, `data/processed/news_preprocessed.csv`)와 공시 결과(`disclosure_chunks`·`event_dart_mapping`)가 모두 있어야 모든 화면이 나옵니다. 없는 쪽 화면은 안내 문구만 나와요
    - 공시 챗봇과 기획안 검토는 `.env` 의 `OPENAI_API_KEY` 가 있어야 답합니다 (챗봇 질문 1개당 검색 + LLM 1회, 기획안 1개당 LLM 2회). 키가 없으면 두 화면만 안내 문구가 나오고 Q1·Q2·Q3 는 그대로 동작해요
    - B 화면(공시 챗봇 · Q3)만 미리 보기: `streamlit run dashboard/preview_b.py --server.port 8502`

## 역할

| 담당 | 범위 |
|---|---|
| A | 뉴스 적재·정제, EDA·급증 시점, LLM 감성·이슈 추출, 앵커 이벤트, Q1·Q2·기획안 검토 화면, 리포트 요약 '뉴스', 대시보드 통합·디자인 |
| B | DART 공시 수집·파싱, 청킹·AI 단계 분류·검수, 이벤트–공시 매칭·반영 판정, 공시 RAG 챗봇·평가, Q3·공시 챗봇 화면, 기획안 검토 → 챗봇 연결, 리포트 요약 '공시·결론' |
| 공동 | 스키마, 결론 문장, 리포트·발표 |
