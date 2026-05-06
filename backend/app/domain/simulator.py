"""
5년 시나리오 시뮬레이터
Bear / Base / Bull 3가지 시나리오로 5년 후 예상 주가 및 수익률 계산
텐배거(+900%) 달성을 위한 필요 EPS CAGR 역산
"""


def simulate_5year(
    current_price: int,
    current_eps: float,
    eps_cagr_scenarios: dict,   # {"bear": 5, "base": 15, "bull": 25}
    per_scenarios: dict,        # {"bear": 10, "base": 15, "bull": 20}
    dividend_yield: float = 0.0,
    hold_years: int = 5,
) -> dict:
    """
    5년 후 주가 = EPS_5Y × PER_목표
    총수익률 = 주가상승률 + 배당 누적 (단순합산)
    텐배거 달성 조건: 필요 EPS CAGR 역산 (이진탐색)
    """
    results = {}

    for scenario in ["bear", "base", "bull"]:
        eps_cagr = eps_cagr_scenarios[scenario]
        target_per = per_scenarios[scenario]

        # 5년 후 EPS
        eps_5y = current_eps * ((1 + eps_cagr / 100) ** hold_years)

        # 5년 후 목표 주가
        target_price = eps_5y * target_per

        # 수익률 계산
        price_return = (target_price / current_price - 1) * 100
        dividend_total = dividend_yield * hold_years  # 단순 합산 (복리 미적용)
        total_return = price_return + dividend_total

        # CAGR 계산 (배당 포함 총수익 기준)
        total_multiplier = target_price / current_price * (1 + dividend_yield / 100) ** hold_years
        cagr_pct = (total_multiplier ** (1 / hold_years) - 1) * 100 if total_multiplier > 0 else 0

        results[scenario] = {
            "eps_cagr": eps_cagr,
            "target_per": target_per,
            "eps_5y": round(eps_5y, 0),
            "target_price": int(target_price),
            "price_return": round(price_return, 1),
            "dividend_total": round(dividend_total, 1),
            "total_return": round(total_return, 1),
            "cagr": round(cagr_pct, 1),
            "is_tenbagger": price_return >= 900,
        }

    # 텐배거 달성을 위한 필요 EPS CAGR 역산 (Base PER 사용, 이진탐색)
    base_per = per_scenarios.get("base", 15)
    required_cagr = _calc_required_cagr_for_tenbagger(
        current_price=current_price,
        current_eps=current_eps,
        target_per=base_per,
        hold_years=hold_years,
        tenbagger_threshold=9.0,  # 10배 = 900% 수익
    )
    results["tenbagger_required_cagr"] = required_cagr

    # 현재 시나리오 중 텐배거 달성 가능한 최소 시나리오
    tenbagger_scenarios = [s for s in ["bear", "base", "bull"] if results[s]["is_tenbagger"]]
    results["tenbagger_possible"] = tenbagger_scenarios[0] if tenbagger_scenarios else None

    return results


def _calc_required_cagr_for_tenbagger(
    current_price: int,
    current_eps: float,
    target_per: float,
    hold_years: int,
    tenbagger_threshold: float = 9.0,
) -> float:
    """
    이진탐색으로 텐배거 달성에 필요한 최소 EPS CAGR 계산
    target_price >= current_price * (1 + tenbagger_threshold) 가 되는 CAGR
    """
    target_price_needed = current_price * (1 + tenbagger_threshold)

    lo, hi = -50.0, 200.0
    for _ in range(60):
        mid = (lo + hi) / 2
        eps_5y = current_eps * ((1 + mid / 100) ** hold_years)
        projected = eps_5y * target_per
        if projected >= target_price_needed:
            hi = mid
        else:
            lo = mid

    result = round((lo + hi) / 2, 1)
    # 비현실적인 값이면 None 반환
    if result > 150 or result < -50:
        return None
    return result
