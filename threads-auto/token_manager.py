"""Threads 액세스 토큰 자동 갱신.

토큰 수명 흐름:
  단기 토큰(1시간) --exchange--> 장기 토큰(60일) --refresh--> 장기 토큰(60일)

장기 토큰은 최소 24시간 이상 사용된(그리고 유효한) 상태에서만 refresh 가능하다.
만료 임박(기본 5일 전)이면 자동으로 refresh 하고 저장소에 기록한다.

저장소는 두 가지다.
  - FileTokenStore     : 로컬 파일(.token.json). 개발용 기본값.
  - PostgresTokenStore : tenbagger 백엔드와 같은 DB의 `threads_token` 테이블.
    Railway처럼 컨테이너 파일시스템이 재배포마다 초기화되는 환경에서는 이쪽이라야
    갱신된 토큰이 살아남아 60일마다 사람이 재발급하는 일이 없어진다.

`build_token_store()`가 DATABASE_URL 유무를 보고 알맞은 저장소를 만든다.
"""
from __future__ import annotations

import os
import json
import time
import logging
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger("threads-auto.token")

GRAPH_HOST = "https://graph.threads.net"

# 장기 토큰 기본 수명(60일). Graph가 expires_in을 안 주면 이 값을 쓴다.
DEFAULT_TTL_SECONDS = 5184000


# ---------------------------------------------------------------------- #
# 저장소
# ---------------------------------------------------------------------- #
class TokenStore:
    """토큰 저장소 공통 인터페이스."""

    def load(self) -> Optional[dict]:
        """{"access_token": str, "expires_at": int} 또는 None."""
        raise NotImplementedError

    def save(self, access_token: str, expires_at: int) -> None:
        raise NotImplementedError


class FileTokenStore(TokenStore):
    """로컬 JSON 파일 저장소."""

    def __init__(self, path: str = ".token.json"):
        self.path = Path(path)

    def load(self) -> Optional[dict]:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            logger.warning("토큰 파일 읽기 실패(%s), 무시함", exc)
            return None

    def save(self, access_token: str, expires_at: int) -> None:
        self.path.write_text(
            json.dumps({"access_token": access_token, "expires_at": expires_at}),
            encoding="utf-8",
        )


class PostgresTokenStore(TokenStore):
    """tenbagger 백엔드와 같은 DB의 `threads_token` 테이블(한 행).

    테이블은 백엔드 기동 시 `scripts/init.sql`이 생성한다.
    """

    TABLE = "threads_token"

    def __init__(self, dsn: str):
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "PostgresTokenStore를 쓰려면 `pip install psycopg2-binary`가 필요합니다."
            ) from exc
        self._psycopg2 = psycopg2
        self._cursor_factory = RealDictCursor
        self.dsn = dsn

    def load(self) -> Optional[dict]:
        conn = self._psycopg2.connect(self.dsn)
        try:
            with conn.cursor(cursor_factory=self._cursor_factory) as cur:
                cur.execute(
                    f"SELECT access_token, expires_at FROM {self.TABLE} WHERE id = 1"
                )
                row = cur.fetchone()
            return {"access_token": row["access_token"], "expires_at": int(row["expires_at"])} if row else None
        except Exception as exc:  # noqa: BLE001 — 저장소 장애가 발행을 막지 않도록
            logger.warning("토큰 조회 실패(%s), 저장된 토큰 없음으로 처리", exc)
            return None
        finally:
            conn.close()

    def save(self, access_token: str, expires_at: int) -> None:
        conn = self._psycopg2.connect(self.dsn)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {self.TABLE} (id, access_token, expires_at, updated_at)"
                    " VALUES (1, %s, %s, NOW())"
                    " ON CONFLICT (id) DO UPDATE SET"
                    " access_token = EXCLUDED.access_token,"
                    " expires_at = EXCLUDED.expires_at,"
                    " updated_at = NOW()",
                    (access_token, expires_at),
                )
            conn.commit()
        finally:
            conn.close()


def build_token_store() -> TokenStore:
    """DATABASE_URL이 있으면 Postgres, 없으면 파일 저장소를 만든다.

    Railway 백엔드에는 DATABASE_URL이 항상 있으므로 자동으로 DB에 저장된다.
    """
    dsn = os.getenv("DATABASE_URL", "")
    if dsn:
        logger.info("토큰 저장소: PostgreSQL")
        return PostgresTokenStore(dsn)
    logger.info("토큰 저장소: 파일")
    return FileTokenStore(os.getenv("TOKEN_FILE", ".token.json"))


# ---------------------------------------------------------------------- #
# 토큰 관리
# ---------------------------------------------------------------------- #
class TokenManager:
    def __init__(
        self,
        app_secret: str,
        store: Optional[TokenStore] = None,
        refresh_margin_days: int = 5,
    ):
        """
        Args:
            app_secret: META_APP_SECRET. 단기→장기 교환에 필요.
            store: 토큰 저장소. 없으면 build_token_store()로 만든다.
            refresh_margin_days: 만료 며칠 전부터 refresh를 시도할지.
        """
        self.app_secret = app_secret
        self.store = store or build_token_store()
        self.refresh_margin = refresh_margin_days * 86400

    def _save(self, access_token: str, expires_in: int) -> None:
        expires_at = int(time.time()) + int(expires_in)
        self.store.save(access_token, expires_at)
        logger.info(
            "토큰 저장 완료 (만료: %s)",
            time.strftime("%Y-%m-%d", time.localtime(expires_at)),
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
        self._save(data["access_token"], data.get("expires_in", DEFAULT_TTL_SECONDS))
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
        self._save(data["access_token"], data.get("expires_in", DEFAULT_TTL_SECONDS))
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
        2) 저장된 토큰이 없으면: seed_token을 장기 토큰으로 만들어 저장한다.
        """
        stored = self.store.load()
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
        return self._adopt(seed_token)

    def _adopt(self, seed_token: str) -> str:
        """seed 토큰을 장기 토큰으로 만들어 저장한다.

        seed가 단기면 교환이 되고, 이미 장기면 교환이 거부된다("Session key invalid").
        그 경우 refresh를 시도하면 만료가 60일 연장된 새 토큰과 만료시각을 받아
        저장할 수 있다. 저장만 되면 이후로는 스스로 갱신하므로 사람이 다시
        발급할 일이 없어진다.

        둘 다 실패하면(예: 발급 24시간이 안 된 장기 토큰은 refresh가 거부된다)
        이번 실행에만 seed를 쓰고 저장하지 않는다. 다음 실행에서 다시 시도한다.
        """
        try:
            return self.exchange_long_lived(seed_token)
        except RuntimeError as exchange_exc:
            logger.info(
                "단기→장기 교환 거부(%s) — 이미 장기 토큰으로 보고 refresh를 시도한다",
                exchange_exc,
            )
        try:
            return self.refresh(seed_token)
        except RuntimeError as refresh_exc:
            logger.warning(
                "refresh도 실패(%s) — seed 토큰을 이번 실행에만 사용(저장 안 함). "
                "장기 토큰은 발급 24시간이 지나야 갱신되므로 다음 실행에서 다시 시도한다",
                refresh_exc,
            )
            return seed_token
