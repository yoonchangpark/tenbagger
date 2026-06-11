import os
import json
import logging
import asyncio
import httpx
import aiofiles
import mimetypes
import subprocess
import re
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
import time
from dotenv import load_dotenv

# 로컬 모듈 import 전에 .env 로드 (모듈 최상위에서 os.getenv 하는 모듈들의 키 누락 방지)
load_dotenv()

from evaluator.alignment import evaluate_script
from evolution.prompt_engine import load_prompt_rules, evolve_prompt
from evolution.broll_optimizer import save_broll_evaluation, load_broll_rules
from analytics.youtube_metrics import get_video_metrics, save_metrics
from tenbagger_topic import pick_tenbagger_topic
from historical_topic import pick_history_topic
import kling_broll

# 로그 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Windows/FastAPI mp3 재생 불가 버그 해결 ---
mimetypes.add_type('audio/mpeg', '.mp3')
mimetypes.add_type('image/png', '.png')
mimetypes.add_type('video/mp4', '.mp4')

app = FastAPI(title="AI Shorts Automation System - 2026 Edition")

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 설정 및 API 키 ---
# dotenv를 통해 환경 변수에서 가져오거나, 기존 키를 기본값으로 사용
# dotenv를 통해 환경 변수에서 가져오거나, 기존 키를 기본값으로 사용
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
os.makedirs(ASSETS_DIR, exist_ok=True)

app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")

# --- 데이터 모델 ---
class ShortsRequest(BaseModel):
    topic: str = "2026 글로벌 비즈니스 인사이트"
    target_audience: str = "investors"
    style: str = "brand_story"

class JobStatus(BaseModel):
    job_id: str
    status: str
    video_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    script: Optional[str] = None

# 임시 저장소
jobs = {}

HOOK_DB = [
    "지금 {기업명}이 {숫자}조를 태웠습니다",
    "{기업명}, 방금 미친 결정 나왔습니다",
    "{숫자}% 상승, 이유는 단 하나입니다",
    "지금 이 시장, 거의 끝났습니다",
    "{기업명} 때문에 판이 뒤집혔습니다",
    "이거 모르면 진짜 돈 못 법니다",
    "오늘 {기업명}에서 터졌습니다",
    "지금 기관들이 몰래 사는 이유",
    "{숫자}배 갑니다, 근거 있습니다",
    "이거 알고 있으면 이미 상위 1%입니다"
]

# --- 핵심 로직 ---

async def call_openai_script(topic: str, context: str):
    """(비동기) OpenAI API를 호출하여 Scene 단위 구조화된 대본(JSON)을 가져옵니다."""
    import random
    
    rules = await load_prompt_rules()
    broll_rules = await load_broll_rules()
    selected_hook = random.choice(HOOK_DB)

    system_prompt = f"""
    너는 시청자의 멱살을 잡고 절대 놓아주지 않는 100만 구독자의 독한 '경제/산업/브랜드 분석' 쇼츠 유튜버야. 
    이전의 단순한 나열 방식 대신, 프리미어 프로 컷 편집에 바로 쓸 수 있는 [Scene 단위 매칭 방식]으로 기획해.
    
    주어진 트렌드 데이터와 팩트 정보를 바탕으로 50초 정도의 자극적이고 밀도 높은 유튜브 쇼츠 대본을 작성해.

    [🔥 SYSTEM CRITICAL RULE (절대 규칙) 🔥]
    1. 너는 '이 주식', '이 기업', '이 회사' 같은 대명사 사용이 절대 금지되어 있다.
    2. 반드시 분석된 `target_company`(예: 엔비디아, 테슬라)의 정확한 이름을 대본(narration)의 첫 문장부터 영상이 끝날 때까지 주기적으로 명시해라.
    (나쁜 예: "지금 이 주식이 폭등 중입니다." / 좋은 예: "지금 엔비디아가 폭등 중입니다.")

    [★ 100만 뷰를 위한 대본 작성 5계명 (Task 1 업그레이드) ★]
    1. 미친 훅(Hook)과 구체적 수치: 무조건 다음 문장 템플릿을 응용 및 변형하여 대본의 가장 첫 문장(Hook)으로 사용해라:
    "{selected_hook}"
    (반드시 추출한 기업명과 내용에 맞는 구체적 수치를 자연스럽게 채워넣을 것. 영상 시작 0~3초 안에 시청자의 시선을 완벽히 앗아가라.)
    2. 구체적인 명분(근거) 제시: "기술력이 좋습니다"라고 퉁치지 마. "국경 간 송금 시간 3초" 등 명확한 근거 데이터를 하나 이상 꽂아 넣을 것. 제공된 [🔥 NotebookLM 심층 분석 인사이트]의 핵심 데이터나 전문가 시각을 반드시 1개 이상 대본에 인용해라.
    [🔥 B-roll 검색어(Search) 생성 절대 규칙: '시각 연출 감독' 모드 🔥]
    1. [가장 중요] B-roll 검색어(`search` 필드)는 반드시 "영상 검색을 위한 구체적인 영어 키워드"로만 작성해라. (한글 절대 금지)
    2. 절대로 추상적 개념(Business, Economy, Investment, Future, Success 등)을 쓰지 마라. 이런 단어를 쓰면 엉뚱한 영상이 매칭된다.
    3. 무조건 눈에 보이는 구체적인 피사체를 묘사하는 영어 명사구(예: 'Stressed man holding head at office desk', 'Falling red stock market graph animation')로만 구성해라.
    4. source_type: 'pexels'는 [형용사 + 구체적 피사체 + 상황] 형태의 구체적 영어 키워드로만 작성할 것.
    5. source_type: 'youtube'는 AI 영상 생성(Kling)으로 처리된다. 실존 기업/제품의 구체적 장면이 필요할 때 쓰되, search 필드가 곧 영상 생성 프롬프트가 되므로 카메라 워크와 분위기를 암시하는 구체적 영어 묘사로 작성해라.
    6. [톤앤매너(Tone & Manner) 절대 규칙] 전체 영상을 관통하는 톤앤매너 테마를 `global_aesthetic`에 1개 기재해라. 단, 검색 결과를 0개로 치명적으로 없애버리는 '긴 문장 복붙 꼬리표' 부착은 절대 금지한다. 대신, `search` 필드는 영상 검색 엔진이 100% 매칭할 수 있도록 **총 3~4개의 핵심 명사 단어**로만 아주 짧게 압축해서 구성하되, 테마를 암시하는 형용사 단어 딱 1개만 피사체 앞에 자연스럽게 섞어라 (예: `dark cinematic wegovy pill`).

    [✅ 완벽한 JSON 반환 예시]
    {{
      "target_company": "엔비디아",
      "top_title": "시선을 끄는 12자 이내 제목",
      "global_aesthetic": "dark cinematic realistic vibe",
      "scenes": [
        {{
          "scene_num": 1, 
          "source_type": "youtube", 
          "visual_intent": "월스트리트의 붉은 황소 동상을 아래에서 위로 올려다보는 역동적인 시네마틱 컷",
          "search": "cinematic Wall Street bull statue", 
          "narration": "월가 전문가들이 내놓은 충격적인 전망,"
        }},
        {{
          "scene_num": 2, 
          "source_type": "pexels", 
          "visual_intent": "구 데이터가 거대한 붉은색 파티클로 부서져 내리는 추상적인 백그라운드 애니메이션",
          "search": "dark red network particles", 
          "narration": "AI 데이터 센터의 미래를 이렇게 예측했습니다."
        }}
      ]
    }}
    
    🚨 [치명적 JSON 문법 에러 및 Pacing 방지 규칙] 🚨
    1. 내부 문자열에 줄바꿈(엔터) 절대 금지.
    2. 숏츠를 위해 한 Scene당 narration 분량은 최대 1.5초~2.5초(약 10~15자) 단위로 아주 잘게 쪼개어라(Micro-segmentation). 
    3. 인접한 Scene에서 `search` 쿼리가 절대 겹치지 않게 무조건 바꿔라.
    4. 대본 전체 길이는 70초(모든 narration 합산 300자~400자) 이내가 되도록 약 5~8개의 Scene 조합으로 쳐라.

    [추가 최적화 규칙]
    {rules}
    
    {broll_rules}
    """
    user_query = f"주제: {topic}\n문맥 데이터: {context}"
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
            {"role": "user", "content": user_query}
        ],
        "temperature": 0.8
    }
    import httpx
    import json
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, headers=headers, json=payload, timeout=40.0)
            response.raise_for_status()
            content_text = response.json()['choices'][0]['message']['content']
            content_text = content_text.replace("```json", "").replace("```", "").strip()
            
            parsed_data = json.loads(content_text)
            return parsed_data
        except Exception as e:
            logger.error(f"OpenAI API 에러: {e}")
            try:
                logger.error(f"실패한 원본 텍스트: {content_text}")
            except: pass
            return {
                "top_title": "한국 돈방석 예약",
                "scenes": [
                    {
                        "scene_num": 1, 
                        "source_type": "pexels", 
                        "visual_intent": "빨갛게 떨어지는 차트", 
                        "search": "falling red stock market graph animation", 
                        "narration": "최근 엄청난 위기가 발생했습니다."
                    },
                    {
                        "scene_num": 2, 
                        "source_type": "youtube", 
                        "visual_intent": "머리를 감싸쥔 회사원의 모습", 
                        "search": "stressed businessman holding head at office desk official b-roll", 
                        "narration": "그런데 이 상황을 반전시킬 기업이 있죠."
                    }
                ]
            }

async def step_2_download_background_video(script_data: list, output_dir: str = None):
    """(비동기) Hybrid Vision Architecture: Thumbnail Picker로 사전 검수 및 단일 다운로드"""
    import httpx
    import json
    import os
    import time
    import asyncio
    import google.generativeai as genai
    from PIL import Image
    import io

    # 제미나이 설정
    genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
    model = genai.GenerativeModel("gemini-2.5-pro")

    async def _fetch_pexels_candidates(query: str):
        candidates = []
        api_key = os.getenv("PEXELS_API_KEY")
        if not api_key: return candidates
        url = "https://api.pexels.com/videos/search"
        headers = {"Authorization": api_key}
        params = {"query": query, "orientation": "portrait", "size": "large", "per_page": 5, "page": 1}
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, headers=headers, params=params, timeout=15.0)
                if response.status_code == 200:
                    data = response.json()
                    for idx, v in enumerate(data.get("videos", [])):
                        valid_files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4"]
                        if valid_files:
                            best_file = max(valid_files, key=lambda x: x.get("width", 0) * x.get("height", 0))
                            candidates.append({
                                "id": f"pex_{idx}",
                                "thumb_url": v.get("image"),
                                "video_url": best_file["link"],
                                "source_type": "pexels"
                            })
            except Exception as e:
                logger.warning(f"Pexels 후보 추출 실패: {e}")
        return candidates

    async def _download_image(client: httpx.AsyncClient, url: str):
        try:
            resp = await client.get(url, timeout=10.0)
            resp.raise_for_status()
            return Image.open(io.BytesIO(resp.content)).convert("RGB")
        except Exception:
            return None

    async def _select_best_candidate(candidates: list, narration: str, query: str):
        if not candidates: return None
        
        async with httpx.AsyncClient() as client:
            tasks = [_download_image(client, c['thumb_url']) for c in candidates]
            images = await asyncio.gather(*tasks)

        valid_candidates = []
        valid_images = []
        for c, img in zip(candidates, images):
            if img:
                valid_candidates.append(c)
                valid_images.append(img)
                
        if not valid_candidates: return None
        if len(valid_candidates) == 1: return valid_candidates[0]

        prompt = f"""당신은 100만 유튜버의 수석 영상 검수 PD입니다. 
다음 이미지들은 대본 나레이션과 매칭되는 배경 영상(B-roll) 후보들의 썸네일(또는 스틸컷)입니다. 후보는 1번부터 {len(valid_images)}번까지 순차적으로 제공됩니다.

[나레이션 내용]: "{narration}"

[평가 기준]
1. 나레이션의 감정이나 맥락, 단어와 가장 완벽하게 일치하는 그림 하나를 고르세요.
2. 유튜버 얼굴이 대문짝만하게 나오거나 텍스트가 너무 많은 썸네일은 정보성 B-roll로 부적합하므로 제외하세요.

오직 순수 JSON 형식으로 가장 적합한 번호(1~{len(valid_images)})를 출력하세요. 
만약 모두 완전히 부적절하다면 0을 대답하세요.
{{
    "best_index": 1,
    "reason": "왜 이 번호를 골랐는지 1줄 요약"
}}"""

        try:
            response = await asyncio.to_thread(
                model.generate_content,
                valid_images + [prompt],
                request_options={"timeout": 60}
            )
            resp_text = response.text.replace("```json", "").replace("```", "").strip()
            data = json.loads(resp_text)
            best_idx = data.get("best_index", 1)
            reason = data.get("reason", "No reason provided")
            
            is_success = True
            if best_idx == 0 or best_idx > len(valid_candidates):
                best_idx = 1 # 부적절하더라도 구동을 위해 1번 강제
                is_success = False
                
            logger.info(f" - 선택된 인덱스: {best_idx}, 사유: {reason}")
            
            best_c = valid_candidates[best_idx - 1]
            asyncio.create_task(save_broll_evaluation(
                narration=narration,
                query=query,
                source_type=best_c['source_type'],
                is_success=is_success,
                reason=reason
            ))
            return best_c
            
        except Exception as e:
            logger.warning(f"제미나이 썸네일 심사 오류 (1번째 강제진행): {e}")
            return valid_candidates[0]

    async def _download_final_video(candidate: dict, path: str):
        import httpx
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(candidate['video_url'], timeout=60.0)
                resp.raise_for_status()
                import aiofiles
                async with aiofiles.open(path, "wb") as f:
                    await f.write(resp.content)
                return True
            except Exception as e:
                logger.warning(f"Pexels 비디오 직접 다운로드 오류: {e}")
                return False

    downloaded_paths = []
    
    save_dir = output_dir if output_dir else ASSETS_DIR
    
    for idx, scene in enumerate(script_data):
        timestamp = int(time.time()) + idx
        final_video_path = os.path.join(save_dir, f"bg_{timestamp}_{idx}.mp4")
        source_type = scene.get("source_type", "youtube")
        query = scene.get("search", "finance background")
        narration = scene.get("narration", "")
        
        logger.info(f"[{idx+1} 씬] [B-roll 소싱 시작] {source_type} | 검색어: {query}")

        # history 모드: 미리 생성된 검색량 차트 클립 사용
        if scene.get("chart_path") and os.path.exists(scene["chart_path"]):
            import shutil
            shutil.copy(scene["chart_path"], final_video_path)
            logger.info(" ✅ [검색량 차트 클립 삽입]")
            downloaded_paths.append(final_video_path)
            continue

        # youtube(yt-dlp) 소싱 제거 → Kling AI 생성 우선, 실패 시 Pexels 검색 폴백
        if source_type != "pexels" and kling_broll.is_configured():
            logger.info(f" - Kling AI 영상 생성 시도: {query[:50]}")
            if await kling_broll.generate_broll(query, final_video_path):
                logger.info(" ✅ [Kling 생성 완료]")
                downloaded_paths.append(final_video_path)
                continue
            logger.warning(" - Kling 생성 실패 → Pexels 폴백")

        # 'official b-roll without interview' 등 유튜브용 꼬리표 제거 후 Pexels 검색
        pexels_query = query.replace("official b-roll without interview", "").strip()
        candidates = await _fetch_pexels_candidates(pexels_query)

        logger.info(f" - {len(candidates)}개의 후보 영상 썸네일 추출 완료. Vision AI 심사 시작... (나레이션: {narration[:15]}...)")

        best_candidate = await _select_best_candidate(candidates, narration, query)

        if best_candidate:
            logger.info(f" ✅ [Vision Picker 선택 완료] 소스: {best_candidate.get('source_type')} URL: {best_candidate.get('video_url')}")
            logger.info(" - 원본 영상 매칭 완료. 최종 단일 다운로드 수행 중...")
            success = await _download_final_video(best_candidate, final_video_path)

            if success and os.path.exists(final_video_path):
                downloaded_paths.append(final_video_path)
            else:
                logger.error(" - 원본 영상 다운로드 실패. Fallback 적용.")
                fb = await _fetch_pexels_candidates("falling red stock market graph animation")
                if fb and await _download_final_video(fb[0], final_video_path):
                    downloaded_paths.append(final_video_path)
        else:
            logger.error(f"[{idx+1} 씬] 후보군 수집 실패. 범용 B-roll 강제 삽입.")
            fallback_path = os.path.join(save_dir, f"fallback_{timestamp}_{idx}.mp4")
            fb = await _fetch_pexels_candidates("finance chart stock animation")
            if fb and await _download_final_video(fb[0], fallback_path):
                downloaded_paths.append(fallback_path)

    return downloaded_paths

async def query_notebooklm(topic: str) -> str:
    """(비동기) NotebookLM MCP CLI를 호출하여 주어진 주제에 대해 내장된 문서 및 방금 주입된 최신 유튜브 영상을 기반으로 인사이트를 가져옵니다."""
    notebook_id = "9a5bb535-bdc2-438e-bed3-ea069989b63a"
    # 질문 프롬프트 구성
    query = f"다음 주제에 대한 핵심 인사이트와 트렌드를 요약 제공해줘: '{topic}'. 제공된 소스(특히 방금 추가된 최신 유튜브 영상 오디오/대본 전체)를 집중적으로 분석하여, 새롭게 파악된 구조적 변화, 투자 인사이트, 2026년 시장 전망 등에 초점을 맞춰서 구체적인 데이터나 사례와 함께 답변해."
    
    cmd = [
        "uvx", "--from", "notebooklm-mcp-cli", "nlm", "query", "notebook", notebook_id, query
    ]
    
    # 윈도우 환경 CP949 인코딩 에러 방지를 위해 UTF-8 강제
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode == 0:
            # 출력된 문자열에서 필요없는 ANSI 코드나 불필요한 공백 제거
            result_text = stdout.decode("utf-8").strip()
            # 간단한 마크다운 클린업 (선택사항)
            # 만약 결과가 너무 길어 AI 요약에 방해된다면 적절히 자를 수 있음
            return result_text
        else:
            logger.error(f"NotebookLM Query 비정상 종료. returncode: {process.returncode}\nSTDOUT: {stdout.decode('utf-8')}\nSTDERR: {stderr.decode('utf-8', errors='replace')}")
            return "NotebookLM에서 데이터를 가져오지 못했습니다. (서버 내부 에러)"
    except Exception as e:
        logger.error(f"NotebookLM 실행 중 에러: {e}")
        return f"NotebookLM 연동 에러: {str(e)}"

async def inject_source_to_notebooklm(youtube_url: str) -> bool:
    """(비동기) NotebookLM MCP CLI를 사용하여 유튜브 동영상을 노트북 소스로 주입합니다."""
    notebook_id = "9a5bb535-bdc2-438e-bed3-ea069989b63a"
    cmd = [
        "uvx", "--from", "notebooklm-mcp-cli", "nlm", "source", "add", notebook_id, "--youtube", youtube_url, "--wait"
    ]
    
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode == 0:
            logger.info(f"선정된 유튜브 소스 추가 완료: {youtube_url}\n출력: {stdout.decode('utf-8')}")
            return True
        else:
            logger.error(f"NotebookLM 소스 주입 실패. returncode: {process.returncode}\nSTDOUT: {stdout.decode('utf-8')}\nSTDERR: {stderr.decode('utf-8', errors='replace')}")
            return False
    except Exception as e:
        logger.error(f"NotebookLM 소스 주입 예외 발생: {e}")
        return False


async def step_3_audio_and_edit(script_data: list, force_free_tts: bool = False, output_dir: str = None):
    """(비동기) ElevenLabs TTS 사용, 에러 시 gTTS 우회. 나레이션 텍스트만 추출하여 읽습니다."""
    
    import re
    # "10.0" → "10" : 소수점 .0 제거 (TTS가 "십분의영"처럼 읽는 것 방지, 자막도 동일 적용)
    narration_parts = [
        re.sub(r'(\d+)\.0(?!\d)', r'\1', scene['narration'])
        for scene in script_data if 'narration' in scene
    ]
    full_narration_text = " ".join(narration_parts)
    # Task 3: TTS 텍스트 정제 (괄호 및 내용 제거)
    full_narration_text = re.sub(r'\(.*?\)', '', full_narration_text)
    
    timestamp = int(time.time())
    file_name = f"voice_{timestamp}.mp3"
    save_dir = output_dir if output_dir else ASSETS_DIR
    audio_path = os.path.join(save_dir, file_name)
    
    # ElevenLabs API 설정 (Task 3)
    # 보이스 변경: .env에 ELEVENLABS_VOICE_ID 설정 (기본값은 기존 Taehyung 보이스)
    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "m3gJBS8OofDJfycyA2Ip")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": ELEVENLABS_API_KEY
    }
    
    payload = {
        "text": full_narration_text[:4000],
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.3,
            "similarity_boost": 0.85,
            "style": 0.8,
            "use_speaker_boost": True
        }
    }
    
    async with httpx.AsyncClient() as client:
        try:
            if not ELEVENLABS_API_KEY or force_free_tts:
                raise ValueError("ElevenLabs API Key is missing or Draft mode forced gTTS.")
                
            response = await client.post(url, json=payload, headers=headers, timeout=60.0)
            response.raise_for_status() 
            
            async with aiofiles.open(audio_path, "wb") as f:
                await f.write(response.content)
                
        except Exception as e:
            logger.warning(f"ElevenLabs TTS 에러 발생({e}). 구글 무료 TTS(gTTS)로 우회합니다.")
            try:
                def run_gtts():
                    from gtts import gTTS
                    tts = gTTS(text=full_narration_text, lang='ko')
                    tts.save(audio_path)
                await asyncio.to_thread(run_gtts)
            except Exception as fallback_e:
                logger.error(f"gTTS 백업 실패: {fallback_e}")
                return None, narration_parts

    # Task: 오디오 텐션 최적화 로직 (pydub 무음 제거 및 배속)
    try:
        def optimize_audio(path):
            from pydub import AudioSegment
            from pydub.silence import split_on_silence
            
            audio = AudioSegment.from_mp3(path)
            
            # 1. 문장 사이 0.2초(200ms) 이상, -45dB 이하 빈 공간 스킵 & 타이트하게 연결
            chunks = split_on_silence(
                audio, 
                min_silence_len=200, 
                silence_thresh=-45,
                keep_silence=50
            )
            if chunks:
                audio = sum(chunks)
                
            # 2. 1.1배속 텐션 업그레이드 (프레임 레이트 조절 트릭 이용, 쇼츠 특유의 통통 튀는 피치)
            speed_factor = 1.1
            new_sample_rate = int(audio.frame_rate * speed_factor)
            fast_audio = audio._spawn(audio.raw_data, overrides={'frame_rate': new_sample_rate})
            fast_audio = fast_audio.set_frame_rate(44100)
            
            fast_audio.export(path, format="mp3")
            
        await asyncio.to_thread(optimize_audio, audio_path)
        logger.info("오디오 초미세 무음 구간 커팅 및 1.1배속 텐션 업그레이드 완료")
    except ImportError:
        logger.warning("pydub 모듈이 설치되어 있지 않아 오디오 최적화를 스킵합니다. 'pip install pydub'를 실행해 주세요.")
    except Exception as e:
        logger.warning(f"오디오 최적화 실패 (원본 오디오 유지): {e}")

    return audio_path, narration_parts

async def step_4_assemble_video(video_paths: list, audio_path: str, narrations: list, topic: str, is_draft_mode: bool = False, output_dir: str = None):
    """(비동기) moviepy를 사용하여 비디오와 오디오를 합성하고, 대략적인 자막을 씌웁니다."""
    def _moviepy_assemble():
        from moviepy.editor import VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip
        import ast
        
        # 오디오 로드 (오디오 길이에 맞춤)
        try:
            audio_clip = AudioFileClip(audio_path)
        except Exception as e:
            logger.error(f"오디오 로드 실패: {e}")
            return None
            
        target_duration = audio_clip.duration
        
        from moviepy.editor import ColorClip, concatenate_videoclips, CompositeAudioClip
        from PIL import Image, ImageDraw, ImageFont
        import numpy as np
        from moviepy.editor import ImageClip
        import moviepy.video.fx.all as vfx
        import moviepy.audio.fx.all as afx
        import math
        
        # BGM 로드 (배경음악)
        bgm_path = os.path.join(ASSETS_DIR, "bgm.mp3")
        bgm_clip = None
        if os.path.exists(bgm_path):
            try:
                bgm_clip = AudioFileClip(bgm_path).fx(afx.volumex, 0.08)
                bgm_clip = afx.audio_loop(bgm_clip, duration=target_duration)
            except Exception as e:
                logger.warning(f"BGM 로드 에러: {e}")
        
        # 효과음 로드 (트랜지션)
        sfx_path = os.path.join(ASSETS_DIR, "sfx_transition.mp3")
        sfx_clip_base = None
        if os.path.exists(sfx_path):
            try:
                sfx_clip_base = AudioFileClip(sfx_path)
            except Exception as e:
                logger.warning(f"SFX 로드 에러: {e}")

        audio_clips_to_mix = [audio_clip]
        if bgm_clip:
            audio_clips_to_mix.append(bgm_clip)
        
        # 비디오 로드 로직 (3분할 중단 레이아웃을 위한 리사이즈 교차 편집)
        bg_clips = []
        valid_paths = [p for p in video_paths if p and os.path.exists(p)]
        
        total_chars = sum(len(s) for s in narrations)
        total_chars = max(1, total_chars)
        
        if valid_paths:
            loaded_clips = []
            for p in valid_paths:
                try:
                    loaded_clips.append(VideoFileClip(p))
                except Exception as e:
                    logger.warning(f"비디오 로딩 에러: {e}")
                    
            if loaded_clips:
                current_time = 0.0
                
                # [최소 클립 길이 보장] 나레이션당 원래 할당 시간(초) 계산
                MIN_CLIP_DURATION = 3.0  # 최소 3초: 이보다 짧으면 인접 씬과 합쳐서 같은 소스 클립 위에서 자막만 교체
                raw_durations = [(len(n) / total_chars) * target_duration for n in narrations]
                
                # 인접한 짧은 나레이션들을 하나의 '비주얼 그룹'으로 묶기
                visual_groups = []  # [(source_clip_idx, total_duration, [scene_indices])]
                current_group_duration = 0.0
                current_group_scenes = []
                current_source_idx = 0
                
                for i, dur in enumerate(raw_durations):
                    current_group_duration += dur
                    current_group_scenes.append(i)
                    
                    # 그룹의 누적 시간이 최소 길이를 넘었거나 마지막 씬이면 그룹 확정
                    if current_group_duration >= MIN_CLIP_DURATION or i == len(raw_durations) - 1:
                        source_idx = current_source_idx % len(loaded_clips)
                        visual_groups.append((source_idx, current_group_duration, list(current_group_scenes)))
                        current_source_idx += 1
                        current_group_duration = 0.0
                        current_group_scenes = []
                
                logger.info(f"[클립 그룹핑] {len(narrations)}개 씬 → {len(visual_groups)}개 비주얼 그룹 (최소 {MIN_CLIP_DURATION}초 보장)")
                
                for group_source_idx, group_duration, scene_indices in visual_groups:
                    if sfx_clip_base:
                        sfx = sfx_clip_base.set_start(current_time)
                        audio_clips_to_mix.append(sfx)
                    
                    source_clip = loaded_clips[group_source_idx]
                    
                    # 원본 비디오 무음 및 길이 조절
                    if source_clip.duration < group_duration:
                         cut = source_clip.fx(vfx.loop, duration=group_duration).without_audio()
                    else:
                         cut = source_clip.subclip(0, group_duration).without_audio()
                         
                    # 9:16 (1080x1920) Scale-to-Fill & Center Crop
                    cw, ch = cut.size
                    target_w, target_h = 1080, 1920
                    
                    if (cw / ch) > (target_w / target_h):
                        cut = cut.resize(height=target_h)
                        cut = cut.crop(x_center=cut.size[0]/2, width=target_w)
                    else:
                        cut = cut.resize(width=target_w)
                        cut = cut.crop(y_center=cut.size[1]/2, height=target_h)
                        
                    # Dynamic Zoom (밋밋함 방지: 104%까지 서서히 줌인)
                    zoom_factor = 1.04
                    gd = group_duration  # 클로저 캡처용
                    cut = cut.resize(lambda t, d=gd: 1 + ((zoom_factor - 1) * (t / d)))
                    cut = cut.crop(x_center=cut.size[0]/2, y_center=cut.size[1]/2, width=target_w, height=target_h)
                    
                    bg_clips.append(cut)
                    current_time += group_duration
                    
                video_track = concatenate_videoclips(bg_clips, method="compose")
                video_track = video_track.subclip(0, target_duration)
            else:
                video_track = ColorClip(size=(1080, 1920), color=(50,50,50)).set_duration(target_duration)
        else:
            video_track = ColorClip(size=(1080, 1920), color=(50,50,50)).set_duration(target_duration)
            
        # [신규] 루프에서 모두 이미 1080x1440으로 맞춰졌으므로 이중 크롭할 필요 없음
        video_track = video_track.set_position(('center', 'center'))

        # 전체 1080x1920 어두운 캔버스 배경 설정
        canvas = ColorClip(size=(1080, 1920), color=(15, 15, 20)).set_duration(target_duration)
        
        # 상단 (제목 영역) 렌더링
        overlays = [video_track]
        
        def _load_title_font(size):
            try:
                return ImageFont.truetype("malgunbd.ttf", size)
            except IOError:
                try:
                    return ImageFont.truetype("malgun.ttf", size)
                except IOError:
                    return ImageFont.load_default()

        def create_title_image(text, width=1080, height=350):
            img = Image.new('RGBA', (width, height), (0,0,0,0))
            draw = ImageDraw.Draw(img)
            font = _load_title_font(90)
                    
            # Wrap text to fit
            words = text.split()
            lines = []
            curr_line = ""
            for w in words:
                test_line = curr_line + " " + w if curr_line else w
                if hasattr(draw, 'textlength'):
                    if draw.textlength(test_line, font=font) <= width - 40:
                        curr_line = test_line
                    else:
                        lines.append(curr_line)
                        curr_line = w
                else: 
                    # fallback
                    lw, _ = draw.textsize(test_line, font=font)
                    if lw <= width - 40:
                        curr_line = test_line
                    else:
                        lines.append(curr_line)
                        curr_line = w
                        
            if curr_line: lines.append(curr_line)
            
            y_offset = 20
            for line in lines:
                line_font = font
                if hasattr(draw, 'textlength'):
                    tw = draw.textlength(line, font=line_font)
                else:
                    tw, _ = draw.textsize(line, font=line_font)

                # 띄어쓰기 없는 긴 단어(종목명 등)가 폭을 넘으면 해당 줄만 폰트 축소
                if tw > width - 60 and tw > 0:
                    line_font = _load_title_font(max(48, int(90 * (width - 60) / tw)))
                    if hasattr(draw, 'textlength'):
                        tw = draw.textlength(line, font=line_font)
                    else:
                        tw, _ = draw.textsize(line, font=line_font)

                x = (width - tw) / 2

                # 상단 레이아웃 배너: 레퍼런스 스타일의 강렬하고 큰 테두리(Stroke)
                draw.text((x, y_offset), line, font=line_font, fill=(255, 240, 100), stroke_width=8, stroke_fill=(0, 0, 0))
                y_offset += 108
                
            return np.array(img)
            
        # 메인 훅(Hook)을 최상단 배너 영역으로 끌어올림
        title_np = create_title_image(topic, width=1080, height=500)
        title_clip = ImageClip(title_np).set_duration(target_duration)
        title_clip = title_clip.set_position(('center', 80)) # Y=80 최상단 밀착
        overlays.append(title_clip)

        bg_video_clip = canvas
        
        # 볼륨 조정 및 복합 오디오(BGM, SFX 포함) 세팅
        final_audio = CompositeAudioClip(audio_clips_to_mix)
        final_audio = final_audio.set_duration(target_duration)
        bg_video_clip = bg_video_clip.set_audio(final_audio)
        
        # 자막 로직 개편 (Dynamic Subtitles)
        from PIL import Image, ImageDraw, ImageFont
        import numpy as np
        from moviepy.editor import ImageClip
        import re
        
        # 문장을 짧은 어절로 분리
        def chunk_sentence_dynamic(sentence, chunk_size=2):  # Hormozi 스타일: 2단어 단위로 끊어 타격감 향상
            words = sentence.split()
            chunks = []
            for idx in range(0, len(words), chunk_size):
                chunk = " ".join(words[idx:idx+chunk_size])
                chunks.append(chunk)
            return chunks

        # [Tone & Manner 동기화] 영상 전체 톤을 하나로 묶어주는 시네마틱 컬러 필터 (Dark Navy 15% 코팅)
        cinematic_tint = ColorClip(size=(1080, 1920), color=(15, 23, 42)).set_opacity(0.15).set_duration(target_duration)
        cinematic_tint = cinematic_tint.set_position(('center', 'center'))
        overlays.append(cinematic_tint)

        # [Gemini 3.1 Pro 피드백 적용 3] 자막 가독성 확보용 다크 박스
        dim_box = ColorClip(size=(1080, 600), color=(0,0,0)).set_opacity(0.45).set_duration(target_duration)
        dim_box = dim_box.set_position(('center', 1000))
        overlays.append(dim_box)
                
        def _load_subtitle_font(size):
            try:
                return ImageFont.truetype("malgunbd.ttf", size)
            except IOError:
                try:
                    return ImageFont.truetype("malgun.ttf", size)
                except IOError:
                    return ImageFont.load_default()

        def create_text_image(text, width=1080, height=300):
            img = Image.new('RGBA', (width, height), (0,0,0,0))
            draw = ImageDraw.Draw(img)

            font_size = 81  # 1080p 해상도에 맞춰 1.5배 확대
            font = _load_subtitle_font(font_size)

            line = text.strip()

            def _measure(f):
                if hasattr(draw, 'textbbox'):
                    bbox = draw.textbbox((0, 0), line, font=f)
                    return bbox[2] - bbox[0], bbox[3] - bbox[1]
                return draw.textsize(line, font=f)

            lw, lh = _measure(font)

            # 좌우 잘림 방지: 텍스트가 안전폭(양쪽 여백 40px + stroke 여유)을 넘으면 폰트 축소
            safe_width = width - 80
            if lw > safe_width and lw > 0:
                font_size = max(40, int(font_size * safe_width / lw))
                font = _load_subtitle_font(font_size)
                lw, lh = _measure(font)
                
            x = (width - lw) / 2
            y = (height - lh) / 2
            
            words = line.split(" ")
            current_x = x
            
            highlight_keywords = ["테슬라", "애플", "엔비디아", "삼전", "돈방석", "폭락", "상승", "폭등", "주가", "최대", "비밀", "머스크", "AI"]
            
            for word in words:
                word_color = (255, 255, 255) # White
                if any(char.isdigit() for char in word):
                    word_color = (255, 255, 0) # Yellow for numbers
                elif any(kw in word for kw in highlight_keywords):
                    word_color = (255, 0, 0) # Red for highlight keywords
                    
                # 메인 텍스트 + Native PIL Stroke 적용으로 폰트 깨짐 완벽 해결 (Task 2)
                draw.text((current_x, y), word, font=font, fill=word_color, stroke_width=4, stroke_fill=(0, 0, 0))
                
                if hasattr(draw, 'textlength'):
                    word_w = draw.textlength(word + " ", font=font)
                elif hasattr(draw, 'textbbox'):
                    bbox = draw.textbbox((0, 0), word + " ", font=font)
                    word_w = bbox[2] - bbox[0]
                else:
                    word_w, _ = draw.textsize(word + " ", font=font)
                    
                current_x += word_w
                
            return np.array(img)

        subtitles = []
        current_time = 0.0
        
        for i, narration in enumerate(narrations):
            scene_duration = (len(narration) / total_chars) * target_duration
            
            chunks = chunk_sentence_dynamic(narration, chunk_size=3)
            total_scene_chars = sum(len(c) for c in chunks)
            if total_scene_chars == 0: total_scene_chars = 1
            
            scene_current_time = current_time
            
            for chunk in chunks:
                chunk_duration = (len(chunk) / total_scene_chars) * scene_duration
                start_time = scene_current_time
                end_time = scene_current_time + chunk_duration
                
                text_np_array = create_text_image(chunk, width=1080, height=300)
                txt_clip = ImageClip(text_np_array).set_duration(end_time - start_time)
                # 하위 3분할(자막 영역)의 Safe Zone: 1150 (모바일 UI에 가리지 않는 유튜브 쇼츠 안전구역)
                txt_clip = txt_clip.set_position(('center', 1150)).set_start(start_time)
                
                subtitles.append(txt_clip)
                scene_current_time = end_time
                
            current_time += scene_duration

        # 워터마크 방어용 로고 오버레이
        # Brand Analyzer2.png 로고 적용. 화면의 최상단 우측에 여백 30을 두고 배치합니다.
        logo_path = os.path.join(ASSETS_DIR, "Brand Analyzer2.png")
        if not os.path.exists(logo_path):
            logo_path = os.path.join(BASE_DIR, "Brand Analyzer2.png") # 최상단 경로 호환성 추가
            
        if os.path.exists(logo_path):
            try:
                logo_clip = ImageClip(logo_path).resize(width=150).set_duration(target_duration)
                logo_y = 50 # 상단에서 50px 아래
                logo_clip = logo_clip.set_position((1080 - 150 - 30, logo_y)).set_start(0)
                overlays.append(logo_clip)
            except Exception as e:
                logger.warning(f"로고 오버레이 실패: {e}")
                
        final_video = CompositeVideoClip([bg_video_clip] + overlays + subtitles)
        
        # 마지막 까만 화면 방지: 음성 길이에 맞춰 비디오 정확히 자르기
        final_video = final_video.set_duration(target_duration)
        
        timestamp = int(time.time())
        final_filename = f"shorts_{timestamp}.mp4"
        save_dir = output_dir if output_dir else ASSETS_DIR
        final_path = os.path.join(save_dir, final_filename)
        
        # 렌더링 최적화 - 해상도 720p, ultrafast 프리셋
        logger.info("비디오 인코딩 시작 (빠른 프리셋)")
        if is_draft_mode:
            logger.info("Draft 모드 활성화: 해상도 360x640 비율로 강제 축소, FPS 12 (초고속 렌더링)")
            final_video = final_video.resize(height=640)
            final_video.write_videofile(final_path, fps=12, codec="libx264", audio_codec="aac", preset="ultrafast", threads=4, logger=None)
        else:
            final_video.write_videofile(final_path, fps=24, codec="libx264", audio_codec="aac", preset="ultrafast", threads=4, logger=None)
        logger.info("비디오 인코딩 완료")
        
        # 썸네일 브랜딩 추출 (1.5초 시점 프레임 캡처)
        thumb_filename = f"thumbnail_{timestamp}.jpg"
        thumb_path = os.path.join(save_dir, thumb_filename)
        try:
            final_video.save_frame(thumb_path, t=1.5)
            logger.info(f"썸네일 캡처 완료: {thumb_filename}")
        except Exception as e:
            logger.warning(f"썸네일 캡처 실패: {e}")
            thumb_filename = ""
            
        rel_dir = os.path.relpath(save_dir, BASE_DIR).replace('\\', '/')
        return {
            "video_url": f"/{rel_dir}/{final_filename}",
            "thumbnail_url": f"/{rel_dir}/{thumb_filename}" if thumb_filename else None
        }
        
    # 복잡한 Moviepy 렌더링은 블로킹 작업이므로 스레드에서 실행
    return await asyncio.to_thread(_moviepy_assemble)

async def step_4_automation_pipeline(job_id: str, topic: str):
    """메인 자동화 파이프라인 - Step별 타이밍 기록 + 실시간 로그 축적"""
    
    def _log(msg, log_type="info"):
        t = time.strftime("%H:%M:%S")
        jobs[job_id].setdefault("live_logs", []).append({"time": t, "text": msg, "type": log_type})
        logger.info(msg)
    
    def _start_step(step_num, label):
        jobs[job_id]["current_step"] = step_num
        jobs[job_id].setdefault("step_timings", {})[str(step_num)] = {"label": label, "start": time.time(), "elapsed": None}
        _log(f"▶ STEP {step_num} 시작: {label}")
    
    def _end_step(step_num):
        timings = jobs[job_id].get("step_timings", {}).get(str(step_num))
        if timings and timings.get("start"):
            elapsed = round(time.time() - timings["start"], 1)
            timings["elapsed"] = elapsed
            _log(f"✅ STEP {step_num} 완료 ({elapsed}초)", "success")
    
    avg_timings = _load_avg_timings()
    jobs[job_id]["avg_timings"] = avg_timings
    jobs[job_id]["live_logs"] = []
    jobs[job_id]["step_timings"] = {}
    jobs[job_id]["current_step"] = 0
    jobs[job_id]["is_running"] = True
    
    try:
        # ===== STEP 1: 주제 선정 및 리서치 =====
        _start_step(1, "주제 선정 및 리서치")
        import data_pipeline
        prompt_topic = topic
        chart_path = ""
        if topic.lower() == "tenbagger":
            # 텐배거 헌터 연동: 추천 종목 + 재무 근거를 주제/문맥으로 사용
            _log("텐배거 추천 종목 조회 중...")
            jobs[job_id].update({"status": "Step 1: 텐배거 종목 선정 중...", "progress": 5})
            history_topics = [item['topic'] for item in load_history()]
            prompt_topic, context_data = await pick_tenbagger_topic(exclude_topics=history_topics)
            _log(f"주제 확정: {prompt_topic}")
        elif topic.lower() == "history":
            # 과거 텐배거 해부: 백테스트 + 검색량 트렌드 기반
            _log("과거 텐배거 종목 선정 + 백테스트 조회 중...")
            jobs[job_id].update({"status": "Step 1: 과거 텐배거 백테스트 중...", "progress": 5})
            history_topics = [item['topic'] for item in load_history()]
            chart_dir = os.path.join(ASSETS_DIR, job_id)
            os.makedirs(chart_dir, exist_ok=True)
            prompt_topic, context_data, chart_path = await pick_history_topic(
                exclude_topics=history_topics, chart_dir=chart_dir
            )
            _log(f"주제 확정: {prompt_topic}" + (" (검색량 차트 포함)" if chart_path else ""))
        else:
            if topic.lower() == "auto":
                _log("트렌드 티커 자동 수집 중...")
                jobs[job_id].update({"status": "Step 1: 트렌드 수집 중...", "progress": 5})
                trends = data_pipeline.get_trending_topics()
                prompt_topic = trends[0] if trends else "미국 주식 시장 트렌드"

            _log(f"주제 확정: {prompt_topic}")
            jobs[job_id].update({"status": f"Step 1: {prompt_topic} 팩트 검색 중...", "progress": 10})
            facts = data_pipeline.fetch_facts_for_topic(prompt_topic)

            _log("NotebookLM 딥 리서치 인사이트 추출 중...")
            jobs[job_id].update({"status": "Step 1: NotebookLM 딥 리서치 중...", "progress": 15})
            notebooklm_insights = await query_notebooklm(prompt_topic)

            context_data = f"[트렌드 주제]: {prompt_topic}\n[단순 팩트/뉴스]: {facts}\n[🔥 NotebookLM 심층 분석 인사이트]: {notebooklm_insights}"
        _end_step(1)
        
        # ===== STEP 2: AI 대본 생성 =====
        _start_step(2, "AI 대본 생성")
        jobs[job_id].update({"status": "Step 2: AI Scene 단위 대본 작성 중...", "progress": 25})
        
        parsed_result = await call_openai_script(prompt_topic, context_data)
        
        if isinstance(parsed_result, dict) and "scenes" in parsed_result and len(parsed_result["scenes"]) > 0:
            target_company = parsed_result.get("target_company", "")
            first_scene = parsed_result["scenes"][0]
            first_narration = first_scene.get("narration", "")
            if target_company and target_company not in first_narration:
                for pronoun in ["이 주식은", "이 기업은", "이 회사는", "이 종목은", "이 주식", "이 기업", "이 회사", "이 종목"]:
                    if pronoun in first_narration:
                        first_narration = first_narration.replace(pronoun, f"{target_company}는(은)")
                first_scene["narration"] = first_narration
        
        if isinstance(parsed_result, dict) and "scenes" in parsed_result:
            top_title = parsed_result.get("top_title", prompt_topic)
            script_data = parsed_result["scenes"]
        else:
            top_title = prompt_topic
            script_data = parsed_result

        # history 모드: source_type 'chart' Scene에 미리 생성한 검색량 차트 연결
        if chart_path and isinstance(script_data, list):
            for scene in script_data:
                if scene.get("source_type") == "chart":
                    scene["chart_path"] = chart_path
                    break

        alignment_result = await evaluate_script(script_data)
        eval_score = alignment_result.get('final_score', 0)
        _log(f"대본 평가 점수: {eval_score}점 ({'통과' if alignment_result['pass'] else '미달(강제 진행)'})")
        
        display_script = f"# 제목: {top_title}\n\n" + "\n\n".join([f"[SCENE {scene.get('scene_num', i+1)}]\nSEARCH: {scene.get('search', '')}\nNARRATION: {scene.get('narration', '')}" for i, scene in enumerate(script_data)])
        jobs[job_id]["script"] = display_script
        _end_step(2)
        
        # ===== STEP 3: B-roll 영상 다운로드 =====
        _start_step(3, "B-roll 영상 다운로드")
        job_dir = os.path.join(ASSETS_DIR, job_id)
        os.makedirs(job_dir, exist_ok=True)
        
        jobs[job_id].update({"status": "Step 3: 하이브리드 비전 매칭 처리 중...", "progress": 40})
        _log(f"{len(script_data)}개 씬의 B-roll 영상 검색 및 다운로드 시작")
        video_local_paths = await step_2_download_background_video(script_data, output_dir=job_dir)
        _log(f"{len(video_local_paths)}개 영상 다운로드 완료")
        _end_step(3)
        
        # ===== STEP 4: TTS 음성 생성 =====
        _start_step(4, "TTS 음성 생성")
        jobs[job_id].update({"status": "Step 4: 나레이션 오디오 추출 중 (TTS)...", "progress": 65})
        audio_local_path, narrations = await step_3_audio_and_edit(script_data, output_dir=job_dir)
        _end_step(4)
        
        # ===== STEP 5: 영상 렌더링 및 자막 합성 =====
        _start_step(5, "영상 렌더링 및 자막 합성")
        jobs[job_id].update({"status": "Step 5: 비디오 인코딩 및 자막 합성 중...", "progress": 80})
        if audio_local_path:
            final_result = await step_4_assemble_video(video_local_paths, audio_local_path, narrations, top_title, output_dir=job_dir)
            jobs[job_id]["video_url"] = final_result.get("video_url")
            jobs[job_id]["thumbnail_url"] = final_result.get("thumbnail_url")
            _log(f"영상 렌더링 완료: {final_result.get('video_url')}", "success")
            _end_step(5)
            jobs[job_id].update({"status": "Completed: 숏츠 생성 완료!", "progress": 100})
        else:
            _log("음성 생성 실패로 비디오를 만들 수 없습니다.", "warning")
            jobs[job_id].update({"status": "Failed: 음성 생성 실패", "progress": 100})
            _end_step(5)
        
        # 텐배거/과거해부 모드: 성공한 종목을 히스토리에 기록해 3일간 같은 종목 반복 방지
        if topic.lower() in ("tenbagger", "history") and str(jobs[job_id].get("status", "")).startswith("Completed"):
            save_history(prompt_topic)

        _save_step_timings(jobs[job_id].get("step_timings", {}))
        jobs[job_id]["is_running"] = False
        _log("전체 파이프라인 완료!", "success")
        
    except Exception as e:
        logger.error(f"파이프라인 에러: {e}")
        jobs[job_id].setdefault("live_logs", []).append({"time": time.strftime("%H:%M:%S"), "text": f"❌ 파이프라인 에러: {str(e)}", "type": "warning"})
        jobs[job_id]["status"] = f"Failed: {str(e)}"
        jobs[job_id]["is_running"] = False

STEP_TIMINGS_FILE = os.path.join("data", "step_timings.json")

def _load_avg_timings():
    if not os.path.exists(STEP_TIMINGS_FILE):
        return {}
    try:
        with open(STEP_TIMINGS_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)
        if not history: return {}
        avgs = {}
        for step_key in ["1","2","3","4","5"]:
            times = [run[step_key]["elapsed"] for run in history if step_key in run and run[step_key].get("elapsed")]
            if times:
                avgs[step_key] = round(sum(times) / len(times), 1)
        return avgs
    except Exception:
        return {}

def _save_step_timings(step_timings: dict):
    history = []
    if os.path.exists(STEP_TIMINGS_FILE):
        try:
            with open(STEP_TIMINGS_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = []
    history.append(step_timings)
    if len(history) > 20:
        history = history[-20:]
    try:
        with open(STEP_TIMINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Step 타이밍 저장 에러: {e}")

# --- 일일 히스토리 및 자동 트렌드 분석 ---
HISTORY_FILE = "history.json"

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_history(topic):
    history = load_history()
    # 3일(72시간) 이내 기록만 유지
    cutoff = time.time() - (3 * 24 * 3600)
    history = [item for item in history if item.get('timestamp', 0) > cutoff]
    history.append({"topic": topic, "timestamp": time.time()})
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

async def pick_auto_topic() -> tuple[str, str]:
    """트렌드 스크래퍼를 실행하고, LLM을 통해 히스토리와 겹치지 않는 바이럴 영상 1개를 선정하여 (주제, URL) 튜플을 반환합니다."""
    FALLBACK_TOPICS = [
        "애플이 몰래 준비 중인 2026년 비밀 프로젝트",
        "워렌 버핏이 현금을 100조나 쌓아둔 진짜 이유",
        "테슬라 로봇택시가 불러올 끔찍한 나비효과",
        "엔비디아 젠슨 황이 끝까지 주식을 안 파는 이유",
        "비트코인 2억 돌파 직전, 고래들의 은밀한 움직임",
        "폭락장에도 기관들이 쓸어담는 단 하나의 섹터",
        "월가 전설들이 지금 당장 도망치라고 경고한 자산",
        "AI 혁명의 진짜 수혜주, 시장이 완전히 속았다",
        "반도체 슈퍼사이클? 다음 타자는 이 기업입니다",
        "결국 승리한 구글, 판을 뒤집어버린 충격 발표",
        "부자들은 이미 돈을 다 빼고 있는 충격적인 데이터",
        "일론 머스크의 10년 후 마스터플랜, 드디어 공개",
        "제2의 테슬라, 10배 오르기 직전 조용한 매집",
        "미국 금리 인하에 환호하면 무조건 물리는 이유",
        "상위 1%만 알고 있는 2025년 최악의 투자처"
    ]
    
    history_topics = [item['topic'] for item in load_history()]
    
    def get_random_fallback():
        import random
        available = [t for t in FALLBACK_TOPICS if t not in history_topics]
        return random.choice(available) if available else random.choice(FALLBACK_TOPICS)

    # 트렌드 스크랩 실행
    import subprocess
    try:
        await asyncio.to_thread(subprocess.run, ["python", "trend_scraper.py"], capture_output=True, text=True)
        with open("scraped_videos.json", "r", encoding="utf-8") as f:
            recent_videos = json.load(f)
    except Exception as e:
        logger.error(f"트렌드 분석 실패: {e}")
        return (get_random_fallback(), "")
    
    # Send a simplified list of titles to the LLM (Filter out previously used topics)
    video_titles = [v["title"] for v in recent_videos if v["title"] not in history_topics]
    
    system_prompt = """
    You are the Head Producer for a Top-Tier Business, Finance, and Tech YouTube Shorts channel.
    I will give you a list of recent trending YouTube video titles. 
    [CRITICAL RULES]
    1. You must scan the list and ONLY select a title if it is strictly related to: Global Business, Stock Market, Mega Corporations (Apple, Tesla, Nvidia etc.), AI Tech trends, or Macroeconomics.
    2. If the list contains ONLY garbage for our niche (e.g., vlogs, hiking, cooking, entertainment, daily news, sports), you MUST IGNORE the entire list.
    3. If you ignore the list, you must invent a highly viral, fresh Business/Tech topic yourself (e.g., "2026 Apple's shocking secret project").
    4. Output ONLY the exact chosen title or your newly invented title, with no other text.
    """
    
    user_query = f"Trending titles: {json.dumps(video_titles, ensure_ascii=False)}\nPast 3-days topics: {history_topics}\nViral Title:"
    
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query}
        ],
        "temperature": 0.7
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, headers=headers, json=payload, timeout=30.0)
            response.raise_for_status()
            chosen_title = response.json()['choices'][0]['message']['content'].strip().strip('"').strip("'")
            
            # Find the corresponding URL
            chosen_url = "" # If invented, url stays empty
            for v in recent_videos:
                if v["title"] in chosen_title or chosen_title in v["title"]:
                    chosen_url = v["url"]
                    chosen_title = v["title"]
                    break
                    
            return (chosen_title, chosen_url)
        except Exception as e:
            logger.error(f"주제 선정 LLM 에러: {e}")
            return (get_random_fallback(), "")

# --- 웹 프론트엔드 (UI) - dashboard.html 파일 서빙 ---
@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    dashboard_path = os.path.join(BASE_DIR, "dashboard.html")
    if os.path.exists(dashboard_path):
        with open(dashboard_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>dashboard.html not found</h1>"




@app.post("/api/pipeline/start")
async def create_shorts(request_data: ShortsRequest, background_tasks: BackgroundTasks):
    topic = request_data.topic
    
    # 히스토리 기록
    save_history(topic)
    
    job_id = f"job_{int(time.time())}"
    jobs[job_id] = {"status": "Queued", "topic": topic, "progress": 0}
    
    background_tasks.add_task(step_4_automation_pipeline, job_id, topic)
    return {"job_id": job_id, "message": "작업 시작"}

@app.post("/api/pipeline/auto_start")
async def create_shorts_auto(background_tasks: BackgroundTasks):
    job_id = f"job_auto_{int(time.time())}"
    jobs[job_id] = {"status": "트렌드 스크래핑 및 영상 분석 준비 중...", "topic": "AI가 주제를 선정 중입니다.", "progress": 5}
    
    async def run_auto_pipeline():
        try:
            topic, url = await pick_auto_topic()
            jobs[job_id]["topic"] = topic
            save_history(topic)
            
            if url:
                jobs[job_id].update({"status": f"선정된 트렌드 유튜브 영상({url})을 NotebookLM에 원문 주입 중...", "progress": 15})
                await inject_source_to_notebooklm(url)
            else:
                jobs[job_id].update({"status": f"트렌드 대신 새 주제({topic}) 창작. 소스 주입을 건너뛰고 대본 생성을 시작합니다...", "progress": 15})
            
            await step_4_automation_pipeline(job_id, topic)
        except Exception as e:
            logger.error(f"자동화 파이프라인 에러: {e}")
            jobs[job_id].update({"status": f"Failed: {str(e)}", "progress": 100})
            
    background_tasks.add_task(run_auto_pipeline)
    return {"job_id": job_id, "message": "자동 스크래핑 기반 작업 시작"}

@app.get("/api/pipeline/status")
async def get_status_query(job_id: Optional[str] = None):
    if not job_id:
        if not jobs:
            return {"job_id": "none", "status": "Waiting for input...", "video_url": None, "script": None}
        latest_job_id = list(jobs.keys())[-1]
        return {**jobs[latest_job_id], "job_id": latest_job_id}
        
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return {**jobs[job_id], "job_id": job_id}

@app.get("/api/pipeline/status/{job_id}")
async def get_status_path(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return {**jobs[job_id], "job_id": job_id}

@app.get("/api/health")
async def health_check():
    return {"status": "ok", "message": "Server is running smoothly"}

async def auto_loop():
    """2시간마다 자동으로 파이프라인 -> 메트릭 평가 -> 프롬프트 진화를 반복하는 무한 루프"""
    logger.info("백그라운드 자가 진화 파이프라인(auto_loop)이 가동됩니다.")
    while True:
        try:
            job_id = f"auto_loop_{int(time.time())}"
            jobs[job_id] = {"status": "무한 루프: 영상 생성을 시작합니다", "topic": "auto"}
            
            # 단계 1: 영상 생성 시도
            final_video_path = await step_4_automation_pipeline(job_id, "auto")
            
            if jobs[job_id]["status"].startswith("Rejected"):
                logger.warning("오토루프: 대본 퀄리티 미달로 생성을 건너뜁니다. 5분 뒤 다시 시도합니다.")
                await asyncio.sleep(300)
                continue

            # (TODO: 유튜브 업로드 API 호출 기능 추가 요망)
            # 단계 2: 업로드 완료를 가정하고 metrics 수집
            video_id = "test_auto_id"
            logger.info("업로드 처리 완료. 메트릭을 수집합니다.")
            
            metrics = await get_video_metrics(video_id)
            await save_metrics(metrics)

            # 단계 3: 프롬프트 자가 진화 (Engagement Rate 분석)
            logger.info("성과 분석 및 프롬프트 규칙 진화 시작...")
            await evolve_prompt()

        except Exception as e:
            logger.error(f"오토 루프 수행 중 에러 발생: {e}")

        # 다음 주기까지 2시간(7200초) 대기
        logger.info("한 사이클(생성->수집->진화)을 완료했습니다. 2시간 대기합니다.")
        await asyncio.sleep(7200)

@app.on_event("startup")
async def startup_event():
    import asyncio
    # 백그라운드 자동 생성 비활성화 (테스트 시 리소스 낭비 방지)
    # asyncio.create_task(auto_loop())
    pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)