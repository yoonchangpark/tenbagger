"""
종목명 / 종목코드 검색 API
GET /api/search?q=삼성전자
"""
from fastapi import APIRouter, Query
from pykrx import stock
import re

router = APIRouter(prefix="/api/search", tags=["Search"])

_ticker_cache: dict[str, list[dict]] = {}  # 인메모리 캐시 (프로세스 재시작 시 초기화)


def _load_all_tickers() -> list[dict]:
    """KOSPI + KOSDAQ 전체 종목 로드 (캐시)"""
    if "all" in _ticker_cache:
        return _ticker_cache["all"]

    result = []
    for market in ["KOSPI", "KOSDAQ"]:
        try:
            tickers = stock.get_market_ticker_list(market=market)
            for ticker in tickers:
                name = stock.get_market_ticker_name(ticker)
                if name:
                    result.append({
                        "ticker": ticker,
                        "name": name,
                        "market": market,
                    })
        except Exception as e:
            print(f"[Search] {market} 로드 실패: {e}")

    _ticker_cache["all"] = result
    return result


@router.get("")
def search_companies(q: str = Query(..., min_length=1, max_length=30)):
    """
    종목명 또는 종목코드로 검색
    - 최대 10개 반환
    - 정확도 순: 코드 일치 > 이름 시작 > 이름 포함
    """
    q = q.strip()
    all_tickers = _load_all_tickers()

    exact_code = []
    name_starts = []
    name_contains = []

    q_clean = re.sub(r"\s+", "", q).lower()

    for item in all_tickers:
        t = item["ticker"]
        n = re.sub(r"\s+", "", item["name"]).lower()

        if t == q or t == q.upper():
            exact_code.append(item)
        elif n.startswith(q_clean):
            name_starts.append(item)
        elif q_clean in n:
            name_contains.append(item)

    combined = exact_code + name_starts + name_contains
    # 중복 제거 (ticker 기준)
    seen = set()
    unique = []
    for item in combined:
        if item["ticker"] not in seen:
            seen.add(item["ticker"])
            unique.append(item)

    return unique[:10]


@router.post("/refresh")
def refresh_ticker_cache():
    """종목 캐시 강제 갱신"""
    _ticker_cache.clear()
    total = len(_load_all_tickers())
    return {"message": f"캐시 갱신 완료: {total}개 종목"}
