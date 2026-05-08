"""
backend/app/api/v2_portfolio.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
주가 히스토리 / 스파크라인 / 가상 포트폴리오 시뮬레이터 API

GET /api/v2/price/history/{ticker}     주가 히스토리 (period: 1m|3m|6m|1y|3y|5y)
GET /api/v2/price/sparkline/{ticker}   52주 스파크라인 (주간 종가 배열)
GET /api/v2/portfolio/simulate         가상 포트폴리오 시뮬레이션
"""

import datetime
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text
from app.core.database import SessionLocal

router = APIRouter(prefix="/api/v2", tags=["portfolio-v2"])


# ── 유틸 ────────────────────────────────────────────────────────────────────

def _period_to_start_date(period: str) -> datetime.date:
    today = datetime.date.today()
    mapping = {
        "1m":  today - datetime.timedelta(days=30),
        "3m":  today - datetime.timedelta(days=90),
        "6m":  today - datetime.timedelta(days=180),
        "1y":  today - datetime.timedelta(days=365),
        "3y":  today - datetime.timedelta(days=365 * 3),
        "5y":  today - datetime.timedelta(days=365 * 5),
    }
    return mapping.get(period, today - datetime.timedelta(days=365))


def _fetch_price_from_pykrx(ticker: str, start: datetime.date, end: datetime.date) -> list[dict]:
    """pykrx로 주가 조회 후 price_daily_cache에 저장"""
    try:
        from pykrx import stock
        start_str = start.strftime("%Y%m%d")
        end_str   = end.strftime("%Y%m%d")
        df = stock.get_market_ohlcv(start_str, end_str, ticker)
        if df is None or df.empty:
            return []

        rows = []
        with SessionLocal() as session:
            for date_idx, row in df.iterrows():
                trade_date = date_idx.date() if hasattr(date_idx, "date") else date_idx
                try:
                    open_p  = int(row.get("시가", 0) or 0)
                    high_p  = int(row.get("고가", 0) or 0)
                    low_p   = int(row.get("저가", 0) or 0)
                    close_p = int(row.get("종가", 0) or 0)
                    vol     = int(row.get("거래량", 0) or 0)
                    if close_p <= 0:
                        continue
                    # 캐시에 저장
                    session.execute(text("""
                        INSERT INTO price_daily_cache
                            (ticker, trade_date, open_price, close_price, high_price, low_price, volume)
                        VALUES (:ticker, :td, :open, :close, :high, :low, :vol)
                        ON CONFLICT (ticker, trade_date) DO NOTHING
                    """), {
                        "ticker": ticker, "td": trade_date,
                        "open": open_p, "close": close_p,
                        "high": high_p, "low": low_p, "vol": vol,
                    })
                    rows.append({
                        "date": str(trade_date),
                        "open": open_p, "high": high_p, "low": low_p,
                        "close": close_p, "volume": vol,
                    })
                except Exception:
                    continue
            session.commit()
        return rows
    except Exception as e:
        print(f"[PRICE] pykrx 오류 {ticker}: {e}")
        return []


def _fetch_price_from_cache(ticker: str, start: datetime.date, end: datetime.date) -> list[dict]:
    """DB 캐시에서 주가 조회"""
    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT trade_date, open_price, high_price, low_price, close_price, volume
            FROM price_daily_cache
            WHERE ticker = :ticker
              AND trade_date BETWEEN :start AND :end
            ORDER BY trade_date ASC
        """), {"ticker": ticker, "start": start, "end": end}).fetchall()
    return [
        {
            "date": str(r.trade_date),
            "open": r.open_price or 0,
            "high": r.high_price or 0,
            "low":  r.low_price or 0,
            "close": r.close_price or 0,
            "volume": r.volume or 0,
        }
        for r in rows
    ]


def _get_ticker_name(ticker: str) -> str:
    with SessionLocal() as session:
        row = session.execute(text(
            "SELECT name FROM scores WHERE ticker = :t LIMIT 1"
        ), {"t": ticker}).fetchone()
    return row.name if row else ticker


# ── 주가 히스토리 ─────────────────────────────────────────────────────────

@router.get("/price/history/{ticker}")
def get_price_history(
    ticker: str,
    period: str = Query("1y", description="1m|3m|6m|1y|3y|5y"),
):
    """
    주가 히스토리 조회.
    DB 캐시 우선 → 캐시 없으면 pykrx 조회 후 캐시 저장.
    """
    end   = datetime.date.today()
    start = _period_to_start_date(period)
    name  = _get_ticker_name(ticker)

    # 1. DB 캐시 확인
    cached = _fetch_price_from_cache(ticker, start, end)

    # 캐시가 있고 최근 데이터(어제 이내)가 포함되어 있으면 캐시 사용
    yesterday = end - datetime.timedelta(days=3)  # 주말 고려 3일
    has_recent = any(r["date"] >= str(yesterday) for r in cached)
    if cached and has_recent:
        data = cached
    else:
        # 2. pykrx로 전체 기간 재조회
        data = _fetch_price_from_pykrx(ticker, start, end)
        if not data:
            # pykrx 실패 시 캐시라도 반환
            data = cached

    if not data:
        raise HTTPException(status_code=404, detail=f"{ticker} 주가 데이터 없음")

    closes = [r["close"] for r in data if r["close"] > 0]
    high52 = max(closes) if closes else None
    low52  = min(closes) if closes else None
    current = closes[-1] if closes else None

    return {
        "ticker": ticker,
        "name": name,
        "period": period,
        "data": data,
        "52w_high": high52,
        "52w_low":  low52,
        "current":  current,
    }


# ── 스파크라인 ─────────────────────────────────────────────────────────────

@router.get("/price/sparkline/{ticker}")
def get_sparkline(
    ticker: str,
    weeks: int = Query(52, description="조회 주 수"),
):
    """
    52주 주간 종가 배열 반환 (워치리스트 스파크라인용).
    주별 마지막 종가를 기준으로 최대 weeks개 반환.
    """
    end   = datetime.date.today()
    start = end - datetime.timedelta(weeks=weeks + 2)

    cached = _fetch_price_from_cache(ticker, start, end)
    if not cached:
        pykrx_data = _fetch_price_from_pykrx(ticker, start, end)
        data = pykrx_data if pykrx_data else []
    else:
        data = cached

    if not data:
        return {"ticker": ticker, "prices": [], "change_52w_pct": None}

    # 주별 마지막 종가 추출
    weekly: dict[str, int] = {}
    for row in data:
        try:
            d = datetime.date.fromisoformat(row["date"])
            # ISO 주차 키 (year-week)
            week_key = f"{d.isocalendar()[0]}-{d.isocalendar()[1]:02d}"
            if row["close"] > 0:
                weekly[week_key] = row["close"]
        except Exception:
            continue

    sorted_prices = [v for k, v in sorted(weekly.items())][-weeks:]

    change = None
    if len(sorted_prices) >= 2 and sorted_prices[0] > 0:
        change = round((sorted_prices[-1] - sorted_prices[0]) / sorted_prices[0] * 100, 2)

    return {
        "ticker": ticker,
        "prices": sorted_prices,
        "change_52w_pct": change,
    }


# ── 가상 포트폴리오 시뮬레이터 ───────────────────────────────────────────────

def _get_kospi_return(start: datetime.date, end: datetime.date) -> float | None:
    """KOSPI 지수 수익률 (벤치마크)"""
    try:
        from pykrx import stock
        df = stock.get_index_ohlcv(
            start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), "1001"
        )
        if df is None or df.empty:
            return None
        closes = df["종가"].dropna()
        if len(closes) < 2:
            return None
        return round((closes.iloc[-1] - closes.iloc[0]) / closes.iloc[0] * 100, 2)
    except Exception as e:
        print(f"[PORTFOLIO] KOSPI 수익률 조회 실패: {e}")
        return None


@router.get("/portfolio/simulate")
def simulate_portfolio(
    grade_filter: str = Query("TENBAGGER", description="등급 필터 (쉼표 구분, 예: TENBAGGER,COMPOUNDER)"),
    invest_amount: int = Query(100_000_000, description="투자 원금 (원)"),
    start_date: str = Query(None, description="시뮬레이션 시작일 YYYY-MM-DD (미입력 시 가장 오래된 이력)"),
    weight_method: str = Query("equal", description="비중 방식: equal | score_weighted"),
):
    """
    가상 포트폴리오 시뮬레이터.
    start_date 기준 해당 등급 종목들에 invest_amount를 투자했을 때 현재 수익률 계산.

    score_history 데이터가 충분하지 않으면 insufficient_data: true 반환.
    """
    grades = [g.strip().upper() for g in grade_filter.split(",")]
    today  = datetime.date.today()

    # 1. score_history에서 시작일 기준 종목 조회
    with SessionLocal() as session:
        if start_date:
            try:
                sim_start = datetime.date.fromisoformat(start_date)
            except ValueError:
                raise HTTPException(400, "start_date 형식 오류 (YYYY-MM-DD)")
            # 시작일 당일 또는 직후 이력
            rows = session.execute(text("""
                SELECT DISTINCT ON (ticker)
                    ticker, name, market, grade, total_score, growth_score,
                    close_price, snapshot_date
                FROM score_history
                WHERE grade = ANY(:grades)
                  AND snapshot_date <= :sd
                ORDER BY ticker, snapshot_date DESC
            """), {"grades": grades, "sd": sim_start}).fetchall()
        else:
            # 가장 오래된 이력 자동 선택
            oldest = session.execute(text("""
                SELECT MIN(snapshot_date) FROM score_history WHERE grade = ANY(:grades)
            """), {"grades": grades}).scalar()
            if not oldest:
                return {
                    "insufficient_data": True,
                    "message": "score_history 데이터가 없습니다. ETL 실행 후 최소 1일이 지나야 합니다.",
                    "meta": {"invest_amount": invest_amount, "grade_filter": grades},
                }
            sim_start = oldest
            rows = session.execute(text("""
                SELECT DISTINCT ON (ticker)
                    ticker, name, market, grade, total_score, growth_score,
                    close_price, snapshot_date
                FROM score_history
                WHERE grade = ANY(:grades)
                  AND snapshot_date = :sd
                ORDER BY ticker, snapshot_date DESC
            """), {"grades": grades, "sd": sim_start}).fetchall()

    if not rows:
        return {
            "insufficient_data": True,
            "message": f"{sim_start} 기준 {grade_filter} 등급 종목이 없습니다.",
            "meta": {"invest_amount": invest_amount, "start_date": str(sim_start), "grade_filter": grades},
        }

    # 시작일 ~ 오늘 날짜 차이
    days_elapsed = (today - sim_start).days
    if days_elapsed < 1:
        return {
            "insufficient_data": True,
            "message": "시뮬레이션 기간이 너무 짧습니다. 최소 1일 이후부터 유효합니다.",
            "meta": {"invest_amount": invest_amount, "start_date": str(sim_start)},
        }

    # 2. 비중 계산
    holdings_raw = [dict(r._mapping) for r in rows]

    if weight_method == "score_weighted":
        total_score_sum = sum(h["total_score"] or 0 for h in holdings_raw)
        for h in holdings_raw:
            h["weight"] = round((h["total_score"] or 0) / total_score_sum * 100, 2) if total_score_sum else 0
    else:  # equal
        w = round(100 / len(holdings_raw), 2)
        for h in holdings_raw:
            h["weight"] = w

    # 3. 현재 주가 조회 (캐시 → pykrx)
    holdings_out = []
    portfolio_current = 0

    for h in holdings_raw:
        ticker      = h["ticker"]
        entry_price = h["close_price"]

        # 현재 종가: 캐시 확인
        with SessionLocal() as session:
            recent = session.execute(text("""
                SELECT close_price FROM price_daily_cache
                WHERE ticker = :t
                ORDER BY trade_date DESC LIMIT 1
            """), {"t": ticker}).fetchone()

        current_price = None
        if recent and recent.close_price:
            current_price = recent.close_price
        else:
            # pykrx fallback
            try:
                from pykrx import stock
                past = (today - datetime.timedelta(days=7)).strftime("%Y%m%d")
                df = stock.get_market_ohlcv(past, today.strftime("%Y%m%d"), ticker)
                if df is not None and not df.empty:
                    current_price = int(df["종가"].iloc[-1])
            except Exception:
                pass

        if not entry_price or not current_price:
            continue  # 가격 없는 종목 제외

        invested    = int(invest_amount * h["weight"] / 100)
        shares      = invested / entry_price
        cur_value   = int(shares * current_price)
        return_pct  = round((current_price - entry_price) / entry_price * 100, 2)

        portfolio_current += cur_value

        holdings_out.append({
            "ticker":        ticker,
            "name":          h["name"],
            "grade":         h["grade"],
            "entry_date":    str(h["snapshot_date"]),
            "entry_price":   entry_price,
            "current_price": current_price,
            "weight":        h["weight"],
            "invested":      invested,
            "current_value": cur_value,
            "return_pct":    return_pct,
            "score_at_entry": h["total_score"],
        })

    holdings_out.sort(key=lambda x: x["return_pct"], reverse=True)

    if not holdings_out:
        return {
            "insufficient_data": True,
            "message": "현재 주가를 조회할 수 있는 종목이 없습니다.",
        }

    total_return_pct = round((portfolio_current - invest_amount) / invest_amount * 100, 2)

    # CAGR
    years = days_elapsed / 365
    cagr = None
    if years > 0 and invest_amount > 0:
        cagr = round(((portfolio_current / invest_amount) ** (1 / years) - 1) * 100, 2)

    # KOSPI 벤치마크
    kospi_return = _get_kospi_return(sim_start, today)
    alpha = round(total_return_pct - kospi_return, 2) if kospi_return is not None else None

    # 월별 포트폴리오 가치 시계열 (차트용) — score_history 기반
    monthly_returns = _build_monthly_series(
        holdings_raw, sim_start, today, invest_amount
    )

    return {
        "meta": {
            "invest_amount":  invest_amount,
            "start_date":     str(sim_start),
            "end_date":       str(today),
            "days_elapsed":   days_elapsed,
            "grade_filter":   grades,
            "weight_method":  weight_method,
            "portfolio_size": len(holdings_out),
        },
        "performance": {
            "current_value":    portfolio_current,
            "total_return_pct": total_return_pct,
            "kospi_return_pct": kospi_return,
            "alpha":            alpha,
            "cagr":             cagr,
        },
        "holdings":       holdings_out,
        "monthly_returns": monthly_returns,
    }


def _build_monthly_series(
    holdings: list[dict],
    start: datetime.date,
    end: datetime.date,
    invest_amount: int,
) -> list[dict]:
    """
    월별 포트폴리오 가치 시계열 생성 (Chart.js 라인 차트용).
    각 종목의 월말 종가를 price_daily_cache에서 조회.
    데이터 부족 시 빈 리스트 반환.
    """
    try:
        # 월 목록 생성
        months = []
        cur = datetime.date(start.year, start.month, 1)
        while cur <= end:
            months.append(cur)
            # 다음 달
            if cur.month == 12:
                cur = datetime.date(cur.year + 1, 1, 1)
            else:
                cur = datetime.date(cur.year, cur.month + 1, 1)

        if len(months) < 2:
            return []

        series = []
        for month_start in months:
            # 월말 날짜
            if month_start.month == 12:
                month_end = datetime.date(month_start.year + 1, 1, 1) - datetime.timedelta(days=1)
            else:
                month_end = datetime.date(month_start.year, month_start.month + 1, 1) - datetime.timedelta(days=1)
            month_end = min(month_end, end)

            portfolio_val = 0
            valid = 0
            for h in holdings:
                entry_price = h.get("close_price")
                if not entry_price:
                    continue
                weight = h.get("weight", 100 / len(holdings))
                invested = invest_amount * weight / 100

                # 해당 월 종가
                with SessionLocal() as session:
                    row = session.execute(text("""
                        SELECT close_price FROM price_daily_cache
                        WHERE ticker = :t AND trade_date <= :me
                        ORDER BY trade_date DESC LIMIT 1
                    """), {"t": h["ticker"], "me": month_end}).fetchone()

                if row and row.close_price:
                    shares = invested / entry_price
                    portfolio_val += int(shares * row.close_price)
                    valid += 1

            if valid > 0:
                series.append({
                    "month": month_start.strftime("%Y-%m"),
                    "value": portfolio_val,
                })

        return series
    except Exception as e:
        print(f"[PORTFOLIO] monthly_series 오류: {e}")
        return []
