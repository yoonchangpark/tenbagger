"""Threads 자동 발행 스케줄러 진입점.

흐름:
  .env 로드 → 유효 토큰 확보(TokenManager) → 콘텐츠 큐 준비 →
  스케줄러 등록 → 지정 시각마다 큐의 다음 아이템을 Threads에 발행.

사용법:
  python main.py            # 스케줄러 상주 실행
  python main.py --once     # 지금 즉시 한 건만 발행(테스트/수동)
  python main.py --add "본문 텍스트"   # 큐에 아이템 추가
"""
from __future__ import annotations

import os
import sys
import logging

from token_manager import TokenManager
from threads_api import ThreadsClient, ThreadsAPIError
from content_queue import build_queue, ContentQueue
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


def build_client() -> ThreadsClient:
    """토큰을 확보하고 ThreadsClient를 만든다."""
    app_secret = os.getenv("META_APP_SECRET", "")
    seed_token = os.getenv("ACCESS_TOKEN", "")
    if not app_secret and not seed_token:
        raise RuntimeError("META_APP_SECRET 또는 ACCESS_TOKEN 중 하나는 필요합니다.")

    token_mgr = TokenManager(
        app_secret=app_secret,
        token_file=os.getenv("TOKEN_FILE", ".token.json"),
    )
    token = token_mgr.get_valid_token(seed_token or None)
    return ThreadsClient(
        access_token=token,
        user_id=os.getenv("THREADS_USER_ID", "me"),
    )


def publish_next(client: ThreadsClient, queue: ContentQueue) -> None:
    """큐에서 다음 pending 아이템 하나를 발행한다."""
    item = queue.next_pending()
    if not item:
        logger.info("발행할 콘텐츠 없음 (큐 비어있음)")
        return

    item_id = item.get("id")
    try:
        media_id = client.publish(
            text=item["text"],
            image_url=item.get("image_url") or None,
            video_url=item.get("video_url") or None,
        )
        queue.mark_published(item_id, media_id)
        logger.info("발행 성공: item=%s media=%s", item_id, media_id)
    except (ThreadsAPIError, KeyError) as exc:
        queue.mark_failed(item_id, str(exc))
        logger.error("발행 실패: item=%s error=%s", item_id, exc)


def main() -> int:
    _load_env()
    args = sys.argv[1:]

    queue = build_queue()

    if args and args[0] == "--add":
        text = " ".join(args[1:]).strip()
        if not text:
            print("사용법: python main.py --add \"발행할 본문\"")
            return 1
        queue.add(text)
        print("큐에 추가했습니다.")
        return 0

    client = build_client()

    if args and args[0] == "--check":
        # 발행 없이 토큰·계정 연결만 확인하는 읽기전용 점검
        try:
            uid = client.resolve_user_id()
        except Exception as exc:  # noqa: BLE001
            print(f"연결 실패: {exc}")
            return 1
        print(f"연결 성공 — Threads user_id={uid}, 대기 콘텐츠 {queue.pending_count()}건")
        return 0

    if args and args[0] == "--once":
        publish_next(client, queue)
        return 0

    logger.info("스케줄러 시작 — 대기 중인 콘텐츠 %d건", queue.pending_count())
    scheduler = build_scheduler(lambda: publish_next(client, queue))
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("스케줄러 종료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
