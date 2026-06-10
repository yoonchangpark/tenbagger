---
description: DB 스코어 현황 요약 — 등급별 종목 수, 평균 점수, 최근 분석 시간 출력.
---

텐배거 DB 스코어 현황을 확인합니다.

다음 명령들을 실행하세요:

1. 등급별 통계:
   ```
   docker exec tenbagger_db psql -U tenbagger -d tenbagger -c \
   "SELECT grade, COUNT(*) as 종목수, ROUND(AVG(total_score)::numeric,2) as 평균점수, ROUND(AVG(growth_score)::numeric,2) as 평균성장점수 FROM scores WHERE grade IS NOT NULL GROUP BY grade ORDER BY AVG(total_score) DESC;"
   ```

2. 최근 분석 시간:
   ```
   docker exec tenbagger_db psql -U tenbagger -d tenbagger -c \
   "SELECT MAX(analyzed_at) as 최근분석, COUNT(*) as 전체종목수 FROM scores;"
   ```

결과를 아래 형식으로 보여주세요:

**📊 텐배거 DB 현황** (최근 분석: YYYY-MM-DD HH:MM)

| 등급 | 종목 수 | 평균 점수 | 평균 성장점수 |
|------|---------|----------|-------------|
| ✨ TENBAGGER | N | X.X | X.X |
| 💙 COMPOUNDER | N | X.X | X.X |
| 🌟 WATCHLIST | N | X.X | X.X |
| 🚫 AVOID | N | X.X | X.X |

전체 분석 종목: N개

DB 연결 실패 시 "docker ps로 컨테이너 상태를 확인해주세요." 안내.
