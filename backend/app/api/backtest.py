"""
백테스트 API
GET /api/backtest/{ticker}?base_year=2015&hold_years=5
GET /api/backtest/market?base_year=2015
POST /api/v2/backtest/explain  ← 신규: AI 백테스트 결과 설명
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from app.domain.backtest import run_backtest, run_market_backtest, generate_backtest_explanation

router = APIRouter(prefix="/api/backtest", tags=["Backtest"])


@router.get("/market")
async def backtest_market(
    base_year: int = Query(2015, description="분석 기준 연도"),
    hold_years: int = Query(5, ge=1, le=10, description="보유 기간 (년)"),
    limit: int = Query(50, ge=1, le=200, description="분석 종목 수"),
):
    result = await run_market_backtest(base_year=base_year, hold_years=hold_years, limit=limit)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/{ticker}")
async def backtest_ticker(
    ticker: str,
    base_year: int = Query(2015, description="분석 기준 연도"),
    hold_years: int = Query(5, ge=1, le=10, description="보유 기간 (년)"),
):
    result = await run_backtest(ticker=ticker, base_year=base_year, hold_years=hold_years)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


class ExplainRequest(BaseModel):
    ticker: str
    name: str = ""
    base_year: int
    end_year: int
    hold_years: int
    predicted_grade: str
    actual_return_pct: float | None
    score_detail: dict
    prediction_correct: bool | None


@router.post("/explain")
async def backtest_explain(req: ExplainRequest):
    """AI가 백테스트 결과(적중/빗나감)를 분석하여 설명 제공"""
    result = await generate_backtest_explanation(
        ticker=req.ticker,
        name=req.name,
        base_year=req.base_year,
        end_year=req.end_year,
        hold_years=req.hold_years,
        predicted_grade=req.predicted_grade,
        actual_return_pct=req.actual_return_pct,
        score_detail=req.score_detail,
        prediction_correct=req.prediction_correct,
    )
    return result
