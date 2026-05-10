"""
backend/app/api/v2_portfolio.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
주가 히스토리 / 스파크라인 / 가상 포트폴리오 시뮬레이터 API

GET /api/v2/price/history/{ticker}       주가 히스토리 (period: 1m|3m|6m|1y|3y|5y)
GET /api/v2/price/sparkline/{ticker}     52주 스파크라인 (주간 종가 배열)
GET /api/v2/portfolio/simulate           가상 포트폴리오 시뮬레이션
GET /api/v2/portfolio/track-record       시스템 신뢰도 검증 — 포트폴리오 트랙레코드
"""

import asyncio
import datetime
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text
from app.core.database import SessionLocal

# ── 트랙레코드 인메모리 캐시 (연산이 무거우므로 서버 재시작 전까지 유지) ──────
_track_record_cache: dict = {}

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
    period: str = Query("3m", description="시뮬레이션 기간: 1m | 3m | 6m | 1y"),
    weight_method: str = Query("equal", description="비중 방식: equal | score_weighted"),
):
    """
    가상 포트폴리오 시뮬레이터 (v2 — score_history 불필요).
    현재 scores 테이블 등급 기준 + price_daily_cache 과거 주가로 수익률 계산.
    period 전에 해당 등급이었다고 가정 (보수적 시뮬레이션).
    """
    grades = [g.strip().upper() for g in grade_filter.split(",")]
    today  = datetime.date.today()

    # period → 시작일 계산
    period_days_map = {"1m": 30, "3m": 90, "6m": 180, "1y": 365}
    period_days = period_days_map.get(period, 90)
    sim_start = today - datetime.timedelta(days=period_days)

    # 1. 현재 등급 기준 종목 조회 (scores 테이블)
    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT ticker, name, market, grade, total_score, growth_score,
                   close AS close_price
            FROM scores
            WHERE grade = ANY(:grades)
            ORDER BY total_score DESC
            LIMIT 50
        """), {"grades": grades}).fetchall()

    if not rows:
        return {
            "insufficient_data": True,
            "message": f"{grade_filter} 등급 종목이 없습니다. ETL을 먼저 실행하세요.",
            "meta": {"invest_amount": invest_amount, "grade_filter": grades},
        }

    holdings_raw = [dict(r._mapping) for r in rows]

    # 2. 비중 계산
    if weight_method == "score_weighted":
        total_score_sum = sum(h["total_score"] or 0 for h in holdings_raw)
        for h in holdings_raw:
            h["weight"] = round((h["total_score"] or 0) / total_score_sum * 100, 2) if total_score_sum else 0
    else:
        w = round(100 / len(holdings_raw), 2)
        for h in holdings_raw:
            h["weight"] = w

    # 3. 시작일 주가(진입가) + 현재 주가 조회
    holdings_out = []
    portfolio_current = 0
    fetched_any_history = False  # price_daily_cache에 과거 데이터가 있는지

    for h in holdings_raw:
        ticker = h["ticker"]

        with SessionLocal() as session:
            # 시작일 기준 가장 가까운 과거 종가
            entry_row = session.execute(text("""
                SELECT close_price, trade_date FROM price_daily_cache
                WHERE ticker = :t AND trade_date <= :sd AND close_price > 0
                ORDER BY trade_date DESC LIMIT 1
            """), {"t": ticker, "sd": sim_start}).fetchone()

            # 현재 종가 (가장 최근)
            current_row = session.execute(text("""
                SELECT close_price, trade_date FROM price_daily_cache
                WHERE ticker = :t AND close_price > 0
                ORDER BY trade_date DESC LIMIT 1
            """), {"t": ticker}).fetchone()

        # 과거 가격 없으면 scores.close를 현재가로, 시작가는 skip
        entry_price   = entry_row.close_price if entry_row else None
        entry_date    = str(entry_row.trade_date) if entry_row else None
        current_price = current_row.close_price if current_row else h.get("close_price")

        # 진입가가 없으면 현재 scores.close를 진입가로 사용 (0% return)
        if not entry_price:
            entry_price = h.get("close_price")
            entry_date  = str(today)

        if not entry_price or not current_price:
            continue

        if entry_row is not None:   # 과거 데이터 존재 여부만 확인
            fetched_any_history = True

        invested   = int(invest_amount * h["weight"] / 100)
        shares     = invested / entry_price
        cur_value  = int(shares * current_price)
        return_pct = round((current_price - entry_price) / entry_price * 100, 2)

        portfolio_current += cur_value

        holdings_out.append({
            "ticker":        ticker,
            "name":          h["name"],
            "grade":         h["grade"],
            "entry_date":    entry_date,
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
            "message": "주가 데이터가 없습니다. 종목 분석 페이지를 먼저 방문해 주가 캐시를 채워주세요.",
        }

    # 주가 캐시가 없어서 모두 0% return인 경우 안내
    if not fetched_any_history:
        return {
            "insufficient_data": True,
            "message": f"price_daily_cache에 {period} 전 주가 데이터가 없습니다.\n종목 분석 페이지에서 개별 종목의 주가 차트를 먼저 로드하거나,\n아래 '주가 일괄 캐시' 버튼을 눌러주세요.",
            "meta": {
                "invest_amount": invest_amount,
                "grade_filter": grades,
                "period": period,
                "hint": "GET /api/v2/price/history/{ticker}?period=1y 를 각 TENBAGGER 종목에 호출하면 캐시가 채워집니다.",
            },
        }

    days_elapsed = (today - sim_start).days
    total_return_pct = round((portfolio_current - invest_amount) / invest_amount * 100, 2)

    years = days_elapsed / 365
    cagr = round(((portfolio_current / invest_amount) ** (1 / years) - 1) * 100, 2) if years > 0 else None

    kospi_return = _get_kospi_return(sim_start, today)
    alpha = round(total_return_pct - kospi_return, 2) if kospi_return is not None else None

    monthly_returns = _build_monthly_series(holdings_raw, sim_start, today, invest_amount)

    return {
        "meta": {
            "invest_amount":  invest_amount,
            "period":         period,
            "start_date":     str(sim_start),
            "end_date":       str(today),
            "days_elapsed":   days_elapsed,
            "grade_filter":   grades,
            "weight_method":  weight_method,
            "portfolio_size": len(holdings_out),
            "data_source":    "price_daily_cache + scores",
        },
        "performance": {
            "current_value":    portfolio_current,
            "total_return_pct": total_return_pct,
            "kospi_return_pct": kospi_return,
            "alpha":            alpha,
            "cagr":             cagr,
        },
        "holdings":        holdings_out,
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

        tickers = [h["ticker"] for h in holdings if h.get("close_price")]

        series = []
        for month_start in months:
            # 월말 날짜
            if month_start.month == 12:
                month_end = datetime.date(month_start.year + 1, 1, 1) - datetime.timedelta(days=1)
            else:
                month_end = datetime.date(month_start.year, month_start.month + 1, 1) - datetime.timedelta(days=1)
            month_end = min(month_end, end)

            # 배치 쿼리: 모든 종목의 월말 종가를 한 번에 조회 (N+1 → 1)
            with SessionLocal() as session:
                rows = session.execute(text("""
                    SELECT DISTINCT ON (ticker) ticker, close_price
                    FROM price_daily_cache
                    WHERE ticker = ANY(:tickers) AND trade_date <= :me AND close_price > 0
                    ORDER BY ticker, trade_date DESC
                """), {"tickers": tickers, "me": month_end}).fetchall()
            price_by_ticker = {r.ticker: r.close_price for r in rows}

            portfolio_val = 0
            valid = 0
            for h in holdings:
                entry_price = h.get("close_price")
                if not entry_price:
                    continue
                close_price = price_by_ticker.get(h["ticker"])
                if not close_price:
                    continue
                weight = h.get("weight", 100 / len(holdings))
                invested = invest_amount * weight / 100
                shares = invested / entry_price
                portfolio_val += int(shares * close_price)
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


# ── 포트폴리오 트랙레코드 ────────────────────────────────────────────────────

async def _get_kospi_return_yearly(base_year: int, end_year: int) -> float | None:
    """KOSPI 지수 연간 수익률 (연말 종가 기준, pykrx asyncio.to_thread 래퍼)"""
    def _sync_fetch():
        try:
            from pykrx import stock
            base_df = stock.get_index_ohlcv_by_date(
                f"{base_year}1201", f"{base_year}1231", "1001"
            )
            end_df  = stock.get_index_ohlcv_by_date(
                f"{end_year}1201",  f"{end_year}1231",  "1001"
            )
            if base_df is None or base_df.empty or end_df is None or end_df.empty:
                return None
            p0 = float(base_df["종가"].dropna().iloc[-1])
            p1 = float(end_df["종가"].dropna().iloc[-1])
            if p0 <= 0:
                return None
            return round((p1 / p0 - 1) * 100, 1)
        except Exception as e:
            print(f"[KOSPI_YEARLY] {base_year}→{end_year} 조회 실패: {e}")
            return None

    return await asyncio.to_thread(_sync_fetch)


@router.get("/portfolio/track-record")
async def portfolio_track_record(
    base_year:    int   = Query(2019, ge=2012, le=2023, description="분석 기준 연도"),
    hold_years:   int   = Query(3,    ge=1,    le=7,    description="보유 기간 (년)"),
    grade_filter: str   = Query("TENBAGGER,COMPOUNDER", description="포함할 등급 (쉼표 구분)"),
    limit:        int   = Query(30,   ge=10,   le=60,   description="분석 종목 수"),
):
    """
    시스템 신뢰도 검증 — 포트폴리오 트랙레코드

    base_year 당시 재무데이터로 스코어링 → grade_filter 해당 종목만 추출
    → hold_years 후 실제 주가 수익률 집계 → KOSPI 대비 알파 계산

    ⚠️ 첫 요청은 DART 역방향 재무 수집으로 1~3분 소요.
       캐시 히트 시 즉시 반환.
    """
    end_year     = base_year + hold_years
    current_year = datetime.date.today().year

    if end_year >= current_year:
        raise HTTPException(
            status_code=400,
            detail=(
                f"보유 기간 종료 시점({end_year}년)이 올해이거나 미래입니다. "
                f"base_year + hold_years < {current_year} 조건을 만족해야 합니다."
            ),
        )

    cache_key = f"tr:{base_year}:{hold_years}:{grade_filter}:{limit}"
    if cache_key in _track_record_cache:
        result = dict(_track_record_cache[cache_key])
        result["cached"] = True
        return result

    # 1. DB에서 현재 상위 종목 (ETL 결과 기준 상위 limit개)
    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT ticker, name, market FROM scores
            ORDER BY total_score DESC LIMIT :limit
        """), {"limit": limit}).fetchall()

    if not rows:
        raise HTTPException(
            status_code=400,
            detail="DB에 종목 데이터가 없습니다. ETL을 먼저 실행하세요.",
        )

    tickers_info = [(r.ticker, r.name, r.market) for r in rows]

    # 2. 병렬 백테스트 (세마포어로 동시 5개 제한 → DART 과부하 방지)
    from app.domain.backtest import run_backtest
    sem = asyncio.Semaphore(5)

    async def _limited(ticker: str, by: int, hy: int):
        async with sem:
            return await run_backtest(ticker, by, hy)

    tasks      = [_limited(t, base_year, hold_years) for t, _, _ in tickers_info]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    # 3. 결과 정리
    grades_to_include = [g.strip().upper() for g in grade_filter.split(",")]
    name_map   = {t: n for t, n, m in tickers_info}
    market_map = {t: m for t, n, m in tickers_info}

    all_stocks:   list[dict] = []
    grade_stocks: list[dict] = []

    for r in raw_results:
        if isinstance(r, Exception) or not isinstance(r, dict) or "error" in r:
            continue
        ticker   = r.get("ticker")
        sc       = r.get("score_at_base_year", {})
        grade    = r.get("predicted_grade")
        actual   = r.get("actual_return_pct")

        info = {
            "ticker":             ticker,
            "name":               name_map.get(ticker, ticker),
            "market":             market_map.get(ticker, ""),
            "predicted_grade":    grade,
            "total_score":        sc.get("total_score"),
            "growth_score":       sc.get("growth_score"),
            "stability_score":    sc.get("stability_score"),
            "actual_return_pct":  actual,
            "price_base":         r.get("price_at_base_year"),
            "price_end":          r.get("price_at_end_year"),
            "prediction_correct": r.get("prediction_correct"),
            "is_tenbagger_actual": r.get("is_tenbagger_actual", False),
        }
        all_stocks.append(info)
        if grade in grades_to_include:
            grade_stocks.append(info)

    # 4. 포트폴리오 집계 (균등 비중)
    returns = [s["actual_return_pct"] for s in grade_stocks
               if s["actual_return_pct"] is not None]

    avg_return    = round(sum(returns) / len(returns), 1) if returns else None
    win_count     = sum(1 for r in returns if r > 0)
    tb_count      = sum(1 for r in returns if r >= 900)

    # 5. KOSPI 벤치마크
    kospi_return = await _get_kospi_return_yearly(base_year, end_year)
    beat_market_count = (
        sum(1 for r in returns if r > kospi_return)
        if (kospi_return is not None and returns) else 0
    )
    alpha = (
        round(avg_return - kospi_return, 1)
        if avg_return is not None and kospi_return is not None else None
    )

    result = {
        "base_year":             base_year,
        "end_year":              end_year,
        "hold_years":            hold_years,
        "grade_filter":          grades_to_include,
        "total_analyzed":        len(all_stocks),
        "total_in_grade":        len(grade_stocks),
        "with_price_data":       len(returns),
        "portfolio_return_pct":  avg_return,
        "win_rate_pct":          round(win_count / len(returns) * 100, 1) if returns else None,
        "beat_market_rate_pct":  round(beat_market_count / len(returns) * 100, 1) if returns else None,
        "tenbagger_rate_pct":    round(tb_count / len(returns) * 100, 1) if returns else None,
        "kospi_return_pct":      kospi_return,
        "alpha_pct":             alpha,
        "cached":                False,
        "stocks": sorted(
            [s for s in grade_stocks if s["actual_return_pct"] is not None],
            key=lambda x: x["actual_return_pct"],
            reverse=True,
        ),
    }

    _track_record_cache[cache_key] = result
    return result
