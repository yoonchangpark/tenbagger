"""최적 시간대 발행 스케줄러.

주 3~4회 리듬으로 큐의 다음 pending 아이템을 하나씩 발행한다.
APScheduler의 BlockingScheduler + CronTrigger.

발행 요일은 POST_DAYS, 시각은 POST_TIMES 환경변수로 재정의한다.
  POST_DAYS=mon,wed,fri   POST_TIMES=18:30   → 주 3회
  POST_DAYS=mon,wed,fri,sun                  → 주 4회
등록되는 잡 수는 (요일 수 × 시각 수)이므로, 주 3~4회를 유지하려면
POST_TIMES는 하나만 두는 것이 기본이다.
"""
from __future__ import annotations

import os
import logging
from typing import Callable, List, Tuple

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger("threads-auto.scheduler")

# 한국 시간(KST) 기준 기본 발행 요일·시각 — 월/수/금 저녁 주 3회
DEFAULT_POST_DAYS = "mon,wed,fri"
DEFAULT_POST_TIMES = "18:30"
TIMEZONE = "Asia/Seoul"

VALID_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def parse_times(spec: str) -> List[Tuple[int, int]]:
    """"07:30,12:30" → [(7, 30), (12, 30)]. 잘못된 항목은 건너뛴다."""
    times: List[Tuple[int, int]] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            hh, mm = chunk.split(":")
            times.append((int(hh), int(mm)))
        except ValueError:
            logger.warning("발행 시각 형식 오류 무시: %r", chunk)
    return times


def parse_days(spec: str) -> List[str]:
    """"mon,wed,fri" → ["mon", "wed", "fri"]. 알 수 없는 요일은 건너뛴다."""
    days: List[str] = []
    for chunk in spec.split(","):
        chunk = chunk.strip().lower()
        if not chunk:
            continue
        if chunk not in VALID_DAYS:
            logger.warning("발행 요일 형식 오류 무시: %r (mon~sun)", chunk)
            continue
        days.append(chunk)
    return days


def build_scheduler(job: Callable[[], None]) -> BlockingScheduler:
    """POST_DAYS의 각 요일, POST_TIMES의 각 시각마다 job()을 호출한다."""
    scheduler = BlockingScheduler(timezone=TIMEZONE)
    times = parse_times(os.getenv("POST_TIMES", DEFAULT_POST_TIMES))
    if not times:
        raise RuntimeError("유효한 발행 시각이 없습니다 (POST_TIMES 확인).")
    days = parse_days(os.getenv("POST_DAYS", DEFAULT_POST_DAYS))
    if not days:
        raise RuntimeError("유효한 발행 요일이 없습니다 (POST_DAYS 확인).")

    day_spec = ",".join(days)
    for hh, mm in times:
        scheduler.add_job(
            job,
            CronTrigger(day_of_week=day_spec, hour=hh, minute=mm, timezone=TIMEZONE),
            id=f"post_{hh:02d}{mm:02d}",
            misfire_grace_time=600,  # 10분 지연까지는 발행 허용
            coalesce=True,
        )
        logger.info("발행 스케줄 등록: %s %02d:%02d KST", day_spec, hh, mm)

    logger.info("주 %d회 발행 예정", len(days) * len(times))
    return scheduler
