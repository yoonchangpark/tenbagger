# threads-auto — Meta Threads 자동 발행 스케줄러

큐에 쌓아둔 콘텐츠를 한국 사용자 활동이 높은 시간대에 맞춰 Meta Threads에
자동 발행한다. 토큰(60일 장기)은 만료 임박 시 자동 갱신된다.

## 구성

| 파일 | 역할 |
|------|------|
| `main.py` | 스케줄러 진입점 (토큰 확보 → 큐 → 스케줄 등록) |
| `threads_api.py` | Threads Graph API 래퍼 (컨테이너 생성 → 발행) |
| `token_manager.py` | 토큰 자동 갱신 (단기→장기 교환, 장기 refresh) |
| `content_queue.py` | 발행 콘텐츠 큐 (JSON 파일 / Supabase) |
| `scheduler.py` | 최적 시간대 발행 로직 (APScheduler cron) |
| `content_generator.py` | 텐배거 주제 → OpenAI로 Threads 게시물 생성 → 큐 적재 |

관련 문서: 텐배거용 프롬프트는 [`prompt_pack.md`](prompt_pack.md), 실계정 연결·발행 검증은 [`SETUP.md`](SETUP.md).

## 설치

```bash
cd threads-auto
pip install -r requirements.txt
cp .env.example .env   # 값 채우기
```

## 사용

```bash
# 연결 점검 (발행 안 함, 읽기전용) — 토큰·계정 확인
python main.py --check

# 큐에 콘텐츠 추가 (수동)
python main.py --add "오늘의 텐배거 인사이트 ..."

# 주제로 콘텐츠 자동 생성 → 큐 적재 (OPENAI_API_KEY 필요)
python content_generator.py "AI 전력 인프라 텐배거" "K-방산 성장주"

# 지금 즉시 한 건 발행 (테스트)
python main.py --once

# 스케줄러 상주 실행 (POST_TIMES 마다 자동 발행)
python main.py
```

## 동작 원리

1. **토큰**: `.token.json`에 유효 장기 토큰이 있으면 사용하고, 만료 5일 전부터
   `refresh_access_token`으로 자동 갱신한다. 없으면 `ACCESS_TOKEN`(단기/장기)을
   장기 토큰으로 교환해 저장한다.
2. **발행**: Threads API는 2단계다. 컨테이너 생성(`/{user}/threads`) 후
   서버 처리 시간을 두고 발행(`/{user}/threads_publish`)한다. 미디어는 30초,
   텍스트는 5초 대기한다.
3. **스케줄**: 기본 발행 시각은 KST 07:30 / 12:30 / 18:30 / 21:00. 매 시각
   큐의 다음 `pending` 아이템 하나를 발행하고 상태를 `published`로 바꾼다.

## Supabase 큐 (선택)

`CONTENT_QUEUE_BACKEND=supabase` + `SUPABASE_URL`/`SUPABASE_KEY` 설정 시 사용.
`content_queue.py`의 `SupabaseQueue` 도크스트링에 테이블 DDL 예시가 있다.
```
