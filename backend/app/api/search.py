"""
종목명 / 종목코드 검색 API
GET /api/search?q=삼성전자        (pykrx 기반)
GET /api/v2/search?q=삼성전자     (DB 기반, pykrx fallback)
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session
from pykrx import stock
import re

from app.core.auth import get_db

router = APIRouter(tags=["Search"])

_ticker_cache: dict[str, list[dict]] = {}  # 인메모리 캐시 (프로세스 재시작 시 초기화)

# ── /api/v2/search — DB 기반 (pykrx fallback) ────────────────────
@router.get("/api/v2/search")
def search_companies_v2(
    q: str = Query(..., min_length=1, max_length=30),
    db: Session = Depends(get_db),
):
    """
    DB(companies + scores)에서 종목명/코드 검색.
    DB가 비어있으면 pykrx fallback.
    최대 10개 반환.
    """
    q = q.strip()
    q_lower = re.sub(r"\s+", "", q).lower()

    # 1. DB 검색 (companies 테이블)
    try:
        rows = db.execute(text("""
            SELECT DISTINCT c.ticker, COALESCE(s.name, c.name) AS name, c.market
            FROM companies c
            LEFT JOIN scores s ON s.ticker = c.ticker
            WHERE REPLACE(LOWER(c.name), ' ', '') LIKE :q
               OR REPLACE(LOWER(s.name), ' ', '') LIKE :q
               OR c.ticker = :ticker
            ORDER BY
              CASE WHEN c.ticker = :ticker THEN 0
                   WHEN LOWER(c.name) LIKE :starts THEN 1
                   ELSE 2 END,
              c.ticker
            LIMIT 10
        """), {
            "q": f"%{q_lower}%",
            "ticker": q.upper(),
            "starts": f"{q_lower}%",
        }).fetchall()

        if rows:
            return [{"ticker": r.ticker, "name": r.name, "market": r.market or ""} for r in rows]
    except Exception:
        pass

    # 2. scores 테이블 직접 검색 (companies 비어있을 때 — ETL은 항상 scores를 채움)
    try:
        rows = db.execute(text("""
            SELECT ticker, name, market
            FROM scores
            WHERE REPLACE(LOWER(name), ' ', '') LIKE :q
               OR ticker = :ticker
            ORDER BY
              CASE WHEN ticker = :ticker THEN 0
                   WHEN LOWER(name) LIKE :starts THEN 1
                   ELSE 2 END,
              total_score DESC
            LIMIT 10
        """), {
            "q": f"%{q_lower}%",
            "ticker": q.upper(),
            "starts": f"{q_lower}%",
        }).fetchall()

        if rows:
            return [{"ticker": r.ticker, "name": r.name, "market": r.market or ""} for r in rows]
    except Exception:
        pass

    # 3. pykrx fallback (DB 전체 비어있을 때)
    return _pykrx_search(q, q_lower)


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


def _pykrx_search(q: str, q_clean: str) -> list[dict]:
    """pykrx 기반 종목 검색 (내부 헬퍼)"""
    all_tickers = _load_all_tickers()

    exact_code, name_starts, name_contains = [], [], []
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
    seen, unique = set(), []
    for item in combined:
        if item["ticker"] not in seen:
            seen.add(item["ticker"])
            unique.append(item)
    return unique[:10]


@router.get("/api/search")
def search_companies(
    q: str = Query(..., min_length=1, max_length=30),
    db: Session = Depends(get_db),
):
    """
    종목명 또는 종목코드로 검색 (scores DB 우선 → pykrx fallback)
    - 최대 10개 반환
    - 정확도 순: 코드 일치 > 이름 시작 > 이름 포함
    """
    q = q.strip()
    q_clean = re.sub(r"\s+", "", q).lower()

    # scores 테이블 우선 검색
    try:
        rows = db.execute(text("""
            SELECT ticker, name, market
            FROM scores
            WHERE REPLACE(LOWER(name), ' ', '') LIKE :q
               OR ticker = :ticker
            ORDER BY
              CASE WHEN ticker = :ticker THEN 0
                   WHEN LOWER(name) LIKE :starts THEN 1
                   ELSE 2 END,
              total_score DESC
            LIMIT 10
        """), {
            "q": f"%{q_clean}%",
            "ticker": q.upper(),
            "starts": f"{q_clean}%",
        }).fetchall()
        if rows:
            return [{"ticker": r.ticker, "name": r.name, "market": r.market or ""} for r in rows]
    except Exception:
        pass

    return _pykrx_search(q, q_clean)


@router.post("/api/search/refresh")
def refresh_ticker_cache():
    """종목 캐시 강제 갱신"""
    _ticker_cache.clear()
    total = len(_load_all_tickers())
    return {"message": f"캐시 갱신 완료: {total}개 종목"}
