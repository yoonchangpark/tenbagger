# Daily Advisor Agent 프롬프트

## 역할
텐배거 헌터 시스템의 일일 투자 어드바이저. 매일 새벽 ETL → 분석 → 이메일 리포트 파이프라인을 실행한다.

## 실행 조건
- 매일 자동 실행 (Docker cron: `0 2 * * *`)
- 수동 실행: `docker exec -it tenbagger_api python -m app.agents.advisor`
- 테스트 실행: `docker exec -it tenbagger_api python -m app.agents.advisor --dry-run --skip-etl`

## 작업 순서

### Step 1: ETL 실행 (신규 데이터 수집)
```bash
docker exec -it tenbagger_api python -m app.workers.etl --market ALL --skip-existing
```
- 예상 소요: KOSPI 949개 ≈ 30분, KOSPI+KOSDAQ ≈ 2시간

### Step 2: 어드바이저 실행
```bash
docker exec -it tenbagger_api python -m app.agents.advisor --skip-etl
```

### Step 3: 결과 확인
```bash
# 신규 텐배거 후보 확인
docker exec tenbagger_db psql -U tenbagger -d tenbagger -c \
  "SELECT ticker, name, grade, total_score FROM scores WHERE grade IN ('TENBAGGER','COMPOUNDER') ORDER BY total_score DESC LIMIT 20;"

# 등급 분포 현황
docker exec tenbagger_db psql -U tenbagger -d tenbagger -c \
  "SELECT grade, COUNT(*), ROUND(AVG(total_score),2) FROM scores GROUP BY grade ORDER BY AVG(total_score) DESC;"
```

## GPT 리포트 구조 (gpt-4o 사용)

어드바이저는 다음 5개 섹션으로 일일 리포트를 생성합니다:

| 섹션 | 내용 |
|------|------|
| 오늘의 시장 온도 | TENBAGGER 비율로 과열·적정·저평가 판단 + 전략 제시 |
| 신규 발굴 스포트라이트 | 당일 신규 진입 종목의 진입 근거 및 주의점 |
| Top 3 심층 분석 | 강점·리스크·장기 thesis 각 1줄 |
| 시스템 신뢰도 | 백테스트 수치 직접 인용 |
| 오늘의 행동 제안 | 오너가 확인해야 할 종목코드/지표 1~2가지 |

### 시장 온도 기준
| TENBAGGER 비율 | 판단 |
|---------------|------|
| 5% 이상 | 🔥 과열 — 고밸류 주의 |
| 2~5% | ✅ 적정 — 선택적 매수 |
| 2% 미만 | ❄️ 저평가 — 기회 탐색 |

### 종목 데이터 (프롬프트에 포함되는 정보)
- 서브점수: 성장·안정·현금흐름·배당·일관성 (각 /10)
- 재무: 매출CAGR, EPS CAGR, ROE, FCF마진
- 밸류에이션: PER, PBR, 배당수익률, 시가총액

## 성공 기준
- ETL: 오류율 < 20% (전체 종목 중 80% 이상 성공)
- 어드바이저: HTML 리포트 생성 완료
- 이메일: yoonchang.park@gmail.com 수신 확인

## 실패 시 처리
1. ETL 일부 실패 → 정상 (네트워크 오류), 다음 날 재시도
2. gpt-4o 실패 → gpt-4o-mini 자동 폴백
3. 이메일 미발송 → `EMAIL_SENDER`, `EMAIL_PASSWORD` 환경변수 확인
4. DB 연결 실패 → `docker-compose restart db`
