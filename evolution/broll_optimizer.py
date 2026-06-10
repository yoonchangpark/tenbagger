import os
import json
import logging
import asyncio
import aiofiles
from datetime import datetime

logger = logging.getLogger(__name__)

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
data_dir = os.path.join(base_dir, "data")
os.makedirs(data_dir, exist_ok=True)

BROLL_EVALS_FILE = os.path.join(data_dir, "broll_evals.json")
BROLL_RULES_FILE = os.path.join(data_dir, "broll_rules.json")

# 동시성 이슈를 막기 위한 락
evals_lock = asyncio.Lock()

async def save_broll_evaluation(narration: str, query: str, source_type: str, is_success: bool, reason: str):
    """
    제미나이 영상 평가 결과를 저장합니다.
    """
    async with evals_lock:
        data = []
        if os.path.exists(BROLL_EVALS_FILE):
            try:
                async with aiofiles.open(BROLL_EVALS_FILE, "r", encoding="utf-8") as f:
                    content = await f.read()
                    data = json.loads(content) if content else []
            except Exception as e:
                logger.error(f"broll_evals.json 로드 에러: {e}")

        entry = {
            "timestamp": datetime.now().isoformat(),
            "narration": narration,
            "query": query,
            "source_type": source_type,
            "is_success": is_success,
            "reason": reason
        }
        data.append(entry)

        # 최대 1000개까지만 유지 (경량화)
        if len(data) > 1000:
            data = data[-1000:]

        try:
            async with aiofiles.open(BROLL_EVALS_FILE, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.error(f"broll_evals.json 저장 에러: {e}")

async def load_broll_rules() -> str:
    """
    서버 프롬프트 인젝션용 오답노트 규칙 문자열을 반환합니다.
    """
    if not os.path.exists(BROLL_RULES_FILE):
        return "특별한 회피 패턴이 없습니다."
    try:
        async with aiofiles.open(BROLL_RULES_FILE, "r", encoding="utf-8") as f:
            content = await f.read()
            rules = json.loads(content)
            
            avoid_list = "\n".join([f"- {r}" for r in rules.get("bad_patterns", [])])
            success_list = "\n".join([f"- {r}" for r in rules.get("good_patterns", [])])
            
            if not avoid_list: avoid_list = "없음"
            if not success_list: success_list = "없음"
            
            result_str = f"**[최근 실패한 검색어 오답노트 - 절대 피할 것]**\n{avoid_list}\n\n"
            result_str += f"**[최근 성공한 모범 패턴 - 적극 활용할 것]**\n{success_list}"
            return result_str
            
    except Exception as e:
        logger.error(f"broll_rules.json 로드 에러: {e}")
        return "특별한 회피 패턴이 없습니다."

async def evolve_broll_rules():
    """
    축적된 평가 데이터를 기반으로 오답 패턴을 추출해 broll_rules.json을 갱신합니다.
    이 함수는 크론탭이나 관리자 단에서 비동기로 트리거됩니다.
    """
    if not os.path.exists(BROLL_EVALS_FILE):
        logger.info("평가 데이터가 없습니다.")
        return

    try:
        async with aiofiles.open(BROLL_EVALS_FILE, "r", encoding="utf-8") as f:
            content = await f.read()
            evals = json.loads(content) if content else []
    except Exception as e:
        logger.error(f"broll 평가 데이터 로드 에러: {e}")
        return

    if len(evals) < 20: # 20개 이상 모였을 때만 진화 수행
        logger.info(f"데이터 부족 ({len(evals)}/20) - B-roll 진화 스킵")
        return

    # 최근 50개 데이터만 분석
    target_evals = evals[-50:]
    
    # LLM을 통한 분석 (OpenAI GPT-4o 재사용)
    import httpx
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY 누락: B-roll 오답노트 진화 불가")
        return

    prompt_data = json.dumps(target_evals, ensure_ascii=False)
    
    system_prompt = """
당신은 AI 영상 생성에 사용되는 B-roll 검색어의 퀄리티 평가자입니다.
지급된 최근 50개의 B-roll 매칭 기록(narration과 그에 맞춰 생성했던 query, 그리고 시각 지능 AI의 평가 코멘트인 reason 및 성공여부 is_success)을 분석하세요.

목표: 어떤 검색어 패턴이 완전히 실패(엉뚱한 영상 매칭, 지나친 추상성 등)를 가져오는지 뽑아내어 '오답 노트'를 만들고, 어떤 검색어가 완벽한 매칭(is_success=true)을 끌어냈는지 파악합니다.

오직 다음 JSON 형식으로만 반환하세요:
{
    "bad_patterns": ["가장 치명적이고 반복적으로 엉뚱한 결과를 초래한 검색 키워드/성향 분석 3가지"],
    "good_patterns": ["가장 정확한 씬을 찾아냈던 우수 검색어 생성 노하우 2가지"]
}
"""

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gpt-4o",
        "response_format": { "type": "json_object" },
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt_data}
        ],
        "temperature": 0.4
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload, timeout=60.0)
            response.raise_for_status()
            content_text = response.json()['choices'][0]['message']['content']
            parsed_data = json.loads(content_text)
            
            async with aiofiles.open(BROLL_RULES_FILE, "w", encoding="utf-8") as f:
                await f.write(json.dumps(parsed_data, ensure_ascii=False, indent=2))
                
            logger.info("=== B-roll 오답 노트(Self-Improving DB) 갱신 완료 ===")
            
    except Exception as e:
        logger.error(f"오답노트 추출을 위한 OpenAI API 호출 에러: {e}")

if __name__ == "__main__":
    # 스탠드얼론 강제 진화 테스트용
    import asyncio
    logging.basicConfig(level=logging.INFO)
    asyncio.run(evolve_broll_rules())
