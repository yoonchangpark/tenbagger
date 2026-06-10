import os
import sys
import google.generativeai as genai
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv()
genai.configure(api_key=os.environ["GEMINI_API_KEY"])

# Read existing report
assets_dir = r"C:\Users\00LG00\Desktop\aiva_server\assets"
report_path = os.path.join(assets_dir, "gemini_review_report.md")

if not os.path.exists(report_path):
    print("No review report found.")
    exit()

with open(report_path, "r", encoding="utf-8") as f:
    review_text = f.read()

model = genai.GenerativeModel("gemini-3.1-pro-preview")
follow_up_prompt = f"다음은 당신이 방금 작성한 숏츠 영상에 대한 PD 리뷰입니다:\n\n{review_text}\n\n이 리뷰에서 지적된 문제점들을 완벽하게 해결하기 위해, 파이썬 서버 코드에서 B-roll 검색 키워드 프롬프트 배열을 어떻게 강화해야 좋을지, 3가지 개선 가이드라인을 작성해주세요."

print("Generating improvements...")
res = model.generate_content(follow_up_prompt)

with open(report_path, "a", encoding="utf-8") as f:
    f.write("\n\n---\n\n## 🛠️ 향후 개선을 위한 액션 플랜 (Actionable Advice)\n\n" + res.text)
    
print("====== AI ACTION PLAN ======")
print(res.text)
