import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.api.company import router as company_router
from app.api.search import router as search_router
from app.api.screener import router as screener_router
from app.api.backtest import router as backtest_router
from app.api.qualitative import router as qualitative_router
from app.api.kakao import router as kakao_router
from app.api.v2_dashboard import router as dashboard_router
from app.api.auth import router as auth_router
from app.api.payment import router as payment_router
from app.api.watchlist import router as watchlist_router
from app.core.database import check_db, SessionLocal


def init_db():
    """DB 테이블 자동 생성 (IF NOT EXISTS)"""
    from sqlalchemy import text
    sql_path = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "init.sql")
    if not os.path.exists(sql_path):
        return
    with open(sql_path, "r", encoding="utf-8") as f:
        sql = f.read()
    db = SessionLocal()
    try:
        db.execute(text(sql))
        db.commit()
        print("✅ DB 초기화 완료")
    except Exception as e:
        print(f"⚠️ DB 초기화 중 오류 (이미 존재할 수 있음): {e}")
        db.rollback()
    finally:
        db.close()


def _run_etl_job():
    """매일 새벽 2시(KST) ETL 자동 실행"""
    import subprocess, sys
    print("🔄 [SCHEDULER] ETL 자동 시작...")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "app.workers.etl", "--market", "ALL", "--skip-existing"],
            capture_output=True, text=True, timeout=7200,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        print(f"✅ [SCHEDULER] ETL 완료\n{result.stdout[-500:] if result.stdout else ''}")
        if result.returncode != 0:
            print(f"⚠️ [SCHEDULER] ETL stderr: {result.stderr[-300:]}")
    except Exception as e:
        print(f"❌ [SCHEDULER] ETL 오류: {e}")


def _run_advisor_job():
    """매일 새벽 7시(KST) 일일 어드바이저 리포트 발송"""
    import subprocess, sys
    print("📧 [SCHEDULER] 어드바이저 리포트 자동 시작...")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "app.agents.advisor", "--skip-etl"],
            capture_output=True, text=True, timeout=600,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        print(f"✅ [SCHEDULER] 어드바이저 완료\n{result.stdout[-300:] if result.stdout else ''}")
    except Exception as e:
        print(f"❌ [SCHEDULER] 어드바이저 오류: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    # ── APScheduler: 새벽 자동 실행 ──────────────────────────────
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger

        scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

        # 매일 새벽 2:00 KST — ETL (전종목 재무데이터 수집)
        scheduler.add_job(
            lambda: asyncio.get_event_loop().run_in_executor(None, _run_etl_job),
            CronTrigger(hour=2, minute=0, timezone="Asia/Seoul"),
            id="etl_daily",
            name="Daily ETL",
            replace_existing=True,
        )

        # 매일 새벽 7:00 KST — 일일 어드바이저 리포트
        scheduler.add_job(
            lambda: asyncio.get_event_loop().run_in_executor(None, _run_advisor_job),
            CronTrigger(hour=7, minute=0, timezone="Asia/Seoul"),
            id="advisor_daily",
            name="Daily Advisor Report",
            replace_existing=True,
        )

        scheduler.start()
        print("✅ [SCHEDULER] 스케줄러 시작: ETL 02:00 KST, 리포트 07:00 KST")
    except Exception as e:
        print(f"⚠️ [SCHEDULER] 스케줄러 시작 실패 (무시): {e}")

    yield


app = FastAPI(
    lifespan=lifespan,
    title="tenbagger API",
    description="Korean Stock Long-term Investment Analysis System",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(company_router)
app.include_router(search_router)
app.include_router(screener_router)
app.include_router(backtest_router)
app.include_router(qualitative_router)
app.include_router(kakao_router, prefix="/api/kakao", tags=["kakao"])
app.include_router(dashboard_router)
app.include_router(auth_router)
app.include_router(payment_router)
app.include_router(watchlist_router)


@app.get("/api/health")
def health():
    db_ok = check_db()
    from app.core.config import settings
    openai_preview = (settings.openai_api_key[:10] + "...") if settings.openai_api_key else "EMPTY"
    env_openai = os.environ.get("OPENAI_API_KEY", "")
    env_preview = (env_openai[:10] + "...") if env_openai else "NOT_IN_ENV"
    return {
        "status": "ok",
        "db": "connected" if db_ok else "disconnected",
        "version": "2.0.0",
        "openai_key_set": bool(settings.openai_api_key),
        "openai_key_preview": openai_preview,
        "env_openai": env_preview,
    }


_frontend_dir = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
if os.path.exists(_frontend_dir):
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
