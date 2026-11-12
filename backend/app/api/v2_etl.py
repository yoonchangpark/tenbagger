"""
ETL 트리거 + 상태 조회 API
POST /api/v2/etl/run    — 백그라운드 ETL 즉시 실행 (중복 실행 방지)
GET  /api/v2/etl/status — 실행 상태 + 다음 스케줄 시각

일일 자동 실행은 main.py의 APScheduler(02:00 KST)가 run_scheduled_etl()을 호출.
"""
import asyncio
import datetime
import os
import sys
from fastapi import APIRouter

router = APIRouter(prefix="/api/v2/etl", tags=["ETL v2"])

KST = datetime.timezone(datetime.timedelta(hours=9))

_state = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "last_trigger": None,   # manual | schedule
    "last_error": None,
}


def _now_kst_iso() -> str:
    return datetime.datetime.now(KST).isoformat(timespec="seconds")


async def _run_etl_guarded(markets: list[str], skip_existing: bool, trigger: str) -> bool:
    """중복 실행 방지 가드 후 별도 프로세스에서 ETL 실행.

    웹서버 프로세스 안(스레드)에서 돌리면 전종목 수집 때 커진 메모리가
    끝난 뒤에도 반환되지 않아 Railway 메모리 과금이 하루 종일 쌓인다.
    별도 프로세스로 돌리면 종료와 함께 메모리가 전부 반환된다.
    """
    if _state["running"]:
        print(f"[ETL] 이미 실행 중 — {trigger} 트리거 무시")
        return False
    _state.update(running=True, started_at=_now_kst_iso(), finished_at=None,
                  last_trigger=trigger, last_error=None)
    market_arg = "ALL" if set(markets) == {"KOSPI", "KOSDAQ"} else markets[0]
    cmd = [sys.executable, "-m", "app.workers.etl", "--market", market_arg]
    if skip_existing:
        cmd.append("--skip-existing")
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=backend_dir)
        returncode = await proc.wait()
        if returncode != 0:
            raise RuntimeError(f"ETL 프로세스 종료 코드 {returncode}")
    except Exception as e:
        _state["last_error"] = str(e)
        print(f"[ETL] 실행 실패 ({trigger}): {e}")
    finally:
        _state.update(running=False, finished_at=_now_kst_iso())
    return True


async def run_scheduled_etl():
    """main.py APScheduler 02:00 KST 잡에서 호출 — 상태 기록 포함"""
    await _run_etl_guarded(["KOSPI", "KOSDAQ"], skip_existing=True, trigger="schedule")


@router.post("/run")
async def trigger_etl(market: str = "ALL", skip_existing: bool = True):
    """ETL 백그라운드 실행. 이미 실행 중이면 거부."""
    if _state["running"]:
        return {"accepted": False, "reason": "이미 실행 중", "status": _state}
    markets = ["KOSPI", "KOSDAQ"] if market.upper() == "ALL" else [market.upper()]
    asyncio.create_task(_run_etl_guarded(markets, skip_existing, trigger="manual"))
    return {"accepted": True, "markets": markets, "skip_existing": skip_existing}


@router.get("/status")
def etl_status():
    """ETL 실행 상태 + 다음 스케줄(02:00 KST) 시각"""
    now = datetime.datetime.now(KST)
    nxt = now.replace(hour=2, minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt += datetime.timedelta(days=1)
    return {**_state, "next_scheduled_run": nxt.isoformat(timespec="seconds")}
