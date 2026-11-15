-- =====================================================================
-- 뉴스·공시 통합 스키마 (SQLite)
-- 목적: 제공 뉴스·보강 뉴스와 DART 공시를 하나의 DB에 저장하고,
--       뉴스 LLM 분석, 공시 AI 워싱 분류, 앵커 이벤트·반영 시차, 표본 검수를 분리해서 관리한다.
-- 사용처: collect/ 에서 적재, analysis/·rag/ 에서 조회·저장, dashboard/ 에서 조회만
-- 기준 문서: docs/AI 마케팅 이슈 × 공시 분석 대시보드 기획안.md (3~5절)
-- 주의: 컬럼 추가·변경 시 docs/decisions.md 에 기록할 것
-- =====================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- 1. companies: 분석 대상 기업 (5개사 + 대조군 선택)
--    DART corpCode.xml 에서 확인한 corp_code 를 기준 키로 사용한다.
--    segment 로 '대형 종합대행 vs 디지털 대행·미디어렙' 유형 비교를 한다.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS companies (
    corp_code     TEXT PRIMARY KEY,          -- DART 고유번호 (8자리)
    corp_name     TEXT NOT NULL,             -- 회사명 (뉴스 기업명 매칭에도 사용)
    stock_code    TEXT,                      -- 종목코드 (비상장이면 NULL)
    parent_group  TEXT,                      -- 모그룹 (삼성 / 현대차 / SOOP / SK스퀘어 / KT)
    segment       TEXT,                      -- 종합대행 / 디지털대행 / 미디어렙
    is_control    INTEGER NOT NULL DEFAULT 0 -- 1 = 대조군 (예: 에코마케팅, 선택)
);

-- ---------------------------------------------------------------------
-- 2. documents: 뉴스와 공시를 함께 담는 통합 테이블
--    source_type 으로 뉴스/공시를 구분한다.
--    뉴스는 news_origin 으로 제공/보강 기사를 구분한다.
--      - Q1·Q2 집계(기사량·급증·감성)는 news_origin = 'provided' 만 사용한다.
--      - 'supplement' 는 앵커 이벤트 근거로만 사용한다.
--    뉴스 중복 판단은 원문URL이 아니라 title_norm + body_hash 로 한다.
--    (제공 데이터에서 서로 다른 기사에 같은 URL이 들어간 사례 확인)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documents (
    doc_id          TEXT PRIMARY KEY,        -- 제공 뉴스: 'N' + 일련번호 / 보강 뉴스: 'S' + 일련번호 / 공시: 'D' + rcept_no
    source_type     TEXT NOT NULL CHECK (source_type IN ('news', 'disclosure')),
    title           TEXT NOT NULL,           -- 기사 제목 또는 공시명
    body            TEXT,                    -- 기사 본문 또는 공시 원문 텍스트
    published_date  TEXT NOT NULL,           -- 'YYYY-MM-DD HH:MM:SS' (뉴스 발행일 / 공시 접수일)
    url             TEXT,                    -- 뉴스 원문 링크 또는 DART 공시 링크

    -- 뉴스 전용 컬럼
    news_origin     TEXT CHECK (news_origin IN ('provided', 'supplement')),  -- 공시는 NULL
    media           TEXT,                    -- 매체명
    keyword         TEXT,                    -- 수집키워드 (여러 개면 '; ' 로 구분된 원본 유지, 보강 뉴스는 검색어)
    category        TEXT,                    -- 가이드 이슈 카테고리 (대시보드 카테고리 필터용)
    title_norm      TEXT,                    -- 특수문자 제거한 제목 (중복 판단용)
    body_hash       TEXT,                    -- 본문 해시 (중복 판단용)
    is_duplicate    INTEGER NOT NULL DEFAULT 0,  -- 1 = 중복 기사 (집계 제외)

    -- 공시 전용 컬럼
    corp_code       TEXT REFERENCES companies(corp_code),
    rcept_no        TEXT,                    -- DART 접수번호
    report_type     TEXT,                    -- 공시 유형 (pblntf_ty: 정기공시 등)
    raw_path        TEXT,                    -- data/raw/dart/ 아래 원본 파일 경로

    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),

    CHECK (source_type = 'disclosure' OR news_origin IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_documents_date   ON documents(published_date);
CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source_type, news_origin);
CREATE INDEX IF NOT EXISTS idx_documents_corp   ON documents(corp_code);

-- ---------------------------------------------------------------------
-- 3. news_company: 뉴스 ↔ 기업 연결 (Day 3, 담당 A)
--    기사 제목·본문에 기업명이 등장하면 연결한다.
--    in_title = 1 이면 제목에 등장 (기업 중심 기사일 가능성이 높음)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS news_company (
    doc_id     TEXT NOT NULL REFERENCES documents(doc_id),
    corp_code  TEXT NOT NULL REFERENCES companies(corp_code),
    in_title   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (doc_id, corp_code)
);

-- ---------------------------------------------------------------------
-- 4. llm_results: 뉴스 LLM 분석 결과 (Day 4, 담당 A)
--    대시보드는 이 테이블을 조회만 한다. (실시간 분류 금지)
--    prompt_version 으로 프롬프트 수정 전후 결과를 구분해 보관한다.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS llm_results (
    doc_id           TEXT NOT NULL REFERENCES documents(doc_id),
    prompt_version   TEXT NOT NULL,          -- 예: 'v1', 'v2'
    is_relevant      INTEGER,                -- 광고·마케팅 산업 관련 여부 (1/0)
    is_ai_related    INTEGER,                -- AI 관련 기사 여부 (1/0)
    sentiment        TEXT CHECK (sentiment IN ('positive', 'negative', 'neutral')),  -- 기대 / 우려 / 중립
    issue_keywords   TEXT,                   -- JSON 배열 (예: '["생성형 AI 광고","GEO"]')
    summary          TEXT,                   -- 한 줄 요약 (선택)
    reason           TEXT,                   -- LLM이 밝힌 분류 근거 (검수 시 참고)
    model            TEXT,                   -- 사용한 모델명
    created_at       TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (doc_id, prompt_version)
);

-- ---------------------------------------------------------------------
-- 5. disclosure_chunks: 공시 청크 + AI 워싱 분류 (Day 3~4, 담당 B)
--    RAG 검색 근거이면서 AI 언급 밀도·실행률 계산의 기준 단위다.
--    ai_term_count 는 정규표현식(AI|인공지능|생성형|LLM|GPT)이 채우고,
--    ai_term_count > 0 인 청크에만 LLM 이 ai_stage·ai_domain 을 채운다.
--      ai_stage : declare = 선언 / plan = 계획 / execute = 실행
--      ai_domain: creative = 제작 / operation = 운영 / analytics = 분석·타겟팅 / new_biz = 신사업·기타
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS disclosure_chunks (
    chunk_id        TEXT PRIMARY KEY,        -- rcept_no + '_' + 청크 순번 (VectorDB 메타데이터와 동일)
    doc_id          TEXT NOT NULL REFERENCES documents(doc_id),
    corp_code       TEXT NOT NULL REFERENCES companies(corp_code),
    rcept_no        TEXT NOT NULL,
    section         TEXT,                    -- 공시 목차 섹션명 (예: 'II. 사업의 내용')
    chunk_index     INTEGER NOT NULL,        -- 보고서 안에서의 순번
    text            TEXT NOT NULL,
    ai_term_count   INTEGER NOT NULL DEFAULT 0,
    ai_stage        TEXT CHECK (ai_stage IN ('declare', 'plan', 'execute')),
    ai_domain       TEXT CHECK (ai_domain IN ('creative', 'operation', 'analytics', 'new_biz')),
    reason          TEXT,                    -- LLM이 밝힌 분류 근거
    model           TEXT,
    prompt_version  TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_chunks_corp ON disclosure_chunks(corp_code, rcept_no);
CREATE INDEX IF NOT EXISTS idx_chunks_ai   ON disclosure_chunks(ai_term_count);

-- ---------------------------------------------------------------------
-- 6. ai_events: 기업별 AI 앵커 이벤트와 공시 반영 시차 (Day 4~5)
--    A: 뉴스에서 이벤트를 뽑아 event_name·event_date·doc_id·search_terms 를 채운다.
--    B: search_terms 로 공시에서 처음 등장한 보고서를 찾아 first_* 를 채운다.
--    공동: lag_days = first_rcept_date - event_date (일수)
--    끝내 반영되지 않은 이벤트는 first_* 와 lag_days 를 NULL 로 둔다. (그 자체가 인사이트)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai_events (
    event_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    corp_code         TEXT NOT NULL REFERENCES companies(corp_code),
    event_name        TEXT NOT NULL,         -- 예: '스텔라이즈 출시'
    event_type        TEXT,                  -- 출시 / 조직 / 도입 / 캠페인 / 수상
    event_date        TEXT NOT NULL,         -- 'YYYY-MM-DD' (첫 보도일 기준)
    doc_id            TEXT REFERENCES documents(doc_id),  -- 근거 기사 (제공 또는 보강)
    search_terms      TEXT,                  -- 공시 검색용 키워드 (JSON 배열, 예: '["스텔라이즈"]')
    first_rcept_no    TEXT,                  -- 처음 반영된 공시 접수번호
    first_rcept_date  TEXT,                  -- 처음 반영된 공시 접수일
    first_stage       TEXT CHECK (first_stage IN ('declare', 'plan', 'execute')),  -- 첫 반영 시 단계
    lag_days          INTEGER,               -- 반영 시차 (일)
    note              TEXT
);

-- ---------------------------------------------------------------------
-- 7. review_samples: LLM 결과 표본 검수 기록 (Day 4, 뉴스·공시 공통)
--    정확도 = is_correct 평균. 리포트의 'AI 기반 이슈 분석' 장에 사용한다.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS review_samples (
    review_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id          TEXT,                    -- 뉴스 검수일 때
    chunk_id        TEXT,                    -- 공시 청크 검수일 때
    target          TEXT NOT NULL CHECK (target IN ('relevance', 'ai_related', 'sentiment', 'ai_stage', 'ai_domain')),
    prompt_version  TEXT,                    -- 어떤 프롬프트 결과를 검수했는지
    llm_label       TEXT,                    -- LLM이 붙인 값
    human_label     TEXT,                    -- 사람이 판단한 값
    is_correct      INTEGER,                 -- 1 = 일치, 0 = 불일치
    reviewer        TEXT,                    -- 'A' 또는 'B'
    note            TEXT,                    -- 틀린 이유 메모 (회고에 사용)
    reviewed_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    CHECK (doc_id IS NOT NULL OR chunk_id IS NOT NULL)
);
