-- =====================================================================
-- 뉴스·공시 통합 스키마 (SQLite)
-- 목적: 뉴스(제공 CSV)와 DART 공시를 하나의 DB에 저장하고,
--       LLM 분석 결과·공시 근거·표본 검수 결과를 분리해서 관리한다.
-- 사용처: collect/ 에서 적재, analysis/·rag/ 에서 조회·저장, dashboard/ 에서 조회만
-- 주의: Day 1 팀 합의용 초안. 컬럼 추가·변경 시 docs/decisions.md 에 기록할 것
-- =====================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- 1. companies: 분석 대상 기업 8개사 기본 정보
--    DART corpCode.xml 에서 확인한 corp_code 를 기준 키로 사용한다.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS companies (
    corp_code     TEXT PRIMARY KEY,          -- DART 고유번호 (8자리)
    corp_name     TEXT NOT NULL,             -- 회사명
    stock_code    TEXT,                      -- 종목코드 (비상장이면 NULL)
    company_type  TEXT,                      -- 종합대행사 / 미디어렙 / 퍼포먼스 / 플랫폼
    is_core       INTEGER NOT NULL DEFAULT 0 -- 핵심 3개사 여부 (1 = 상세 분석 대상)
);

-- ---------------------------------------------------------------------
-- 2. documents: 뉴스와 공시를 함께 담는 통합 테이블
--    source_type 으로 뉴스/공시를 구분한다.
--    뉴스 중복 판단은 원문URL이 아니라 title_norm + body_hash 로 한다.
--    (제공 데이터에서 서로 다른 기사에 같은 URL이 들어간 사례 확인)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documents (
    doc_id          TEXT PRIMARY KEY,        -- 뉴스: 'N' + 일련번호 / 공시: 'D' + rcept_no
    source_type     TEXT NOT NULL CHECK (source_type IN ('news', 'disclosure')),
    title           TEXT NOT NULL,           -- 기사 제목 또는 공시명
    body            TEXT,                    -- 기사 본문 또는 공시 원문 텍스트
    published_date  TEXT NOT NULL,           -- 'YYYY-MM-DD HH:MM:SS' (뉴스 발행일 / 공시 접수일)
    url             TEXT,                    -- 뉴스 원문 링크 또는 DART 공시 링크

    -- 뉴스 전용 컬럼
    media           TEXT,                    -- 매체명
    keyword         TEXT,                    -- 수집키워드 (여러 개면 '; ' 로 구분된 원본 유지)
    category        TEXT,                    -- 가이드 카테고리 (규제 및 정책 / 기술 및 트렌드 / ...)
    title_norm      TEXT,                    -- 특수문자 제거한 제목 (중복 판단용)
    body_hash       TEXT,                    -- 본문 해시 (중복 판단용)

    -- 공시 전용 컬럼
    corp_code       TEXT REFERENCES companies(corp_code),
    rcept_no        TEXT,                    -- DART 접수번호
    report_type     TEXT,                    -- 공시 유형 (정기공시, 주요사항보고 등)

    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_documents_date   ON documents(published_date);
CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source_type);
CREATE INDEX IF NOT EXISTS idx_documents_corp   ON documents(corp_code);

-- ---------------------------------------------------------------------
-- 3. llm_results: 뉴스 LLM 분류 결과 (Day 4, 담당 A)
--    대시보드는 이 테이블을 조회만 한다. (실시간 분류 금지)
--    prompt_version 으로 프롬프트 수정 전후 결과를 구분해 보관한다.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS llm_results (
    doc_id           TEXT NOT NULL REFERENCES documents(doc_id),
    prompt_version   TEXT NOT NULL,          -- 예: 'v1', 'v2'
    is_relevant      INTEGER,                -- 광고·마케팅 산업 관련 여부 (1/0)
    strategy_themes  TEXT,                   -- JSON 배열, 0~2개 (예: '["숏폼·영상","인플루언서·SNS"]')
    sentiment        TEXT CHECK (sentiment IN ('positive', 'negative', 'neutral')),
    issue_keywords   TEXT,                   -- JSON 배열
    summary          TEXT,                   -- 한 줄 요약 (선택)
    reason           TEXT,                   -- LLM이 밝힌 분류 근거 (검수 시 참고)
    model            TEXT,                   -- 사용한 모델명
    created_at       TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (doc_id, prompt_version)
);

-- ---------------------------------------------------------------------
-- 4. disclosure_evidence: 사업보고서에서 추출한 전략 테마 근거 (Day 4, 담당 B)
--    evidence_level 로 '언급 / 계획 / 실제 투자'를 구분해서
--    사업 언급과 실제 투자를 혼동하지 않게 한다.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS disclosure_evidence (
    evidence_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    corp_code       TEXT NOT NULL REFERENCES companies(corp_code),
    rcept_no        TEXT NOT NULL,           -- 근거가 나온 공시의 접수번호
    strategy_theme  TEXT NOT NULL,           -- 전략 테마 이름
    evidence_level  TEXT NOT NULL CHECK (evidence_level IN ('mention', 'plan', 'actual')),
    evidence_text   TEXT NOT NULL,           -- 근거 문장 원문
    report_date     TEXT,                    -- 공시 접수일
    source_url      TEXT,                    -- DART 공시 링크
    model           TEXT,                    -- 추출에 사용한 모델명
    reviewed        INTEGER NOT NULL DEFAULT 0  -- 사람이 검수했는지 여부
);

CREATE INDEX IF NOT EXISTS idx_evidence_theme ON disclosure_evidence(strategy_theme);

-- ---------------------------------------------------------------------
-- 5. review_samples: LLM 결과 표본 검수 기록 (Day 4)
--    정확도 = is_correct 평균. 리포트의 'AI 기반 이슈 분석' 장에 사용한다.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS review_samples (
    review_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id       TEXT,                       -- 뉴스 검수일 때
    evidence_id  INTEGER,                    -- 공시 근거 검수일 때
    target       TEXT NOT NULL CHECK (target IN ('relevance', 'theme', 'sentiment', 'evidence_level')),
    llm_label    TEXT,                       -- LLM이 붙인 값
    human_label  TEXT,                       -- 사람이 판단한 값
    is_correct   INTEGER,                    -- 1 = 일치, 0 = 불일치
    reviewer     TEXT,                       -- 'A' 또는 'B'
    note         TEXT,                       -- 틀린 이유 메모 (회고에 사용)
    reviewed_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
