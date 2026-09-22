"""큐의 다음 아이템을 Threads에 발행하는 공통 로직.

main.py(상주 스케줄러/CLI)와 tenbagger 백엔드 스케줄러가 함께 쓴다.
"""
from __future__ import annotations

import os
import logging

import requests

from token_manager import TokenManager
from threads_api import ThreadsClient, ThreadsAPIError
from content_queue import ContentQueue

logger = logging.getLogger("threads-auto.publisher")


def build_client() -> ThreadsClient:
    """유효한 토큰을 확보해 ThreadsClient를 만든다.

    호출할 때마다 TokenManager를 거치므로, 저장된 토큰이 만료에 가까우면
    이 시점에 자동으로 refresh 된다. 상주 프로세스는 발행 직전에 매번
    불러야 60일 뒤 토큰 만료로 조용히 멈추는 일이 없다.
    """
    app_secret = os.getenv("META_APP_SECRET", "")
    seed_token = os.getenv("ACCESS_TOKEN", "")
    if not app_secret and not seed_token:
        raise RuntimeError("META_APP_SECRET 또는 ACCESS_TOKEN 중 하나는 필요합니다.")

    # 저장소는 DATABASE_URL 유무에 따라 자동으로 정해진다(DB 또는 파일).
    token = TokenManager(app_secret=app_secret).get_valid_token(seed_token or None)
    return ThreadsClient(
        access_token=token,
        user_id=os.getenv("THREADS_USER_ID", "me"),
    )


def images_ready(image_urls: list) -> bool:
    """캐러셀 이미지가 실제로 공개돼 있는지 확인한다.

    Meta는 발행 시점에 image_url을 직접 가져간다. 카드 PNG는 저장소에
    커밋한 뒤 배포돼야 주소가 살아나므로, 아직 배포 전이면 발행이 실패하고
    콘텐츠가 버려진다. 미리 확인해서 그런 건은 큐에 남겨둔다.
    """
    for url in image_urls:
        try:
            res = requests.head(url, timeout=10, allow_redirects=True)
        except requests.RequestException as exc:
            logger.warning("이미지 확인 실패: %s (%s)", url, exc)
            return False
        if res.status_code != 200:
            logger.warning("이미지 아직 배포 전: %s (%d)", url, res.status_code)
            return False
    return True


def publish_next(client: ThreadsClient, queue: ContentQueue) -> None:
    """큐에서 다음 pending 아이템 하나를 발행한다."""
    item = queue.next_pending()
    if not item:
        logger.warning("발행할 콘텐츠 없음 — 큐가 비었습니다. 생성기로 채워주세요.")
        return

    item_id = item.get("id")
    image_urls = item.get("image_urls") or None
    if image_urls and not images_ready(image_urls):
        # 실패 처리하지 않는다. 배포가 끝나면 다음 발행일에 그대로 나간다.
        logger.warning("item=%s 이미지가 아직 공개되지 않아 이번 회차는 건너뜁니다.", item_id)
        return

    try:
        if image_urls:
            media_id = client.publish_carousel(text=item["text"], image_urls=image_urls)
        else:
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

    logger.info("남은 대기 콘텐츠 %d건", queue.pending_count())
