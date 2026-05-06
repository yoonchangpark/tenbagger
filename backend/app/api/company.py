from fastapi import APIRouter, HTTPException, Query
import datetime
from pykrx import stock
from app.infra.clients.dart_client import get_corp_code, fetch_yearly_financials
from app.domain.scoring import calculate_tenbagger_score
from app.domain.simulator import simulate_5year
from app.infra.repositories.company_repo import (
    save_company, save_financials, save_score,
    get_score_cached, get_financials_cached,
)

router = APIRouter(prefix="/api/company", tags=["Company"])

# 캐시 DB 필드 → score 딕셔너리로 재구성
_SCORE_KEYS = [
    "total_score", "grade",
    "growth_score", "stability_score", "cashflow_score",
    "dividend_score", "consistency_score",
    "revenue_cagr_5y", "eps_cagr_5y", "avg_roe_5y",
    "avg_operating_margin", "avg_fcf_margin",
    "debt_ratio", "current_ratio", "avg_payout_ratio",
]


def _cached_to_response(cached: dict) -> dict:
    """get_score_cached() flat dict → 프론트 표준 응답 포맷 변환"""
    score = {k: cached.get(k) for k in _SCORE_KEYS}
    fins = get_financials_cached(cached["ticker"])
    return {
        "ticker": cached["ticker"],
        "name": cached.get("name"),
        "market": cached.get("market"),
        "close": cached.get("close"),
        "per": cached.get("per"),
        "pbr": cached.get("pbr"),
        "dividend_yield": cached.get("dividend_yield"),
        "market_cap": cached.get("market_cap"),
        "score": score,
        "financials": fins,
    }


async def _fetch_market_data(ticker: str) -> dict:
    """pykrx에서 주가/밸류에이션 수집"""
    today = datetime.datetime.today().strftime("%Y%m%d")
    past = (datetime.datetime.today() - datetime.timedelta(days=7)).strftime("%Y%m%d")

    name = stock.get_market_ticker_name(ticker)

    df_ohlcv = stock.get_market_ohlcv(past, today, ticker)
    close = int(df_ohlcv['종가'].iloc[-1]) if not df_ohlcv.empty else None

    df_fund = stock.get_market_fundamental(past, today, ticker)
    per = float(df_fund['PER'].iloc[-1]) if not df_fund.empty else None
    pbr = float(df_fund['PBR'].iloc[-1]) if not df_fund.empty else None
    dividend_yield = float(df_fund['DIV'].iloc[-1]) if not df_fund.empty else None

    df_cap = stock.get_market_cap(past, today, ticker)
    market_cap = int(df_cap['시가총액'].iloc[-1]) if not df_cap.empty else None

    market = "KOSPI"
    try:
        kosdaq = stock.get_market_ticker_list(market="KOSDAQ")
        if ticker in kosdaq:
            market = "KOSDAQ"
    except Exception:
        pass

    return {
        "name": name,
        "market": market,
        "close": close,
        "per": per,
        "pbr": pbr,
        "dividend_yield": dividend_yield,
        "market_cap": market_cap,
    }


@router.get("/{query}/simulate")
async def simulate_company(
    query: str,
    bear_eps_cagr: float = Query(5.0),
    base_eps_cagr: float = Query(15.0),
    bull_eps_cagr: float = Query(25.0),
    bear_per: float = Query(10.0),
    base_per: float = Query(15.0),
    bull_per: float = Query(25.0),
    hold_years: int = Query(5, ge=1, le=10),
):
    """5년 시나리오 시뮬레이터"""
    cached = get_score_cached(query)
    if not cached:
        raise HTTPException(status_code=404, detail="먼저 /api/company/{query} 로 분석을 실행해 주세요.")

    close = cached.get("close")
    if not close:
        raise HTTPException(status_code=400, detail="현재 주가 데이터가 없습니다.")

    per = cached.get("per") or 15.0
    current_eps = round(close / per) if per and per > 0 else None
    if not current_eps:
        raise HTTPException(status_code=400, detail="EPS를 계산할 수 없습니다.")

    result = simulate_5year(
        current_price=close,
        current_eps=current_eps,
        eps_cagr_scenarios={"bear": bear_eps_cagr, "base": base_eps_cagr, "bull": bull_eps_cagr},
        per_scenarios={"bear": bear_per, "base": base_per, "bull": bull_per},
        dividend_yield=cached.get("dividend_yield") or 0.0,
        hold_years=hold_years,
    )

    return {
        "ticker": cached["ticker"],
        "name": cached.get("name"),
        "current_price": close,
        "current_eps": current_eps,
        "simulation": result,
    }


@router.get("/{query}")
async def analyze_company(
    query: str,
    force: bool = Query(False, description="캐시 무시하고 재분석"),
):
    """종목 분석 (24시간 DB 캐시)"""
    # 1. 캐시 확인 → 표준 포맷으로 변환해서 반환
    if not force:
        cached = get_score_cached(query)
        if cached:
            return _cached_to_response(cached)

    # 2. DART corp_code 조회
    corp_code, ticker = await get_corp_code(query)
    if not corp_code:
        raise HTTPException(status_code=404, detail=f"'{query}' 종목을 찾을 수 없습니다.")

    # 3. 재무데이터 수집 (캐시 확인)
    fins = get_financials_cached(ticker)
    if not fins:
        fins = await fetch_yearly_financials(corp_code, ticker, years=8)

    if not fins:
        raise HTTPException(status_code=404, detail="재무데이터를 가져올 수 없습니다.")

    # 4. 주가/밸류에이션 수집
    try:
        mkt = await _fetch_market_data(ticker)
    except Exception as e:
        print(f"[Company] 시장데이터 수집 실패: {e}")
        mkt = {"name": query, "market": "KOSPI", "close": None, "per": None,
               "pbr": None, "dividend_yield": None, "market_cap": None}

    # 5. 스코어 계산
    score = calculate_tenbagger_score(
        financials=fins,
        per=mkt.get("per"),
        pbr=mkt.get("pbr"),
        dividend_yield=mkt.get("dividend_yield"),
    )

    # 6. DB 저장
    try:
        company_id = save_company(
            ticker=ticker,
            name=mkt["name"] or query,
            market=mkt["market"],
            dart_code=corp_code,
        )
        save_financials(company_id, fins)
        save_score(
            company_id=company_id,
            ticker=ticker,
            name=mkt["name"] or query,
            market=mkt["market"],
            score=score,
            close=mkt.get("close"),
            market_cap=mkt.get("market_cap"),
        )
    except Exception as e:
        print(f"[Company] DB 저장 실패: {e}")

    return {
        "ticker": ticker,
        "name": mkt["name"] or query,
        "market": mkt["market"],
        "close": mkt.get("close"),
        "per": mkt.get("per"),
        "pbr": mkt.get("pbr"),
        "dividend_yield": mkt.get("dividend_yield"),
        "market_cap": mkt.get("market_cap"),
        "score": score,
        "financials": fins,
    }
