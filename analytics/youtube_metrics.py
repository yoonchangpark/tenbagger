import os
import httpx
import logging
import json
import aiofiles

logger = logging.getLogger(__name__)

# 환경변수에서 키 로드
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

async def get_video_metrics(video_id: str) -> dict:
    """비동기 방식으로 YouTube Data API v3를 호출하여 조회수 및 참여도 메트릭을 반환합니다."""
    if not YOUTUBE_API_KEY:
        logger.warning("YOUTUBE_API_KEY가 설정되지 않아 메트릭을 가져올 수 없습니다.")
        return {"video_id": video_id, "views": 0, "engagement_rate": 0}

    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "part": "statistics,contentDetails",
        "id": video_id,
        "key": YOUTUBE_API_KEY
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, params=params, timeout=15.0)
            response.raise_for_status()
            data = response.json()
            
            if not data.get('items'):
                logger.warning(f"비디오 정보를 찾을 수 없습니다: {video_id}")
                return {"video_id": video_id, "views": 0, "engagement_rate": 0}

            stats = data['items'][0]['statistics']
            
            views = int(stats.get('viewCount', 0))
            likes = int(stats.get('likeCount', 0))
            comments = int(stats.get('commentCount', 0))

            engagement_rate = (likes + comments) / views if views > 0 else 0

            return {
                "video_id": video_id,
                "views": views,
                "engagement_rate": round(engagement_rate, 4)
            }
            
        except httpx.HTTPStatusError as e:
            logger.error(f"YouTube API HTTP 에러 ({e.response.status_code}): {e.response.text}")
            return {"video_id": video_id, "views": 0, "engagement_rate": 0}
        except Exception as e:
            logger.error(f"YouTube metrics 추출 중 에러 발생: {e}")
            return {"video_id": video_id, "views": 0, "engagement_rate": 0}

async def save_metrics(data: dict):
    """비동기 방식으로 지표 데이터를 JSON 파일에 누적 저장합니다."""
    # 현재 파일 기준 최상단 data 폴더 경로 설정
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    metrics_file = os.path.join(base_dir, "data", "metrics.json")
    
    try:
        async with aiofiles.open(metrics_file, "r", encoding="utf-8") as f:
            content = await f.read()
            existing = json.loads(content) if content else []
    except (FileNotFoundError, json.JSONDecodeError):
        existing = []

    existing.append(data)

    async with aiofiles.open(metrics_file, "w", encoding="utf-8") as f:
        await f.write(json.dumps(existing, indent=2, ensure_ascii=False))
