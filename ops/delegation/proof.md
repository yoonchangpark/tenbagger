# 작업 위임 — Proof (모든 코드 작업 공통)

각 항목은 명령 출력이나 링크로 판정한다. "확인했다"는 문장만으로는 통과가 아니다.

| # | 체크 | 판정 방법 |
|---|------|----------|
| 1 | 브리프의 완료 기준 문장이 사실이다 | 그 문장을 확인한 출력·링크·스크린샷을 답장에 첨부 |
| 2 | 수정한 Python 파일 AST 통과 | `python3 -c "import ast,sys; [ast.parse(open(f).read()) for f in sys.argv[1:]]" <파일들>` 종료코드 0 |
| 3 | v1 `/api/*` 라우트 변화 없음 | `git diff origin/main -- backend/app/api/company.py backend/app/api/screener.py backend/app/api/backtest.py` 에 라우트 데코레이터·응답 키 변경 0줄 |
| 4 | CHANGELOG 번호가 유일 | `git fetch origin main` 후 main과 열린 PR 브랜치들의 `grep '^## v' CHANGELOG.md`에 내 번호가 한 번만 |
| 5 | CHANGELOG 새 항목 구조 | 내 항목 아래에 `### 배경` 1개, `### 추가`/`### 변경`/`### 수정` 중 1개 이상 |
| 6 | 같은 영역을 만지는 열린 PR 없음 | 열린 PR 목록의 변경 파일과 내 변경 파일 교집합 0 (있으면 그 스레드에 알림) |
| 7 | 되돌릴 수 없는 단계의 승인 출처 | 이 스레드에서 오너가 쓴 메시지 링크. 다른 스레드가 전한 승인은 불인정 |
| 8 | 오너가 할 일이 답장 첫 줄에 있음 | 답장 첫 문장 확인 (없으면 "없음"이라고 명시) |
