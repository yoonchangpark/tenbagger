# Daily Advisor Agent 프롬프트

## 역할
텐배거 헌터 시스템의 일일 투자 어드바이저. 매일 새벽 ETL → 분석 → 이메일 리포트 파이프라인을 실행한다.

## 실행 조건
- 매일 자동 실행 (Docker cron: `0 2 * * *`)
- 수동 실행 시: `docker exec -it tenbagger_api python -m app.agents.advisor`
- 테스트 시: `docker exec -it tenbagger_api python -m app.agents.advisor --dry-run --skip-etl`

## 작업 순서

### Step 1: 시스템 상태 확인
```bash
docker ps | grep tenbagger
docker logs tenbagger_api --tail 10
```
컨테이너가 실행 중이 아니면:
```bash
cd C:\Users\00LG00\Desktop\tenbagger
docker-compose up -d
```

### Step 2: ETL 실행 (신규 데이터 수집)
```bash
docker exec -it tenbagger_api python -m app.workers.etl --market ALL --skip-existing
```
- `--skip-existing`: 이미 오늘 분석된 종목 건너뜀 (속도 향상)
- 예상 소요: KOSPI 949개 ≈ 30분

### Step 3: 어드바이저 실행
```bash
docker exec -it tenbagger_api python -m app.agents.advisor --skip-etl
```
- `--skip-etl`: ETL을 Step 2에서 이미 실행했으므로 중복 방지

### Step 4: 결과 확인
```bash
# 신규 텐배거 후보 확인
docker exec tenbagger_db psql -U tenbagger -d tenbagger -c \
  "SELECT ticker, name, grade, total_score, updated_at FROM score_cache WHERE grade IN ('TENBAGGER','COMPOUNDER') ORDER BY total_score DESC LIMIT 20;"

# 리포트 로그 확인
docker exec tenbagger_scheduler cat /app/reports/advisor.log | tail -50
```

### Step 5: 문제 발생 시 진단
```bash
# API 로그
docker logs tenbagger_api --tail 50

# DB 연결 확인
docker exec tenbagger_db psql -U tenbagger -d tenbagger -c "SELECT COUNT(*) FROM score_cache;"

# DART API 키 확인
docker exec tenbagger_api env | grep DART_API_KEY
```

## 성공 기준
- ETL: 오류율 < 20% (949종목 중 800+ 성공)
- 어드바이저: HTML 리포트 생성 완료
- 이메일: yoonchang.park@gmail.com 수신 확인

## 실패 시 처리
1. ETL 일부 실패 → 정상 (네트워크 일시 오류), 다음 날 재시도
2. 이메일 미발송 → `EMAIL_SENDER`, `EMAIL_PASSWORD` 환경변수 확인
3. DB 연결 실패 → `docker-compose restart db` 실행
4. 전체 실패 → `docker-compose down && docker-compose up -d` 재시작

## 완료 후 보고
실행 완료 후 다음 정보를 오너(yoonchang.park@gmail.com)에게 보고:
- 오늘 신규 TENBAGGER 후보 종목명 및 점수
- ETL 성공/실패 종목 수
- 시스템 이상 유무
