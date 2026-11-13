# 뷰티 브랜드를 위한 마케팅 산업 이슈 기반 광고 전략 의사결정 대시보드

마케팅 산업 뉴스와 DART 공시를 분석해, 광고 경험이 적은 뷰티 브랜드가
어떤 광고 전략을 먼저 검토할지 근거를 갖고 판단하도록 돕는 Streamlit 대시보드입니다.

> 이 대시보드는 산업 동향 기반의 검토 순서를 제공하며, 광고 성과나 예산 배분을 예측하지 않습니다.

## 폴더 구조

> 파일별 담당·일정·데이터 흐름은 [docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md) 참고

```
.
├── config/           # 공통 설정, 8개사 목록, 전략 테마 사전
├── collect/          # 데이터 수집·적재 (뉴스 CSV 적재, DART 공시 수집)
├── analysis/         # 전처리, EDA, LLM 분류, 지표 계산
├── rag/              # 공시 청킹·임베딩, VectorDB, 질의응답 체인
├── dashboard/        # Streamlit 앱 (저장된 결과만 조회)
├── data/
│   ├── raw/          # API 응답 원본, 제공 CSV (저장소 제외)
│   └── processed/    # 정제 데이터, DB 파일 (저장소 제외)
├── docs/
│   └── decisions.md  # 설계 결정 기록
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

## 실행 순서

1. DB 생성: `sqlite3 data/processed/project.db < schema.sql`
2. 뉴스 적재: `python collect/load_news.py`
3. 공시 수집: `python collect/dart_collect.py`  (재실행 시 이미 받은 파일은 건너뜀)
4. 전처리·EDA: `analysis/eda.ipynb`
5. LLM 분류: `python analysis/classify.py`
6. 공시 근거 추출·VectorDB 구축: `python rag/build_index.py`
7. 지표 계산: `python analysis/metrics.py`
8. 대시보드 실행: `streamlit run dashboard/app.py`

> 파일명은 Day 1 초안이며 구현하면서 업데이트합니다.

## 역할

| 담당 | 범위 |
|---|---|
| A | 뉴스 적재·정제, EDA, LLM 감성·테마 분류, 이슈 분석 화면 |
| B | DART 공시 수집·파싱, 공시 근거 추출, RAG 질의응답, 공시 Q&A 화면 |
| 공동 | 스키마 설계, 대시보드 통합, 결론·리포트·발표 |
