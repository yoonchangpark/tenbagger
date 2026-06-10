import os
import sys
import glob
import google.generativeai as genai
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv()
genai.configure(api_key=os.environ["GEMINI_API_KEY"])

print("Listing all available models for current API Key:")
for m in genai.list_models():
    print(m.name)

