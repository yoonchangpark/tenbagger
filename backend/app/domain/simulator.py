"""
미래 가치 시뮬레이터
Bear / Base / Bull 3가지 시나리오로 N년 후 예상 주가 및 수익률 계산
투자 원금 입력 시 연도별 금액 추이 + 벤치마크 비교 제공
"""


def simulate_5year(
    current_price: int,
    current_eps: float,
    eps_cagr_scenarios: dict,   # {"bear": 5, "base": 15, "bull": 25}
    per_scenarios: dict,        # {"bear": 10, "base": 15, "bull": 20}
    dividend_yield: float = 0.0,
    hold_years: int = 5,
    invest_amount: int = 0,     # 0이면 % 수익률만, 양수이면 금액 추이 포함
) -> dict:
    """
    N년 후 주가 = EPS_NY × PER_목표
    총수익률 = 주가상승률 + 배당 누적 (단순합산)
    텐배거 달성 조건: 필요 EPS CAGR 역산 (이진탐색)
    invest_amount > 0: CAGR 기반 연도별 금액 추이 계산
    """
    results = {}

    for scenario in ["bear", "base", "bull"]:
        eps_cagr = eps_cagr_scenarios[scenario]
        target_per = per_scenarios[scenario]

        # N년 후 EPS
        eps_ny = current_eps * ((1 + eps_cagr / 100) ** hold_years)

        # N년 후 목표 주가
        target_price = eps_ny * target_per

        # 수익률 계산
        price_return = (target_price / current_price - 1) * 100
        dividend_total = dividend_yield * hold_years
        total_return = price_return + dividend_total

        # CAGR 계산 (배당 포함 총수익 기준)
        total_multiplier = target_price / current_price * (1 + dividend_yield / 100) ** hold_years
        cagr_pct = (total_multiplier ** (1 / hold_years) - 1) * 100 if total_multiplier > 0 else 0

        # 투자 금액 기반 연도별 금액 추이 (CAGR 복리 적용)
        yearly_values = None
        final_amount = None
        total_gain = None
        if invest_amount > 0:
            cagr_rate = cagr_pct / 100
            yearly_values = [
                round(invest_amount * ((1 + cagr_rate) ** yr))
                for yr in range(hold_years + 1)
            ]
            final_amount = yearly_values[-1]
            total_gain = final_amount - invest_amount

        results[scenario] = {
            "eps_cagr": eps_cagr,
            "target_per": target_per,
            "eps_5y": round(eps_ny, 0),
            "target_price": int(target_price),
            "price_return": round(price_return, 1),
            "dividend_total": round(dividend_total, 1),
            "total_return": round(total_return, 1),
            "cagr": round(cagr_pct, 1),
            "is_tenbagger": price_return >= 900,
            "yearly_values": yearly_values,
            "final_amount": final_amount,
            "total_gain": total_gain,
        }

    # 텐배거 달성을 위한 필요 EPS CAGR 역산 (Base PER 사용, 이진탐색)
    base_per = per_scenarios.get("base", 15)
    required_cagr = _calc_required_cagr_for_tenbagger(
        current_price=current_price,
        current_eps=current_eps,
        target_per=base_per,
        hold_years=hold_years,
        tenbagger_threshold=9.0,
    )
    results["tenbagger_required_cagr"] = required_cagr

    tenbagger_scenarios = [s for s in ["bear", "base", "bull"] if results[s]["is_tenbagger"]]
    results["tenbagger_possible"] = tenbagger_scenarios[0] if tenbagger_scenarios else None

    return results


def calculate_benchmarks(invest_amount: int, years: int) -> dict:
    """
    적금·서울아파트·물가 장기 역사 평균 기반 복리 성장 계산 (정적 상수)
    실시간 API 불필요 — 장기 비교 기준으로 충분
    """
    benchmarks = {
        "savings":   {"rate": 3.5, "label": "적금"},
        "housing":   {"rate": 6.0, "label": "서울아파트"},
        "inflation": {"rate": 2.5, "label": "물가상승률"},
    }
    result = {}
    for key, info in benchmarks.items():
        rate = info["rate"] / 100
        yearly = [round(invest_amount * ((1 + rate) ** yr)) for yr in range(years + 1)]
        result[key] = {
            "rate": info["rate"],
            "label": info["label"],
            "yearly_values": yearly,
            "final_amount": yearly[-1],
        }
    return result


def _calc_required_cagr_for_tenbagger(
    current_price: int,
    current_eps: float,
    target_per: float,
    hold_years: int,
    tenbagger_threshold: float = 9.0,
) -> float:
    """이진탐색으로 텐배거 달성에 필요한 최소 EPS CAGR 계산"""
    target_price_needed = current_price * (1 + tenbagger_threshold)

    lo, hi = -50.0, 200.0
    for _ in range(60):
        mid = (lo + hi) / 2
        eps_ny = current_eps * ((1 + mid / 100) ** hold_years)
        projected = eps_ny * target_per
        if projected >= target_price_needed:
            hi = mid
        else:
            lo = mid

    result = round((lo + hi) / 2, 1)
    if result > 150 or result < -50:
        return None
    return result
