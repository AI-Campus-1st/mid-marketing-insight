"""
프로젝트 공통 설정 모듈.

모든 수집·분석 스크립트가 이 파일에서 경로와 기간을 가져온다.
경로를 한 곳에서 관리하면 폴더 구조가 바뀌어도 이 파일만 고치면 된다.

사용처: collect/*.py, analysis/*.py, rag/*.py, dashboard/*.py
"""
from pathlib import Path
import os

from dotenv import load_dotenv

# 프로젝트 루트: 이 파일(config/settings.py)의 한 단계 위 폴더
ROOT = Path(__file__).resolve().parent.parent

# .env 파일에서 API 키를 읽어 환경변수로 등록한다. (키는 코드에 직접 쓰지 않는다)
load_dotenv(ROOT / ".env")
DART_API_KEY = os.getenv("DART_API_KEY")

# ----- 경로 -----
DATA_RAW = ROOT / "data" / "raw"
DART_RAW = DATA_RAW / "dart"            # DART API 응답 원본 저장 위치
DATA_PROCESSED = ROOT / "data" / "processed"
DB_PATH = DATA_PROCESSED / "project.db"
COMPANIES_CSV = ROOT / "config" / "companies.csv"

# ----- 수집 기간 (뉴스 데이터와 동일하게 맞춘다) -----
START_DATE = "20231019"
END_DATE = "20261019"

# ----- DART API -----
DART_BASE_URL = "https://opendart.fss.or.kr/api"
REQUEST_DELAY_SEC = 0.7                 # 호출 제한을 고려한 요청 간 대기 시간
