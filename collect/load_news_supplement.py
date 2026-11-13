"""
[A·D2~4] 보강 기사 CSV → documents 적재 (news_origin = 'supplement')

입력: data/raw/news_supplement/supplement_news.csv (supplement_fetch.py 결과)
      컬럼: 제목, 본문전문, 원문URL, 발행일, 매체명, 문서종류, 수집키워드, 기업명, fetch_status
출력: data/processed/project.db 의 documents (+ news_company)

보강 기사는 앵커 이벤트 근거로만 쓴다. Q1·Q2 집계에는 넣지 않는다.

실행: python collect/load_news_supplement.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402,F401


def main() -> None:
    # TODO 1. fetch_status == 'ok' 인 행만 사용 (실패 건수는 출력해서 기록)
    # TODO 2. 제공 뉴스와 같은 기사(title_norm 일치)는 건너뛰기 → 제공 뉴스 쪽을 근거로 사용
    # TODO 3. doc_id = 'S' + 일련번호, source_type = 'news', news_origin = 'supplement'
    # TODO 4. 기업명 → companies.corp_code 로 바꿔 news_company 에 연결 (in_title 계산)
    # TODO 5. documents 에 INSERT OR REPLACE (재실행 가능하게)
    raise NotImplementedError


if __name__ == "__main__":
    main()
