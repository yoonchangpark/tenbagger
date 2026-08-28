# 다음 세션 할 일 (2026-08-27 작성)

> 오늘(8/27) 첫 영상 YouTube 업로드 + Threads 카드뉴스 발행까지 완료.
> 이어서 진행할 작업 3가지를 우선순위 순으로 기록한다.

---

## 1. ~~aiva_server를 GitHub에 push~~ ✅ 완료 (2026-08-28)

`https://github.com/yoonchangpark/aiva_server.git` main 브랜치에 push 완료 (`9ba596a`).
Claude가 `git clone`으로 직접 읽는 것까지 검증했다 — 앞으로 복붙 왕복 불필요.

- `.env`는 `.gitignore`에 있고, 전체 히스토리에서도 커밋된 적 없음을 확인
  (`git log --all -- .env` / `git ls-files` 둘 다 빈 결과)
- 일회성 작업 파일(`patch_*.py`, `frames/`, `review_sheet.png`)도 gitignore에 추가
- 런타임 상태 파일(`history.json`, `data/step_timings.json`)은 매 실행마다 바뀌어
  diff 노이즈가 되므로 커밋에서 제외했다. 추후 gitignore + `git rm --cached` 고려.
- ⚠️ 남은 제약: 이 세션의 GitHub MCP 도구는 `yoonchangpark/tenbagger`로만 제한돼
  aiva_server에 PR은 못 올린다. 읽기는 clone으로 가능하므로, 수정 결과는
  패치 스크립트나 파일 내용으로 전달해 사용자가 로컬에서 커밋하는 방식.

---

## 2. "미래 텐배거 추천" 모드 서사 지침 작성

**배경**: 오늘 "과거 텐배거 해부"(history) 모드에 서사 5단 구조를 강제하는
`NARRATIVE_DIRECTIVE`를 `historical_topic.py`에 추가했다. 하지만 "미래 텐배거
추천"(tenbagger) 모드는 `tenbagger_topic.py`라는 별도 파일이라 아직 미적용.

**왜 다르게 짜야 하나**: 회고편은 "그때 샀어야 했다"는 공감 구조인데, 전망편은
과거 결과가 없으니 **"지금 이 신호를 보라"** 는 구조여야 한다. 단정적 표현
금지(미확정 시나리오), `risk_note` 필수 노출 원칙도 지켜야 한다.

**현재 상태**: `pick_tenbagger_topic()`에 `preferred` 파라미터만 받도록 해둠.
종목 우선선택 로직 자체는 미완성(history 모드에는 구현 완료).

**참고할 것**:
- `historical_topic.py`의 `NARRATIVE_DIRECTIVE` (오늘 추가한 5단 구조)
- `agent_prompts/topdown_discovery_agent.md` (오늘 "브랜드/사례" 요구사항 추가)
- `backend/app/api/v2_shorts_feed.py`의 `CANDIDATES` (전망편 데이터 소스)

---

## 3. threads-auto를 프로그램화 (웹 UI + 카드뉴스 자동 생성)

**목표**: tenbagger·AIVA처럼 웹 UI를 가진 프로그램으로 만들어서, tenbagger 데이터와
YouTube에 올라간 영상을 조합해 카드뉴스를 자동 생성 → 발행까지.

**이미 있는 부품**:
- `threads-auto/content_generator.py` — shorts-feed에서 facts 자동 로드(`--ticker`)
- `threads-auto/threads_api.py` — 캐러셀(이미지 여러 장) 발행 지원 (오늘 추가, PR #109)
- `threads-auto/main.py` — `--add "본문" --images url1,url2,...` CLI
- `frontend/media/threads-posts/` — 이미지 정적 호스팅 경로

**만들어야 할 것**:
- **카드뉴스 이미지 자동 생성기** — 오늘은 Claude가 HTML을 손으로 짜고 Playwright로
  캡처했는데(수동), 이걸 템플릿화해서 프로그램이 생성하도록
- **웹 UI** — AIVA 대시보드처럼 주제/종목 선택 → 카드 미리보기 → 발행 큐 적재
- threads-auto는 tenbagger 레포 안에 있으므로 Claude가 직접 작업 가능(복붙 불필요)

---

## 4. Scene 순서 규칙 추가 (짧지만 잊기 쉬움 — 먼저 처리해도 좋음)

**증상**: 한화에어로스페이스 대본(서사품질 100/100, 팩트체크 통과)에서 Scene 19~20이
CTA("전체 예측 기록은 프로필 링크에서")로 끝났는데, Scene 21에서 다시 "검색량이
터졌을 땐 이미 고점이었습니다"로 돌아갔다. 시청자 입장에선 끝난 줄 알았다가 다시
시작되는 느낌 → 이탈 지점이 된다.

**수정**: `historical_topic.py`의 `NARRATIVE_DIRECTIVE` 5단계 지시에 순서 강제를 추가.
- CTA와 면책은 **반드시 맨 마지막 2개 Scene**에 위치
- 검색량 차트(`source_type: chart`)·분석 카드(`card`) 같은 에셋 Scene은 CTA **앞**에 배치
- 마지막 메시지에 여운을 남겨 다음 영상을 보고 싶게 만들 것
  (AI 피드백도 "마지막에 여운 부족" + "반전 계기를 더 명확히"를 지적함)

---

## 오늘(8/27) 완료된 것 — 참고용

- 첫 영상 제작 → YouTube Shorts 업로드 (에코프로비엠 회고편, 0:57)
- Threads 카드뉴스 5장 캐러셀 발행 (빌딩인퍼블릭 첫 포스트)
- PR #109: threads-auto 캐러셀 발행 지원
- PR #110: `topdown_discovery_agent.md`에 "브랜드/사례" 요구사항 추가 (머지 대기)
- YouTube 채널 리브랜딩: 핸들 `@텐베거헌터`, 설명 문구 갱신, 프로필 링크 연결
- aiva_server 로컬 수정 4건 (아직 GitHub 미반영 — 1번 작업 시 함께 push됨):
  - 자막/음성 싱크 버그 수정 (Scene별 TTS 개별 생성 → 실측 길이로 자막 타이밍)
  - 팩트 그라운딩 규칙 추가 (문맥 데이터 숫자 2개 이상 인용 강제)
  - **모드/주제 분리** — 모드 버튼이 입력창을 덮어쓰던 구조적 버그 수정
    (`mode` state 신설, `preferred` 필드로 종목 지정 분리)
  - history 모드 서사 5단 구조 강제 (`NARRATIVE_DIRECTIVE`)

---

## 오늘의 시행착오 — 내일 카드뉴스 작업 시 참고

### 1) Windows PowerShell 5.1 인코딩 함정 (여러 번 당함)
- `Get-Content -Raw` / `Set-Content -Encoding utf8` → 한글 깨짐 또는 BOM 삽입.
  BOM이 들어가면 파이썬이 `SyntaxError: invalid non-printable character U+FEFF`로 죽는다.
- **안전한 패턴**: 읽기 `[System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)`,
  쓰기 `[System.IO.File]::WriteAllText($path, $c, (New-Object System.Text.UTF8Encoding($false)))`
- 파이썬 패치 스크립트로 파일을 수정할 때도 원본 BOM이 남을 수 있으니, 수정 후
  `ast.parse()`로 반드시 검증할 것.
- `Select-String -Pattern`에 작은따옴표 문자열을 넣을 땐 이스케이프 주의
  (`'history'` 같은 패턴이 인자 파싱 에러를 냈음).
- 콘솔에 한글이 깨져 보여도 파일 자체는 멀쩡한 경우가 많다 — `chcp 65001` 후 재확인.

### 2) 파일을 직접 못 읽으면 디버깅이 몇 배 느려진다
- aiva_server가 로컬에만 있어서, 오늘 버그 하나 잡는 데 "출력 붙여넣기 → 패치 스크립트
  작성 → 실행 → 결과 붙여넣기" 왕복을 6~7회 반복했다. 추측으로 짠 패치가 실제 코드와
  안 맞아 실패한 것도 2번(`mode` 변수가 아예 없었음, `lines` 앵커 불일치).
- **교훈**: 작업 대상 코드는 Claude가 읽을 수 있는 곳(GitHub)에 두는 게 압도적으로 빠르다.
  → 이것이 1번 작업(aiva GitHub push)을 최우선으로 둔 이유.

### 3) 카드뉴스 이미지 생성은 이 방법으로 했다 (3번 작업에서 프로그램화할 것)
- HTML로 카드 5장을 4:5 비율 캐러셀로 작성 → Playwright(Node)로 각 `.card` 요소를
  `deviceScaleFactor: 2`로 개별 스크린샷 → PNG 5장.
- 실행 환경: `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers`,
  `executablePath: '/opt/pw-browsers/chromium'` (심볼릭 링크. `chromium/chrome-linux/chrome`
  경로로 직접 지정하면 실패함)
- 생성된 이미지는 `frontend/media/threads-posts/`에 커밋 → Railway 배포 후 공개 URL이 되고,
  Meta가 그 URL을 직접 fetch해서 캐러셀로 발행한다(공개 접근 필수).

### 4) Threads API 연동 시 걸린 것들
- Meta 앱은 이미 존재했으나(앱명 `Tenbagger`), 로컬에 클론이 없어 `.env`가 전부
  placeholder 상태였다. → `threads-auto/SETUP.md` 절차대로 재설정.
- **App Secret은 화면에 평문으로 안 보이고 "복사됨" 토스트만 뜬다** — 클립보드에 담긴
  상태이므로 다른 걸 복사하기 전에 즉시 `.env`에 붙여넣어야 한다.
- 액세스 토큰은 앱 → 이용 사례 → Threads API 액세스 → **맞춤 설정 → 설정 탭** →
  "사용자 토큰 생성기"에서 발급. 여기서 나오는 건 이미 60일 장기 토큰이라,
  실행 시 뜨는 "장기 토큰 교환 실패" 경고는 무시해도 된다(정상 동작).
  ⚠️ 단 60일 후 자동 갱신이 안 되므로 재발급 필요 — **만료 예상: 2026-10-26 무렵**
