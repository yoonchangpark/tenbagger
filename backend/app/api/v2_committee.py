"""
Phase 6: AI 투자위원회 API
  GET  /api/v2/committee/{ticker}        위원회 분석 (캐시 우선, 24시간)
  POST /api/v2/committee/{ticker}/run    강제 재분석 (캐시 무시)
  GET  /api/v2/committee/recent          최근 위원회 분석 목록 (관리자/모니터링용)
"""
import json
import datetime
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from app.core.database import SessionLocal
from app.agents.committee import run_committee


router = APIRouter(prefix="/api/v2/committee", tags=["ai-committee"])

CACHE_HOURS = 24  # 24시간 이내 결과 재사용


def _get_cached(ticker: str) -> dict | None:
    """24시간 이내 캐시 조회"""
    with SessionLocal() as session:
        row = session.execute(text("""
            SELECT result_json, analyzed_at, decision, confidence, consensus_score
            FROM committee_cache
            WHERE ticker = :t
              AND analyzed_at >= NOW() - INTERVAL '%d hours'
            ORDER BY analyzed_at DESC LIMIT 1
        """ % CACHE_HOURS), {"t": ticker}).fetchone()
    if not row:
        return None
    result = row.result_json if isinstance(row.result_json, dict) else json.loads(row.result_json)
    result["from_cache"] = True
    result["cached_at"] = row.analyzed_at.isoformat() if row.analyzed_at else None
    return result


def _save_cache(ticker: str, result: dict) -> None:
    """결과 캐시 저장"""
    try:
        with SessionLocal() as session:
            session.execute(text("""
                INSERT INTO committee_cache
                    (ticker, name, decision, confidence, consensus_score, result_json, analyzed_at)
                VALUES (:t, :n, :d, :c, :s, CAST(:r AS JSONB), NOW())
            """), {
                "t": ticker,
                "n": result.get("name", ""),
                "d": result.get("committee_decision", "HOLD"),
                "c": result.get("confidence", 0.5),
                "s": result.get("consensus_score", 5.0),
                "r": json.dumps(result, ensure_ascii=False),
            })
            session.commit()
    except Exception as e:
        print(f"[committee] 캐시 저장 실패 (무시): {e}")


@router.get("/{ticker}")
async def get_committee_analysis(ticker: str, force: bool = Query(False, description="True면 캐시 무시 강제 재분석")):
    """
    AI 투자위원회 분석 결과 조회.
    - 24시간 이내 캐시 있으면 즉시 반환 (~0.1초)
    - 없거나 force=True면 4개 에이전트 병렬 실행 (~15초, $0.04)
    """
    import traceback
    ticker = ticker.upper().strip()
    if not force:
        try:
            cached = _get_cached(ticker)
            if cached:
                return cached
        except Exception as e:
            print(f"[committee] 캐시 조회 오류 (계속 진행): {e}")
            traceback.print_exc()

    # 신규 분석
    try:
        result = await run_committee(ticker)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"위원회 분석 오류: {type(e).__name__}: {e}")

    if result.get("error"):
        raise HTTPException(
            status_code=404 if result["error"] == "ticker_not_in_scores" else 500,
            detail=result["error"]
        )

    result["from_cache"] = False
    try:
        _save_cache(ticker, result)
    except Exception as e:
        print(f"[committee] 캐시 저장 오류 (응답은 정상): {e}")
        traceback.print_exc()
    return result


@router.post("/{ticker}/run")
async def trigger_committee_analysis(ticker: str):
    """강제 재분석 트리거 (캐시 무시)"""
    ticker = ticker.upper().strip()
    result = await run_committee(ticker)
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])
    result["from_cache"] = False
    _save_cache(ticker, result)
    return result


@router.get("/recent")
def list_recent_committee(limit: int = Query(20, ge=1, le=100)):
    """최근 위원회 분석 결과 목록 (관리자/모니터링)"""
    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT ticker, name, decision, confidence, consensus_score, analyzed_at
            FROM committee_cache
            ORDER BY analyzed_at DESC LIMIT :lim
        """), {"lim": limit}).fetchall()
    return {
        "items": [
            {
                "ticker": r.ticker,
                "name": r.name,
                "decision": r.decision,
                "confidence": round(r.confidence or 0, 2),
                "score": round(r.consensus_score or 0, 2),
                "analyzed_at": r.analyzed_at.isoformat() if r.analyzed_at else None,
            }
            for r in rows
        ],
        "count": len(rows),
    }
