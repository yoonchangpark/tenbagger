import codecs

with codecs.open('c:/Users/00LG00/Desktop/aiva_server/server.py', 'r', 'utf-8') as f:
    text = f.read()
    
# Find call_gemini_script def
start_idx = text.find('async def call_gemini_script')
end_idx = text.find('async def step_2_download_background_video')

new_func = """async def call_openai_script(topic: str, context: str):
    \"\"\"(비동기) OpenAI API를 호출하여 Scene 단위 구조화된 대본(JSON)을 가져옵니다.\"\"\"
    system_prompt = f\"\"\"
    너는 시청자의 멱살을 잡고 절대 놓아주지 않는 100만 구독자의 독한 '경제/산업/브랜드 분석' 쇼츠 유튜버야. 
    이전의 단순한 나열 방식 대신, 프리미어 프로 컷 편집에 바로 쓸 수 있는 [Scene 단위 매칭 방식]으로 기획해.
    
    주어진 트렌드 데이터와 팩트 정보를 바탕으로 50초 정도의 자극적이고 밀도 높은 유튜브 쇼츠 대본을 작성해.

    [★ 100만 뷰를 위한 대본 작성 5계명 ★]
    1. 추상적인 클리셰 금지: \"누구도 예상 못한 상황입니다\", \"알아보겠습니다\" 같은 지루한 멘트는 절대 금지. 모든 문장에 '구체적인 팩트', '돈(수치)', '특정 인물/기관명'이 들어가야 해.
    2. 미친 훅(Hook)과 반전 전개: \"개미들이 공포에 질려 던질 때, OO은 조용히 쓸어 담았습니다\" 같은 대조법을 사용해. 첫 3초 안에 시청자의 도파민을 터뜨려야 해.
    3. 구체적인 명분(근거) 제시: \"기술력이 좋습니다\"라고 퉁치지 마. \"국경 간 송금 시간 3초\" 등 명확한 근거 데이터를 하나 이상 꽂아 넣을 것.
    4. 구어체 사용: 뉴스 앵커처럼 딱딱하게 말하지 말고, 중학교 1학년 친구에게 비밀 정보를 알려주듯 다급하고 흡입력 있는 구어체를 사용해.
    5. Scene 단위 검색어 강제: 각 문장(Scene)마다 영상 소스 자동화 프로그램이 정확한 클립을 찾을 수 있도록 구체적인 고유명사를 포함한 핀포인트 영어 검색어(예: \"Elon Musk angry interview\", \"Stock market crash graph\")를 필수적으로 1개씩 매칭시켜.

    [- 필수 대본 길이 (중요) -]
    대본 전체 나레이션 글자 수는 무조건 **최소 400자 이상, 500자 이하**가 되도록 매우 상세히 서술해. 총 6~8개의 Scene으로 나누어 줘.

    [출력 형식 - 순수 JSON 배열만 반환]
    - 마크다운 블록(```json 등) 없이 순수 JSON 문자열만 반환해야 합니다.
    [
      {{"scene_num": 1, "search": "구체적인 영어 핀포인트 검색어", "narration": "흡입력 있는 구어체 대화 멘트"}},
      {{"scene_num": 2, "search": "구체적인 영어 핀포인트 검색어", "narration": "...(계속)"}}
    ]
    \"\"\"
    user_query = f"주제: {topic}\\n문맥 데이터: {context}"
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "gpt-4o-mini",
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
            return [
                {"scene_num": 1, "search": "Stock market chart falling red", "narration": "전쟁 터지자 한국이 돈방석 예약했습니다!"},
                {"scene_num": 2, "search": "Confused investors wide shot", "narration": "근데 이거 개미들은 아무도 모릅니다."},
                {"scene_num": 3, "search": "Server room flashing lights", "narration": "결국 승자는 기술력 가진 이 기업이죠."}
            ]

"""

text = text[:start_idx] + new_func + text[end_idx:]

text = text.replace('call_gemini_script', 'call_openai_script')

with codecs.open('c:/Users/00LG00/Desktop/aiva_server/server.py', 'w', 'utf-8') as f:
    f.write(text)

print('Success')
