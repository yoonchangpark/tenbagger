"""Threads 액세스 토큰 자동 갱신.

토큰 수명 흐름:
  단기 토큰(1시간) --exchange--> 장기 토큰(60일) --refresh--> 장기 토큰(60일)

장기 토큰은 최소 24시간 이상 사용된(그리고 유효한) 상태에서만 refresh 가능하다.
만료 임박(기본 5일 전)이면 자동으로 refresh 하고 파일에 저장한다.
"""
from __future__ import annotations

import json
import time
import logging
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger("threads-auto.token")

GRAPH_HOST = "https://graph.threads.net"


class TokenManager:
    def __init__(
        self,
        app_secret: str,
        token_file: str = ".token.json",
        refresh_margin_days: int = 5,
    ):
        """
        Args:
            app_secret: META_APP_SECRET. 단기→장기 교환에 필요.
            token_file: 토큰과 만료시각을 저장할 경로.
            refresh_margin_days: 만료 며칠 전부터 refresh를 시도할지.
        """
        self.app_secret = app_secret
        self.token_file = Path(token_file)
        self.refresh_margin = refresh_margin_days * 86400

    # ------------------------------------------------------------------ #
    # 영속화
    # ------------------------------------------------------------------ #
    def _load(self) -> Optional[dict]:
        if not self.token_file.exists():
            return None
        try:
            return json.loads(self.token_file.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            logger.warning("토큰 파일 읽기 실패(%s), 무시함", exc)
            return None

    def _save(self, access_token: str, expires_in: int) -> None:
        payload = {
            "access_token": access_token,
            "expires_at": int(time.time()) + int(expires_in),
        }
        self.token_file.write_text(json.dumps(payload), encoding="utf-8")
        logger.info(
            "토큰 저장 완료 (만료: %s)",
            time.strftime("%Y-%m-%d", time.localtime(payload["expires_at"])),
        )

    # ------------------------------------------------------------------ #
    # Graph 호출
    # ------------------------------------------------------------------ #
    def exchange_long_lived(self, short_token: str) -> str:
        """단기 토큰을 60일 장기 토큰으로 교환하고 저장 후 반환한다."""
        resp = requests.get(
            f"{GRAPH_HOST}/access_token",
            params={
                "grant_type": "th_exchange_token",
                "client_secret": self.app_secret,
                "access_token": short_token,
            },
            timeout=30,
        )
        data = self._handle(resp)
        self._save(data["access_token"], data.get("expires_in", 5184000))
        return data["access_token"]

    def refresh(self, long_token: str) -> str:
        """장기 토큰을 갱신(만료 60일 연장)하고 저장 후 반환한다."""
        resp = requests.get(
            f"{GRAPH_HOST}/refresh_access_token",
            params={
                "grant_type": "th_refresh_token",
                "access_token": long_token,
            },
            timeout=30,
        )
        data = self._handle(resp)
        self._save(data["access_token"], data.get("expires_in", 5184000))
        return data["access_token"]

    @staticmethod
    def _handle(resp: requests.Response) -> dict:
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if not resp.ok or "error" in data:
            err = data.get("error", {"message": resp.text})
            raise RuntimeError(f"토큰 API {resp.status_code}: {err.get('message', err)}")
        return data

    # ------------------------------------------------------------------ #
    # 주 진입점
    # ------------------------------------------------------------------ #
    def get_valid_token(self, seed_token: Optional[str] = None) -> str:
        """항상 유효한 장기 토큰을 반환한다.

        1) 저장된 토큰이 있으면: 만료 임박 시 refresh, 아니면 그대로 사용.
        2) 저장된 토큰이 없으면: seed_token(단기 또는 장기)을 교환/저장.
        """
        stored = self._load()
        if stored:
            remaining = stored["expires_at"] - int(time.time())
            if remaining <= 0:
                logger.warning("저장된 토큰이 만료됨 — seed_token으로 재발급 필요")
            elif remaining <= self.refresh_margin:
                logger.info("만료 %d일 전 — 토큰 refresh 시도", remaining // 86400)
                try:
                    return self.refresh(stored["access_token"])
                except RuntimeError as exc:
                    logger.warning("refresh 실패(%s), 기존 토큰 유지", exc)
                    return stored["access_token"]
            else:
                return stored["access_token"]

        if not seed_token:
            raise RuntimeError(
                "저장된 유효 토큰이 없고 seed_token(ACCESS_TOKEN)도 없습니다."
            )
        # seed가 이미 장기 토큰일 수도 있으나, 교환은 멱등에 가깝고 만료를 리셋하므로
        # 우선 교환을 시도하고 실패하면 seed를 그대로 저장한다.
        try:
            return self.exchange_long_lived(seed_token)
        except RuntimeError as exc:
            logger.warning("장기 토큰 교환 실패(%s), seed 토큰을 그대로 사용", exc)
            self._save(seed_token, 5184000)
            return seed_token
