# threads-auto — Meta Threads 자동 발행 스케줄러

큐에 쌓아둔 콘텐츠를 **주 3~4회**(기본 월·수·금 18:30 KST) Meta Threads에
자동 발행한다. 토큰(60일 장기)은 만료 임박 시 자동 갱신된다.

운영 환경에서는 별도 프로세스를 띄우지 않고 **tenbagger 백엔드 스케줄러**가
이 모듈을 호출한다(아래 "운영 배포" 참고).

## 구성

| 파일 | 역할 |
|------|------|
| `main.py` | 스케줄러 진입점 (토큰 확보 → 큐 → 스케줄 등록) |
| `threads_api.py` | Threads Graph API 래퍼 (컨테이너 생성 → 발행) |
| `token_manager.py` | 토큰 자동 갱신 (단기→장기 교환, 장기 refresh) |
| `content_queue.py` | 발행 콘텐츠 큐 (JSON 파일 / Postgres / Supabase) |
| `scheduler.py` | 발행 요일·시각 스케줄 (APScheduler cron) |
| `publisher.py` | 토큰 확보 + 큐의 다음 1건 발행 (백엔드와 공유) |
| `content_generator.py` | 텐배거 주제 → OpenAI로 Threads 게시물 생성 → 큐 적재 |
| `devlog_generator.py` | 개발 내용을 AS-IS → TO-BE 빌드로그로 생성 + 큐 자동 보충 |
| `preview.py` | 발행 전 큐 내용을 HTML로 미리보기 + 품질 점검 |

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

# 콘텐츠 자동 생성 → 큐 적재 (OPENAI_API_KEY 필요)
# 실제 재무 수치를 함께 주면 그 숫자를 중심으로 쓴다 (권장)
python content_generator.py "제룡전기" --facts "매출 1,806억→3,067억(2022→2024), 영업이익률 28%, 부채비율 21%"

# 발행 전 미리보기 (브라우저로 열림) — 글자 수·재무 근거·책임 고지 점검
python preview.py

# 지금 즉시 한 건 발행 (테스트)
python main.py --once

# 스케줄러 상주 실행 (POST_DAYS × POST_TIMES 마다 자동 발행)
python main.py
```

## 동작 원리

1. **토큰**: 저장된 유효 장기 토큰이 있으면 사용하고, 만료 5일 전부터
   `refresh_access_token`으로 자동 갱신한다. 없으면 `ACCESS_TOKEN`을 장기 토큰으로
   만들어 저장한다 — 단기면 교환하고, 이미 장기여서 교환이 거부되면 refresh로
   만료를 연장해 저장한다. 저장소는 `DATABASE_URL`이 있으면 DB(`threads_token`
   테이블), 없으면 파일(`.token.json`)이다. 한 번 저장되면 이후로는 스스로
   갱신하므로 사람이 토큰을 다시 발급할 일이 없다.
2. **발행**: Threads API는 2단계다. 컨테이너 생성(`/{user}/threads`) 후
   서버 처리 시간을 두고 발행(`/{user}/threads_publish`)한다. 미디어는 30초,
   텍스트는 5초 대기한다.
3. **스케줄**: 기본은 `POST_DAYS=mon,wed,fri` × `POST_TIMES=18:30` → **주 3회**.
   주 4회로 늘리려면 요일을 하나 더 넣는다(`mon,wed,fri,sun`). 매 발행 시각에
   큐의 다음 `pending` 아이템 하나를 발행하고 상태를 `published`로 바꾼다.
   주간 발행 횟수 = 요일 수 × 시각 수이므로, 시각은 하나만 두는 것이 기본이다.
4. **토큰 갱신 시점**: 발행 직전마다 토큰을 다시 확인한다. 상주 프로세스가
   60일 넘게 살아 있어도 만료 전에 refresh 된다.

## 운영 배포 — 백엔드 스케줄러가 호출한다

threads-auto는 Railway에 따로 배포되지 않는다. 대신 이미 상주 중인 tenbagger
백엔드(`backend/app/main.py`)의 APScheduler가 월·수·금 18:30 KST에
`python main.py --once`를 실행한다. 새로 띄울 프로세스가 없다.

| 환경변수 | 기본값 | 설명 |
|---------|--------|------|
| `THREADS_POST_DAYS` | `mon,wed,fri` | 백엔드 잡의 발행 요일 |
| `THREADS_POST_TIME` | `18:30` | 백엔드 잡의 발행 시각(KST) |
| `CONTENT_QUEUE_BACKEND` | 백엔드 잡에서 `postgres` | 큐 저장소 |

컨테이너 파일시스템은 재배포마다 초기화되므로 운영 큐는 Postgres에 둔다
(`threads_queue` 테이블, `scripts/init.sql`이 기동 시 생성). 로컬에서 같은 큐에
콘텐츠를 넣으려면 운영 `DATABASE_URL`을 주고 `--add` 하면 된다:

```bash
CONTENT_QUEUE_BACKEND=postgres DATABASE_URL="postgresql://..." \
  python main.py --add "오늘의 텐배거 인사이트 ..."
```

토큰도 같은 DB(`threads_token` 테이블)에 저장되므로 재배포해도 남고, 만료 5일
전부터 스스로 갱신한다. `ACCESS_TOKEN`은 최초 한 번만 필요하고 그 뒤로는 손대지
않아도 된다.

> 갓 발급한 장기 토큰은 24시간이 지나야 갱신할 수 있어, 첫 실행에서는 저장되지 않고
> 로그에 경고가 남을 수 있다. 다음 실행에서 자동으로 다시 시도하므로 그대로 두면 된다.

## 콘텐츠 품질 — `--facts`가 핵심이다

주제만 주면 모델은 확인되지 않은 수치를 지어낼 수 없어 "미래의 핵심입니다"
같은 일반론으로 흐른다. 이 계정의 차별점은 DART 재무데이터이므로,
**확인된 숫자를 `--facts`로 넣어주는 것이 사실상 필수**다.

```bash
python content_generator.py "제룡전기" \
  --facts "매출 1,806억→3,067억(2022→2024), 영업이익률 28%, 부채비율 21%"
```

모델은 주어진 숫자만 인용하고 없는 수치는 만들지 않도록 지시받는다.
`--facts` 없이 생성하면 수치 대신 '지표 해석·흔한 실수' 방향으로 쓰게 된다.

발행 전 `python preview.py`로 확인하면 숫자가 없는 글은
**"숫자 없음 — 일반론 위험"** 으로 표시된다.

## 빌드로그 — AS-IS → TO-BE 공개

지금 만들고 있는 걸 그대로 열어 반응을 보는 용도다. 투자 콘텐츠와 톤·목적이
달라서 별도 모듈(`devlog_generator.py`)과 별도 페르소나를 쓴다. 종목 추천이
아니므로 투자 책임 고지를 붙이지 않고, 대신 마지막 줄을 질문으로 끝낸다.

글감은 `CHANGELOG.md`다. 항목이 이미 `### 배경`(AS-IS)과
`### 추가`/`### 변경`/`### 수정`(TO-BE)으로 나뉘어 있어 그대로 쓴다.

```bash
# CHANGELOG 최신 항목으로 생성 → 큐 적재
python devlog_generator.py --from-changelog --count 2

# CHANGELOG에 없는 내용을 직접
python devlog_generator.py --asis "전에는 이랬다" --tobe "이렇게 바꿨다" --title "제목"
```

모델에는 파일명·함수명·환경변수명을 쓰지 말고 독자가 화면에서 겪는 일로 옮겨
쓰도록, 주어지지 않은 수치(사용자 수·수익·정확도)는 지어내지 말도록 지시한다.

## 큐 자동 보충

큐가 비면 스케줄이 돌아도 아무것도 올라가지 않는다. 그래서 매주 일요일 21:00 KST에
백엔드 스케줄러가 대기 건수를 보고 목표치보다 적으면 채운다.

```bash
python devlog_generator.py --refill
```

| 환경변수 | 기본값 | 설명 |
|---------|--------|------|
| `QUEUE_TARGET_PENDING` | `3` | 큐를 이 개수까지 채운다(주 3회 발행 = 약 한 주치) |

- 이미 목표치를 채우고 있으면 아무것도 하지 않는다.
- 같은 CHANGELOG 버전으로 두 번 적재하지 않는다(`source_key`로 막는다).
- 글감은 최신 5개 항목까지만 본다. 오래된 변경을 이제 와서 "방금 고쳤다"로
  내보내지 않기 위해서다. 최근 것을 다 썼으면 새로 만든 게 나올 때까지 쉰다.

발행일(월·수·금)보다 앞선 일요일에 채우므로, 발행 전에 `preview.py`로 걸러낼
시간이 있다.

## Supabase 큐 (선택)

`CONTENT_QUEUE_BACKEND=supabase` + `SUPABASE_URL`/`SUPABASE_KEY` 설정 시 사용.
`content_queue.py`의 `SupabaseQueue` 도크스트링에 테이블 DDL 예시가 있다.
```
