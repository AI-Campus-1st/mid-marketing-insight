"""AI 실행 단계 정의 (L0~L3) — 뉴스·공시 공통 기준

뉴스의 '회사가 주장한 수준'(ai_events.ai_level_claimed)과
공시의 '확인되는 수준'(disclosure_chunks.ai_level)을 같은 자로 재야
말-실행 격차(= 뉴스 수준 − 공시 수준)를 계산할 수 있다.

- 사람용 설명·예시·검수 방법: docs/ai_level_guide.md
- 결정 기록: docs/decisions.md (Day 2, 4단계 채택)
- 공시 분류 프롬프트는 prompt_block("disclosure") 를 그대로 넣어 쓴다.
"""

LEVELS = {
    0: ("언급", "AI 가 트렌드·환경·업계 전망으로만 나온다. 주어가 시장·업계이거나 회사의 의지가 없다."),
    1: ("계획", "회사가 주어이고 미래형이다 (~할 예정, 추진, 검토). 대상이 특정된다."),
    2: ("도입·적용", "외부 AI 를 실제 업무·캠페인·서비스에 사용했다 (과거·현재형)."),
    3: ("자체 개발·상용화", "자체 개발, AI 서비스 출시·판매, AI 전담 조직, 특허, 매출·투자 금액 등 숫자가 있다."),
}

# 두 단계 사이에서 갈릴 때 적용하는 규칙 (뉴스·공시 공통)
BOUNDARY_RULES = [
    "미래형(~할 예정, 추진, 계획)이면 2 이상이 될 수 없다.",
    "고유 서비스명·조직명이나 숫자(매출·인원·건수·금액)가 있으면 한 단계 위로 본다. 단 3 을 넘지 않는다.",
    "2 와 3 은 'AI 가 들어간 결과물이 누구의 제품인가'로 가른다. 외부 모델·기술을 써도 자사 제품·서비스로 출시·제공하면 3, 외부 완성 솔루션을 쓰기만 하거나 다른 회사(모회사 포함) AI 서비스를 운영 대행하면 2. 불분명하면 2 로 본다.",  # Day 3 A·B 합의 (PR #13): 기반 기술이 아니라 결과물 기준
]

# 공시에만 추가로 적용하는 규칙 (docs/ai_level_guide.md 부록 A-4 근거)
DISCLOSURE_RULES = [
    "광고주 캠페인·상품 이름 속 AI (예: 'BESPOKE AI 냉장고', 'AI 가전 트로이카')는 회사의 AI 가 아니다 → 단계 없음(null).",
    "영문 회사명·지명 속 글자(HYUNDAI, THAILAND 등)처럼 AI 와 무관한 정규표현식 오탐 → 단계 없음(null).",
    "업계 일반론·시장 전망 인용 ('광고 산업은 AI 기술 혁신을 축으로 성장') → 0.",
    "회사가 주어인 막연한 방향 ('AI 기술 변화에 적극 대응', '적용 과제를 발굴·검토하는 체계') → 1.",
    "연구개발 실적·출시 실적 문장('~를 개발 완료', '~기능 오픈', '특허 출원')은 대부분 3 이다. 주로 '6. 주요계약 및 연구개발활동' 에 있다.",
]


def level_name(level: int | None) -> str:
    return "단계 없음" if level is None else f"L{level} {LEVELS[level][0]}"


def prompt_block(target: str = "disclosure") -> str:
    """LLM 프롬프트에 넣을 단계 정의 문단. target: 'news' 또는 'disclosure'"""
    lines = ["[AI 단계 ai_level] 0~3 중 하나, 해당 없으면 null"]
    lines += [f"  {k} {name}: {desc}" for k, (name, desc) in LEVELS.items()]
    lines.append("[경계 규칙]")
    lines += [f"  - {r}" for r in BOUNDARY_RULES]
    if target == "disclosure":
        lines.append("[공시 추가 규칙]")
        lines += [f"  - {r}" for r in DISCLOSURE_RULES]
    return "\n".join(lines)


if __name__ == "__main__":
    print(prompt_block("disclosure"))
