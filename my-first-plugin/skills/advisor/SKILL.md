---
description: 일일 어드바이저 리포트 실행 (dry-run, 이메일 미발송). 신규 텐배거 탐지 + GPT 투자의견 출력.
---

일일 어드바이저 리포트를 실행합니다.

다음 명령을 실행하세요:
```
docker exec tenbagger_api python -m app.agents.advisor --dry-run --skip-etl
```

실행 후:
1. 새로 발굴된 TENBAGGER 종목 목록을 표로 정리
2. 주목할 만한 종목 2~3개에 대해 한 줄 투자의견 추가
3. "실제 이메일 발송은 --dry-run 제거 후 실행하세요." 안내
