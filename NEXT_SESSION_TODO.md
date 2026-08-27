# 다음 세션 할 일 (2026-08-27 작성)

> 오늘(8/27) 첫 영상 YouTube 업로드 + Threads 카드뉴스 발행까지 완료.
> 이어서 진행할 작업 3가지를 우선순위 순으로 기록한다.

---

## 1. aiva_server를 GitHub에 push (최우선 — 2번의 선행 조건)

**왜**: aiva_server는 현재 사용자 Windows PC 로컬(`C:\Users\yoonc\OneDrive\DIGITAL NOMAD\aiva_server`)
에만 있어서, Claude가 파일을 직접 못 읽는다. 오늘 작업 내내 사용자가 PowerShell 출력을
복붙 → Claude가 패치 스크립트 작성 → 사용자가 실행하는 왕복이 반복됐다(매우 느림).

**할 일**:
- 로컬 aiva_server를 `https://github.com/yoonchangpark/aiva_server.git`에 push
  (레포는 이미 생성돼 있음)
- ⚠️ `.env`는 절대 커밋하지 말 것 — `.gitignore` 확인 필수
  (ELEVENLABS_API_KEY, OPENAI_API_KEY, META 토큰 등 포함)
- 이후 세션에서 Claude가 clone해서 직접 읽기/수정 가능해짐
- 단, 현재 세션의 GitHub MCP 도구는 `yoonchangpark/tenbagger`로만 제한돼 있어
  aiva_server에 PR을 올리려면 세션 환경 설정 변경이 필요할 수 있음
  (최소한 `git clone`으로 읽는 것은 가능)

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
