"""
③ 내 돈 그려보기 — 약세·기본·강세 시나리오와 그 가정의 출처

오너 결정(2026-11-24): 가정은 '회사 과거 실적 + 비슷한 과거 사례의 범위'로 잡고 출처를 보여 준다.
등급별 고정 기대수익은 쓰지 않는다 (ops/product/proof.md #6).

규칙 (응답의 assumptions.rule에 그대로 싣는다):
- 비교 사례 = backfill_results에 쌓인 과거 보유 사례의 연환산 수익률 분포
  (아직 5고리 단계별 사례가 없으므로 전체 표본. 단계별 사례가 쌓이면 좁힌다)
- 약세 = 사례 하위 25%, 기본 = 사례 중앙값, 강세 = min(회사 최근 2년 매출 CAGR, 사례 상위 10%)
- 사례가 5개 미만이면 회사 실적만: 약세 0%, 기본 = 회사 CAGR의 절반, 강세 = 회사 CAGR
- 모든 연 성장률은 -20% ~ +30%로 자른다 (연 30%를 10년 이어 가면 약 13.8배. 그 이상은 가정이 아니라 기대다)
"""
from typing import Optional

from sqlalchemy import text

from app.core.database import SessionLocal

MIN_CASES = 5
CLAMP = (-20.0, 30.0)
RULE = ("약세=비교 사례 하위 25%, 기본=사례 중앙값, 강세=min(회사 최근 2년 매출 CAGR, 사례 상위 10%). "
        "사례가 5개 미만이면 약세 0%, 기본=회사 CAGR의 절반, 강세=회사 CAGR. 연 -20%~+30%로 제한.")


def _clamp(v: float) -> float:
    return round(max(CLAMP[0], min(CLAMP[1], v)), 1)


def _pct(sorted_vals: list[float], q: float) -> float:
    i = (len(sorted_vals) - 1) * q
    lo, hi = int(i), min(int(i) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo)


def case_returns() -> tuple[list[float], Optional[int]]:
    """backfill_results의 연환산 수익률(%) 목록과 그 보유 기간. 테이블이 없으면 빈 목록."""
    db = SessionLocal()
    try:
        if not db.execute(text("SELECT to_regclass('backfill_results')")).scalar():
            return [], None
        hold = db.execute(text("SELECT MAX(hold_years) FROM backfill_results WHERE return_pct IS NOT NULL")).scalar()
        if not hold:
            return [], None
        rows = db.execute(text("""
            SELECT return_pct FROM backfill_results
             WHERE hold_years = :h AND return_pct IS NOT NULL AND return_pct > -100
        """), {"h": hold}).fetchall()
        vals = [((1 + float(r[0]) / 100) ** (1 / hold) - 1) * 100 for r in rows]
        return sorted(vals), int(hold)
    finally:
        db.close()


def scenarios(company_cagr: Optional[float], cases: list[float], hold: Optional[int]) -> dict:
    if len(cases) >= MIN_CASES:
        bear, base = _pct(cases, 0.25), _pct(cases, 0.5)
        top = _pct(cases, 0.9)
        bull = min(company_cagr, top) if company_cagr is not None else top
        basis = f"비교 사례 {len(cases)}개({hold}년 보유, 전체 표본)"
    elif company_cagr is not None:
        bear, base, bull = 0.0, company_cagr / 2, company_cagr
        basis = f"비교 사례 {len(cases)}개 — 회사 실적만 사용"
    else:
        return {"available": False, "reason": "회사 실적과 비교 사례가 모두 없습니다"}
    bear, base, bull = sorted((_clamp(bear), _clamp(base), _clamp(bull)))
    return {"available": True, "bear": bear, "base": base, "bull": bull,
            "company_cagr": None if company_cagr is None else round(company_cagr, 1),
            "case_count": len(cases), "case_hold_years": hold, "basis": basis, "rule": RULE}


def future_value(amount: float, rate_pct: float, years: int, monthly: bool) -> list[int]:
    """연말마다의 평가액 (0년 차 포함). monthly면 amount를 매달 넣는다."""
    r = rate_pct / 100
    out = []
    for y in range(years + 1):
        if monthly:
            m = y * 12
            v = sum(amount * (1 + r) ** ((m - i) / 12) for i in range(m))
        else:
            v = amount * (1 + r) ** y
        out.append(round(v))
    return out


def simulate(company_cagr: Optional[float], amount: float, years: int, monthly: bool) -> dict:
    cases, hold = case_returns()
    sc = scenarios(company_cagr, cases, hold)
    if not sc["available"]:
        return {"assumptions": sc}
    invested = amount * years * 12 if monthly else amount
    paths = {k: future_value(amount, sc[k], years, monthly) for k in ("bear", "base", "bull")}
    return {
        "mode": "monthly" if monthly else "lump",
        "amount": amount, "years": years, "invested": round(invested),
        "assumptions": sc,
        "paths": paths,
        "final": {k: v[-1] for k, v in paths.items()},
        "note": "시나리오는 과거 데이터에 기반한 가정이며 예측이 아닙니다. 배당은 넣지 않았습니다.",
    }
