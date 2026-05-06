# 텐배거 헌터 Phase 2~5 실행 안내

## 1. Docker 재빌드 (DB 스키마 초기화 포함)

```bash
# tenbagger 폴더에서 실행
cd C:\Users\00LG00\Desktop\tenbagger

# 기존 볼륨 포함 완전 초기화 (init.sql 새 테이블 적용)
docker-compose down -v
docker-compose up --build -d

# 상태 확인
docker-compose logs -f backend
```

## 2. 접속 URL

| 서비스 | URL |
|--------|-----|
| 종목 분석 | `frontend/index.html` (브라우저에서 직접 열기) |
| 스크리너 | `frontend/screener.html` |
| 백테스트 | `frontend/backtest.html` |
| API 문서 | http://localhost:8000/docs |

## 3. 새로 추가된 API 엔드포인트

```
GET /api/search?q=삼성전자           ← 종목명 자동완성
GET /api/company/{ticker}?force=true ← 캐시 무시 강제 재분석
GET /api/company/{ticker}/simulate   ← 5년 시나리오 시뮬레이터
GET /api/screener                    ← 종목 스크리너 (필터 파라미터)
GET /api/screener/summary            ← 등급별 통계 요약
GET /api/backtest/{ticker}?base_year=2015  ← 개별 백테스트
GET /api/backtest/market?base_year=2015    ← 시장 전체 백테스트
```

## 4. 전체 시장 ETL 실행 (Phase 3 스크리너 활용 전 필수)

```bash
# 전체 KOSPI + KOSDAQ 분석 (약 2~3시간)
docker exec -it tenbagger_api python -m app.workers.etl

# KOSPI만 빠르게 (약 1시간)
docker exec -it tenbagger_api python -m app.workers.etl --market KOSPI

# 테스트용 100개만
docker exec -it tenbagger_api python -m app.workers.etl --limit 100

# 24시간 내 분석된 종목 건너뛰기
docker exec -it tenbagger_api python -m app.workers.etl --skip-existing
```

## 5. 테스트 추천 종목

| 종목 | 코드 | 특징 |
|------|------|------|
| 삼성전자 | 005930 | 우량주 기준 |
| POSCO홀딩스 | 005490 | 배당주 |
| 셀트리온 | 068270 | 성장주 |
| 두산에너빌리티 | 034020 | 낮은 점수 확인용 |

## 6. Phase별 변경 사항 요약

### Phase 2 - 종목명 검색 + DB 캐싱
- `GET /api/search?q=` 자동완성 검색
- 분석 결과 24시간 DB 캐시 (DART 재호출 없음)
- 프론트엔드: 드롭다운 자동완성 + 최근 분석 히스토리

### Phase 3 - 전체 시장 스크리너
- `python -m app.workers.etl` 전체 시장 순차 분석
- `GET /api/screener` 필터 기반 종목 검색
- `screener.html`: 등급/점수/배당/부채 필터 UI

### Phase 4 - 5년 시나리오 시뮬레이터
- `GET /api/company/{ticker}/simulate` Bear/Base/Bull 시나리오
- 텐배거 달성에 필요한 EPS CAGR 역산
- `index.html` 하단 슬라이더 UI 추가

### Phase 5 - 백테스트
- `GET /api/backtest/{ticker}?base_year=2015` 개별 백테스트
- `GET /api/backtest/market` 시장 전체 백테스트
- `backtest.html`: 산점도 차트 + 등급별 수익률 요약
