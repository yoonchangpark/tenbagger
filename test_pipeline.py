import asyncio
import logging
import json
import os
import sys

# Windows 환경에 맞게 강제 변경
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

from server import call_openai_script, step_2_download_background_video

# 로깅 레벨 설정 (서버 로그와 동일하게)
logging.basicConfig(level=logging.INFO)

async def test_pipeline():
    # 1. 가상의 테스트 주제와 문맥 데이터
    topic = "2026년 일론 머스크 화성 진출과 테슬라의 미친 계획"
    context = "SpaceX가 2026년에 화성 식민지 개척을 위한 초대형 로켓을 발사할 예정입니다. 동시에 테슬라의 로보택시가 지구의 교통을 완전히 장악한다는 최신 뉴스 및 투자 리포트가 발표되었습니다."
    
    print("="*60)
    print("🚀 [Step 1] 대본 및 B-roll 검색어 생성 시작 (GPT-4o)")
    print("="*60)
    
    import time
    job_id = f"test_pick_{int(time.time())}"
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", job_id)
    os.makedirs(output_dir, exist_ok=True)
    
    script_data = await call_openai_script(topic, context)
    
    print(f"\n✅ 생성이 완료된 대본 ({output_dir}/script.json 에도 저장됨):")
    print(json.dumps(script_data, indent=2, ensure_ascii=False))
    
    with open(os.path.join(output_dir, "script.json"), "w", encoding="utf-8") as f:
        json.dump(script_data, f, indent=2, ensure_ascii=False)
        
    scenes = script_data.get("scenes", [])
    if not scenes:
        print("❌ 씬 정보가 없어 다운로드를 중단합니다.")
        return

    print("\n" + "="*60)
    print("🎬 [Step 2] Hybrid Vision Thumbnail Picker 다운로드 시작")
    print("="*60)
    
    # 서버의 신규 다운로드 및 1회 심사 모듈 호출
    downloaded_paths = await step_2_download_background_video(scenes, output_dir=output_dir)
    
    print("\n✅ [테스트 성공] 최종 영상들이 어셋 폴더에 성공적으로 저장되었습니다!")
    print("저장된 경로:")
    for path in downloaded_paths:
        print(f" 📍 {path}")
        
    print(f"\n지금 {output_dir} 폴더를 확인해보세요! 고유한 폴더에 대본과 다운로드된 영상들만 격리되어 저장되었습니다.")

if __name__ == "__main__":
    import os
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(test_pipeline())
