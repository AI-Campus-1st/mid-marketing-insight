"""
[B·D2] DART 정기공시 원문(document.xml) 내려받기 → 텍스트 추출

하는 일
    1. data/raw/dart/disclosure_list.csv 에서 정기공시(pblntf_ty = 'A': 사업·반기·분기보고서)를 고른다.
    2. 공시별 원문 ZIP 을 data/raw/dart/document/{rcept_no}.zip 에 저장한다. (있으면 건너뜀)
    3. ZIP 안의 XML 에서 태그를 걷어 내고 텍스트만 data/processed/dart_text/{rcept_no}.txt 로 저장한다.
       - 목차 제목(TITLE 태그)은 '## 제목' 줄로 남긴다. rag/chunk.py 가 섹션을 나누는 기준.
       - 표(TABLE)의 칸은 ' | ' 로 이어 한 줄로 만든다.
       - ZIP 안에 파일이 여러 개면(본문 + 감사보고서 등 첨부) '# 파일명' 줄로 구분해 이어 붙인다.
    4. 추출한 텍스트를 DB documents.body 에, ZIP 경로를 documents.raw_path 에 넣는다.
       (공시 행은 collect/dart_collect.py 가 먼저 만들어 둔다)

실행
    python collect/dart_document.py                 # 받지 않은 원문만 받기
    python collect/dart_document.py --retext        # 저장된 ZIP 에서 텍스트만 다시 추출
    python collect/dart_document.py --rcept 20260310002959   # 특정 공시만

다음 단계
    rag/chunk.py 가 dart_text/*.txt 를 섹션 단위로 청킹한다.
"""
import argparse
import html
import io
import re
import sqlite3
import sys
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings  # noqa: E402

LIST_CSV = settings.RAW_DART_DIR / "disclosure_list.csv"
ZIP_DIR = settings.RAW_DART_DIR / "document"
TEXT_DIR = settings.DATA_DIR / "processed" / "dart_text"


def download(rcept_no: str) -> Path:
    """원문 ZIP 을 받아 저장하고 경로를 돌려준다. 이미 있으면 다시 받지 않는다."""
    path = ZIP_DIR / f"{rcept_no}.zip"
    if path.exists():
        return path

    resp = requests.get(f"{settings.DART_BASE_URL}/document.xml",
                        params={"crtfc_key": settings.DART_API_KEY, "rcept_no": rcept_no},
                        timeout=60)
    resp.raise_for_status()
    time.sleep(settings.REQUEST_DELAY_SEC)

    # 오류(인증키, 호출 한도, 원문 없음)는 ZIP 대신 XML 메시지로 온다. ZIP 서명(PK)으로 구분한다.
    if not resp.content.startswith(b"PK"):
        raise RuntimeError(resp.text[:300])

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(resp.content)
    return path


def decode(raw: bytes) -> str:
    """DART 원문은 대부분 UTF-8 이고 오래된 문서는 EUC-KR 이다."""
    for enc in ("utf-8", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def xml_to_text(xml: str) -> str:
    """DART 공시 XML 을 사람이 읽을 수 있는 텍스트로 바꾼다."""
    # 목차 제목 → '## 제목' (rag/chunk.py 의 섹션 구분 기준)
    xml = re.sub(r"<TITLE[^>]*>(.*?)</TITLE>",
                 lambda m: "\n## " + re.sub(r"<[^>]+>", "", m.group(1)).strip() + "\n",
                 xml, flags=re.S)
    # 표: 칸 끝 → ' | ', 행 끝 → 줄바꿈
    xml = re.sub(r"</(TD|TH|TE|TU)>", " | ", xml)
    xml = re.sub(r"</TR>", "\n", xml)
    # 문단·줄바꿈 태그 → 줄바꿈
    xml = re.sub(r"<(/P|BR|/SECTION-\d|/TABLE)[^>]*>", "\n", xml)
    # 나머지 태그 제거, &amp; 같은 문자 참조 복원
    text = html.unescape(re.sub(r"<[^>]+>", "", xml))
    # 공백 정리: 줄 안의 연속 공백 → 한 칸, 빈 줄은 하나만
    lines = [re.sub(r"[ \t ]+", " ", ln).strip(" |") for ln in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(ln for ln in lines)).strip() + "\n"


def extract(zip_path: Path) -> str:
    """ZIP 안의 모든 XML 을 텍스트로 바꿔 이어 붙인다. 본문 파일(이름이 가장 짧은 것)을 맨 앞에 둔다."""
    with zipfile.ZipFile(zip_path) as zf:
        names = sorted(zf.namelist(), key=lambda n: (len(n), n))
        parts = [f"# {name}\n\n{xml_to_text(decode(zf.read(name)))}" for name in names]
    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description="DART 정기공시 원문 수집·텍스트 추출")
    parser.add_argument("--retext", action="store_true", help="저장된 ZIP 에서 텍스트를 다시 추출")
    parser.add_argument("--rcept", nargs="*", help="이 접수번호만 처리")
    args = parser.parse_args()

    if not LIST_CSV.exists():
        sys.exit("[오류] disclosure_list.csv 가 없습니다. collect/dart_collect.py 를 먼저 실행하세요.")
    if not settings.DART_API_KEY:
        sys.exit("[오류] .env 에 DART_API_KEY 가 없습니다.")

    df = pd.read_csv(LIST_CSV, dtype=str)
    targets = df[df["pblntf_ty"] == "A"]
    if args.rcept:
        targets = targets[targets["rcept_no"].isin(args.rcept)]
    print(f"[정보] 정기공시 {len(targets)}건 처리")

    if not settings.DB_PATH.exists():
        sys.exit("[오류] project.db 가 없습니다. collect/dart_collect.py 를 먼저 실행하세요.")
    con = sqlite3.connect(settings.DB_PATH)

    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    failed = []
    for i, r in enumerate(targets.itertuples(), 1):
        txt_path = TEXT_DIR / f"{r.rcept_no}.txt"
        try:
            zip_path = download(r.rcept_no)
            if args.retext or not txt_path.exists():
                txt_path.write_text(extract(zip_path), encoding="utf-8")
            body = txt_path.read_text(encoding="utf-8")
            # raw_path 는 프로젝트 루트 기준 상대경로 (다른 컴퓨터에서도 같은 값)
            updated = con.execute(
                "UPDATE documents SET body = ?, raw_path = ? WHERE doc_id = ?",
                (body, str(zip_path.relative_to(settings.ROOT)), "D" + r.rcept_no),
            ).rowcount
            if updated == 0:
                raise RuntimeError("DB 에 공시 행이 없습니다. dart_collect.py 를 다시 실행하세요.")
            con.commit()
            print(f"  [{i}/{len(targets)}] {r.corp_name} {r.report_nm} → {len(body):,}자")
        except Exception as e:  # 한 건 실패로 전체가 멈추지 않게 기록만 하고 넘어간다
            failed.append(r.rcept_no)
            print(f"  [{i}/{len(targets)}] [실패] {r.corp_name} {r.report_nm} ({r.rcept_no}): {e}")
    con.close()

    print(f"[완료] 성공 {len(targets) - len(failed)}건, 실패 {len(failed)}건")
    if failed:
        print("  실패한 접수번호 (다시 실행하면 이것만 재시도):", " ".join(failed))


if __name__ == "__main__":
    main()
