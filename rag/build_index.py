"""
[B·D7] 검색 파일(임베딩 캐시) 미리 만들기 — 대시보드가 켜질 때 임베딩 API 를 부르지 않게

    python rag/build_index.py

만드는 것 (data/vectordb/, 저장소 제외)
    chunk_emb_{모델}.npz : 공시 청크 전체 (공시 챗봇·기획안 검토·매칭 후보 공용, rag/embeddings.py)
    news_emb_{모델}.npz  : 제공 뉴스 중 관련 기사 (기획안 검토의 뉴스 사례, rag/news_retriever.py)
                           뉴스 결과(llm_results·news_preprocessed.csv)가 없는 DB 면 건너뜀

왜: 검색기(Retriever·NewsRetriever)는 캐시가 없으면 처음 만들 때 임베딩 API 를 부른다.
    이 단계를 실행 순서에 넣어 두면 대시보드는 만들어 둔 파일만 읽고, 화면에서 부르는 API 는
    사용자 입력에 답하는 것(챗봇 질문·기획안 검토)뿐이다 (decisions.md '대시보드 API 호출 범위').
이미 만든 캐시가 있으면 본문이 바뀐 것만 새로 부른다 (처음 한 번: 공시 약 2,000개 + 뉴스 약 2,500건, $0.2 미만).
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402
from rag.embeddings import CACHE as CHUNK_CACHE, chunk_embeddings  # noqa: E402


def main() -> None:
    con = sqlite3.connect(settings.DB_PATH)
    ids, mat = chunk_embeddings(con)
    print(f"공시 청크 {len(ids):,}개 → {CHUNK_CACHE.relative_to(settings.ROOT)}")
    try:
        from rag.news_retriever import CACHE as NEWS_CACHE, load_news, news_embeddings
        news = load_news(con)
    except Exception as e:  # 뉴스 결과가 없는 DB (A 의 handoff 전)
        print(f"뉴스 건너뜀: 뉴스 결과가 이 DB 에 없어요 ({type(e).__name__}). analysis/handoff.py import 뒤 다시 실행")
        return
    news_embeddings(news)
    print(f"관련 뉴스 {len(news):,}건 → {NEWS_CACHE.relative_to(settings.ROOT)}")


if __name__ == "__main__":
    main()
