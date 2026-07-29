"""발행할 콘텐츠 큐.

두 가지 백엔드를 제공한다.
  - JSONFileQueue : 파일 기반. 자격증명 없이 바로 동작(개발/테스트 기본값).
  - SupabaseQueue : 운영용. Supabase 테이블 `threads_queue`를 사용.

`build_queue()`가 환경변수(CONTENT_QUEUE_BACKEND)에 따라 적절한 백엔드를 만든다.

콘텐츠 아이템 스키마
  {
    "id":        임의 식별자,
    "text":      본문(필수),
    "image_url": 선택,
    "video_url": 선택,
    "status":    "pending" | "published" | "failed",
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

    def mark_published(self, item_id: Any, media_id: str) -> None:
        raise NotImplementedError

    def mark_failed(self, item_id: Any, error: str) -> None:
        raise NotImplementedError

    def add(self, text: str, image_url: str = "", video_url: str = "") -> None:
        raise NotImplementedError

    def pending_count(self) -> int:
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

    def add(self, text: str, image_url: str = "", video_url: str = "") -> None:
        items = self._read()
        next_id = max((int(i.get("id", 0)) for i in items), default=0) + 1
        items.append(
            {
                "id": next_id,
                "text": text,
                "image_url": image_url,
                "video_url": video_url,
                "status": "pending",
            }
        )
        self._write(items)
        logger.info("큐에 아이템 추가: #%d", next_id)

    def pending_count(self) -> int:
        return sum(1 for i in self._read() if i.get("status", "pending") == "pending")


class SupabaseQueue(ContentQueue):
    """Supabase 테이블 `threads_queue` 기반 큐(운영용).

    필요한 테이블(예시):
      create table threads_queue (
        id         bigint generated always as identity primary key,
        text       text not null,
        image_url  text,
        video_url  text,
        status     text not null default 'pending',
        media_id   text,
        error      text,
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

    def mark_published(self, item_id: Any, media_id: str) -> None:
        self.client.table(self.TABLE).update(
            {"status": "published", "media_id": media_id}
        ).eq("id", item_id).execute()

    def mark_failed(self, item_id: Any, error: str) -> None:
        self.client.table(self.TABLE).update(
            {"status": "failed", "error": error}
        ).eq("id", item_id).execute()

    def add(self, text: str, image_url: str = "", video_url: str = "") -> None:
        self.client.table(self.TABLE).insert(
            {"text": text, "image_url": image_url, "video_url": video_url, "status": "pending"}
        ).execute()
        logger.info("Supabase 큐에 아이템 추가")

    def pending_count(self) -> int:
        res = (
            self.client.table(self.TABLE)
            .select("id", count="exact")
            .eq("status", "pending")
            .execute()
        )
        return res.count or 0


def build_queue() -> ContentQueue:
    """환경변수를 보고 큐 백엔드를 만든다.

    CONTENT_QUEUE_BACKEND=supabase 이고 SUPABASE_URL/SUPABASE_KEY가 있으면 Supabase,
    그 외에는 JSON 파일 큐(QUEUE_FILE, 기본 queue.json)를 사용한다.
    """
    backend = os.getenv("CONTENT_QUEUE_BACKEND", "json").lower()
    if backend == "supabase":
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_KEY", "")
        if url and key:
            logger.info("콘텐츠 큐 백엔드: Supabase")
            return SupabaseQueue(url, key)
        logger.warning("SUPABASE_URL/KEY 미설정 — JSON 파일 큐로 대체")
    logger.info("콘텐츠 큐 백엔드: JSON 파일")
    return JSONFileQueue(os.getenv("QUEUE_FILE", "queue.json"))
