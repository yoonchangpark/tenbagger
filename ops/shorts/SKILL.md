# 숏츠 제작·발행

**목적**: 텐배거 종목 이야기를 담은 숏츠를 주 2~3회 발행해, 보는 사람을 텐배거 구독으로 데려온다.

**언제 쓰나**: "숏츠 소재 채워줘", "이번 숏츠 왜 이렇게 나왔어?", "숏츠 주제 바꾸자".

**입력과 위치**
- 회고편 소재: aiva_server `data/tenbagger_history.json`
- 전망편 소재: tenbagger `backend/app/api/v2_shorts_feed.py` `CANDIDATES`, `RETRO_NARRATIVES`
- 실행: aiva_server Railway Cron `30 9 * * 1,3,5` UTC = 월·수·금 18:30 KST → `run_scheduled_publish.py` → `server.py:run_one_cycle()`

## 단계
1. **INTERVIEW** — 이번 회차 소재를 정한다. 소재 재고가 4편 미만이면 소재 보충을 먼저 한다.
2. **MANUAL** — 아래 판단 규칙과 서사 5단 구조(`historical_topic.py` `NARRATIVE_DIRECTIVE`)를 따른다.
3. **TOOLBOX** — Railway 변수(OpenAI·Gemini·Pexels·YouTube 토큰)가 살아 있는지는 오너가 Railway 로그로 확인한다. 세션은 볼 수 없다.
4. **EXECUTE** — 크론이 대본 → 대본 평가 → B-roll → TTS → 렌더 → YouTube 업로드 → 스크린샷·`data/videos.json` 기록 → 조회수 수집 → 프롬프트 진화(`evolve_prompt`)를 한 번에 돈다.
5. **VERIFY** — `proof.md`를 돌린다.

## 판단 규칙
- 소재는 **최근 연도 매출과 영업이익이 함께 늘어난 종목만** (2026-09-21 오너 결정).
- 숫자는 DART 실공시로 확인한 것만 쓴다. 확인 못 한 숫자는 빼고, 지어내지 않는다.
- 한화오션과 HD한국조선해양은 "조선 턴어라운드" 서사가 겹쳐 같은 회차에 넣지 않는다.
- 전망편은 단정하지 않는다. `risk_note`를 반드시 노출한다.
- 발행 전 사람 검토는 두지 않는다 (2026-09-23 오너 결정). 대신 proof로 거른다.

## 완료 기준
월·수·금 회차마다 텐배거 소재 영상 1편이 YouTube에 공개되고, 설명란 링크로 텐배거에 들어올 수 있다.

## 알려진 차이 (지금 코드가 매뉴얼과 다른 곳)
- 크론은 `step_4_automation_pipeline(job_id, "auto")`로 **범용 주제(`FALLBACK_TOPICS`)**를 고른다. 텐배거 소재를 쓰는 `tenbagger`/`history` 모드는 크론 경로에서 안 쓰인다. → proof #1 실패. 고칠지는 오너 결정 대기.
- 대본 평가가 70점 미만이어도 `미달(강제 진행)`으로 계속 간다. `run_one_cycle()`의 `Rejected` 분기는 그 상태를 세우는 코드가 없어 실제로는 타지 않는다. → proof #3 실패.
- 설명란에는 면책 문구만 붙고 텐배거 링크가 없다. → proof #5 실패.
- Docker에 `uvx`가 없어 NotebookLM 리서치 단계가 매번 실패한다.

## 과거 엣지케이스
- 포스코퓨처엠은 의도적으로 소재에서 뺐다 (`v2_shorts_feed.py` 주석 참고).
- Railway UI의 Python 버전 배지는 실제 런타임과 다르다. 로그를 믿는다.
