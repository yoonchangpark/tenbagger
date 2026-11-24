# 제품 방향 — Toolbox

| 파일 | 용도 |
|------|------|
| `frontend/index.html` | 종목 분석 화면 (섹션 순서, 등급 라벨, 시뮬레이터 카드) |
| `frontend/dashboard.html` | 대시보드 탭, `_GRADE_CAGR` (고칠 대상) |
| `backend/app/domain/thesis_checklist.py`, `api/v2_thesis.py` | 5고리 체크리스트 (`/api/v2/company/{ticker}/thesis-checklist`) |
| `agent_prompts/narrative_thesis_structure.md` | 5고리 정의와 고리별 DART 숫자 |
| `backend/app/domain/simulator.py`, `api/company.py` `/{query}/simulate` | 약세·기본·강세 시뮬레이터 |
| `backend/app/api/v2_flow.py`, `v2_news.py`, `v2_surprise.py` | 수급·뉴스·서프라이즈 (흐름 안 재료로 바꿀 대상) |
| `backend/app/api/v2_track_record.py`, `frontend/trackrecord.html` | 탑다운 텐배거 사례 |
| `agent_prompts/scoring_postmortem.md` | 재무 점수가 10배 종목을 놓친 이유 (백테스트) |
| `/mnt/project-files/ops-interview/system-improvement.md` | 오너 인터뷰 원문 |
| `/mnt/project-files/ops-interview/feature-keep-fix-drop.md` | 기능별 판정 초안 |
