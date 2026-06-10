import asyncio
import logging
import json
import os
import sys

# Windows 환경에 맞게 강제 변경
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

from server import call_openai_script, step_2_download_background_video, step_3_audio_and_edit, step_4_assemble_video, query_notebooklm

# 로깅 레벨 설정
logging.basicConfig(level=logging.INFO)

async def test_full_draft_pipeline():
    # 1. 낯선 주제 (편향성 방지)
    topic = "미국 비만 치료제 시장의 폭발적인 성장과 위고비의 미래"
    
    print("="*60)
    print("📚 [Step 0] NotebookLM 딥 리서치 엔진 가동")
    print("="*60)
    
    # 노트북LM 인사이트 추출 연동
    notebooklm_insights = await query_notebooklm(topic)
    print(f"✅ 추출된 NotebookLM 인사이트 일부: {notebooklm_insights[:150]}...")
    
    context = f"[트렌드 주제]: {topic}\n[🔥 NotebookLM 심층 분석 인사이트]: {notebooklm_insights}"
    
    print("\n" + "="*60)
    print("🚀 [Step 1] 대본 및 B-roll 검색어 생성 시작")
    print("="*60)
    
    import time
    job_id = f"test_draft_{int(time.time())}"
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", job_id)
    os.makedirs(output_dir, exist_ok=True)
    
    print(f"📁 고유 테스트 폴더가 생성되었습니다: {output_dir}")
    
    script_data = await call_openai_script(topic, context)
    scenes = script_data.get("scenes", [])
    if not scenes:
        print("❌ 대본 생성이 실패했습니다.")
        return
        
    with open(os.path.join(output_dir, "script.json"), "w", encoding="utf-8") as f:
        json.dump(script_data, f, indent=2, ensure_ascii=False)

    print("\n" + "="*60)
    print("🎬 [Step 2] Hybrid Vision Thumbnail Picker 다운로드 시작")
    print("="*60)
    
    video_paths = await step_2_download_background_video(scenes, output_dir=output_dir)
    if not video_paths:
        print("❌ 비디오 다운로드 실패")
        return

    print("\n" + "="*60)
    print("🎙️ [Step 3] 무료 TTS 강제 생성 (Draft 모드)")
    print("="*60)
    
    # force_free_tts=True 로 과금 방지 및 gTTS 강제 우회
    audio_path, narrations = await step_3_audio_and_edit(script_data, force_free_tts=True, output_dir=output_dir)
    if not audio_path:
        print("❌ 오디오 생성 실패")
        return

    print("\n" + "="*60)
    print("✂️ [Step 4] 초고속 Draft 렌더링 시작 (360p, 12fps)")
    print("="*60)
    
    # is_draft_mode=True 명시로 해상도 비율 축소 및 프레임 속도 저하
    result = await step_4_assemble_video(video_paths, audio_path, narrations, topic, is_draft_mode=True, output_dir=output_dir)
    
    print("\n✅ [테스트 완벽 성공!] 초안(Draft) 영상이 렌더링 되었습니다!")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"빠르게 렌더링된 요소들과 숏츠 파일을 {output_dir} 폴더에서 직접 확인해보세요.")

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(test_full_draft_pipeline())
