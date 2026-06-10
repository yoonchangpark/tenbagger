import os
import json
import statistics
import logging
import aiofiles

logger = logging.getLogger(__name__)

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METRICS_FILE = os.path.join(base_dir, "data", "metrics.json")
OUTPUT_RULES_FILE = os.path.join(base_dir, "data", "prompts.json")

async def load_metrics():
    if not os.path.exists(METRICS_FILE):
        return []
    try:
        async with aiofiles.open(METRICS_FILE, "r", encoding="utf-8") as f:
            content = await f.read()
            return json.loads(content) if content else []
    except Exception as e:
        logger.error(f"메트릭 파일 로드 에러: {e}")
        return []

async def save_prompt_rules(rules):
    try:
        async with aiofiles.open(OUTPUT_RULES_FILE, "w", encoding="utf-8") as f:
            await f.write(json.dumps(rules, ensure_ascii=False, indent=2))
    except Exception as e:
        logger.error(f"프롬프트 규칙 저장 에러: {e}")

def extract_hook(script):
    try:
        # script가 리스트 객체라고 가정
        if isinstance(script, list) and len(script) > 0:
            return script[0].get("narration", "")
        return ""
    except Exception:
        return ""

async def evolve_prompt():
    """GPT 대신 자체 로컬 통계(CTR, 시청 지속시간 등)를 이용해 프롬프트를 진화시킵니다."""
    data = await load_metrics()
    
    if len(data) < 10:
        logger.info("데이터 부족(10개 미만) - 진화 스킵")
        return
    
    # 🔥 상위 영상 필터
    good_videos = []
    for v in data:
        # 기존 체계 호환성(engagement_rate) 유지 및 신규 키(ctr, watch_time) 처리
        ctr = v.get("ctr", 0)
        watch_time = v.get("watch_time", 0)
        
        # 조건에 맞는 우수 영상 필터 (CTR 8% 초과, 시청지속 60% 초과)
        if ctr > 0.08 and watch_time > 0.6:
            good_videos.append(v)
    
    if len(good_videos) < 3:
        logger.info("성과 좋은 영상 부족 (CTR > 0.08 및 Watch Time > 0.6 인 데이터 3개 미만)")
        return
    
    # 🔥 패턴 추출
    hooks = [extract_hook(v.get("script", [])) for v in good_videos]
    hooks = [h for h in hooks if h]  # 빈 후킹 문구 제거
    
    scene_counts = [len(v.get("script", [])) for v in good_videos]
    avg_scene = int(statistics.mean(scene_counts)) if scene_counts else 6
    
    # 🔥 키워드 분석 (간단 버전)
    keywords = []
    for h in hooks:
        words = h.split()
        keywords.extend(words)
    
    top_keywords = list(set([w for w in keywords if len(w) >= 2]))[:10]
    
    # 🔥 규칙 생성
    rules = {
        "hook_patterns": hooks[:5],
        "avg_scene": avg_scene,
        "keywords": top_keywords,
        "writing_rules": [
            "첫 문장에 숫자 반드시 포함",
            "기업명 반드시 포함",
            "강한 감정 단어 사용 (폭등, 충격, 미쳤다 등)",
            "3초 안에 핵심 전달"
        ]
    }
    
    await save_prompt_rules(rules)
    logger.info("로컬 통계 기반 프롬프트 진화 완료")

async def load_prompt_rules() -> str:
    """server.py와의 호환을 위해 파일에 쓰여진 규칙 딕셔너리를 예쁜 JSON 문자열로 반환합니다."""
    try:
        async with aiofiles.open(OUTPUT_RULES_FILE, "r", encoding="utf-8") as f:
            content = await f.read()
            data = json.loads(content)
            # 서버 메인 프롬프트에 주입할 때 가독성이 좋도록 문자열 처리
            return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception:
        return ""
