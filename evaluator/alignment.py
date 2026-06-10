import os
import asyncio
import httpx
import logging

logger = logging.getLogger(__name__)

# 환경변수에서 가져오거나 server.py와 동일하게 세팅
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

async def evaluate_scene(scene: dict) -> int:
    prompt = f"""
    narration: {scene.get('narration', '')}
    search: {scene.get('search', '')}

    Evaluate how well the search visually represents narration.
    Score 0~100 only.
    """

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, headers=headers, json=payload, timeout=20.0)
            response.raise_for_status()
            content = response.json()['choices'][0]['message']['content'].strip()
            # 안전하게 숫자만 추출
            score = int(''.join(filter(str.isdigit, content)))
            return score
        except Exception as e:
            logger.error(f"Scene 평가 API 에러: {e}")
            return 0  # 에러 시 기본 점수 반환

async def evaluate_script(script: list) -> dict:
    tasks = [evaluate_scene(s) for s in script]
    scores = await asyncio.gather(*tasks)

    if not scores:
        return {"scene_scores": [], "final_score": 0, "pass": False}

    final_score = sum(scores) / len(scores)

    return {
        "scene_scores": scores,
        "final_score": final_score,
        "pass": final_score >= 70
    }
