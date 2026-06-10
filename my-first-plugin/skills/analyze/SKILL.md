---
description: 종목코드로 텐배거 분석 실행 후 핵심 지표 요약. 예) /tenbagger:analyze 005930
---

종목 분석을 실행합니다. 종목코드: $ARGUMENTS

다음 순서로 진행하세요:

1. 종목코드가 없으면 "종목코드를 입력해주세요. 예) /tenbagger:analyze 005930" 안내 후 종료.

2. 기본 스코어 조회:
   ```
   curl -s http://localhost:8000/api/company/$ARGUMENTS
   ```

3. AI 정성 분석 조회 (병렬):
   ```
   curl -s http://localhost:8000/api/v2/company/$ARGUMENTS/qualitative
   ```

4. 결과를 아래 형식으로 요약해서 보여주세요:

   **[종목명] ($ARGUMENTS) — [등급]**
   
   | 항목 | 값 |
   |------|-----|
   | 종합점수 | X.X / 10 |
   | 성장성 | X.X |
   | 안정성 | X.X |
   | 현금흐름 | X.X |
   | 배당수익률 | X.X% |
   | 매출CAGR 5년 | X.X% |
   | ROE 평균 | X.X% |
   
   **AI 투자의견**: (qualitative 결과 2~3줄 요약)
   
   **판단**: TENBAGGER/COMPOUNDER/WATCHLIST/AVOID 등급 근거 한 줄

5. API 오류 시 "분석 데이터가 없습니다. ETL 실행 후 다시 시도해주세요." 안내.
