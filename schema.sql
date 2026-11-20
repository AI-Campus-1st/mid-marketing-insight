-- =====================================================================
-- 뉴스·공시 통합 스키마 (SQLite)
-- 목적: 제공 뉴스·보강 뉴스와 DART 공시를 하나의 DB에 저장하고,
--       뉴스 LLM 분석, 공시 AI 워싱 분류, 앵커 이벤트·반영 시차, 표본 검수를 분리해서 관리한다.
-- 사용처: collect/ 에서 적재, analysis/·rag/ 에서 조회·저장, dashboard/ 에서 조회만
-- 기준 문서: docs/archive/AI 마케팅 이슈 × 공시 분석 대시보드 기획안.md (3~5절)
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
--    ai_term_count > 0 인 청크에만 LLM 이 ai_level·ai_domain 을 채운다.
--      ai_level : 0 언급 / 1 계획 / 2 도입·적용 / 3 자체 개발·상용화, NULL = 회사 AI 와 무관
--                 (광고주 상품명·정규표현식 오탐). 정의·경계 규칙은 config/ai_levels.py, docs/ai_level_guide.md
--                 뉴스 이벤트의 ai_level_claimed 와 같은 자 → 말-실행 격차 = 주장 수준 − 공시 최고 수준
--                 (Day 2 결정: 기획안의 ai_stage 3단계 declare/plan/execute 에서 변경)
--                 Day 2 전에 만든 DB 는 이 정의로 바뀌지 않는다 (CREATE TABLE IF NOT EXISTS) — 안 쓰는 ai_stage 가 남고
--                 ai_level 의 CHECK 가 없음. rag/rebuild_chunk_table.py --apply 로 이 정의대로 다시 만든다 (분류 결과 유지)
--      ai_domain: creative = 제작 / operation = 운영 / analytics = 분석·타겟팅 / new_biz = 신사업·기타
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS disclosure_chunks (
    chunk_id        TEXT PRIMARY KEY,        -- rcept_no + '_' + 청크 순번 (VectorDB 메타데이터와 동일)
    doc_id          TEXT NOT NULL REFERENCES documents(doc_id),
    corp_code       TEXT NOT NULL REFERENCES companies(corp_code),
    rcept_no        TEXT NOT NULL,
    report_date     TEXT,                    -- 'YYYY-MM-DD' 원본 접수일. 정정본만 청킹해도 처음 공개된 날을 둔다 (반영 시차 계산용)
    section         TEXT,                    -- 공시 목차 섹션명 (예: 'II. 사업의 내용')
    chunk_index     INTEGER NOT NULL,        -- 보고서 안에서의 순번
    text            TEXT NOT NULL,
    ai_term_count   INTEGER NOT NULL DEFAULT 0,
    ai_level        INTEGER CHECK (ai_level BETWEEN 0 AND 3),
    has_outcome_figure INTEGER,              -- 1 = 매출·고객 수 등 운영 성과 숫자가 있음 (단계와 별도)
    ai_domain       TEXT CHECK (ai_domain IN ('creative', 'operation', 'analytics', 'new_biz')),
    reason          TEXT,                    -- LLM이 밝힌 분류 근거
    model           TEXT,
    prompt_version  TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_chunks_corp ON disclosure_chunks(corp_code, rcept_no);
CREATE INDEX IF NOT EXISTS idx_chunks_ai   ON disclosure_chunks(ai_term_count);

-- ---------------------------------------------------------------------
-- 6. ai_events: 확정 앵커 이벤트 (Day 2 확정 30개, 담당 A)
--    data/processed/anchor_events.csv 와 같은 형식 (A·B 합의, Day 2). 적재: analysis/anchor_events.py
--    analysis_scope = 'judge' 만 반영 판정·기업 비교에 쓰고, 'reference'(REFLECTION_CUTOFF 이후)는 타임라인 표시만.
--    공시 반영 결과(첫 반영 공시·반영 시차·판정)는 이 표에 저장하지 않고 event_reflection 뷰가 계산한다 (Day 3 합의).
--    예전 DB 에는 매칭 방식 확정 전 임시 칸 first_rcept_no·first_rcept_date·first_level·lag_days(값 없음)가 남아 있다 → 지우기 (Day 7):
--      ALTER TABLE ai_events DROP COLUMN first_rcept_no;  (first_rcept_date · first_level · lag_days 도 같은 방식)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai_events (
    event_id           TEXT PRIMARY KEY,      -- 'E001' … (이벤트 파일과 동일)
    corp_code          TEXT NOT NULL REFERENCES companies(corp_code),
    event_name         TEXT NOT NULL,         -- 예: '스텔라이즈 정식 출시'
    event_type         TEXT CHECK (event_type IN ('launch', 'organization', 'adoption', 'investment',
                                                  'partnership', 'patent', 'campaign', 'award')),
    official_date      TEXT NOT NULL,         -- 'YYYY-MM-DD' 사람이 확인한 발표일 (첫 보도 기준)
    ai_level_claimed   INTEGER CHECK (ai_level_claimed BETWEEN 0 AND 3),  -- 뉴스가 주장한 단계 (L0~L3)
    analysis_scope     TEXT NOT NULL CHECK (analysis_scope IN ('judge', 'reference')),
    primary_source_url TEXT,                  -- 1차 출처 (언론사 원문 URL)
    search_terms       TEXT,                  -- 공시 매칭용 키워드 (JSON 배열, 예: '["스텔라이즈"]'), Day 3~4 에 채움
    note               TEXT
);

-- ---------------------------------------------------------------------
-- 6-1. event_dart_mapping: 이벤트 ↔ 공시 청크 매칭 후보와 사람 검증 (Day 4~5, 담당 B, A·B 검증 분담)
--    후보 = 키워드 후보 전부 + 임베딩 상위 5개 (같은 기업, 기준일 −1년 ~ 수집 종료). rag/match_candidates.py
--    단계(ai_level)는 복사하지 않고 disclosure_chunks 에서 조인한다 → 분류가 바뀌어도 매칭을 다시 할 필요 없음.
--    반영 판정(on_time / delayed_k / preceded / not_found / reference)은 저장하지 않고 뷰로 계산한다.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS event_dart_mapping (
    event_id       TEXT NOT NULL REFERENCES ai_events(event_id),
    chunk_id       TEXT NOT NULL REFERENCES disclosure_chunks(chunk_id),
    rcept_no       TEXT NOT NULL,
    report_date    TEXT NOT NULL,             -- 원본 접수일 (disclosure_chunks.report_date)
    report_kind    TEXT CHECK (report_kind IN ('annual', 'half', 'quarter', 'other')),
    match_method   TEXT NOT NULL CHECK (match_method IN ('keyword', 'embedding', 'manual')),
    matched_terms  TEXT,                      -- 키워드 후보: 걸린 검색어 (JSON 배열)
    match_score    REAL,                      -- 이벤트 설명과 청크의 코사인 유사도 (모든 후보)
    verified       INTEGER CHECK (verified IN (0, 1)),   -- NULL 미검증 / 1 같은 사건 / 0 다른 사건
    verified_by    TEXT,                      -- 'A' 또는 'B'
    verify_note    TEXT,                      -- 판정 이유 (반복 문장이면 '반복')
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (event_id, chunk_id)
);

-- ---------------------------------------------------------------------
-- 6-2. event_reflection (뷰): 이벤트별 공시 반영 판정 (실행 설계서 4장, A·B 합의 '반영 지표 = 판정형')
--    기준일 d = official_date. 기대 보고서 = d 이후 처음 끝나는 보고 기간(분기말)의 정기보고서.
--    첫 매칭 = event_dart_mapping.verified = 1 중 원본 접수일(report_date)이 가장 이른 청크.
--      reference  : analysis_scope = 'reference' (REFLECTION_CUTOFF 이후 이벤트, 타임라인 표시만)
--      not_found  : 검증된 매칭 없음 → 화면 표기 '공시에서 미확인' ('미반영' 금지)
--      preceded   : 첫 매칭 접수일 < d 이고 그 청크가 L1 이상 (공시가 먼저 말함). 발표 전 매칭이 단계 없음·L0 이면 후보에서 뺀다
--      on_time    : 첫 매칭 보고 기간 ≤ 기대 보고 기간 (예: 1월 발표가 직전 사업보고서(3월 접수)에 실림 포함)
--                   분기보고서에는 연혁이 없으므로(34건 중 33건 생략), 첫 매칭이 '회사의 연혁'이고 기대 보고서가
--                   1·3분기 보고서면 다음 반기·사업보고서를 기대 보고서로 본다 (실행 설계서 4장)
--      delayed_k  : k 분기 늦은 보고서에서 처음 매칭 (quarters_late = k)
--    lag_days = 첫 매칭 접수일 − d (보조 지표, 분포만).
--    말-실행 격차 say_do_gap = 뉴스 주장 단계 − 검증된 매칭 청크의 최고 단계 (AI 용어가 없어 분류하지 않은 청크는 제외)
--    판정 규칙이 바뀌어도 다시 계산할 필요 없도록 표가 아니라 뷰로 둔다.
-- ---------------------------------------------------------------------
DROP VIEW IF EXISTS event_reflection;
CREATE VIEW event_reflection AS
WITH matched AS (
    SELECT m.event_id, m.chunk_id, m.report_date, d.title AS report_name, k.ai_level, k.section,
           date(replace(substr(d.title, instr(d.title, '(') + 1, 7), '.', '-') || '-01', '+1 month', '-1 day') AS period_end
    FROM event_dart_mapping m
    JOIN disclosure_chunks k USING (chunk_id)
    JOIN documents d ON d.doc_id = k.doc_id
    WHERE m.verified = 1
),
first_match AS (
    -- 발표일 이전 매칭은 L1 이상일 때만 후보 (합의: preceded = 발표 전 공시에 L1 이상 매칭).
    -- 단계가 없거나(AI 용어 없는 연혁·수상표) L0 인 발표 전 매칭이 발표 뒤의 제대로 된 매칭을 가리지 않게 (A 리뷰, PR #16)
    SELECT * FROM (
        SELECT matched.*, ROW_NUMBER() OVER (PARTITION BY matched.event_id ORDER BY matched.report_date, period_end, chunk_id) AS rn
        FROM matched JOIN ai_events e ON e.event_id = matched.event_id
        WHERE matched.report_date >= e.official_date OR matched.ai_level >= 1
    ) WHERE rn = 1
),
levels AS (
    SELECT event_id, MAX(ai_level) AS disclosed_level, COUNT(*) AS n_matched FROM matched GROUP BY event_id
),
ev AS (
    SELECT e.*, c.corp_name,
           date(strftime('%Y', e.official_date) || '-'
                || printf('%02d', ((CAST(strftime('%m', e.official_date) AS INTEGER) + 2) / 3) * 3) || '-01',
                '+1 month', '-1 day') AS expected_period_end
    FROM ai_events e JOIN companies c USING (corp_code)
),
joined AS (
    SELECT ev.*, f.report_name AS first_report, f.report_date AS first_report_date, f.period_end AS first_period_end,
           f.chunk_id AS first_chunk_id, f.section AS first_section, l.disclosed_level, l.n_matched,
           CASE WHEN f.section LIKE '%회사의 연혁%' AND strftime('%m', ev.expected_period_end) IN ('03', '09')
                THEN 1 ELSE 0 END AS history_shift,
           (CAST(strftime('%Y', f.period_end) AS INTEGER) * 4 + (CAST(strftime('%m', f.period_end) AS INTEGER) + 2) / 3)
         - (CAST(strftime('%Y', ev.expected_period_end) AS INTEGER) * 4 + (CAST(strftime('%m', ev.expected_period_end) AS INTEGER) + 2) / 3)
           AS quarters_late
    FROM ev
    LEFT JOIN first_match f ON f.event_id = ev.event_id
    LEFT JOIN levels l ON l.event_id = ev.event_id
)
SELECT event_id, corp_code, corp_name, event_name, event_type, official_date, analysis_scope,
       date(expected_period_end, 'start of month', '+' || (history_shift * 3) || ' months', '+1 month', '-1 day') AS expected_period_end,
       first_report, first_report_date, first_period_end, first_chunk_id, first_section,
       CAST(julianday(first_report_date) - julianday(official_date) AS INTEGER) AS lag_days,
       quarters_late - history_shift AS quarters_late,
       CASE
           WHEN analysis_scope = 'reference' THEN 'reference'
           WHEN first_report_date IS NULL THEN 'not_found'
           WHEN first_report_date < official_date THEN 'preceded'
           WHEN quarters_late - history_shift <= 0 THEN 'on_time'
           ELSE 'delayed_' || (quarters_late - history_shift)
       END AS verdict,
       ai_level_claimed, disclosed_level, n_matched,
       CASE WHEN disclosed_level IS NOT NULL THEN ai_level_claimed - disclosed_level END AS say_do_gap
FROM joined;

-- ---------------------------------------------------------------------
-- 7. review_samples: LLM 결과 표본 검수 기록 (Day 4, 뉴스·공시 공통)
--    정확도 = is_correct 평균. 리포트의 'AI 기반 이슈 분석' 장에 사용한다.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS review_samples (
    review_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id          TEXT,                    -- 뉴스 검수일 때
    chunk_id        TEXT,                    -- 공시 청크 검수일 때
    target          TEXT NOT NULL CHECK (target IN ('relevance', 'ai_related', 'sentiment', 'ai_level', 'ai_domain', 'event')),
    prompt_version  TEXT,                    -- 어떤 프롬프트 결과를 검수했는지
    llm_label       TEXT,                    -- LLM이 붙인 값
    human_label     TEXT,                    -- 사람이 판단한 값
    is_correct      INTEGER,                 -- 1 = 일치, 0 = 불일치
    reviewer        TEXT,                    -- 'A' 또는 'B'
    note            TEXT,                    -- 틀린 이유 메모 (회고에 사용)
    reviewed_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    CHECK (doc_id IS NOT NULL OR chunk_id IS NOT NULL)
);
