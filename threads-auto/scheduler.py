"""최적 시간대 발행 스케줄러.

한국 사용자 활동이 높은 시간대(아침 출근 / 점심 / 저녁 / 밤)에 큐의 다음
pending 아이템을 하나씩 발행한다. APScheduler의 BlockingScheduler + CronTrigger.

발행 시각은 POST_TIMES 환경변수로 재정의할 수 있다(예: "07:30,12:30,18:30,21:00").
"""
from __future__ import annotations

import os
import logging
from typing import Callable, List, Tuple

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger("threads-auto.scheduler")

# 한국 시간(KST) 기준 기본 발행 시각
DEFAULT_POST_TIMES = "07:30,12:30,18:30,21:00"
TIMEZONE = "Asia/Seoul"


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


def build_scheduler(job: Callable[[], None]) -> BlockingScheduler:
    """POST_TIMES마다 job()을 호출하는 BlockingScheduler를 만든다."""
    scheduler = BlockingScheduler(timezone=TIMEZONE)
    times = parse_times(os.getenv("POST_TIMES", DEFAULT_POST_TIMES))
    if not times:
        raise RuntimeError("유효한 발행 시각이 없습니다 (POST_TIMES 확인).")

    for hh, mm in times:
        scheduler.add_job(
            job,
            CronTrigger(hour=hh, minute=mm, timezone=TIMEZONE),
            id=f"post_{hh:02d}{mm:02d}",
            misfire_grace_time=600,  # 10분 지연까지는 발행 허용
            coalesce=True,
        )
        logger.info("발행 스케줄 등록: %02d:%02d KST", hh, mm)

    return scheduler
