"""
텐배거 스코어링 엔진
입력: 연간 재무데이터 리스트
출력: 카테고리별 점수 + 종합 점수 + 등급
"""
import math
from typing import Optional
from app.core.config import settings, THRESHOLDS, GRADE_THRESHOLDS


def safe_div(a, b) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    return a / b


def cagr(start, end, years) -> Optional[float]:
    """연평균 성장률 계산"""
    if not start or not end or years <= 0 or start <= 0:
        return None
    try:
        return (math.pow(end / start, 1 / years) - 1) * 100
    except Exception:
        return None


def normalize(value: float, low: float, high: float) -> float:
    """값을 0~10 사이로 정규화"""
    if value <= low:
        return 0.0
    if value >= high:
        return 10.0
    return round((value - low) / (high - low) * 10, 2)


# ── 카테고리별 점수 계산 ──────────────────────────────────────────────────────

def score_growth(financials: list[dict]) -> tuple[float, dict]:
    """
    성장성 점수 (0~10)
    - 매출 5Y CAGR (40%)
    - EPS 5Y CAGR  (40%)
    - ROE 평균      (20%)
    """
    if len(financials) < 2:
        return 0.0, {}

    sorted_f = sorted(financials, key=lambda x: x["year"])
    oldest, latest = sorted_f[0], sorted_f[-1]
    years = latest["year"] - oldest["year"]

    rev_cagr = cagr(oldest.get("revenue"), latest.get("revenue"), years)
    eps_cagr = cagr(oldest.get("eps") or oldest.get("net_income"),
                    latest.get("eps") or latest.get("net_income"), years)

    roe_list = []
    for f in sorted_f:
        roe = safe_div(f.get("net_income"), f.get("total_equity"))
        if roe:
            roe_list.append(roe * 100)
    avg_roe = sum(roe_list) / len(roe_list) if roe_list else None

    s_rev  = normalize(rev_cagr or 0, 0, THRESHOLDS["revenue_cagr_excellent"])
    s_eps  = normalize(eps_cagr or 0, 0, THRESHOLDS["eps_cagr_excellent"])
    s_roe  = normalize(avg_roe  or 0, 0, THRESHOLDS["roe_excellent"])

    score = s_rev * 0.4 + s_eps * 0.4 + s_roe * 0.2

    return round(score, 2), {
        "revenue_cagr_5y": round(rev_cagr, 2) if rev_cagr else None,
        "eps_cagr_5y": round(eps_cagr, 2) if eps_cagr else None,
        "avg_roe_5y": round(avg_roe, 2) if avg_roe else None,
    }


def score_stability(financials: list[dict]) -> tuple[float, dict]:
    """
    재무 안정성 점수 (0~10)
    - 부채비율    (50%)
    - 유동비율    (30%)
    - 이자보상배율 (20%)
    """
    if not financials:
        return 0.0, {}

    latest = sorted(financials, key=lambda x: x["year"])[-1]

    debt_ratio    = safe_div(latest.get("total_debt"), latest.get("total_equity"))
    current_ratio = safe_div(latest.get("current_assets"), latest.get("current_liab"))

    # 이자보상배율: 영업이익 / 이자비용 (이자비용 데이터가 없으면 부채비율로 대체)
    int_coverage = None
    if latest.get("operating_profit") and latest.get("total_debt"):
        # 이자비용 추정: 부채 * 4% (보수적)
        est_interest = latest["total_debt"] * 0.04
        int_coverage = safe_div(latest["operating_profit"], est_interest)

    # 부채비율: 낮을수록 좋음 → 역수로 점수화
    if debt_ratio is not None:
        debt_ratio_pct = debt_ratio * 100
        if debt_ratio_pct <= THRESHOLDS["debt_ratio_safe"]:
            s_debt = 10.0
        elif debt_ratio_pct >= THRESHOLDS["debt_ratio_caution"]:
            s_debt = 0.0
        else:
            s_debt = normalize(
                THRESHOLDS["debt_ratio_caution"] - debt_ratio_pct,
                0,
                THRESHOLDS["debt_ratio_caution"] - THRESHOLDS["debt_ratio_safe"]
            )
    else:
        s_debt = 5.0

    s_current  = normalize(current_ratio or 0, 0.5, 3.0)
    s_interest = normalize(int_coverage  or 0, 1.0, 10.0)

    score = s_debt * 0.5 + s_current * 0.3 + s_interest * 0.2

    return round(score, 2), {
        "debt_ratio": round(debt_ratio * 100, 2) if debt_ratio else None,
        "current_ratio": round(current_ratio, 2) if current_ratio else None,
    }


def score_cashflow(financials: list[dict]) -> tuple[float, dict]:
    """
    현금흐름 품질 점수 (0~10)
    - FCF 마진    (50%)
    - FCF/순이익  (30%) → 이익 품질
    - FCF 성장성  (20%)
    """
    if not financials:
        return 0.0, {}

    sorted_f = sorted(financials, key=lambda x: x["year"])

    fcf_margins = []
    fcf_quality = []
    fcf_values  = []

    for f in sorted_f:
        fcf = f.get("fcf")
        rev = f.get("revenue")
        ni  = f.get("net_income")

        if fcf and rev and rev > 0:
            fcf_margins.append(fcf / rev * 100)
        if fcf and ni and ni > 0:
            fcf_quality.append(fcf / ni)
        if fcf:
            fcf_values.append(fcf)

    avg_fcf_margin  = sum(fcf_margins) / len(fcf_margins) if fcf_margins else None
    avg_fcf_quality = sum(fcf_quality) / len(fcf_quality) if fcf_quality else None
    fcf_cagr_val = cagr(fcf_values[0], fcf_values[-1], len(fcf_values) - 1) \
        if len(fcf_values) >= 2 and fcf_values[0] > 0 else None

    s_margin  = normalize(avg_fcf_margin  or 0, 0, THRESHOLDS["fcf_margin_excellent"])
    s_quality = normalize(avg_fcf_quality or 0, 0, 1.5)
    s_growth  = normalize(fcf_cagr_val    or 0, 0, 25.0)

    score = s_margin * 0.5 + s_quality * 0.3 + s_growth * 0.2

    return round(score, 2), {
        "avg_fcf_margin": round(avg_fcf_margin, 2) if avg_fcf_margin else None,
    }


def score_dividend(financials: list[dict], dividend_yield: float = None) -> tuple[float, dict]:
    """
    배당 정책 점수 (0~10)
    - 배당성향 (50%) → 20~50%가 이상적 (재투자 여력 확인)
    - 배당수익률 (30%)
    - 배당 성장 일관성 (20%)
    """
    if not financials:
        return 0.0, {}

    payout_ratios = []
    dps_values = []

    for f in sorted(financials, key=lambda x: x["year"]):
        pr = f.get("payout_ratio")
        if pr and 0 < pr < 100:
            payout_ratios.append(pr)
        dps = f.get("dps")
        if dps and dps > 0:
            dps_values.append(dps)

    avg_payout = sum(payout_ratios) / len(payout_ratios) if payout_ratios else None

    # 배당성향: 20~50% 구간이 최고점
    if avg_payout:
        ideal_min = THRESHOLDS["payout_ratio_ideal_min"]
        ideal_max = THRESHOLDS["payout_ratio_ideal_max"]
        if ideal_min <= avg_payout <= ideal_max:
            s_payout = 10.0
        elif avg_payout < ideal_min:
            s_payout = normalize(avg_payout, 0, ideal_min) * 0.7
        else:
            s_payout = max(0, 10 - (avg_payout - ideal_max) / 10)
    else:
        s_payout = 3.0

    s_yield = normalize(dividend_yield or 0, 0, 4.0)

    # 배당 성장 일관성: 매년 증가 횟수
    div_growth_count = sum(
        1 for i in range(1, len(dps_values)) if dps_values[i] >= dps_values[i - 1]
    )
    s_consistency = normalize(div_growth_count, 0, max(len(dps_values) - 1, 1))

    score = s_payout * 0.5 + s_yield * 0.3 + s_consistency * 0.2

    return round(score, 2), {
        "avg_payout_ratio": round(avg_payout, 2) if avg_payout else None,
        "dividend_yield": dividend_yield,
    }


def score_consistency(financials: list[dict]) -> tuple[float, dict]:
    """
    실적 일관성 점수 (0~10)
    - 영업이익률 변동성 (40%) → 낮을수록 좋음
    - 적자 연도 수      (40%) → 0이 최고
    - 매출 성장 연속성  (20%)
    """
    if len(financials) < 2:
        return 0.0, {}

    sorted_f = sorted(financials, key=lambda x: x["year"])

    op_margins = []
    loss_years = 0
    rev_growth_count = 0

    for i, f in enumerate(sorted_f):
        rev = f.get("revenue")
        op  = f.get("operating_profit")
        ni  = f.get("net_income")

        if rev and op and rev > 0:
            op_margins.append(op / rev * 100)
        if ni is not None and ni < 0:
            loss_years += 1
        if i > 0:
            prev_rev = sorted_f[i - 1].get("revenue")
            if rev and prev_rev and rev > prev_rev:
                rev_growth_count += 1

    import statistics
    op_margin_std = statistics.stdev(op_margins) if len(op_margins) >= 2 else 10.0
    avg_op_margin = sum(op_margins) / len(op_margins) if op_margins else 0

    # 영업이익률 변동성: 낮을수록 좋음
    s_stability = max(0, 10 - op_margin_std)

    # 적자 연도: 0 → 10점, 1 → 6점, 2+ → 0점
    s_no_loss = 10.0 if loss_years == 0 else (6.0 if loss_years == 1 else 0.0)

    # 매출 연속 성장
    total_periods = len(sorted_f) - 1
    s_rev_growth = normalize(rev_growth_count, 0, max(total_periods, 1))

    score = s_stability * 0.4 + s_no_loss * 0.4 + s_rev_growth * 0.2

    return round(score, 2), {
        "avg_operating_margin": round(avg_op_margin, 2),
        "loss_years": loss_years,
    }


# ── DART 재무지표(financial_index) 기반 스코어링 ─────────────────────────────

def _find_idx(items: list, keyword: str) -> Optional[float]:
    """재무지표 리스트에서 키워드로 값 추출"""
    for it in items:
        if keyword in it.get("idx_nm", ""):
            val = it.get("idx_val", "")
            if val not in ("", None, "-"):
                try:
                    return float(val)
                except (ValueError, TypeError):
                    continue
    return None


def calculate_score_from_indices(idx_list: list) -> dict:
    """
    DART fnlttCmpnyIndx API 응답(4개 카테고리 합산 리스트)으로 텐배거 점수 계산.
    raw 재무제표 파싱 없이 DART에서 제공하는 전처리된 지표를 직접 사용.

    idx_list: M210000/M220000/M230000/M240000 모두 합산된 리스트
    """
    if not idx_list:
        return {"error": "재무지표 없음", "source": "index"}

    # ── 성장성 (30%) ──────────────────────────────────────────────────────────
    rev_g  = _find_idx(idx_list, "매출액증가율")
    op_g   = _find_idx(idx_list, "영업이익증가율")
    net_g  = _find_idx(idx_list, "순이익증가율")
    eq_g   = _find_idx(idx_list, "자기자본증가율")
    asset_g= _find_idx(idx_list, "총자산증가율")

    growth_vals = [v for v in [rev_g, op_g, net_g, eq_g] if v is not None]
    avg_growth  = sum(growth_vals) / len(growth_vals) if growth_vals else 0
    # 0%→5, +100%→10, -50%→2.5
    g_score = min(10.0, max(0.0, 5.0 + avg_growth / 20.0))

    # ── 안정성 (20%) ──────────────────────────────────────────────────────────
    debt  = _find_idx(idx_list, "부채비율")
    curr  = _find_idx(idx_list, "유동비율")
    quick = _find_idx(idx_list, "당좌비율")

    st_score = 5.0
    if debt is not None:
        st_score += (2.5 if debt < 50 else 1.5 if debt < 100 else
                     0.5 if debt < 150 else 0.0 if debt < 200 else -2.0)
    if curr is not None:
        st_score += (1.5 if curr > 250 else 0.5 if curr > 150 else
                     0.0 if curr > 100 else -1.0)
    st_score = min(10.0, max(0.0, st_score))

    # ── 수익성/현금흐름 (25%) ─────────────────────────────────────────────────
    roe     = _find_idx(idx_list, "ROE")
    roa     = _find_idx(idx_list, "ROA")
    op_mgn  = _find_idx(idx_list, "영업이익률")
    net_mgn = _find_idx(idx_list, "순이익률")

    cf_score = 5.0
    if roe     is not None: cf_score += (2.0 if roe > 20 else 1.0 if roe > 10 else 0.0 if roe > 0 else -2.0)
    if op_mgn  is not None: cf_score += (1.5 if op_mgn > 20 else 0.5 if op_mgn > 10 else 0.0 if op_mgn > 0 else -1.0)
    if net_mgn is not None: cf_score += (0.5 if net_mgn > 15 else 0.0 if net_mgn > 5 else -0.5)
    cf_score = min(10.0, max(0.0, cf_score))

    # ── 일관성 (25%) ──────────────────────────────────────────────────────────
    positive = sum(1 for v in [rev_g, op_g, net_g, eq_g] if v is not None and v > 0)
    total_g  = sum(1 for v in [rev_g, op_g, net_g, eq_g] if v is not None)

    con_score = 5.0
    if total_g > 0:
        con_score += (positive / total_g) * 3.0 - 1.5
    if eq_g   is not None and eq_g > 10:  con_score += 1.0
    if roe    is not None and roe > 10 \
       and op_mgn is not None and op_mgn > 10:  con_score += 0.5
    if asset_g is not None and asset_g > 0:     con_score += 0.5
    con_score = min(10.0, max(0.0, con_score))

    # ── 배당 (기존 weight 유지, 지표 없으면 중립) ──────────────────────────────
    div_score = 5.0  # 재무지표에는 배당 데이터 없음 → 중립값

    # ── 총점 (가중치: 성장30, 안정20, 수익/현금25, 배당15→0, 일관성25) ─────────
    total = (
        g_score   * settings.weight_growth     +
        st_score  * settings.weight_stability  +
        cf_score  * (settings.weight_cashflow + settings.weight_dividend) +  # 배당 가중치를 수익성에 흡수
        con_score * settings.weight_consistency
    )
    total = round(total, 2)

    if total >= GRADE_THRESHOLDS["TENBAGGER"]["min_total"] \
            and g_score >= GRADE_THRESHOLDS["TENBAGGER"]["min_growth"]:
        grade = "TENBAGGER"
    elif total >= GRADE_THRESHOLDS["COMPOUNDER"]["min_total"]:
        grade = "COMPOUNDER"
    elif total >= GRADE_THRESHOLDS["WATCHLIST"]["min_total"]:
        grade = "WATCHLIST"
    else:
        grade = "AVOID"

    return {
        "total_score":          total,
        "grade":                grade,
        "growth_score":         round(g_score, 2),
        "stability_score":      round(st_score, 2),
        "cashflow_score":       round(cf_score, 2),
        "dividend_score":       round(div_score, 2),
        "consistency_score":    round(con_score, 2),
        "source":               "index",
        # save_score 필드 매핑 (DART 인덱스 → DB 컬럼)
        "revenue_cagr_5y":      round(rev_g, 2) if rev_g is not None else None,
        "avg_roe_5y":           round(roe, 2)   if roe   is not None else None,
        "avg_operating_margin": round(op_mgn, 2) if op_mgn is not None else None,
        "avg_fcf_margin":       round(net_mgn, 2) if net_mgn is not None else None,
        "debt_ratio":           round(debt, 2)  if debt  is not None else None,
        "current_ratio":        round(curr, 2)  if curr  is not None else None,
        # 추가 스냅샷
        "roe":                  roe,
        "roa":                  roa,
        "revenue_growth":       rev_g,
        "op_profit_growth":     op_g,
        "net_income_growth":    net_g,
        "equity_growth":        eq_g,
        "earnings_yield":       _find_idx(idx_list, "이익수익률"),
    }


# ── 종합 스코어링 ─────────────────────────────────────────────────────────────

def calculate_tenbagger_score(
    financials: list[dict],
    dividend_yield: float = None,
    per: float = None,
    pbr: float = None,
) -> dict:
    """
    텐배거 종합 스코어 계산
    반환: 카테고리 점수, 종합 점수, 등급, 핵심 지표
    """
    if not financials:
        return {"error": "재무 데이터 없음"}

    g_score,  g_meta  = score_growth(financials)
    st_score, st_meta = score_stability(financials)
    cf_score, cf_meta = score_cashflow(financials)
    div_score,div_meta= score_dividend(financials, dividend_yield)
    con_score,con_meta= score_consistency(financials)

    # 가중 합산
    total = (
        g_score  * settings.weight_growth      +
        st_score * settings.weight_stability   +
        cf_score * settings.weight_cashflow    +
        div_score* settings.weight_dividend    +
        con_score* settings.weight_consistency
    )
    total = round(total, 2)

    # 등급 결정 — 텐배거는 성장 점수도 높아야 함
    if total >= GRADE_THRESHOLDS["TENBAGGER"]["min_total"] \
            and g_score >= GRADE_THRESHOLDS["TENBAGGER"]["min_growth"]:
        grade = "TENBAGGER"
    elif total >= GRADE_THRESHOLDS["COMPOUNDER"]["min_total"]:
        grade = "COMPOUNDER"
    elif total >= GRADE_THRESHOLDS["WATCHLIST"]["min_total"]:
        grade = "WATCHLIST"
    else:
        grade = "AVOID"

    earnings_yield = round(100.0 / per, 2) if per and per > 0 else None

    return {
        "total_score":    total,
        "grade":          grade,
        "growth_score":   g_score,
        "stability_score":st_score,
        "cashflow_score": cf_score,
        "dividend_score": div_score,
        "consistency_score": con_score,
        "per": per,
        "pbr": pbr,
        "earnings_yield": earnings_yield,
        **g_meta, **st_meta, **cf_meta, **div_meta, **con_meta,
    }
