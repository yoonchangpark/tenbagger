"""
GET  /api/v2/accuracy/results  — 정확도 검증 최신 결과 조회
POST /api/v2/accuracy/run      — 수동 트리거 (백그라운드)
"""
from fastapi import APIRouter, Query
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
