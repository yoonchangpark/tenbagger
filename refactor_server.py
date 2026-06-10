import os
import codecs

with codecs.open('c:\\Users\\00LG00\\Desktop\\aiva_server\\server.py', 'r', 'utf-8') as f:
    content = f.read()

# 1. Replace call_openai_script and generate_youtube_query
start_idx = content.find("async def call_openai_script")
end_idx = content.find("async def step_2_download_background_video")

new_func = '''async def call_gemini_script(topic: str, context: str):
    """(비동기) Gemini 1.5 Pro API를 호출하여 Scene 단위 구조화된 대본(JSON)을 가져옵니다."""
    system_prompt = f"""
    너는 시청자의 멱살을 잡고 절대 놓아주지 않는 100만 구독자의 독한 '경제/산업/브랜드 분석' 쇼츠 유튜버야. 
    이전의 단순한 나열 방식 대신, 프리미어 프로 컷 편집에 바로 쓸 수 있는 [Scene 단위 매칭 방식]으로 기획해.
    
    주어진 트렌드 데이터와 팩트 정보를 바탕으로 50초 정도의 자극적이고 밀도 높은 유튜브 쇼츠 대본을 작성해.

    [★ 100만 뷰를 위한 대본 작성 5계명 ★]
    1. 추상적인 클리셰 금지: "누구도 예상 못한 상황입니다", "알아보겠습니다" 같은 지루한 멘트는 절대 금지. 모든 문장에 '구체적인 팩트', '돈(수치)', '특정 인물/기관명'이 들어가야 해.
    2. 미친 훅(Hook)과 반전 전개: "개미들이 공포에 질려 던질 때, OO은 조용히 쓸어 담았습니다" 같은 대조법을 사용해. 첫 3초 안에 시청자의 도파민을 터뜨려야 해.
    3. 구체적인 명분(근거) 제시: "기술력이 좋습니다"라고 퉁치지 마. "국경 간 송금 시간 3초" 등 명확한 근거 데이터를 하나 이상 꽂아 넣을 것.
    4. 구어체 사용: 뉴스 앵커처럼 딱딱하게 말하지 말고, 중학교 1학년 친구에게 비밀 정보를 알려주듯 다급하고 흡입력 있는 구어체를 사용해.
    5. Scene 단위 검색어 강제: 각 문장(Scene)마다 영상 소스 자동화 프로그램이 정확한 클립을 찾을 수 있도록 구체적인 고유명사를 포함한 핀포인트 영어 검색어(예: "Elon Musk angry interview", "Stock market crash graph")를 필수적으로 1개씩 매칭시켜.

    [- 필수 대본 길이 (중요) -]
    대본 전체 나레이션 글자 수는 무조건 **최소 400자 이상, 500자 이하**가 되도록 매우 상세히 서술해. 총 6~8개의 Scene으로 나누어 줘.

    [출력 형식 - 순수 JSON 배열만 반환]
    - 마크다운 블록(```json 등) 없이 순수 JSON 문자열만 반환해야 합니다.
    [
      {{"scene_num": 1, "search": "구체적인 영어 핀포인트 검색어", "narration": "흡입력 있는 구어체 대화 멘트"}},
      {{"scene_num": 2, "search": "구체적인 영어 핀포인트 검색어", "narration": "...(계속)"}}
    ]
    """
    user_query = f"주제: {topic}\\n문맥 데이터: {context}"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent?key={os.getenv('GEMINI_API_KEY')}"
    
    payload = {
        "contents": [{"parts": [{"text": system_prompt + "\\n\\n" + user_query}]}],
        "generationConfig": {
            "temperature": 0.8,
            "responseMimeType": "application/json"
        }
    }
    import httpx
    import json
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=payload, timeout=40.0)
            response.raise_for_status()
            data = response.json()
            content_text = data['candidates'][0]['content']['parts'][0]['text']
            content_text = content_text.replace("```json", "").replace("```", "").strip()
            
            parsed_data = json.loads(content_text)
            return parsed_data
        except Exception as e:
            logger.error(f"Gemini API 에러: {e}")
            return [
                {"scene_num": 1, "search": "Stock market chart falling red", "narration": "전쟁 터지자 한국이 돈방석 예약했습니다!"},
                {"scene_num": 2, "search": "Confused investors wide shot", "narration": "근데 이거 개미들은 아무도 모릅니다."},
                {"scene_num": 3, "search": "Server room flashing lights", "narration": "결국 승자는 기술력 가진 이 기업이죠."}
            ]

'''

content = content[:start_idx] + new_func + content[end_idx:]

# 2. Update step_3_audio_and_edit
old_step_3_start = """    # 1. 시나리오 JSON에서 나레이션 텍스트만 추출
    narration_parts = [block['text'] for block in script_data if block.get('type') == 'narration']
    full_narration_text = " ".join(narration_parts)"""
new_step_3_start = """    # 1. 시나리오 JSON에서 나레이션 텍스트만 추출
    narration_parts = [scene['narration'] for scene in script_data if 'narration' in scene]
    full_narration_text = " ".join(narration_parts)"""
content = content.replace(old_step_3_start, new_step_3_start)

# Update return inside step_3_audio_and_edit (Returns list instead of text)
content = content.replace("return audio_path, full_narration_text", "return audio_path, narration_parts")
content = content.replace("return None, full_narration_text", "return None, narration_parts")

# 3. Update step_4_assemble_video signature
old_sig_4 = "async def step_4_assemble_video(video_paths: list, audio_path: str, narration_text: str):"
new_sig_4 = "async def step_4_assemble_video(video_paths: list, audio_path: str, narrations: list):"
content = content.replace(old_sig_4, new_sig_4)

# 4. Replace the text splitting inside step_4_assemble_video
old_text_split = """        # 문장 단위로 분할하되, 각 문장의 '글자 수'에 비례하여 화면 노출 시간(duration)을 할당합니다. (싱크오차 최소화)
        raw_sentences = narration_text.replace("?", ".").replace("!", ".").split(".")
        sentences = [(s.strip() + ".") for s in raw_sentences if len(s.strip()) > 2]
        
        if len(sentences) == 0:
            sentences = [narration_text]"""
new_text_split = """        # 씬 단위 나레이션을 그대로 활용합니다.
        sentences = narrations"""
content = content.replace(old_text_split, new_text_split)

# 5. Fix video clip looping and scaling inside step_4_assemble_video!
old_clip_logic = """        if valid_paths:
            loaded_clips = []
            for p in valid_paths:
                try:
                    loaded_clips.append(VideoFileClip(p))
                except Exception as e:
                    logger.warning(f"비디오 로딩 에러: {e}")
                    
            if loaded_clips:
                clip_duration = 2.5 # 2.5초마다 컷 전환
                num_cuts = math.ceil(target_duration / clip_duration)
                
                # 각 클립별 진행된 재생 시간(start_t) 저장 로직
                clip_times = [0.0] * len(loaded_clips)
                
                for i in range(num_cuts):
                    source_idx = i % len(loaded_clips)
                    source_clip = loaded_clips[source_idx]
                    
                    # 현재 클립의 재생 시작 위치 확보 및 클립이 끝나가면 다시 0초로 롤백
                    start_t = clip_times[source_idx]
                    if start_t + clip_duration > source_clip.duration:
                        start_t = 0.0
                    
                    end_t = min(source_clip.duration, start_t + clip_duration)
                    
                    # 실제 자를 수 있는 초(duration)가 너무 짧으면 그냥 루프
                    if end_t - start_t <= 0.5:
                        import moviepy.video.fx.all as vfx
                        cut = source_clip.fx(vfx.loop, duration=clip_duration)
                    else:
                        cut = source_clip.subclip(start_t, end_t)
                    
                    clip_times[source_idx] = end_t
                    bg_clips.append(cut)
                    
                bg_video_clip = concatenate_videoclips(bg_clips, method="compose")"""

new_clip_logic = """        import moviepy.video.fx.all as vfx
        
        # 전체 텍스트 수
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
                
                for i, narration in enumerate(narrations):
                    char_count = len(narration)
                    duration_for_scene = (char_count / total_chars) * target_duration
                    
                    source_idx = i % len(loaded_clips)
                    source_clip = loaded_clips[source_idx]
                    
                    # 각 씬(Scene)의 영상은 그 씬의 오디오 길이만큼만 사용합니다.
                    if source_clip.duration < duration_for_scene:
                         cut = source_clip.fx(vfx.loop, duration=duration_for_scene)
                    else:
                         cut = source_clip.subclip(0, duration_for_scene)
                    
                    bg_clips.append(cut)
                    current_time += duration_for_scene
                    
                bg_video_clip = concatenate_videoclips(bg_clips, method="compose")"""
content = content.replace(old_clip_logic, new_clip_logic)

# 6. Update step_4_automation_pipeline
old_pipeline = """    try:
        jobs[job_id]["status"] = "Step 0: NotebookLM 2026 비즈니스 리포트 검색 중..."
        context_2026 = await query_notebooklm(topic)
        
        jobs[job_id]["status"] = "Step 1: AI 대본 분석 중 (JSON Parsing)..."
        script_data = await call_openai_script(topic, context_2026)
        
        # 스크립트 데이터를 화면용 마크다운 형식으로 보기 좋게 결합
        display_script = "\\n\\n".join([f"[{block['type'].upper()}] {block['text']}" for block in script_data])
        jobs[job_id]["script"] = display_script
        
        jobs[job_id]["status"] = "Step 2: 배경 유튜브 영상 3개 검색 및 다운로드 중 (yt-dlp)..."
        # 1. AI를 시켜 대본에 맞는 3개의 영문 유튜브 검색어 배열 생성
        optimized_queries = await generate_youtube_query(topic, script_data)
        # 2. 해당 검색어 배열로 영상 다운로드 (다수)
        video_local_paths = await step_2_download_background_video(optimized_queries)
        
        jobs[job_id]["status"] = "Step 3: 나레이션 오디오 추출 중 (TTS)..."
        audio_local_path, narration_text = await step_3_audio_and_edit(script_data)
        
        jobs[job_id]["status"] = "Step 4: 비디오 인코딩 및 자막 합성 중 (MoviePy 렌더링은 수분 소요됩니다)..."
        if audio_local_path:
            final_video_url = await step_4_assemble_video(video_local_paths, audio_local_path, narration_text)"""

new_pipeline = """    try:
        # 데이터 파이프라인 연동
        import data_pipeline
        prompt_topic = topic
        if topic.lower() == "auto":
            jobs[job_id]["status"] = "Step 0: 트렌드 티커 수집 중..."
            trends = data_pipeline.get_trending_topics()
            prompt_topic = trends[0] if trends else "미국 주식 시장 트렌드"
            
        jobs[job_id]["status"] = f"Step 0: {prompt_topic} 팩트 및 뉴스 검색 중..."
        facts = data_pipeline.fetch_facts_for_topic(prompt_topic)
        context_data = f"트렌드 주제: {prompt_topic}\\n실제 팩트/뉴스 데이터: {facts}"
        
        jobs[job_id]["status"] = "Step 1: AI Scene 단위 기획 및 대본 작성 중..."
        script_data = await call_gemini_script(prompt_topic, context_data)
        
        # 스크립트 데이터를 화면용 마크다운 형식으로 보기 좋게 결합
        display_script = "\\n\\n".join([f"[SCENE {scene.get('scene_num', i+1)}]\\nSEARCH: {scene.get('search', '')}\\nNARRATION: {scene.get('narration', '')}" for i, scene in enumerate(script_data)])
        jobs[job_id]["script"] = display_script
        
        jobs[job_id]["status"] = "Step 2: Scene별 매칭 영상 다운로드 중 (yt-dlp)..."
        scene_queries = [scene.get('search', 'finance news') for scene in script_data]
        # 최대 다운로드 개수 제한이 있다면 늘리거나 유지
        video_local_paths = await step_2_download_background_video(scene_queries)
        
        jobs[job_id]["status"] = "Step 3: 나레이션 오디오 추출 중 (TTS)..."
        audio_local_path, narrations = await step_3_audio_and_edit(script_data)
        
        jobs[job_id]["status"] = "Step 4: 비디오 인코딩 및 자막 합성 중 (MoviePy 렌더링은 수분 소요됩니다)..."
        if audio_local_path:
            final_video_url = await step_4_assemble_video(video_local_paths, audio_local_path, narrations)"""
content = content.replace(old_pipeline, new_pipeline)

with codecs.open('c:\\Users\\00LG00\\Desktop\\aiva_server\\server.py', 'w', 'utf-8') as f:
    f.write(content)

print("Refactor completed")
