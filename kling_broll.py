"""
Kling AI 텍스트→비디오 B-roll 생성 모듈
yt-dlp(YouTube 다운로드) 대체 — 저작권 리스크 없는 자체 생성 B-roll.

환경변수:
  KLING_ACCESS_KEY / KLING_SECRET_KEY — klingai.com 개발자 콘솔에서 발급
  KLING_API_BASE — 기본: https://api-singapore.klingai.com
  KLING_MODEL — 기본: kling-v1-6
"""
import os
import time
import hmac
import json
import base64
import hashlib
import asyncio
import logging
import httpx

logger = logging.getLogger(__name__)

KLING_API_BASE = os.getenv("KLING_API_BASE", "https://api-singapore.klingai.com")
KLING_MODEL = os.getenv("KLING_MODEL", "kling-v1-6")
POLL_INTERVAL_SEC = 10
POLL_TIMEOUT_SEC = 360


def is_configured() -> bool:
    return bool(os.getenv("KLING_ACCESS_KEY") and os.getenv("KLING_SECRET_KEY"))


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _make_jwt() -> str:
    """Kling API JWT (HS256) — pyjwt 의존성 없이 직접 생성"""
    ak = os.getenv("KLING_ACCESS_KEY", "")
    sk = os.getenv("KLING_SECRET_KEY", "")
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps({"iss": ak, "exp": now + 1800, "nbf": now - 5}).encode())
    signing_input = f"{header}.{payload}".encode()
    sig = _b64url(hmac.new(sk.encode(), signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


async def generate_broll(prompt: str, output_path: str) -> bool:
    """
    프롬프트로 9:16 세로 영상 1개 생성 후 output_path에 저장.
    성공 시 True. 실패 시 False (호출 측에서 Pexels 폴백).
    """
    if not is_configured():
        return False

    headers = {
        "Authorization": f"Bearer {_make_jwt()}",
        "Content-Type": "application/json",
    }
    body = {
        "model_name": KLING_MODEL,
        "prompt": f"{prompt}, vertical video, cinematic b-roll, no text overlay",
        "mode": "std",
        "duration": "5",
        "aspect_ratio": "9:16",
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{KLING_API_BASE}/v1/videos/text2video", headers=headers, json=body, timeout=30.0
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                logger.warning(f"Kling 태스크 생성 실패: {data.get('message')}")
                return False
            task_id = data["data"]["task_id"]
        except Exception as e:
            logger.warning(f"Kling API 호출 실패: {e}")
            return False

        # 생성 완료까지 폴링
        deadline = time.time() + POLL_TIMEOUT_SEC
        video_url = None
        while time.time() < deadline:
            await asyncio.sleep(POLL_INTERVAL_SEC)
            try:
                r = await client.get(
                    f"{KLING_API_BASE}/v1/videos/text2video/{task_id}",
                    headers={"Authorization": f"Bearer {_make_jwt()}"},
                    timeout=15.0,
                )
                r.raise_for_status()
                d = r.json().get("data", {})
                status = d.get("task_status")
                if status == "succeed":
                    videos = d.get("task_result", {}).get("videos", [])
                    if videos:
                        video_url = videos[0].get("url")
                    break
                if status == "failed":
                    logger.warning(f"Kling 생성 실패: {d.get('task_status_msg')}")
                    return False
            except Exception as e:
                logger.warning(f"Kling 폴링 오류 (계속 시도): {e}")

        if not video_url:
            logger.warning("Kling 생성 타임아웃 또는 결과 없음")
            return False

        try:
            dl = await client.get(video_url, timeout=120.0)
            dl.raise_for_status()
            with open(output_path, "wb") as f:
                f.write(dl.content)
            return os.path.exists(output_path)
        except Exception as e:
            logger.warning(f"Kling 영상 다운로드 실패: {e}")
            return False
