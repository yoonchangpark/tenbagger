"""
backend/app/api/admin.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
관리자 전용 수동 트리거 API
  POST /api/admin/run-etl       ETL 즉시 실행
  POST /api/admin/run-advisor   어드바이저 리포트 즉시 실행
  GET  /api/admin/status        DB 현황 조회

인증: X-Admin-Secret 헤더 또는 ?secret= 쿼리파라미터
"""
import os
import subprocess
import sys
import asyncio
from fastapi import APIRouter, Header, HTTPException, Query
from sqlalchemy import text
from app.core.config import settings
from app.core.database import SessionLocal

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _check_secret(secret: str):
    """관리자 시크릿 키 검증"""
    admin_secret = settings.admin_secret or os.environ.get("ADMIN_SECRET", "")
    if not admin_secret:
        raise HTTPException(status_code=503, detail="ADMIN_SECRET 환경변수가 설정되지 않았습니다.")
    if secret != admin_secret:
        raise HTTPException(status_code=403, detail="인증 실패: 시크릿 키가 올바르지 않습니다.")


def _run_subprocess(cmd: list, timeout: int = 7200) -> dict:
    """서브프로세스 실행 후 결과 반환"""
    cwd = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout[-2000:] if result.stdout else "",
        "stderr": result.stderr[-1000:] if result.stderr else "",
    }


# ── DB 마이그레이션 (init.sql 재실행) ─────────────────────────────
@router.post("/init-db")
def admin_init_db(secret: str = Query(...)):
    """init.sql 재실행 — 새로 추가된 테이블 생성 (IF NOT EXISTS 안전)"""
    _check_secret(secret)
    sql_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "..", "scripts", "init.sql"
    )
    if not os.path.exists(sql_path):
        raise HTTPException(status_code=500, detail=f"init.sql not found at {sql_path}")
    with open(sql_path, "r", encoding="utf-8") as f:
        sql = f.read()
    try:
        with SessionLocal() as db:
            db.execute(text(sql))
            db.commit()
        # 생성 결과 확인
        with SessionLocal() as db:
            tables = db.execute(text("""
                SELECT tablename FROM pg_tables WHERE schemaname='public'
                ORDER BY tablename
            """)).fetchall()
        return {
            "status": "ok",
            "message": "init.sql 재실행 완료",
            "tables": [t.tablename for t in tables],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"init.sql 실행 오류: {e}")


# ── DB 현황 조회 ───────────────────────────────────────────────────
@router.get("/status")
def admin_status(secret: str = Query(...)):
    """DB 현황 조회 (종목 수, 등급 분포, 최근 분석 시각)"""
    _check_secret(secret)

    with SessionLocal() as db:
        total = db.execute(text("SELECT COUNT(*) FROM scores")).scalar() or 0
        grades = db.execute(text("""
            SELECT grade, COUNT(*) AS cnt
            FROM scores
            GROUP BY grade ORDER BY cnt DESC
        """)).fetchall()
        latest = db.execute(text("""
            SELECT MAX(analyzed_at) FROM scores
        """)).scalar()
        companies = db.execute(text("SELECT COUNT(*) FROM companies")).scalar() or 0

    return {
        "db_status": "connected",
        "companies_total": companies,
        "scores_total": total,
        "latest_analyzed_at": str(latest) if latest else None,
        "grade_distribution": {r.grade: r.cnt for r in grades},
    }


# ── ETL 수동 실행 ──────────────────────────────────────────────────
@router.post("/run-etl")
async def run_etl(
    secret: str = Query(...),
    market: str = Query("ALL", description="KOSPI / KOSDAQ / ALL"),
    skip_existing: bool = Query(True, description="이미 분석된 종목 스킵 여부"),
):
    """
    ETL을 백그라운드에서 즉시 실행합니다.
    응답은 즉시 반환 (ETL은 수 분~수 시간 소요).
    """
    _check_secret(secret)

    cmd = [sys.executable, "-m", "app.workers.etl", "--market", market]
    if skip_existing:
        cmd.append("--skip-existing")

    # 백그라운드 실행 (응답을 기다리지 않음)
    asyncio.create_task(asyncio.to_thread(_run_subprocess, cmd, 7200))

    return {
        "status": "started",
        "message": f"ETL 시작됨 (market={market}, skip_existing={skip_existing}). Railway 로그에서 진행상황을 확인하세요.",
        "cmd": " ".join(cmd),
    }


# ── 어드바이저 수동 실행 ────────────────────────────────────────────
@router.post("/run-advisor")
async def run_advisor(
    secret: str = Query(...),
    dry_run: bool = Query(False, description="True면 카톡 발송 없이 내용만 생성"),
):
    """
    일일 어드바이저 리포트 + 카카오 Push를 즉시 실행합니다.
    응답은 즉시 반환 (어드바이저는 수 분 소요).
    """
    _check_secret(secret)

    cmd = [sys.executable, "-m", "app.agents.advisor", "--skip-etl"]
    if dry_run:
        cmd.append("--dry-run")

    asyncio.create_task(asyncio.to_thread(_run_subprocess, cmd, 600))

    return {
        "status": "started",
        "message": f"어드바이저 리포트 시작됨 (dry_run={dry_run}). Railway 로그에서 결과를 확인하세요.",
        "cmd": " ".join(cmd),
    }


# ── ETL 동기 실행 (결과 대기) ───────────────────────────────────────
@router.post("/run-etl-sync")
def run_etl_sync(
    secret: str = Query(...),
    market: str = Query("KOSPI", description="KOSPI / KOSDAQ / ALL"),
    skip_existing: bool = Query(True),
    limit: int = Query(50, description="최대 처리 종목 수"),
):
    """
    ETL을 동기 실행하고 결과를 반환합니다 (소규모 테스트용).
    market=KOSPI, limit=50 정도로 테스트하세요.
    """
    _check_secret(secret)

    cmd = [sys.executable, "-m", "app.workers.etl",
           "--market", market, "--limit", str(limit)]
    if skip_existing:
        cmd.append("--skip-existing")

    try:
        result = _run_subprocess(cmd, timeout=300)
        return {
            "status": "done" if result["returncode"] == 0 else "error",
            "returncode": result["returncode"],
            "stdout": result["stdout"],
            "stderr": result["stderr"],
        }
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "message": "300초 초과. /run-etl (비동기) 사용 권장"}
