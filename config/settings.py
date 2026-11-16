"""공통 설정: 경로, 수집 기간, DB 위치, 요청 규칙, API 키

모든 스크립트는 경로·기간을 직접 적지 않고 여기서 가져다 쓴다.
API 키는 코드에 쓰지 않고 .env 에서 읽는다.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# ── 경로 ──────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"

RAW_NEWS_DIR = RAW_DIR / "news"                 # 제공 뉴스 CSV
SUPPLEMENT_DIR = RAW_DIR / "news_supplement"    # 앵커 이벤트 보강 기사
RAW_DART_DIR = RAW_DIR / "dart"                 # DART 응답 원본

DB_PATH = DATA_DIR / "processed" / "project.db"
VECTORDB_DIR = DATA_DIR / "vectordb"
COMPANIES_CSV = CONFIG_DIR / "companies.csv"    # 분석 대상 5개사 (dart_corp_code.py 가 생성)

# ── 수집 기간 (제공 뉴스와 동일, 공시도 같은 기간) ─────────
PERIOD_START = "2023-10-19"
PERIOD_END = "2026-10-19"

# 공시 반영 판정 기준일: 마지막으로 확인 가능한 보고서가 2026 반기보고서(3분기 보고서는 수집 범위 밖)
# 이 날짜 이후 이벤트는 '참고용'(타임라인 표시만, 반영 판정·기업 비교 집계 제외)
REFLECTION_CUTOFF = "2026-06-30"

# ── 분석 공통 ────────────────────────────────────────
# AI 용어 정규표현식 (Day 3 확정, A·B 합의 — decisions.md)
# - AI·LLM 과 AI 복합어는 앞뒤가 영문자가 아닐 때만 인정 → KAIST·KAI·HYUNDAI·THAILAND 같은 영문 단어 속 글자 오탐 제거
# - AI 복합어(GenAI, OpenAI, AICC 등)는 목록으로 살림 → 경계 규칙 때문에 진짜 AI 용어를 버리지 않도록
# - GPT 는 경계 없음 (ChatGPT 포함). 그룹은 비캡처(?:) → pandas str.contains 경고 없음
AI_COMPOUND_TERMS = ["AI", "LLM", "sLLM", "GenAI", "OpenAI", "xAI", "AIoT", "AIGC", "AICC", "AIDC", "CAIO"]
AI_TERM_PATTERN = (
    r"(?<![A-Za-z])(?:" + "|".join(AI_COMPOUND_TERMS) + r")(?![A-Za-z])"
    r"|GPT|인공지능|생성형"
)

# ── 외부 요청 규칙 ────────────────────────────────────
USER_AGENT = "Mozilla/5.0 (compatible; mid-marketing-insight/0.1; portfolio research)"
REQUEST_DELAY_SEC = 1.0   # 같은 사이트 연속 요청 사이 대기
REQUEST_TIMEOUT_SEC = 15

# ── API 키 (.env 에서 읽음) ───────────────────────────
load_dotenv(ROOT / ".env")
DART_API_KEY = os.getenv("DART_API_KEY")

# ── DART API ─────────────────────────────────────────
DART_BASE_URL = "https://opendart.fss.or.kr/api"
DART_RAW = RAW_DART_DIR   # dart_corp_code.py 에서 쓰는 이름
