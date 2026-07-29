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

# 스케줄러 상주 실행 → 07:30/12:30/18:30/21:00 KST 자동 발행
python main.py
```

**검증 포인트:** 지정 시각에 큐의 글이 하나씩 발행되고 로그에 `발행 성공`이 남는다.

---

## 체크리스트

- [ ] Meta 앱 생성 · `threads_basic` / `threads_content_publish` 권한
- [ ] `.env`에 APP_ID / APP_SECRET / ACCESS_TOKEN 입력
- [ ] `python main.py --check` → 계정 ID 확인 (읽기전용)
- [ ] `--add` + `--once` → 실제 발행 1건 확인
- [ ] (선택) `content_generator.py` → 자동 생성 확인
- [ ] `python main.py` 상주 → 스케줄 발행 확인
