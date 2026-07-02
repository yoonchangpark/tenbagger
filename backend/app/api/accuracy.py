"""
GET  /api/v2/accuracy/results   — 정확도 검증 최신 결과 조회 (예측 시점 등급 전수 추적)
POST /api/v2/accuracy/run       — 수동 트리거 (백그라운드)
GET  /api/v2/accuracy/backfill  — 백필 v1 집계 조회 (점-인-타임 재구성)
POST /api/v2/accuracy/backfill  — 백필 실행 (백그라운드)
"""
from fastapi import APIRouter, Query, BackgroundTasks
from sqlalchemy import text

from app.core.database import SessionLocal

router = APIRouter(prefix="/api/v2/accuracy", tags=["accuracy"])

_TARGETS = {
    "TENBAGGER":  {"target_pct": 50.0, "label": "2년 +50%"},
    "COMPOUNDER": {"target_pct": 20.0, "label": "1년 +20%"},
    "WATCHLIST":  {"target_pct": 10.0, "label": "1년 +10%"},
}


@router.get("/results")
def get_accuracy_results(grade: str = Query(None, description="등급 필터 (TENBAGGER|COMPOUNDER|WATCHLIST)")):
    """정확도 검증 결과 조회. 등급별 요약 + 개별 종목 최신 100건."""
    with SessionLocal() as session:
        table_exists = session.execute(text("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'accuracy_results'
            )
        """)).scalar()

        if not table_exists:
            return {
                "summary": {},
                "items": [],
                "message": "검증 데이터 없음. 매주 일요일 23:00 KST 자동 실행됩니다.",
            }

        where = "WHERE 1=1"
        params: dict = {}
        if grade:
            where += " AND grade = :grade"
            params["grade"] = grade.upper()

        summary_rows = session.execute(text(f"""
            SELECT
                grade,
                COUNT(*) AS count,
                ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY return_pct)::numeric, 2) AS median_pct,
                ROUND(AVG(return_pct)::numeric, 2) AS mean_pct,
                MAX(checked_at) AS last_checked
            FROM accuracy_results
            {where}
            GROUP BY grade
            ORDER BY grade
        """), params).fetchall()

        item_rows = session.execute(text(f"""
            SELECT ticker, grade, analyzed_at, entry_price, current_price,
                   return_pct, hold_days, checked_at
            FROM accuracy_results
            {where}
            ORDER BY checked_at DESC
            LIMIT 100
        """), params).fetchall()

    summary = {}
    for r in summary_rows:
        d = dict(r._mapping)
        info = _TARGETS.get(d["grade"], {})
        target_pct = info.get("target_pct", 0)
        summary[d["grade"]] = {
            "count":        int(d["count"]),
            "median_pct":   float(d["median_pct"]) if d["median_pct"] is not None else None,
            "mean_pct":     float(d["mean_pct"])   if d["mean_pct"]   is not None else None,
            "target_pct":   target_pct,
            "target_label": info.get("label"),
            "last_checked": d["last_checked"].isoformat() if d["last_checked"] else None,
        }

    items = []
    for r in item_rows:
        d = dict(r._mapping)
        d["analyzed_at"]   = d["analyzed_at"].isoformat()   if d["analyzed_at"]   else None
        d["checked_at"]    = d["checked_at"].isoformat()     if d["checked_at"]    else None
        d["entry_price"]   = float(d["entry_price"])   if d["entry_price"]   is not None else None
        d["current_price"] = float(d["current_price"]) if d["current_price"] is not None else None
        d["return_pct"]    = float(d["return_pct"])    if d["return_pct"]    is not None else None
        items.append(d)

    return {"summary": summary, "items": items}


@router.post("/run")
def trigger_accuracy_run(grade: str = Query(None, description="특정 등급만 검증 (없으면 전체)")):
    """정확도 검증 수동 트리거. 백그라운드 프로세스로 실행."""
    import os, subprocess, sys
    args = [sys.executable, "-m", "app.agents.accuracy_validator"]
    if grade:
        args += ["--grade", grade.upper()]
    subprocess.Popen(
        args,
        cwd=os.path.join(os.path.dirname(__file__), "..", "..", ".."),
    )
    return {"message": "정확도 검증 시작 (백그라운드)", "grade": grade or "ALL"}


# ── 백필 v1 (점-인-타임 재구성) ──────────────────────────────
_backfill_progress: dict = {"status": "idle", "total": 0, "done": 0, "stored": 0}


@router.get("/backfill")
def get_backfill(hold_years: int = Query(None, ge=1, le=10,
                 description="보유기간 필터 (없으면 전체 혼합 집계)")):
    """백필 집계 + 진행 상황 조회. hold_years 지정 시 해당 보유기간만 집계."""
    from app.domain.backfill import get_backfill_summary
    result = get_backfill_summary(hold_years=hold_years)
    result["progress"] = _backfill_progress
    return result


@router.post("/backfill")
async def run_backfill_endpoint(
    background_tasks: BackgroundTasks,
    base_years: str = Query("2016", description="쉼표구분 연도 (기본 2016 → 하단 사례와 동일 창)"),
    hold_years: int = Query(10, ge=1, le=10),
    limit: int = Query(200, ge=1, le=1000, description="상위 N종목 (점수순)"),
):
    """백필 실행 — (종목 × 연도) 점-인-타임 등급·수익률을 재구성해 저장. 백그라운드."""
    from app.domain.backfill import run_backfill
    if _backfill_progress.get("status") == "running":
        return {"message": "이미 백필이 실행 중입니다.", "progress": _backfill_progress}

    try:
        years = sorted({int(y.strip()) for y in base_years.split(",") if y.strip()})
    except ValueError:
        return {"error": "base_years 형식 오류 (예: 2018,2019,2020)"}
    if not years:
        return {"error": "base_years가 비어 있습니다."}

    _backfill_progress.update({"status": "running", "total": 0, "done": 0, "stored": 0})

    async def _job():
        try:
            await run_backfill(years, hold_years, limit, progress=_backfill_progress)
        except Exception as e:
            _backfill_progress.update({"status": "error", "error": str(e)})

    background_tasks.add_task(_job)
    return {"message": "백필 시작 (백그라운드)", "base_years": years,
            "hold_years": hold_years, "limit": limit}


# ── 팩터 평가 (자율 검증용 — 밴드×코호트×시장) ──────────────────────────────
_SCORE_COLS = {"momentum": "momentum_score", "discovery": "discovery_score", "quality": "total_score"}


@router.get("/factor-eval")
def factor_eval(
    score: str = Query("momentum", description="momentum | discovery | quality"),
    hold_years: int = Query(5, ge=1, le=10),
    threshold: float = Query(7.0, description="상위 밴드 기준점"),
):
    """점수 팩터의 예측력을 밴드별·코호트별로 집계해 반환한다.
    GitHub Actions 등 외부에서 curl로 검증 결과를 읽을 수 있게 하는 자율 검증 엔드포인트."""
    col = _SCORE_COLS.get(score)
    if not col:
        return {"error": f"score는 {list(_SCORE_COLS)} 중 하나"}

    with SessionLocal() as session:
        # 밴드별 표본·중간수익률 (단조성 확인)
        bands = session.execute(text(f"""
            SELECT CASE WHEN {col} >= :th THEN 'top'
                        WHEN {col} >= 5 THEN 'mid'
                        WHEN {col} >= 3 THEN 'low' ELSE 'bottom' END AS band,
                   COUNT(return_pct) AS n,
                   ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY return_pct)::numeric, 1) AS median_pct
            FROM backfill_results
            WHERE hold_years = :hy AND {col} IS NOT NULL AND return_pct IS NOT NULL
            GROUP BY 1
        """), {"th": threshold, "hy": hold_years}).fetchall()

        # 코호트별 상위밴드 vs 시장 (강세장 의존/알파 분리)
        cohorts = session.execute(text(f"""
            SELECT base_year,
                   COUNT(*) FILTER (WHERE {col} >= :th AND return_pct IS NOT NULL) AS n_top,
                   ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY return_pct)
                         FILTER (WHERE {col} >= :th)::numeric, 1) AS top_median,
                   ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY return_pct)::numeric, 1) AS market_median
            FROM backfill_results
            WHERE hold_years = :hy AND return_pct IS NOT NULL
            GROUP BY base_year ORDER BY base_year
        """), {"th": threshold, "hy": hold_years}).fetchall()

    band_order = {"top": 0, "mid": 1, "low": 2, "bottom": 3}
    return {
        "score": score, "column": col, "hold_years": hold_years, "threshold": threshold,
        "bands": [
            {"band": r[0], "count": int(r[1]), "median_pct": float(r[2]) if r[2] is not None else None}
            for r in sorted(bands, key=lambda x: band_order.get(x[0], 9))
        ],
        "cohorts": [
            {"base_year": int(r[0]), "n_top": int(r[1]),
             "top_median": float(r[2]) if r[2] is not None else None,
             "market_median": float(r[3]) if r[3] is not None else None}
            for r in cohorts
        ],
    }
