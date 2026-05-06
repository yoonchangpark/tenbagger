"""
기업 정보 및 스코어 DB 레포지토리
- 분석 결과를 PostgreSQL에 저장/조회
- 24시간 캐시 TTL 적용
"""
from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy import text
from app.core.database import SessionLocal

CACHE_TTL_HOURS = 24


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ── 기업 기본 정보 ────────────────────────────────────────────────────────────

def save_company(ticker: str, name: str, market: str, dart_code: str) -> int:
    """companies 테이블에 upsert, company_id 반환"""
    sql = text("""
        INSERT INTO companies (ticker, name, market, dart_code, updated_at)
        VALUES (:ticker, :name, :market, :dart_code, NOW())
        ON CONFLICT (ticker) DO UPDATE
            SET name=EXCLUDED.name,
                market=EXCLUDED.market,
                dart_code=EXCLUDED.dart_code,
                updated_at=NOW()
        RETURNING id
    """)
    with SessionLocal() as session:
        row = session.execute(sql, {
            "ticker": ticker,
            "name": name,
            "market": market,
            "dart_code": dart_code or "",
        }).fetchone()
        session.commit()
        return row[0]


# ── 재무 데이터 ────────────────────────────────────────────────────────────────

def save_financials(company_id: int, financials: list[dict]) -> None:
    """financials_annual 테이블에 upsert"""
    if not financials:
        return
    sql = text("""
        INSERT INTO financials_annual (
            company_id, year, revenue, operating_profit, net_income,
            total_assets, total_equity, total_debt, cash,
            current_assets, current_liab, cfo, capex, fcf,
            eps, dps, payout_ratio
        ) VALUES (
            :company_id, :year, :revenue, :operating_profit, :net_income,
            :total_assets, :total_equity, :total_debt, :cash,
            :current_assets, :current_liab, :cfo, :capex, :fcf,
            :eps, :dps, :payout_ratio
        )
        ON CONFLICT (company_id, year) DO UPDATE SET
            revenue=EXCLUDED.revenue,
            operating_profit=EXCLUDED.operating_profit,
            net_income=EXCLUDED.net_income,
            total_assets=EXCLUDED.total_assets,
            total_equity=EXCLUDED.total_equity,
            total_debt=EXCLUDED.total_debt,
            cash=EXCLUDED.cash,
            current_assets=EXCLUDED.current_assets,
            current_liab=EXCLUDED.current_liab,
            cfo=EXCLUDED.cfo,
            capex=EXCLUDED.capex,
            fcf=EXCLUDED.fcf,
            eps=EXCLUDED.eps,
            dps=EXCLUDED.dps,
            payout_ratio=EXCLUDED.payout_ratio
    """)
    with SessionLocal() as session:
        for f in financials:
            session.execute(sql, {
                "company_id": company_id,
                "year": f.get("year"),
                "revenue": f.get("revenue"),
                "operating_profit": f.get("operating_profit"),
                "net_income": f.get("net_income"),
                "total_assets": f.get("total_assets"),
                "total_equity": f.get("total_equity"),
                "total_debt": f.get("total_debt"),
                "cash": f.get("cash"),
                "current_assets": f.get("current_assets"),
                "current_liab": f.get("current_liab"),
                "cfo": f.get("cfo"),
                "capex": f.get("capex"),
                "fcf": f.get("fcf"),
                "eps": f.get("eps"),
                "dps": f.get("dps"),
                "payout_ratio": f.get("payout_ratio"),
            })
        session.commit()


# ── 스코어 저장 ────────────────────────────────────────────────────────────────

def save_score(company_id: int, ticker: str, name: str, market: str,
               score: dict, close: int = None, market_cap: int = None) -> None:
    """scores 테이블에 upsert (ticker 기준 최신 1건 유지)"""
    # 기존 레코드 삭제 후 insert (TTL 기반 갱신)
    del_sql = text("DELETE FROM scores WHERE ticker = :ticker")
    ins_sql = text("""
        INSERT INTO scores (
            company_id, ticker, name, market,
            total_score, grade,
            growth_score, stability_score, cashflow_score, dividend_score, consistency_score,
            revenue_cagr_5y, eps_cagr_5y, avg_roe_5y,
            avg_operating_margin, avg_fcf_margin, debt_ratio, current_ratio,
            avg_payout_ratio, dividend_yield, per, pbr,
            close, market_cap, analyzed_at
        ) VALUES (
            :company_id, :ticker, :name, :market,
            :total_score, :grade,
            :growth_score, :stability_score, :cashflow_score, :dividend_score, :consistency_score,
            :revenue_cagr_5y, :eps_cagr_5y, :avg_roe_5y,
            :avg_operating_margin, :avg_fcf_margin, :debt_ratio, :current_ratio,
            :avg_payout_ratio, :dividend_yield, :per, :pbr,
            :close, :market_cap, NOW()
        )
    """)
    with SessionLocal() as session:
        session.execute(del_sql, {"ticker": ticker})
        session.execute(ins_sql, {
            "company_id": company_id,
            "ticker": ticker,
            "name": name,
            "market": market,
            "total_score": score.get("total_score"),
            "grade": score.get("grade"),
            "growth_score": score.get("growth_score"),
            "stability_score": score.get("stability_score"),
            "cashflow_score": score.get("cashflow_score"),
            "dividend_score": score.get("dividend_score"),
            "consistency_score": score.get("consistency_score"),
            "revenue_cagr_5y": score.get("revenue_cagr_5y"),
            "eps_cagr_5y": score.get("eps_cagr_5y"),
            "avg_roe_5y": score.get("avg_roe_5y"),
            "avg_operating_margin": score.get("avg_operating_margin"),
            "avg_fcf_margin": score.get("avg_fcf_margin"),
            "debt_ratio": score.get("debt_ratio"),
            "current_ratio": score.get("current_ratio"),
            "avg_payout_ratio": score.get("avg_payout_ratio"),
            "dividend_yield": score.get("dividend_yield"),
            "per": score.get("per"),
            "pbr": score.get("pbr"),
            "close": close,
            "market_cap": market_cap,
        })
        session.commit()


# ── 캐시 조회 ────────────────────────────────────────────────────────────────

def get_score_cached(ticker: str) -> Optional[dict]:
    """
    24시간 이내 분석 결과가 있으면 반환, 없으면 None
    """
    cutoff = _now_utc() - timedelta(hours=CACHE_TTL_HOURS)
    sql = text("""
        SELECT
            s.ticker, s.name, s.market,
            s.total_score, s.grade,
            s.growth_score, s.stability_score, s.cashflow_score,
            s.dividend_score, s.consistency_score,
            s.revenue_cagr_5y, s.eps_cagr_5y, s.avg_roe_5y,
            s.avg_operating_margin, s.avg_fcf_margin,
            s.debt_ratio, s.current_ratio, s.avg_payout_ratio,
            s.dividend_yield, s.per, s.pbr,
            s.close, s.market_cap, s.analyzed_at
        FROM scores s
        WHERE s.ticker = :ticker
          AND s.analyzed_at >= :cutoff
        ORDER BY s.analyzed_at DESC
        LIMIT 1
    """)
    with SessionLocal() as session:
        row = session.execute(sql, {"ticker": ticker, "cutoff": cutoff}).fetchone()
        if not row:
            return None
        keys = [
            "ticker", "name", "market",
            "total_score", "grade",
            "growth_score", "stability_score", "cashflow_score",
            "dividend_score", "consistency_score",
            "revenue_cagr_5y", "eps_cagr_5y", "avg_roe_5y",
            "avg_operating_margin", "avg_fcf_margin",
            "debt_ratio", "current_ratio", "avg_payout_ratio",
            "dividend_yield", "per", "pbr",
            "close", "market_cap", "analyzed_at",
        ]
        return dict(zip(keys, row))


def get_financials_cached(ticker: str) -> list[dict]:
    """캐시된 재무 데이터 반환"""
    sql = text("""
        SELECT f.year, f.revenue, f.operating_profit, f.net_income,
               f.total_assets, f.total_equity, f.total_debt, f.cash,
               f.current_assets, f.current_liab, f.cfo, f.capex, f.fcf,
               f.eps, f.dps, f.payout_ratio
        FROM financials_annual f
        JOIN companies c ON c.id = f.company_id
        WHERE c.ticker = :ticker
        ORDER BY f.year
    """)
    with SessionLocal() as session:
        rows = session.execute(sql, {"ticker": ticker}).fetchall()
        cols = ["year", "revenue", "operating_profit", "net_income",
                "total_assets", "total_equity", "total_debt", "cash",
                "current_assets", "current_liab", "cfo", "capex", "fcf",
                "eps", "dps", "payout_ratio"]
        return [dict(zip(cols, r)) for r in rows]


# ── 스크리너용 조회 ────────────────────────────────────────────────────────────

def query_scores(
    grades: list[str] = None,
    min_score: float = None,
    min_growth: float = None,
    min_dividend_yield: float = None,
    max_debt_ratio: float = None,
    market: str = None,
    sort_by: str = "total_score",
    limit: int = 50,
) -> list[dict]:
    """스크리너 필터 조회"""
    conditions = ["1=1"]
    params = {}

    if grades:
        conditions.append("grade = ANY(:grades)")
        params["grades"] = grades
    if min_score is not None:
        conditions.append("total_score >= :min_score")
        params["min_score"] = min_score
    if min_growth is not None:
        conditions.append("growth_score >= :min_growth")
        params["min_growth"] = min_growth
    if min_dividend_yield is not None:
        conditions.append("dividend_yield >= :min_div")
        params["min_div"] = min_dividend_yield
    if max_debt_ratio is not None:
        conditions.append("(debt_ratio IS NULL OR debt_ratio <= :max_debt)")
        params["max_debt"] = max_debt_ratio
    if market and market != "전체":
        conditions.append("market = :market")
        params["market"] = market

    allowed_sorts = {
        "total_score", "growth_score", "stability_score",
        "cashflow_score", "dividend_score", "consistency_score",
        "dividend_yield", "analyzed_at",
    }
    order_col = sort_by if sort_by in allowed_sorts else "total_score"

    sql = text(f"""
        SELECT ticker, name, market, total_score, grade,
               growth_score, stability_score, cashflow_score,
               dividend_score, consistency_score,
               revenue_cagr_5y, avg_roe_5y, dividend_yield,
               debt_ratio, close, market_cap, analyzed_at
        FROM scores
        WHERE {' AND '.join(conditions)}
        ORDER BY {order_col} DESC NULLS LAST
        LIMIT :limit
    """)
    params["limit"] = limit

    with SessionLocal() as session:
        rows = session.execute(sql, params).fetchall()
        cols = [
            "ticker", "name", "market", "total_score", "grade",
            "growth_score", "stability_score", "cashflow_score",
            "dividend_score", "consistency_score",
            "revenue_cagr_5y", "avg_roe_5y", "dividend_yield",
            "debt_ratio", "close", "market_cap", "analyzed_at",
        ]
        return [dict(zip(cols, r)) for r in rows]
