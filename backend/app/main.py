import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.api.company import router as company_router
from app.api.search import router as search_router
from app.api.screener import router as screener_router, router_v2 as screener_v2_router
from app.api.backtest import router as backtest_router
from app.api.qualitative import router as qualitative_router
from app.api.kakao import router as kakao_router
from app.api.v2_dashboard import router as dashboard_router
from app.api.auth import router as auth_router
from app.api.payment import router as payment_router
from app.api.watchlist import router as watchlist_router
from app.api.admin import router as admin_router
from app.api.v2_portfolio import router as portfolio_router
from app.api.v2_committee import router as committee_router
from app.api.v2_news import router as news_router
from app.api.accuracy import router as accuracy_router
from app.api.v2_risk import router as risk_router
from app.api.v2_rebalance import router as rebalance_router
from app.api.v2_flow import router as flow_router
from app.api.v2_surprise import router as surprise_router
from app.api.v2_holdings import router as holdings_router
from app.api.v2_phase_d import router as phase_d_router
from app.api.v2_swap import router as swap_router
from app.api.v2_macro import router as macro_router
from app.api.v2_etl import router as etl_router, run_scheduled_etl
from app.api.v2_track_record import router as track_record_router
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


def _run_weekly_report_job():
    """매주 월요일 08:00(KST) 주간 관심종목 리포트 이메일 발송"""
    import subprocess, sys
    print("📧 [SCHEDULER] 주간 리포트 발송 시작...")
    try:
        result = subprocess.run(
            [sys.executable, "-c",
             "from app.workers.weekly_report import run_weekly_report; run_weekly_report()"],
            capture_output=True, text=True, timeout=600,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        print(f"✅ [SCHEDULER] 주간 리포트 완료\n{result.stdout[-300:] if result.stdout else ''}")
    except Exception as e:
        print(f"❌ [SCHEDULER] 주간 리포트 오류: {e}")


def _run_price_alert_job():
    """매일 16:00(KST) 목표가 알림 체크 (장 마감 후)"""
    import subprocess, sys
    print("🔔 [SCHEDULER] 목표가 알림 체크 시작...")
    try:
        result = subprocess.run(
            [sys.executable, "-c",
             "from app.workers.price_alert import run_price_alerts; run_price_alerts()"],
            capture_output=True, text=True, timeout=300,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        print(f"✅ [SCHEDULER] 목표가 알림 완료\n{result.stdout[-300:] if result.stdout else ''}")
        if result.returncode != 0:
            print(f"⚠️ [SCHEDULER] stderr: {result.stderr[-200:]}")
    except Exception as e:
        print(f"❌ [SCHEDULER] 목표가 알림 오류: {e}")


def _run_quarterly_alert_job():
    """매일 07:30(KST) 분기보고서 공시 감지 → 내 종목 보유 유저 카카오 알림"""
    import subprocess, sys
    print("📋 [SCHEDULER] 분기보고서 알림 체크 시작...")
    try:
        result = subprocess.run(
            [sys.executable, "-c",
             "from app.workers.quarterly_alert import run_quarterly_alerts; run_quarterly_alerts()"],
            capture_output=True, text=True, timeout=300,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        print(f"✅ [SCHEDULER] 분기보고서 알림 완료\n{result.stdout[-300:] if result.stdout else ''}")
        if result.returncode != 0:
            print(f"⚠️ [SCHEDULER] stderr: {result.stderr[-200:]}")
    except Exception as e:
        print(f"❌ [SCHEDULER] 분기보고서 알림 오류: {e}")


def _run_accuracy_job():
    """매주 일요일 23:00(KST) 정확도 검증 자동 실행"""
    import subprocess, sys
    print("📊 [SCHEDULER] 정확도 검증 자동 시작...")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "app.agents.accuracy_validator"],
            capture_output=True, text=True, timeout=1800,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        print(f"✅ [SCHEDULER] 정확도 검증 완료\n{result.stdout[-500:] if result.stdout else ''}")
        if result.returncode != 0:
            print(f"⚠️ [SCHEDULER] 정확도 검증 stderr: {result.stderr[-300:]}")
    except Exception as e:
        print(f"❌ [SCHEDULER] 정확도 검증 오류: {e}")


def _run_weight_tuner_job():
    """매주 일요일 23:30(KST) 가중치 자동 조정 분석 (정확도 검증 직후)"""
    import subprocess, sys
    print("⚖️ [SCHEDULER] 가중치 자동 조정 분석 시작...")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "app.agents.weight_tuner"],
            capture_output=True, text=True, timeout=300,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        print(f"✅ [SCHEDULER] 가중치 분석 완료\n{result.stdout[-500:] if result.stdout else ''}")
        if result.returncode != 0:
            print(f"⚠️ [SCHEDULER] 가중치 분석 stderr: {result.stderr[-200:]}")
    except Exception as e:
        print(f"❌ [SCHEDULER] 가중치 분석 오류: {e}")


async def _run_news_job():
    """
    매일 새벽 4시(KST) 뉴스 감성 분석 실행 (Lazy 모드)
    구독자 0명 단계 비용 최소화:
    - 업종 분석만 자동 실행 (10개, ~$0.05/일)
    - 종목 분석은 사용자 조회 시 lazy 트리거 (v2_news.py에서 처리)
    - 구독자 100명+ 모이면 추가 등급 분석 켤 것
    """
    print("📰 [SCHEDULER] 뉴스 감성 분석 자동 시작 (Lazy 모드: 업종만)...")
    try:
        from app.workers.news_worker import run_news_analysis
        # 업종 분석만 (10개 업종)
        await run_news_analysis(grade_filter=None, limit=None, sector_only=True)
        print("✅ [SCHEDULER] 업종 감성 분석 완료 (종목은 사용자 조회 시 lazy 분석)")
    except Exception as e:
        print(f"❌ [SCHEDULER] 뉴스 분석 오류: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    # ── APScheduler: 새벽 자동 실행 ──────────────────────────────
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger

        scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

        # 매일 새벽 2:00 KST — ETL (전종목 재무데이터 수집)
        # v2_etl의 가드 러너 사용: 중복 실행 방지 + /api/v2/etl/status로 실행 기록 확인 가능
        scheduler.add_job(
            run_scheduled_etl,
            CronTrigger(hour=2, minute=0, timezone="Asia/Seoul"),
            id="etl_daily",
            name="Daily ETL",
            replace_existing=True,
        )

        # 매일 새벽 4:00 KST — 뉴스 감성 분석 (ETL 완료 후)
        scheduler.add_job(
            _run_news_job,
            CronTrigger(hour=4, minute=0, timezone="Asia/Seoul"),
            id="news_daily",
            name="Daily News Sentiment",
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

        # 매일 07:30 KST — 분기보고서 공시 감지 → 내 종목 카카오 알림
        scheduler.add_job(
            lambda: asyncio.get_event_loop().run_in_executor(None, _run_quarterly_alert_job),
            CronTrigger(hour=7, minute=30, timezone="Asia/Seoul"),
            id="quarterly_alert_daily",
            name="Daily Quarterly Report Alert",
            replace_existing=True,
        )

        # 매주 월요일 08:00 KST — 주간 관심종목 리포트
        scheduler.add_job(
            lambda: asyncio.get_event_loop().run_in_executor(None, _run_weekly_report_job),
            CronTrigger(day_of_week="mon", hour=8, minute=0, timezone="Asia/Seoul"),
            id="weekly_report",
            name="Weekly Watchlist Report",
            replace_existing=True,
        )

        # 매일 16:00 KST — 목표가 알림 (장 마감 후)
        scheduler.add_job(
            lambda: asyncio.get_event_loop().run_in_executor(None, _run_price_alert_job),
            CronTrigger(hour=16, minute=0, timezone="Asia/Seoul"),
            id="price_alert_daily",
            name="Daily Price Alert",
            replace_existing=True,
        )

        # 매주 일요일 23:00 KST — 정확도 검증
        scheduler.add_job(
            lambda: asyncio.get_event_loop().run_in_executor(None, _run_accuracy_job),
            CronTrigger(day_of_week="sun", hour=23, minute=0, timezone="Asia/Seoul"),
            id="accuracy_weekly",
            name="Weekly Accuracy Validation",
            replace_existing=True,
        )

        # 매주 일요일 23:30 KST — 가중치 자동 조정 분석 (정확도 검증 30분 후)
        scheduler.add_job(
            lambda: asyncio.get_event_loop().run_in_executor(None, _run_weight_tuner_job),
            CronTrigger(day_of_week="sun", hour=23, minute=30, timezone="Asia/Seoul"),
            id="weight_tuner_weekly",
            name="Weekly Weight Tuner Analysis",
            replace_existing=True,
        )

        scheduler.start()
        print("✅ [SCHEDULER] ETL 02:00 | 뉴스 04:00 | 어드바이저 07:00 | 분기보고서알림 07:30 | 주간리포트 월 08:00 | 목표가알림 16:00 | 정확도 일 23:00 | 가중치분석 일 23:30 KST")
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
app.include_router(screener_v2_router)
app.include_router(backtest_router)
app.include_router(qualitative_router)
app.include_router(kakao_router, prefix="/api/kakao", tags=["kakao"])
app.include_router(dashboard_router)
app.include_router(auth_router)
app.include_router(payment_router)
app.include_router(watchlist_router)
app.include_router(admin_router)
app.include_router(portfolio_router)
app.include_router(committee_router)
app.include_router(news_router)
app.include_router(accuracy_router)
app.include_router(risk_router)
app.include_router(rebalance_router)
app.include_router(flow_router)
app.include_router(surprise_router)
app.include_router(holdings_router)
app.include_router(phase_d_router)
app.include_router(swap_router)
app.include_router(macro_router)
app.include_router(etl_router)
app.include_router(track_record_router)


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
