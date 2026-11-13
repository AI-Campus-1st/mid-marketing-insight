# AI 마케팅 이슈 × 공시 분석 대시보드

생성형 AI가 광고대행 산업을 어떻게 바꾸고 있는지, 뉴스 이슈와 DART 공시를 비교해
**"말뿐인 AI인가, 실행하는 AI인가"** 를 판단하는 Streamlit 대시보드입니다.

- **대상:** 제일기획 · 이노션 · 플레이디 · 인크로스 · 나스미디어 (5개사)
- **사용자:** 디지털 광고대행사 신사업기획팀장 (메인), 광고주 마케팅 담당자 (서브, 대행사 1차 스크리닝)
- **핵심 질문:** AI 마케팅 이슈는 언제 급증했고, 기업들은 공시에서 AI를 얼마나, 얼마나 구체적으로 언급하는가?

> 기획 상세는 [기획안](docs/AI%20마케팅%20이슈%20×%20공시%20분석%20대시보드%20기획안.md), 설계 결정은 [decisions.md](docs/decisions.md) 참고

## 폴더 구조

> 파일별 담당·일정·데이터 흐름은 [docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md) 참고

```
.
├── config/           # 공통 설정, 대상 5개사 목록
├── collect/          # 데이터 수집·적재 (제공·보강 뉴스 적재, DART 공시 수집)
├── analysis/         # 전처리, EDA, 뉴스 LLM 분류, 앵커 이벤트, 반영 시차·지표 계산
├── rag/              # 공시 청킹, AI 단계·영역 분류, Chroma, 질의응답 체인
├── dashboard/        # Streamlit 앱 (저장된 결과만 조회)
├── data/
│   ├── raw/          # 제공 뉴스, 보강 기사, DART 응답 원본 (저장소 제외)
│   └── processed/    # DB 파일 (저장소 제외)
├── docs/             # 기획안, 설계 결정 기록, 파일 구조도
├── schema.sql        # 뉴스·공시 통합 스키마
└── .env.example      # 환경변수 예시 (실제 키는 .env 에만)
```

## 환경 설정

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # .env 에 DART_API_KEY, LLM API 키 입력
```

제공 뉴스 CSV는 `data/raw/news/`에 직접 넣습니다. (저장소에 포함하지 않음)

## 실행 순서

1. DB 생성: `sqlite3 data/processed/project.db < schema.sql`
2. 뉴스 적재: `python collect/load_news.py` → `python collect/load_news_supplement.py`
3. 공시 수집: `python collect/dart_collect.py` → `python collect/dart_document.py` (재실행 시 이미 받은 파일은 건너뜀)
4. 전처리·EDA: `python analysis/preprocess.py`, `analysis/eda.ipynb`
5. 공시 청킹·AI 분류: `python rag/chunk.py` → `python rag/classify_chunks.py`
6. 뉴스 LLM 분류·앵커 이벤트: `python analysis/classify_news.py` → `python analysis/anchor_events.py`
7. 반영 시차·지표: `python analysis/lag.py` → `python analysis/metrics.py`
8. VectorDB 구축: `python rag/build_index.py`
9. 대시보드 실행: `streamlit run dashboard/app.py`

> 파일명은 기획안 기준 초안이며 구현하면서 업데이트합니다.

## 역할

| 담당 | 범위 |
|---|---|
| A | 뉴스 적재·정제, EDA·급증 시점, LLM 감성·이슈 추출, 앵커 이벤트, Q1·Q2 화면 |
| B | DART 공시 수집·파싱, 청킹·AI 언급 집계, 단계·영역 분류, RAG, Q3·공시 Q&A 화면 |
| 공동 | 스키마, 반영 시차 계산, 대시보드 통합, 결론·리포트·발표 |
