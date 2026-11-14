# Threads 자동 발행

**목적**: 텐배거를 만드는 과정을 AS-IS → TO-BE 빌드로그로 주 3~4회 공개하고, 사람들이 어디에 반응하는지 본다.

**언제 쓰나**: "스레드 글이 안 올라갔어", "다음 스레드 글 뭐 나가?", "스레드 요일 바꾸자".

**입력과 위치**
- 글감: `CHANGELOG.md` 최근 항목 (`### 배경` = AS-IS, `### 추가/변경/수정` = TO-BE)
- 실행: tenbagger 백엔드 APScheduler (`backend/app/main.py`)
  - `threads_refill` 일 21:00 KST — 대기 글이 3건(`QUEUE_TARGET_PENDING`)보다 적으면 보충
  - `threads_publish` 월·수·금 18:30 KST (`THREADS_POST_DAYS`, `THREADS_POST_TIME`으로 변경)

## 단계
1. **INTERVIEW** — 아직 글이 되지 않은 CHANGELOG 항목을 최신 5개(`MAX_BACKLOG`) 안에서 고른다.
2. **MANUAL** — 아래 판단 규칙.
3. **TOOLBOX** — 토큰은 DB에 있고 만료 5일 전 자동 갱신 (PR #117). 손댈 것 없음.
4. **EXECUTE** — `devlog_generator.refill()`이 글을 만들어 큐에 넣고, `publisher.publish_next()`가 한 건 발행. 첨부 이미지가 아직 배포 전이면 그 글을 남기고 다음 글을 낸다.
5. **VERIFY** — `proof.md`. 큐 확인은 `python main.py --list`.

## 판단 규칙
- 종목 추천이 아니라 작업 기록 톤. 투자 고지 없음, 마지막 줄은 반응을 묻는 질문.
- 파일명·함수명·환경변수명을 쓰지 않는다. 독자가 화면에서 겪는 일로 옮긴다.
- CHANGELOG에 없는 숫자를 만들지 않는다.
- 발행 전 사람 검토 없음 (2026-09-21 오너 결정). 승인 게이트를 임의로 추가하지 않는다.
- 발행·큐 파일은 이 업무 담당만 고친다. 카드뉴스 쪽 접점은 `queue.add(text, image_urls, source_key)` 하나.

## 완료 기준
월·수·금 18:30에 글 1건이 Threads에 올라가고, 그 글이 어느 CHANGELOG 항목에서 왔는지 `source_key`로 추적된다.

## 알려진 차이
- 발행 후 반응(조회·좋아요·답글)을 수집하는 코드가 없다. "반응 보고 다듬자"는 목적의 VERIFY가 비어 있다.
- CHANGELOG 개발이 멈추면 글감도 멈춘다.

## 과거 엣지케이스
- 2026-09-23 첫 큐 3건은 오너가 콘솔로 직접 넣은 것이다. 세션이 넣은 글과 구분할 것.
- `preview.py`는 JSON 파일 큐만 읽고 브라우저를 열어 서버 콘솔에서는 못 쓴다.
