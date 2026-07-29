"""Meta Threads API 래퍼.

발행은 항상 2단계다.
  1) 미디어 컨테이너 생성 (POST /{user_id}/threads)
  2) 컨테이너 발행       (POST /{user_id}/threads_publish)

Meta는 컨테이너 생성 직후 서버 처리 시간을 두고 발행할 것을 권고한다(특히 이미지/영상).
"""
from __future__ import annotations

import time
import logging
from typing import Optional

import requests

logger = logging.getLogger("threads-auto.api")

GRAPH_BASE = "https://graph.threads.net/v1.0"


class ThreadsAPIError(RuntimeError):
    """Threads API가 에러 응답을 반환했을 때."""


class ThreadsClient:
    def __init__(self, access_token: str, user_id: str = "me", timeout: int = 30):
        """
        Args:
            access_token: 장기(60일) 액세스 토큰.
            user_id: Threads 사용자 ID. 기본 "me"는 토큰 소유자를 가리킨다.
            timeout: HTTP 타임아웃(초).
        """
        self.access_token = access_token
        self.user_id = user_id
        self.timeout = timeout

    # ------------------------------------------------------------------ #
    # 내부 HTTP
    # ------------------------------------------------------------------ #
    def _post(self, path: str, params: dict) -> dict:
        params = {**params, "access_token": self.access_token}
        resp = requests.post(f"{GRAPH_BASE}/{path}", params=params, timeout=self.timeout)
        return self._handle(resp)

    def _get(self, path: str, params: dict) -> dict:
        params = {**params, "access_token": self.access_token}
        resp = requests.get(f"{GRAPH_BASE}/{path}", params=params, timeout=self.timeout)
        return self._handle(resp)

    @staticmethod
    def _handle(resp: requests.Response) -> dict:
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if not resp.ok or "error" in data:
            err = data.get("error", {"message": resp.text})
            raise ThreadsAPIError(
                f"Threads API {resp.status_code}: {err.get('message', err)}"
            )
        return data

    # ------------------------------------------------------------------ #
    # 공개 API
    # ------------------------------------------------------------------ #
    def resolve_user_id(self) -> str:
        """user_id가 'me'면 실제 숫자 ID로 치환하고 반환한다."""
        if self.user_id and self.user_id != "me":
            return self.user_id
        data = self._get("me", {"fields": "id,username"})
        self.user_id = data["id"]
        logger.info("Threads 계정 확인: @%s (%s)", data.get("username"), self.user_id)
        return self.user_id

    def create_container(
        self,
        text: str,
        image_url: Optional[str] = None,
        video_url: Optional[str] = None,
    ) -> str:
        """미디어 컨테이너를 생성하고 creation_id를 반환한다."""
        uid = self.resolve_user_id()
        params: dict = {"text": text}
        if video_url:
            params["media_type"] = "VIDEO"
            params["video_url"] = video_url
        elif image_url:
            params["media_type"] = "IMAGE"
            params["image_url"] = image_url
        else:
            params["media_type"] = "TEXT"

        data = self._post(f"{uid}/threads", params)
        creation_id = data["id"]
        logger.info("컨테이너 생성 완료: %s", creation_id)
        return creation_id

    def publish_container(self, creation_id: str) -> str:
        """컨테이너를 발행하고 media_id를 반환한다."""
        uid = self.resolve_user_id()
        data = self._post(f"{uid}/threads_publish", {"creation_id": creation_id})
        media_id = data["id"]
        logger.info("발행 완료: %s", media_id)
        return media_id

    def publish(
        self,
        text: str,
        image_url: Optional[str] = None,
        video_url: Optional[str] = None,
        processing_delay: int = 30,
    ) -> str:
        """컨테이너 생성 → 대기 → 발행을 한 번에 수행하고 media_id를 반환한다.

        텍스트만 있는 게시물은 대기가 거의 필요 없지만, 미디어는 서버 처리 시간이
        필요하므로 기본 30초를 둔다.
        """
        creation_id = self.create_container(text, image_url, video_url)
        delay = processing_delay if (image_url or video_url) else min(processing_delay, 5)
        if delay:
            logger.debug("발행 전 %d초 대기(미디어 처리)", delay)
            time.sleep(delay)
        return self.publish_container(creation_id)
