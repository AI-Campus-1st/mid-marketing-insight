"""
[B · Day 1] DART 고유번호(corp_code) 확인 스크립트.

하는 일
    1. DART에서 전체 기업 고유번호 파일(corpCode.xml, ZIP)을 내려받아 원본으로 저장한다.
    2. 분석 대상 8개사를 '종목코드'로 찾아 corp_code와 DART 등록 회사명을 확인한다.
    3. 결과를 config/companies.csv 로 저장한다. (스키마의 companies 테이블과 같은 컬럼)

왜 종목코드로 찾나
    회사명은 DART 등록명과 다를 수 있다. (예: 영문/한글 표기 차이, 사명 변경)
    종목코드는 상장사마다 하나라서 더 정확하다. 찾은 뒤 회사명을 눈으로 다시 확인한다.

실행
    python3 collect/dart_corp_code.py            # 원본이 있으면 재사용
    python3 collect/dart_corp_code.py --refresh  # 원본을 새로 내려받기

다음 단계에서 사용하는 곳
    collect/dart_collect.py 가 companies.csv 의 corp_code 로 공시 목록을 수집한다.
"""
import argparse
import io
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import requests

# config 폴더를 불러오기 위해 프로젝트 루트를 경로에 추가
sys.path.append(str(Path(__file__).resolve().parent.parent))
from config.settings import COMPANIES_CSV, DART_API_KEY, DART_BASE_URL, DART_RAW  # noqa: E402

# 분석 대상 8개사: (종목코드, 기획안의 회사명, 유형, 핵심 기업 여부)
TARGETS = [
    ("030000", "제일기획", "종합대행사", 1),
    ("214320", "이노션", "종합대행사", 0),
    ("035000", "HS애드", "종합대행사", 0),
    ("089600", "KT나스미디어", "미디어렙", 1),
    ("216050", "인크로스", "미디어렙", 0),
    ("237820", "플레이디", "퍼포먼스", 0),
    ("035420", "NAVER", "플랫폼", 1),
    ("035720", "카카오", "플랫폼", 0),
]

ZIP_PATH = DART_RAW / "corpCode.zip"


def download_corp_code(refresh: bool = False) -> bytes:
    """
    corpCode.zip 을 내려받아 data/raw/dart/ 에 저장하고 ZIP 바이트를 돌려준다.

    Args:
        refresh: True면 이미 저장된 파일이 있어도 새로 받는다.
    Returns:
        ZIP 파일 바이트
    """
    if ZIP_PATH.exists() and not refresh:
        print(f"[재사용] 저장된 원본을 사용합니다: {ZIP_PATH}")
        return ZIP_PATH.read_bytes()

    if not DART_API_KEY:
        sys.exit("[오류] .env 에 DART_API_KEY 가 없습니다. 프로젝트 루트의 .env 를 확인하세요.")

    resp = requests.get(f"{DART_BASE_URL}/corpCode.xml",
                        params={"crtfc_key": DART_API_KEY}, timeout=60)
    resp.raise_for_status()

    # 키가 틀리면 ZIP 대신 오류 메시지(JSON/XML)가 온다. ZIP 서명(PK)으로 구분한다.
    if not resp.content.startswith(b"PK"):
        sys.exit(f"[오류] ZIP이 아닌 응답을 받았습니다. 인증키를 확인하세요.\n{resp.text[:300]}")

    ZIP_PATH.parent.mkdir(parents=True, exist_ok=True)
    ZIP_PATH.write_bytes(resp.content)
    print(f"[저장] {ZIP_PATH} ({len(resp.content):,} bytes)")
    return resp.content


def parse_corp_code(zip_bytes: bytes) -> pd.DataFrame:
    """
    ZIP 안의 CORPCODE.xml 을 읽어 상장사만 표(DataFrame)로 만든다.

    Returns:
        corp_code, corp_name, stock_code 컬럼의 DataFrame (종목코드가 있는 회사만)
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        xml_name = zf.namelist()[0]
        root = ET.fromstring(zf.read(xml_name))

    rows = []
    for item in root.iter("list"):
        stock_code = (item.findtext("stock_code") or "").strip()
        if stock_code:  # 비상장사는 종목코드가 비어 있으므로 제외
            rows.append({
                "corp_code": item.findtext("corp_code").strip(),
                "corp_name": item.findtext("corp_name").strip(),
                "stock_code": stock_code,
            })
    return pd.DataFrame(rows)


def match_targets(listed: pd.DataFrame) -> pd.DataFrame:
    """
    분석 대상 8개사를 종목코드로 찾아 companies.csv 형식으로 만든다.
    찾지 못한 회사는 corp_code 를 비워 두고 경고를 출력한다.
    """
    by_code = listed.set_index("stock_code")
    out = []
    for stock_code, plan_name, ctype, is_core in TARGETS:
        if stock_code in by_code.index:
            hit = by_code.loc[stock_code]
            out.append({"corp_code": hit["corp_code"], "corp_name": hit["corp_name"],
                        "stock_code": stock_code, "company_type": ctype, "is_core": is_core,
                        "plan_name": plan_name})
        else:
            print(f"[확인 필요] {plan_name}({stock_code}) 를 DART 상장사 목록에서 찾지 못했습니다.")
            out.append({"corp_code": "", "corp_name": "", "stock_code": stock_code,
                        "company_type": ctype, "is_core": is_core, "plan_name": plan_name})
    return pd.DataFrame(out)


def main():
    parser = argparse.ArgumentParser(description="DART corp_code 확인")
    parser.add_argument("--refresh", action="store_true", help="원본 ZIP 새로 받기")
    args = parser.parse_args()

    listed = parse_corp_code(download_corp_code(args.refresh))
    print(f"[정보] DART 상장사 수: {len(listed):,}")

    result = match_targets(listed)
    # 기획안 이름과 DART 등록명이 다르면 눈으로 확인할 수 있게 표시
    result["name_check"] = result.apply(
        lambda r: "OK" if r["corp_name"] == r["plan_name"] else "이름 확인", axis=1)
    print(result[["plan_name", "corp_name", "stock_code", "corp_code", "name_check"]].to_string(index=False))

    COMPANIES_CSV.parent.mkdir(parents=True, exist_ok=True)
    result.drop(columns=["plan_name", "name_check"]).to_csv(COMPANIES_CSV, index=False, encoding="utf-8-sig")
    print(f"[저장] {COMPANIES_CSV}")


if __name__ == "__main__":
    main()
