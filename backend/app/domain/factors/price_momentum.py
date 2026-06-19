"""
모멘텀 요소 (Factor v3)

가설: 직전 6개월 주가 수익률이 높은 종목이 이후에도 계속 상승 (추세 지속)
데이터: pykrx (한국 주식 가격 데이터)
출력: 0~10 점수 (수익률 높을수록 높음)

주의: 단기 과매수 위험 → 1개월 반전 제외 (6개월 수익률 - 1개월 수익률)
"""
from datetime import date, timedelta
from typing import Optional


def _to_str(d: date) -> str:
    return d.strftime("%Y%m%d")


def get_momentum_score(ticker: str, base_date: date,
                       lookback_months: int = 6) -> Optional[dict]:
    """기준일 기준 직전 lookback_months 수익률 → 0~10 점수.

    Returns: {"return_pct": float, "momentum_score": float 0~10}
    None if price data unavailable.
    """
    try:
        import pykrx.stock as stock  # type: ignore

        end = base_date - timedelta(days=1)
        start = end - timedelta(days=30 * lookback_months + 10)  # 여유분
        skip_start = end - timedelta(days=35)  # 1개월 반전 구간

        df_full = stock.get_market_ohlcv_by_date(_to_str(start), _to_str(end), ticker)
        if df_full is None or df_full.empty:
            return None

        closes = df_full["종가"]
        price_6m_ago = float(closes.iloc[0])
        price_1m_ago_df = df_full[df_full.index >= str(skip_start)]
        price_1m_ago = float(price_1m_ago_df["종가"].iloc[0]) if not price_1m_ago_df.empty else float(closes.iloc[-1])
        price_now = float(closes.iloc[-1])

        if price_6m_ago == 0:
            return None

        ret_6m = (price_now - price_6m_ago) / price_6m_ago * 100
        ret_1m = (price_now - price_1m_ago) / price_1m_ago * 100 if price_1m_ago else 0
        # 1개월 반전 제거한 모멘텀
        adjusted_ret = ret_6m - ret_1m

        # -50% ~ +100% → 0~10 정규화
        score = (adjusted_ret + 50) / 150 * 10
        score = round(max(0.0, min(10.0, score)), 2)

        return {"return_6m_pct": round(ret_6m, 2), "return_1m_pct": round(ret_1m, 2),
                "adjusted_ret": round(adjusted_ret, 2), "momentum_score": score}
    except Exception:
        return None
