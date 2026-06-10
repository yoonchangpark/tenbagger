---
description: KOSPI/KOSDAQ 전종목 ETL 실행. 인수 없으면 skip-existing 모드, "full"이면 전체 재수집.
---

텐배거 ETL을 실행합니다. 인수: $ARGUMENTS

다음 순서로 진행하세요:

1. 인수가 "full"이면 `--skip-existing` 없이, 그 외에는 `--skip-existing` 포함해서 아래 명령을 실행하세요:
   ```
   docker exec tenbagger_api python -m app.workers.etl --market ALL --skip-existing
   ```

2. 실행 중 로그를 스트리밍하지 말고, 완료 후 결과만 요약하세요.

3. 완료 후 `/tenbagger:score-check`를 자동으로 실행해서 현재 분석 현황을 보여주세요.
