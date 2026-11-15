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

# ── 분석 공통 ────────────────────────────────────────
AI_TERM_PATTERN = r"AI|인공지능|생성형|LLM|GPT"

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
