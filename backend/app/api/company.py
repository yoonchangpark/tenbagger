from fastapi import APIRouter, HTTPException, Query
import asyncio
import datetime
import httpx
from pykrx import stock
from app.infra.clients.dart_client import get_corp_code, fetch_yearly_financials
from app.domain.scoring import calculate_tenbagger_score
from app.domain.simulator import simulate_5year, calculate_benchmarks
from app.infra.repositories.company_repo import (
    save_company, save_financials, save_score,
    get_score_cached, get_financials_cached, query_scores,
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


def _parse_num(val, cast):
    s = str(val or "").replace(",", "").replace("%", "").strip()
    try:
        return cast(s) if s and s not in ("-", "N/A") else None
    except (ValueError, TypeError):
        return None


def _parse_naver_total_infos(infos: list) -> dict:
    """Naver totalInfos 배열 → {code: 숫자값} 딕셔너리
    예: {"code":"dividendYieldRatio","value":"1.43%"} → {"dividendYieldRatio": 1.43}
    """
    result = {}
    for item in (infos or []):
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        raw  = str(item.get("value") or "")
        # 한국어 단위 제거: 배, 원, %, 콤마, 공백
        cleaned = raw.replace(",", "").replace("%", "").replace("배", "").replace("원", "").strip()
        if code and cleaned and cleaned not in ("-", "N/A"):
            try:
                result[code] = float(cleaned)
            except (ValueError, TypeError):
                pass
    return result


async def _fetch_price_naver(ticker: str) -> dict:
    """Naver Finance 모바일 API로 현재가/PER/PBR/배당수익률 조회"""
    try:
        async with httpx.AsyncClient(timeout=6.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
            results = await asyncio.gather(
                client.get(f"https://m.stock.naver.com/api/stock/{ticker}/basic"),
                client.get(f"https://m.stock.naver.com/api/stock/{ticker}/integration"),
                return_exceptions=True,
            )

        d_basic = {}
        d_integ = {}
        for r, target in zip(results, ("basic", "integration")):
            if isinstance(r, Exception) or r.status_code != 200:
                continue
            try:
                payload = r.json()
            except Exception:
                continue
            if target == "basic":
                d_basic = payload
            else:
                d_integ = payload

        # totalInfos 배열 파싱 (per/pbr/배당수익률 모두 여기에 있음)
        infos = _parse_naver_total_infos(d_integ.get("totalInfos", []))

        dividend_yield = infos.get("dividendYieldRatio")
        if dividend_yield is not None and not (0.01 < dividend_yield < 50):
            dividend_yield = None

        merged = {**d_integ, **d_basic}

        return {
            "name":           merged.get("stockName") or merged.get("name"),
            "close":          _parse_num(merged.get("closePrice"), int),
            "per":            infos.get("per") or infos.get("cnsPer"),
            "pbr":            infos.get("pbr"),
            "dividend_yield": dividend_yield,
        }
    except Exception as e:
        print(f"[Company] Naver 가격 조회 실패 {ticker}: {e}")
        return {}


@router.get("/{ticker}/naver-debug")
async def naver_debug(ticker: str):
    """Naver API 원본 응답 확인용 (디버그 전용)"""
    try:
        async with httpx.AsyncClient(timeout=6.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
            results = await asyncio.gather(
                client.get(f"https://m.stock.naver.com/api/stock/{ticker}/basic"),
                client.get(f"https://m.stock.naver.com/api/stock/{ticker}/integration"),
                return_exceptions=True,
            )
        basic_status = results[0].status_code if not isinstance(results[0], Exception) else str(results[0])
        integ_status = results[1].status_code if not isinstance(results[1], Exception) else str(results[1])
        basic_body = results[0].json() if not isinstance(results[0], Exception) and results[0].status_code == 200 else None
        integ_body = results[1].json() if not isinstance(results[1], Exception) and results[1].status_code == 200 else None
        return {
            "basic": {"status": basic_status, "body": basic_body},
            "integration": {"status": integ_status, "body": integ_body},
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/{ticker}/price")
async def get_stock_price(ticker: str):
    """현재가 빠른 조회 (내 주식 탭용) — Naver Finance 우선, DB 캐시 fallback"""
    cached = get_score_cached(ticker)
    grade = cached.get("grade", "") if cached else ""
    cached_name = cached.get("name") if cached else None
    cached_div  = cached.get("dividend_yield") if cached else None
    cached_per  = cached.get("per") if cached else None

    naver = await _fetch_price_naver(ticker)

    # Naver 우선, 없으면 DB 캐시
    dividend_yield = naver.get("dividend_yield") if naver.get("dividend_yield") is not None else cached_div
    per            = naver.get("per") if naver.get("per") is not None else cached_per

    return {
        "ticker":         ticker,
        "name":           naver.get("name") or cached_name or ticker,
        "close":          naver.get("close"),
        "grade":          grade,
        "dividend_yield": dividend_yield,
        "per":            per,
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
    invest_amount: int = Query(0, ge=0),
):
    """미래 가치 시뮬레이터 (Bear/Base/Bull + 벤치마크 비교 + 교체 추천)"""
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

    ticker = cached["ticker"]

    simulation = simulate_5year(
        current_price=close,
        current_eps=current_eps,
        eps_cagr_scenarios={"bear": bear_eps_cagr, "base": base_eps_cagr, "bull": bull_eps_cagr},
        per_scenarios={"bear": bear_per, "base": base_per, "bull": bull_per},
        dividend_yield=cached.get("dividend_yield") or 0.0,
        hold_years=hold_years,
        invest_amount=invest_amount,
    )

    response = {
        "ticker": ticker,
        "name": cached.get("name"),
        "current_price": close,
        "current_eps": current_eps,
        "simulation": simulation,
        "benchmarks": None,
        "replacement": [],
    }

    if invest_amount > 0:
        # 벤치마크 비교 (적금 / 서울아파트 / 물가)
        response["benchmarks"] = calculate_benchmarks(invest_amount, hold_years)

        # 교체 추천: TENBAGGER 상위 종목 중 현재 종목보다 base 수익이 높은 상위 3개
        try:
            candidates = query_scores(grades=["TENBAGGER"], limit=10)
            replacement = []
            base_final = simulation["base"].get("final_amount") or 0

            for cand in candidates:
                if cand["ticker"] == ticker:
                    continue
                c_close = cand.get("close")
                c_per = cand.get("per") or 15.0
                c_eps_cagr = cand.get("eps_cagr_5y") or 10.0
                if not c_close or c_close <= 0:
                    continue
                c_eps = round(c_close / c_per) if c_per > 0 else None
                if not c_eps:
                    continue

                bear_c = max(-5.0, c_eps_cagr * 0.5)
                bull_c = c_eps_cagr * 1.5
                c_sim = simulate_5year(
                    current_price=c_close,
                    current_eps=c_eps,
                    eps_cagr_scenarios={"bear": bear_c, "base": c_eps_cagr, "bull": bull_c},
                    per_scenarios={"bear": bear_per, "base": base_per, "bull": bull_per},
                    dividend_yield=cand.get("dividend_yield") or 0.0,
                    hold_years=hold_years,
                    invest_amount=invest_amount,
                )
                c_base_final = c_sim["base"].get("final_amount") or 0
                replacement.append({
                    "ticker": cand["ticker"],
                    "name": cand.get("name"),
                    "grade": cand["grade"],
                    "total_score": cand.get("total_score"),
                    "base_final_amount": c_base_final,
                    "base_total_return_pct": c_sim["base"]["total_return"],
                    "base_cagr": c_sim["base"]["cagr"],
                    "vs_current": c_base_final - base_final,
                })

            replacement.sort(key=lambda x: x["base_final_amount"], reverse=True)
            response["replacement"] = replacement[:3]
        except Exception as e:
            print(f"[Simulate] 교체 추천 조회 실패: {e}")

    return response


@router.get("/portfolio-recommend")
async def portfolio_recommend(tickers: str = Query(..., description="쉼표로 구분된 종목코드")):
    """보유 종목 등급 진단 및 교체 후보 추천"""
    ticker_list = [t.strip() for t in tickers.split(",") if t.strip()]
    if not ticker_list:
        raise HTTPException(status_code=400, detail="tickers 파라미터가 필요합니다")

    # 각 보유 종목 스코어 조회
    current = []
    for t in ticker_list:
        cached = get_score_cached(t)
        current.append({
            "ticker": t,
            "name":          cached.get("name") if cached else t,
            "grade":         cached.get("grade") if cached else "UNKNOWN",
            "total_score":   cached.get("total_score") if cached else None,
            "close":         cached.get("close") if cached else None,
            "dividend_yield":cached.get("dividend_yield") if cached else None,
            "eps_cagr_5y":   cached.get("eps_cagr_5y") if cached else None,
        })

    # TENBAGGER → COMPOUNDER 순으로 상위 후보 (보유 중이지 않은 것)
    held_set = set(ticker_list)
    candidates: list[dict] = []
    for grade in ["TENBAGGER", "COMPOUNDER"]:
        if len(candidates) >= 10:
            break
        pool = query_scores(grades=[grade], limit=20)
        for s in pool:
            if s["ticker"] not in held_set:
                candidates.append({
                    "ticker":        s["ticker"],
                    "name":          s.get("name"),
                    "grade":         s.get("grade"),
                    "total_score":   s.get("total_score"),
                    "close":         s.get("close"),
                    "dividend_yield":s.get("dividend_yield"),
                    "eps_cagr_5y":   s.get("eps_cagr_5y"),
                })
            if len(candidates) >= 10:
                break

    return {"current": current, "candidates": candidates}


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
