# threads-auto 실계정 연결 & 발행 검증 가이드

실제 Meta Threads 계정에 붙여 발행하기까지의 순서다. 각 단계 끝에
**검증 포인트**가 있으니 통과하는지 확인하며 진행한다.

## 1. Meta 앱 만들기

1. https://developers.facebook.com/ → 로그인 → **My Apps → Create App**
2. 앱 유형에서 **Threads** 사용 사례 추가
3. 앱의 **App ID / App Secret** 확인 → `META_APP_ID`, `META_APP_SECRET`
4. Threads API 권한 요청: `threads_basic`, `threads_content_publish`

## 2. 액세스 토큰 발급

1. 앱의 Threads → **Generate Access Token** (본인 계정 연결)
2. 발급된 **단기 토큰**을 복사 → `ACCESS_TOKEN`

> 단기 토큰이어도 된다. 최초 실행 시 `token_manager`가 60일 장기 토큰으로
> 교환해 `.token.json`에 저장하고, 이후 만료 전 자동 갱신한다.

## 3. .env 채우기

```bash
cd threads-auto
cp .env.example .env
```

`.env`에 최소 세 값을 채운다:

```
META_APP_ID=...
META_APP_SECRET=...
ACCESS_TOKEN=...          # 2단계에서 받은 단기(또는 장기) 토큰
```

콘텐츠 자동 생성까지 쓰려면:

```
OPENAI_API_KEY=...        # content_generator.py 용
```

## 4. 연결 점검 (발행 안 함) — 읽기전용

```bash
pip install -r requirements.txt
python main.py --check
```

**검증 포인트:** 아래처럼 계정 ID가 찍히면 토큰·권한이 정상이다.

```
연결 성공 — Threads user_id=1784..., 대기 콘텐츠 0건
```

실패 시: 토큰 만료/권한 부족이 대부분. 2단계에서 토큰을 다시 발급한다.

## 5. 첫 발행 테스트 (수동 1건)

```bash
python main.py --add "테스트 게시물입니다. #텐배거"
python main.py --once
```

**검증 포인트:** 실제 Threads 계정에 글이 올라오는지 확인. `queue.json`의
해당 항목 `status`가 `published`로 바뀐다.

## 6. 자동 생성 → 자동 발행 (선택)

```bash
# 주제로 글 생성 → 큐 적재
python content_generator.py "AI 전력 인프라 텐배거" "K-방산 성장주"

# 스케줄러 상주 실행 → 월·수·금 18:30 KST 자동 발행 (주 3회)
python main.py
```

**검증 포인트:** 지정 요일·시각에 큐의 글이 하나씩 발행되고 로그에 `발행 성공`이 남는다.

## 7. 운영(Railway) 자동 발행 — 상주 프로세스 없이

운영에서는 `python main.py`를 따로 띄우지 않는다. 이미 상주 중인 tenbagger
백엔드의 스케줄러가 월·수·금 18:30 KST에 `main.py --once`를 실행한다.

Railway 백엔드 서비스 환경변수에 아래를 넣으면 끝이다.

```
META_APP_SECRET=...
ACCESS_TOKEN=...          # 장기 토큰 (60일마다 재발급 필요 — 아래 주의)
```

`DATABASE_URL`은 이미 설정돼 있고, 큐 백엔드는 백엔드 잡이 `postgres`로 넘긴다.
요일·시각을 바꾸려면 `THREADS_POST_DAYS` / `THREADS_POST_TIME`을 설정한다.

**검증 포인트:** 배포 로그에 `✅ [SCHEDULER] ... | 스레드발행 mon,wed,fri 18:30 KST`가
찍히고, 발행 요일에 `🧵 [SCHEDULER] Threads 자동 발행 시작...`이 남는다.

큐 적재는 로컬에서 운영 DB를 보고 하면 된다:

```bash
CONTENT_QUEUE_BACKEND=postgres DATABASE_URL="<운영 DATABASE_URL>" \
  python main.py --add "오늘의 텐배거 인사이트 ..."
```

> ⚠️ 컨테이너 파일시스템은 재배포마다 초기화되므로 `.token.json`이 남지 않는다.
> 매 발행마다 `ACCESS_TOKEN`으로 다시 교환하므로, 60일마다 토큰을 새로 발급해
> 넣어야 발행이 끊기지 않는다. 토큰을 DB에 보관하는 것은 후속 작업.

---

## 체크리스트

- [x] Meta 앱 생성 · `threads_basic` / `threads_content_publish` 권한
- [x] `.env`에 APP_ID / APP_SECRET / ACCESS_TOKEN 입력
- [x] `python main.py --check` → 계정 ID 확인 (읽기전용)
- [x] `--add` + `--once` → 실제 발행 1건 확인 (2026-07-30 @yoonchangpark 성공)
- [ ] (선택) `content_generator.py` → 자동 생성 확인
- [ ] Railway 백엔드에 `META_APP_SECRET` / `ACCESS_TOKEN` 설정
- [ ] 운영 DB 큐에 콘텐츠 3건 이상 적재
- [ ] 첫 발행 요일(월/수/금 18:30)에 실제 발행 확인
