"""
GET  /api/v2/risk/{ticker}   — 종목 리스크 지표 (MDD, 연간변동성, 샤프비율)
POST /api/v2/risk/portfolio  — 포트폴리오 가중 리스크 집계
"""
import datetime
import statistics
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.core.database import SessionLocal

router = APIRouter(prefix="/api/v2/risk", tags=["risk"])

_RISK_FREE_ANNUAL = 0.035   # 한국 무위험 수익률 가정 (3.5%)


# ── 데이터 조회 ──────────────────────────────────────────────────────────────

def _get_closes(ticker: str, days: int) -> list[float]:
    """price_daily_cache → pykrx fallback 순서로 종가 배열 반환"""
    end   = datetime.date.today()
    start = end - datetime.timedelta(days=days)

    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT close_price FROM price_daily_cache
            WHERE ticker = :t AND trade_date BETWEEN :s AND :e AND close_price > 0
            ORDER BY trade_date ASC
        """), {"t": ticker, "s": start, "e": end}).fetchall()
    closes = [float(r.close_price) for r in rows]

    if len(closes) < 10:
        try:
            from pykrx import stock
            df = stock.get_market_ohlcv(
                start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker
            )
            if df is not None and not df.empty:
                closes = [float(p) for p in df["종가"].dropna() if p > 0]
        except Exception:
            pass

    return closes


# ── 지표 계산 ────────────────────────────────────────────────────────────────

def _compute_metrics(closes: list[float]) -> dict:
    if len(closes) < 5:
        return {}

    daily_ret = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]

    # 최대 낙폭 (MDD)
    peak = mdd = closes[0]
    for p in closes:
        peak = max(peak, p)
        mdd  = min(mdd, (p - peak) / peak * 100)

    # 연간 변동성 + 샤프 비율
    vol = sharpe = None
    if len(daily_ret) > 1:
        std = statistics.stdev(daily_ret)
        if std > 0:
            vol    = round(std * (252 ** 0.5) * 100, 2)
            rf_d   = _RISK_FREE_ANNUAL / 252
            avg    = sum(daily_ret) / len(daily_ret)
            sharpe = round((avg - rf_d) / std * (252 ** 0.5), 2)

    high52 = max(closes)
    return {
        "mdd_pct":          round(mdd, 2),
        "annual_vol_pct":   vol,
        "sharpe_ratio":     sharpe,
        "total_return_pct": round((closes[-1] - closes[0]) / closes[0] * 100, 2),
        "high_52w":         high52,
        "low_52w":          min(closes),
        "current":          closes[-1],
        "from_high_pct":    round((closes[-1] - high52) / high52 * 100, 2),
    }


def _risk_level(vol: float | None, mdd_abs: float) -> tuple[str, str]:
    v = vol or 0
    if v < 20 and mdd_abs < 20:
        return "낮음",  "#10b981"
    if v < 40 and mdd_abs < 40:
        return "중간",  "#f59e0b"
    return "높음", "#ef4444"


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.get("/{ticker}")
def get_ticker_risk(
    ticker: str,
    period: str = Query("1y", description="1m | 3m | 6m | 1y"),
):
    """종목 리스크 지표. DB 캐시 우선, 없으면 pykrx 실시간 수집."""
    days = {"1m": 35, "3m": 95, "6m": 185, "1y": 370}.get(period, 370)
    closes = _get_closes(ticker, days)

    if len(closes) < 5:
        with SessionLocal() as session:
            row = session.execute(text(
                "SELECT close FROM scores WHERE ticker = :t LIMIT 1"
            ), {"t": ticker}).fetchone()
        return {
            "ticker":           ticker,
            "insufficient_data": True,
            "current":          float(row.close) if row and row.close else None,
            "message":          "주가 이력 없음 — 종목 분석 탭에서 차트 먼저 로드",
        }

    m = _compute_metrics(closes)

    # 낙폭 시계열 (최근 60개, 차트용)
    peak, dd_series = closes[0], []
    for p in closes:
        peak = max(peak, p)
        dd_series.append(round((p - peak) / peak * 100, 2))

    lvl, color = _risk_level(m.get("annual_vol_pct"), abs(m.get("mdd_pct") or 0))

    return {
        "ticker":      ticker,
        "period":      period,
        **m,
        "risk_level":  lvl,
        "risk_color":  color,
        "dd_series":   dd_series[-60:],
        "data_points": len(closes),
    }


class PortfolioRiskRequest(BaseModel):
    positions: list[dict]   # [{"ticker": "005930", "weight": 30.0}, ...]


@router.post("/portfolio")
def portfolio_risk(body: PortfolioRiskRequest):
    """여러 종목의 가중 MDD·변동성 집계 + 집중도·분산도 산출."""
    positions = body.positions
    if not positions:
        return {"error": "포지션 없음"}

    w_mdd = w_vol = 0.0
    valid = 0
    max_weight = max((p.get("weight", 0) for p in positions), default=0)

    for pos in positions:
        w      = pos.get("weight", 100 / len(positions)) / 100
        closes = _get_closes(pos["ticker"], 370)
        if len(closes) < 10:
            continue
        m      = _compute_metrics(closes)
        w_mdd += (m.get("mdd_pct")          or 0) * w
        w_vol += (m.get("annual_vol_pct")   or 0) * w
        valid += 1

    n = len(positions)
    lvl, color = _risk_level(w_vol if valid else None, abs(w_mdd))

    return {
        "portfolio_mdd_pct":     round(w_mdd, 2),
        "portfolio_vol_pct":     round(w_vol, 2),
        "concentration_pct":     round(max_weight, 2),
        "diversification_score": min(100, n * 12),
        "stock_count":           n,
        "stocks_with_data":      valid,
        "risk_level":            lvl,
        "risk_color":            color,
    }
