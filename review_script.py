import os
import sys
import glob
import time
import google.generativeai as genai
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding='utf-8')

load_dotenv()

# Verify API key
if "GEMINI_API_KEY" not in os.environ:
    print("Error: GEMINI_API_KEY is not set in .env")
    exit(1)

genai.configure(api_key=os.environ["GEMINI_API_KEY"])

assets_dir = r"C:\Users\00LG00\Desktop\aiva_server\assets"
print("Waiting for final_shorts to complete rendering (Checking for moov atom via thumbnail presence)...")

target_video = None
for _ in range(60): # Wait up to 10 minutes
    mp4_files = glob.glob(os.path.join(assets_dir, "shorts_*.mp4"))
    # Sort files by modification time
    mp4_files = sorted([f for f in mp4_files if os.path.getsize(f) > 1000000], key=os.path.getmtime, reverse=True)
    
    if mp4_files:
        latest = mp4_files[0]
        # Check if the thumbnail exists (indicating moviepy finished the pipeline)
        timestamp = os.path.basename(latest).split('_')[1].split('.')[0]
        if os.path.exists(os.path.join(assets_dir, f"thumbnail_{timestamp}.jpg")):
            target_video = latest
            break
    time.sleep(10)

if not target_video:
    print("No completed shorts found within timeout.")
    exit(0)
print(f"🎬 Uploading {os.path.basename(target_video)} to Gemini 1.5 Pro Vision API...")

try:
    print("📸 Extracting keyframes for Gemini 1.5 Pro Multimodal Vision...")
    from moviepy.editor import VideoFileClip
    from PIL import Image
    
    clip = VideoFileClip(target_video)
    dur = clip.duration
    frames = []
    
    for i, pct in enumerate([0.1, 0.3, 0.5, 0.7, 0.9]):
        t = dur * pct
        img_path = os.path.join(assets_dir, f"tmp_gemini_frame_{i}.jpg")
        clip.save_frame(img_path, t=t)
        frames.append(Image.open(img_path))
        
    clip.close()
        
    print("🧠 Keyframes acquired. Generating 1M-View PD Review Report...")
    
    prompt = """
    당신은 100만 뷰 유튜브 채널을 총괄하는 엄격한 영상 편집 PD입니다. 
    첨부된 5장의 숏츠 연속 프레임 화면을 분석하고, 다음 항목들을 100점 만점으로 가차 없이 평가하세요:
    1. 시각적 정보와 나레이션/자막의 일치성 (B-roll 매칭 오류 없이 자연스러운가?)
    2. 다크 네이비 배너 레이아웃(위아래 여백) 및 3:4 꽉 찬 컷의 시인성
    3. 빠른 전개와 시각적 텐션
    4. 최종 총평 및 필수 개선점 1줄 요약
    
    출력은 마크다운 포맷으로 가독성 좋고 신랄하게 작성해주세요.
    """
    
    model = genai.GenerativeModel("gemini-3.1-pro-preview")
    
    # Pass array of images + prompt
    response = model.generate_content(
        frames + [prompt],
        request_options={"timeout": 600}
    )
    
    review_text = response.text
    
    print("💡 Asking Gemini for an actionable improvement guide based on the review...")
    follow_up_prompt = f"다음은 당신이 방금 작성한 숏츠 영상에 대한 PD 리뷰입니다:\n\n{review_text}\n\n이 리뷰에서 지적된 문제점들을 시스템 파이프라인에서 완벽하게 해결하기 위해, 다음에 생성 봇이 대본(JSON)을 쓰거나 '유튜브 B-roll 검색 키워드(search 필드)'를 작성할 때 구체적으로 어떻게 시스템 프롬프트를 보강해야 하는지 당장 소스코드에 적용 가능한 '프롬프트 엔지니어링 튜닝 가이드' 3가지를 명쾌하게 도출해주세요."
    
    advice_response = model.generate_content(
        follow_up_prompt,
        request_options={"timeout": 600}
    )
    
    report_text = f"# 🤖 Gemini 3.1 Pro AI Automated PD Review\n\n## Analyzed File: {os.path.basename(target_video)}\n\n{review_text}\n\n---\n\n## 🛠️ 향후 개선을 위한 액션 플랜 (Actionable Advice)\n\n{advice_response.text}"
    
    report_path = os.path.join(assets_dir, "gemini_review_report.md")
    with open(report_path, "w", encoding="utf-8") as rf:
        rf.write(report_text)
        
    print(f"\n✅ Review Report & Action Plan generated: {report_path}")
    print("\n" + "="*50 + "\n")
    print(report_text)
    print("\n" + "="*50 + "\n")
    
    # Cleanup tmp frames
    for i in range(5):
        try: os.remove(os.path.join(assets_dir, f"tmp_gemini_frame_{i}.jpg"))
        except: pass

    
except Exception as e:
    print(f"❌ Error during Gemini evaluation: {e}")
