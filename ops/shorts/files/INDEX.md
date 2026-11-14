# 숏츠 — Toolbox

| 파일 | 리포 | 용도 |
|------|------|------|
| `data/tenbagger_history.json` | aiva_server | 회고편 소재 (DART 검증 숫자) |
| `backend/app/api/v2_shorts_feed.py` | tenbagger | 전망편 소재 `CANDIDATES`, 회고 서사 `RETRO_NARRATIVES` |
| `run_scheduled_publish.py` | aiva_server | Railway Cron 진입점 |
| `server.py` `run_one_cycle()` | aiva_server | 한 회차 전체 실행 |
| `historical_topic.py` / `tenbagger_topic.py` | aiva_server | 회고편·전망편 대본 지시 (서사 5단 구조) |
| `tenbagger_card.py` | aiva_server | 분석 카드 클립 |
| `evaluator/alignment.py` | aiva_server | 대본 평가 (70점 기준) |
| `youtube_uploader.py` | aiva_server | YouTube 업로드, 설명란 구성 |
| `review/capture.py` | aiva_server | 발행 페이지 스크린샷 |
| `data/videos.json`, `data/metrics.json` | aiva_server | 발행 기록, 조회수 시계열 |
| `evolution/prompt_engine.py` | aiva_server | 성과로 프롬프트 규칙 조정 |
| `content/shorts/001_*.md` | tenbagger | 손으로 만든 완성본 예시 (승인된 예시) |
