"""발행할 콘텐츠 큐.

세 가지 백엔드를 제공한다.
  - JSONFileQueue : 파일 기반. 자격증명 없이 바로 동작(개발/테스트 기본값).
  - PostgresQueue : 운영용. tenbagger 백엔드와 같은 DB의 `threads_queue` 테이블.
  - SupabaseQueue : 운영용. Supabase 테이블 `threads_queue`를 사용.

`build_queue()`가 환경변수(CONTENT_QUEUE_BACKEND)에 따라 적절한 백엔드를 만든다.

콘텐츠 아이템 스키마
  {
    "id":         임의 식별자,
    "text":       본문(필수),
    "image_url":  선택 (단일 이미지),
    "image_urls": 선택 (캐러셀 — 2장 이상의 공개 이미지 URL 리스트, image_url보다 우선),
    "video_url":  선택,
    "status":     "pending" | "published" | "failed",
    "source_key": 선택 (출처 식별자. 같은 소스로 두 번 적재하는 것을 막는다),
  }
"""
from __future__ import annotations

import os
import json
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger("threads-auto.queue")


class ContentQueue:
    """큐 백엔드 공통 인터페이스."""

    def next_pending(self) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def pending_items(self, limit: int = 5) -> List[Dict[str, Any]]:
        """앞에서부터 pending 아이템 여러 건.

        발행 쪽에서 맨 앞 아이템을 지금 낼 수 없을 때(카드 이미지가 아직
        배포 전인 경우) 다음 것을 이어서 시도하려고 쓴다.
        """
        item = self.next_pending()
        return [item] if item else []

    def mark_published(self, item_id: Any, media_id: str) -> None:
        raise NotImplementedError

    def mark_failed(self, item_id: Any, error: str) -> None:
        raise NotImplementedError

    def add(
        self,
        text: str,
        image_url: str = "",
        video_url: str = "",
        image_urls: Optional[List[str]] = None,
        source_key: str = "",
    ) -> None:
        raise NotImplementedError

    def pending_count(self) -> int:
        raise NotImplementedError

    def has_source(self, source_key: str) -> bool:
        """이 출처로 이미 적재한 적이 있는지. 자동 보충의 중복 방지용."""
        raise NotImplementedError


class JSONFileQueue(ContentQueue):
    """로컬 JSON 파일 기반 큐. 자격증명 불필요."""

    def __init__(self, path: str = "queue.json"):
        self.path = Path(path)
        if not self.path.exists():
            self._write([])

    def _read(self) -> List[Dict[str, Any]]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return []

    def _write(self, items: List[Dict[str, Any]]) -> None:
        self.path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    def next_pending(self) -> Optional[Dict[str, Any]]:
        for item in self._read():
            if item.get("status", "pending") == "pending":
                return item
        return None

    def pending_items(self, limit: int = 5) -> List[Dict[str, Any]]:
        pending = [i for i in self._read() if i.get("status", "pending") == "pending"]
        return pending[:limit]

    def _update(self, item_id: Any, **fields: Any) -> None:
        items = self._read()
        for item in items:
            if item.get("id") == item_id:
                item.update(fields)
                break
        self._write(items)

    def mark_published(self, item_id: Any, media_id: str) -> None:
        self._update(item_id, status="published", media_id=media_id)

    def mark_failed(self, item_id: Any, error: str) -> None:
        self._update(item_id, status="failed", error=error)

    def add(
        self,
        text: str,
        image_url: str = "",
        video_url: str = "",
        image_urls: Optional[List[str]] = None,
        source_key: str = "",
    ) -> None:
        items = self._read()
        next_id = max((int(i.get("id", 0)) for i in items), default=0) + 1
        item: Dict[str, Any] = {
            "id": next_id,
            "text": text,
            "image_url": image_url,
            "video_url": video_url,
            "status": "pending",
        }
        if image_urls:
            item["image_urls"] = list(image_urls)
        if source_key:
            item["source_key"] = source_key
        items.append(item)
        self._write(items)
        logger.info("큐에 아이템 추가: #%d", next_id)

    def pending_count(self) -> int:
        return sum(1 for i in self._read() if i.get("status", "pending") == "pending")

    def has_source(self, source_key: str) -> bool:
        return any(i.get("source_key") == source_key for i in self._read())


class SupabaseQueue(ContentQueue):
    """Supabase 테이블 `threads_queue` 기반 큐(운영용).

    필요한 테이블(예시):
      create table threads_queue (
        id         bigint generated always as identity primary key,
        text       text not null,
        image_url  text,
        image_urls jsonb,
        video_url  text,
        status     text not null default 'pending',
        media_id   text,
        error      text,
        source_key text unique,
        created_at timestamptz default now()
      );
    """

    TABLE = "threads_queue"

    def __init__(self, url: str, key: str):
        try:
            from supabase import create_client
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "SupabaseQueue를 쓰려면 `pip install supabase`가 필요합니다."
            ) from exc
        self.client = create_client(url, key)

    def next_pending(self) -> Optional[Dict[str, Any]]:
        res = (
            self.client.table(self.TABLE)
            .select("*")
            .eq("status", "pending")
            .order("created_at")
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None

    def pending_items(self, limit: int = 5) -> List[Dict[str, Any]]:
        res = (
            self.client.table(self.TABLE)
            .select("*")
            .eq("status", "pending")
            .order("created_at")
            .limit(limit)
            .execute()
        )
        return list(res.data or [])

    def mark_published(self, item_id: Any, media_id: str) -> None:
        self.client.table(self.TABLE).update(
            {"status": "published", "media_id": media_id}
        ).eq("id", item_id).execute()

    def mark_failed(self, item_id: Any, error: str) -> None:
        self.client.table(self.TABLE).update(
            {"status": "failed", "error": error}
        ).eq("id", item_id).execute()

    def add(
        self,
        text: str,
        image_url: str = "",
        video_url: str = "",
        image_urls: Optional[List[str]] = None,
        source_key: str = "",
    ) -> None:
        row: Dict[str, Any] = {
            "text": text,
            "image_url": image_url,
            "video_url": video_url,
            "status": "pending",
        }
        if image_urls:
            row["image_urls"] = list(image_urls)
        if source_key:
            row["source_key"] = source_key
        self.client.table(self.TABLE).insert(row).execute()
        logger.info("Supabase 큐에 아이템 추가")

    def pending_count(self) -> int:
        res = (
            self.client.table(self.TABLE)
            .select("id", count="exact")
            .eq("status", "pending")
            .execute()
        )
        return res.count or 0

    def has_source(self, source_key: str) -> bool:
        res = (
            self.client.table(self.TABLE)
            .select("id")
            .eq("source_key", source_key)
            .limit(1)
            .execute()
        )
        return bool(res.data)


class PostgresQueue(ContentQueue):
    """PostgreSQL 테이블 `threads_queue` 기반 큐(운영용).

    tenbagger 백엔드와 같은 DB(DATABASE_URL)를 쓴다. Railway처럼 컨테이너
    파일시스템이 재배포마다 초기화되는 환경에서도 큐가 살아남는다.
    테이블은 백엔드 기동 시 `scripts/init.sql`이 생성한다.
    """

    TABLE = "threads_queue"

    def __init__(self, dsn: str):
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor, Json
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "PostgresQueue를 쓰려면 `pip install psycopg2-binary`가 필요합니다."
            ) from exc
        self._psycopg2 = psycopg2
        self._cursor_factory = RealDictCursor
        self._json = Json
        self.dsn = dsn

    def _run(self, sql: str, params: tuple = (), fetch: bool = False) -> Optional[Dict[str, Any]]:
        conn = self._psycopg2.connect(self.dsn)
        try:
            with conn.cursor(cursor_factory=self._cursor_factory) as cur:
                cur.execute(sql, params)
                row = cur.fetchone() if fetch else None
            conn.commit()
            return dict(row) if row else None
        finally:
            conn.close()

    def _run_all(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        conn = self._psycopg2.connect(self.dsn)
        try:
            with conn.cursor(cursor_factory=self._cursor_factory) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def next_pending(self) -> Optional[Dict[str, Any]]:
        return self._run(
            f'SELECT * FROM {self.TABLE} WHERE status = %s'
            " ORDER BY created_at, id LIMIT 1",
            ("pending",),
            fetch=True,
        )

    def pending_items(self, limit: int = 5) -> List[Dict[str, Any]]:
        return self._run_all(
            f'SELECT * FROM {self.TABLE} WHERE status = %s'
            " ORDER BY created_at, id LIMIT %s",
            ("pending", limit),
        )

    def mark_published(self, item_id: Any, media_id: str) -> None:
        self._run(
            f"UPDATE {self.TABLE} SET status = %s, media_id = %s,"
            " published_at = NOW() WHERE id = %s",
            ("published", media_id, item_id),
        )

    def mark_failed(self, item_id: Any, error: str) -> None:
        self._run(
            f"UPDATE {self.TABLE} SET status = %s, error = %s WHERE id = %s",
            ("failed", error, item_id),
        )

    def add(
        self,
        text: str,
        image_url: str = "",
        video_url: str = "",
        image_urls: Optional[List[str]] = None,
        source_key: str = "",
    ) -> None:
        self._run(
            f'INSERT INTO {self.TABLE}'
            ' ("text", image_url, video_url, image_urls, status, source_key)'
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (
                text,
                image_url,
                video_url,
                self._json(list(image_urls)) if image_urls else None,
                "pending",
                source_key or None,
            ),
        )
        logger.info("Postgres 큐에 아이템 추가")

    def pending_count(self) -> int:
        row = self._run(
            f"SELECT COUNT(*) AS n FROM {self.TABLE} WHERE status = %s",
            ("pending",),
            fetch=True,
        )
        return int(row["n"]) if row else 0

    def has_source(self, source_key: str) -> bool:
        row = self._run(
            f"SELECT 1 AS hit FROM {self.TABLE} WHERE source_key = %s LIMIT 1",
            (source_key,),
            fetch=True,
        )
        return row is not None


def build_queue() -> ContentQueue:
    """환경변수를 보고 큐 백엔드를 만든다.

    CONTENT_QUEUE_BACKEND=postgres 이고 DATABASE_URL이 있으면 PostgreSQL,
    =supabase 이고 SUPABASE_URL/SUPABASE_KEY가 있으면 Supabase,
    그 외에는 JSON 파일 큐(QUEUE_FILE, 기본 queue.json)를 사용한다.
    """
    backend = os.getenv("CONTENT_QUEUE_BACKEND", "json").lower()
    if backend == "postgres":
        dsn = os.getenv("DATABASE_URL", "")
        if dsn:
            logger.info("콘텐츠 큐 백엔드: PostgreSQL")
            return PostgresQueue(dsn)
        logger.warning("DATABASE_URL 미설정 — JSON 파일 큐로 대체")
    if backend == "supabase":
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_KEY", "")
        if url and key:
            logger.info("콘텐츠 큐 백엔드: Supabase")
            return SupabaseQueue(url, key)
        logger.warning("SUPABASE_URL/KEY 미설정 — JSON 파일 큐로 대체")
    logger.info("콘텐츠 큐 백엔드: JSON 파일")
    return JSONFileQueue(os.getenv("QUEUE_FILE", "queue.json"))
