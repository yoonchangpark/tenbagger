"""Threads 자동 발행 스케줄러 진입점.

흐름:
  .env 로드 → 콘텐츠 큐 준비 → 스케줄러 등록 →
  지정 요일·시각마다 유효 토큰 확보(TokenManager) 후 큐의 다음 아이템을 발행.

사용법:
  python main.py            # 스케줄러 상주 실행
  python main.py --once     # 지금 즉시 한 건만 발행(테스트/수동)
  python main.py --add "본문 텍스트"   # 큐에 텍스트 아이템 추가
  python main.py --add "본문 텍스트" --images url1,url2,url3   # 캐러셀 아이템 추가
                                                                  # (2장 이상, 공개 접근 가능한 URL만 가능)
"""
from __future__ import annotations

import sys
import logging

from content_queue import build_queue
from publisher import build_client, publish_next
from scheduler import build_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("threads-auto.main")


def _load_env() -> None:
    """python-dotenv가 있으면 .env를 로드한다(없어도 무방)."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        logger.debug("python-dotenv 미설치 — OS 환경변수만 사용")


def main() -> int:
    _load_env()
    args = sys.argv[1:]

    queue = build_queue()

    if args and args[0] == "--add":
        rest = args[1:]
        image_urls: list[str] = []
        if "--images" in rest:
            idx = rest.index("--images")
            image_urls = [u.strip() for u in rest[idx + 1].split(",") if u.strip()]
            rest = rest[:idx]
        text = " ".join(rest).strip()
        if not text:
            print("사용법: python main.py --add \"발행할 본문\" [--images url1,url2,...]")
            return 1
        queue.add(text, image_urls=image_urls or None)
        print(f"큐에 추가했습니다{'(캐러셀 ' + str(len(image_urls)) + '장)' if image_urls else ''}.")
        return 0

    if args and args[0] == "--check":
        # 발행 없이 토큰·계정 연결만 확인하는 읽기전용 점검
        try:
            uid = build_client().resolve_user_id()
        except Exception as exc:  # noqa: BLE001
            print(f"연결 실패: {exc}")
            return 1
        print(f"연결 성공 — Threads user_id={uid}, 대기 콘텐츠 {queue.pending_count()}건")
        return 0

    if args and args[0] == "--once":
        publish_next(build_client(), queue)
        return 0

    logger.info("스케줄러 시작 — 대기 중인 콘텐츠 %d건", queue.pending_count())
    # 발행 시점마다 build_client()를 다시 호출한다. 그래야 장기 토큰이
    # 만료에 가까워졌을 때 TokenManager가 refresh 할 기회를 얻는다.
    scheduler = build_scheduler(lambda: publish_next(build_client(), queue))
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("스케줄러 종료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
