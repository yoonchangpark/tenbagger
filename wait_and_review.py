import os, time, glob
from moviepy.editor import VideoFileClip

assets_dir = r"C:\Users\00LG00\Desktop\aiva_server\assets"

print("Polling for video completion...")
while True:
    # Get the latest shorts video
    mp4_files = glob.glob(os.path.join(assets_dir, "shorts_*.mp4"))
    # Filter only recently created large videos
    mp4_files = sorted([f for f in mp4_files if os.path.getsize(f) > 5000000], key=os.path.getmtime, reverse=True)
    if not mp4_files:
        time.sleep(10)
        continue
        
    latest_video = mp4_files[0]
    timestamp = latest_video.split('_')[-1].split('.')[0]
    thumbnail_path = os.path.join(assets_dir, f"thumbnail_{timestamp}.jpg")
    
    # 썸네일 파일이 생성되었다면 렌더링이 100% 완료된 것
    if os.path.exists(thumbnail_path):
        print(f"Video {latest_video} is complete! Extracting frames...")
        try:
            clip = VideoFileClip(latest_video)
            dur = clip.duration
            print(f"Duration: {dur}s")
            for pct in [0.05, 0.4, 0.7, 0.9]:
                t = dur * pct
                out_path = os.path.join(assets_dir, f"review_pct_{int(pct*100)}.jpg")
                clip.save_frame(out_path, t=t)
                print(f"Saved {out_path}")
            clip.close()
            print("EXTRACTION_SUCCESS")
            break
        except Exception as e:
            print(f"Extraction failed: {e}")
            break
    else:
        # 아직 렌더링 중이므로 대기
        time.sleep(10)
