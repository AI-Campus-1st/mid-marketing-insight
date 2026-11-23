"""
[B·D7] report_B.md 그래프 3개 만들기 — ①·② 는 DB 에서 바로 읽고, ③ 은 rag/eval_retrieval.py 결과를 옮겨 적음
(③ 은 질문 임베딩에 API 가 필요해 그래프마다 다시 돌리지 않음. 2026-11-20 다시 돌려 같은 값 확인: 4/10(3) · 7/10(5) · 10/10(10))

    python docs/report/make_b_charts.py  → docs/report/img/report_b/b_levels.png · b_verdicts.png · b_retrieval.png

색: 대시보드와 같은 팔레트 (dashboard/theme.py, 색약 검증 도구로 라이트 테마 PASS) — 판정 = 파랑·주황·청록·회색, 단계 = 파랑 진하기
검색 성능 표(상위 5개 안 · 1위)는 rag/eval_retrieval.py 결과 (report_B.md 5-2 표와 같은 값)
"""
import sqlite3
import sys
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from config import settings  # noqa: E402
from dashboard.theme import LEVEL_NAME, LEVEL_ORDER, PALETTE, VERDICT_LABEL, verdict_ko  # noqa: E402

OUT = ROOT / "docs" / "report" / "img" / "report_b"
C = PALETTE["light"]
plt.rcParams.update({"font.family": "Apple SD Gothic Neo", "font.size": 11, "axes.unicode_minus": False,
                     "axes.edgecolor": C["grid"], "axes.labelcolor": C["text2"], "xtick.color": C["text2"],
                     "ytick.color": C["text"], "text.color": C["text"]})


def _clean(ax):
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(C["grid"])
    ax.tick_params(axis="y", length=0)


def levels() -> None:
    """① 기업별 AI 단계 분포 (청크 수) + 공시 내 L3 서술 비중"""
    import dashboard.data_loader as dl
    lv = dl.chunk_levels.__wrapped__() if hasattr(dl.chunk_levels, "__wrapped__") else dl.chunk_levels()
    lv = lv[lv.level.notna()]
    t = pd.crosstab(lv.corp_name, lv.level).reindex(columns=LEVEL_ORDER, fill_value=0)
    share = t["L3"] / t.sum(axis=1)
    t = t.loc[share.sort_values().index]
    fig, ax = plt.subplots(figsize=(9, 3.9), dpi=200)
    left = pd.Series(0, index=t.index)
    for lvl in LEVEL_ORDER:
        ax.barh(t.index, t[lvl], left=left, color=C["level"][lvl], edgecolor="white", linewidth=1.5, height=0.58,
                label=LEVEL_NAME[lvl])
        for y, (v, l) in enumerate(zip(t[lvl], left)):
            if v >= 12:   # 칸이 넓을 때만 숫자 (좁은 칸은 표에 있음)
                ax.text(l + v / 2, y, str(v), ha="center", va="center", fontsize=9.5,
                        color="white" if lvl in ("L1", "L2", "L3") else C["text"])
        left += t[lvl]
    for y, c in enumerate(t.index):
        ax.text(left[c] + 3, y, f"{t.sum(axis=1)[c]}개 · L3 서술 비중 {share[c]:.1%}", va="center", fontsize=10, color=C["text2"])
    ax.set_xlim(0, left.max() * 1.42)
    ax.set_xlabel("AI 용어가 있는 공시 청크 수 (L0~L3, '무관' 제외)", fontsize=10)
    ax.legend(ncol=4, loc="lower left", bbox_to_anchor=(0, 1.0), frameon=False, fontsize=10, handlelength=1.2)
    _clean(ax)
    fig.tight_layout()
    fig.savefig(OUT / "b_levels.png", facecolor="white")
    print("b_levels.png", t.to_dict("index"))


def verdicts() -> None:
    """② 뉴스 이벤트 25개의 공시 반영 판정"""
    con = sqlite3.connect(settings.DB_PATH)
    r = pd.read_sql("SELECT verdict FROM event_reflection WHERE analysis_scope = 'judge'", con)
    n = r.verdict.map(verdict_ko).value_counts().reindex(["제때", "늦게", "공시가 먼저", "미확인"], fill_value=0)
    total = int(n.sum())
    fig, ax = plt.subplots(figsize=(9, 2.9), dpi=200)
    labels = [VERDICT_LABEL[k] for k in n.index][::-1]
    vals = list(n.values)[::-1]
    cols = [C["verdict"][k] for k in n.index][::-1]
    ax.barh(labels, vals, color=cols, height=0.58)
    for y, v in enumerate(vals):
        ax.text(v + 0.25, y, f"{v}개 ({v / total:.0%})", va="center", fontsize=10.5, color=C["text"])
    found = int(n["제때"] + n["늦게"] + n["공시가 먼저"])
    ax.set_title(f"판정 대상 {total}개 중 {found}개({found / total:.0%})가 정기공시에서 확인됨 — 미확인이 실행하지 않았다는 뜻은 아님",
                 loc="left", fontsize=11, color=C["text2"], pad=10)
    ax.set_xlim(0, n.max() * 1.3)
    ax.set_xticks([])
    _clean(ax)
    ax.spines["bottom"].set_visible(False)
    fig.tight_layout()
    fig.savefig(OUT / "b_verdicts.png", facecolor="white")
    print("b_verdicts.png", n.to_dict())


RETRIEVAL = [   # rag/eval_retrieval.py — 답이 있는 질문 10개에서 정답 근거를 찾았나 (report_B.md 5-2)
    ("의미 검색만", 4, 3),
    ("+ 기업 필터", 7, 5),
    ("+ 키워드 혼합 · 구 일치 가산점 (최종)", 10, 10),
]


def retrieval() -> None:
    """③ 검색 방식별 정답 근거 찾기 (개선에 쓴 질문 중 답이 있는 10개)"""
    fig, ax = plt.subplots(figsize=(9, 3.0), dpi=200)
    names = [x[0] for x in RETRIEVAL][::-1]
    top5 = [x[1] for x in RETRIEVAL][::-1]
    top1 = [x[2] for x in RETRIEVAL][::-1]
    y = range(len(names))
    h = 0.32
    ax.barh([i + h / 2 for i in y], top5, height=h, color=C["verdict"]["제때"], label="상위 5개 안", edgecolor="white", linewidth=1.5)
    ax.barh([i - h / 2 for i in y], top1, height=h, color=C["verdict"]["늦게"], label="1위", edgecolor="white", linewidth=1.5)
    for i, (a, b) in enumerate(zip(top5, top1)):
        ax.text(a + 0.12, i + h / 2, f"{a}/10", va="center", fontsize=10)
        ax.text(b + 0.12, i - h / 2, f"{b}/10", va="center", fontsize=10)
    ax.set_yticks(list(y), names)
    ax.set_xlim(0, 11.5)
    ax.set_xticks([])
    ax.legend(ncol=2, loc="lower left", bbox_to_anchor=(0, 1.0), frameon=False, fontsize=10, handlelength=1.2)
    _clean(ax)
    ax.spines["bottom"].set_visible(False)
    fig.tight_layout()
    fig.savefig(OUT / "b_retrieval.png", facecolor="white")
    print("b_retrieval.png", RETRIEVAL)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    levels()
    verdicts()
    retrieval()
