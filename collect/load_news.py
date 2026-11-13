"""
[A·D2] 제공 뉴스 CSV → documents 적재 (news_origin = 'provided')

입력: data/raw/news/ 의 묶음 파일 (01_마케팅_묶음_마케팅_6469건_*.csv)
      컬럼: 제목, 본문전문, 원문URL, 발행일, 매체명, 문서종류, 수집키워드
출력: data/processed/project.db 의 documents

실행: python collect/load_news.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402,F401


def main() -> None:
    # TODO 1. 묶음 CSV 찾기 (macOS 파일명 NFD → unicodedata.normalize('NFC') 로 비교)
    # TODO 2. 컬럼 매핑: 제목→title, 본문전문→body, 원문URL→url, 발행일→published_date,
    #         매체명→media, 수집키워드→keyword  (문서종류는 전부 'news' 라 저장 안 함)
    # TODO 3. doc_id = 'N' + 일련번호, source_type = 'news', news_origin = 'provided'
    # TODO 4. title_norm, body_hash 계산 → 같은 값이면 is_duplicate = 1 (원문URL은 중복 기준으로 쓰지 않음)
    # TODO 5. 본문 결측 2건 처리 방법 정하기 → decisions.md 기록
    # TODO 6. documents 에 INSERT OR REPLACE (재실행 가능하게)
    raise NotImplementedError


if __name__ == "__main__":
    main()
