# 텐배거 헌터 — Claude 세션 온보딩 가이드

> **목적**: 이 문서를 읽으면 새 Claude 세션이 프로젝트 전체 맥락을 즉시 이해하고 작업을 이어받을 수 있다.

---

## 1. 프로젝트 개요

**텐배거 헌터** — 한국 주식 장기투자 분석 유료 구독 서비스  
KOSPI/KOSDAQ 전종목을 DART 재무데이터로 분석해 10배 수익 가능성이 있는 "텐배거" 종목을 자동 발굴한다.

- **오너**: yoonchang.park@gmail.com  
- **배포 URL**: `https://tenbagger-production.up.railway.app`  
- **GitHub**: `https://github.com/yoonchangpark/tenbagger` (main 브랜치)  
- **로컬 경로**: `C:\Users\00LG00\Desktop\tenbagger`  
- **오늘 날짜**: 2026-05-10

---

## 2. 시스템 구조

```
tenbagger/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── company.py           # GET /api/company/{ticker}
│   │   │   ├── screener.py          # GET /api/screener
│   │   │   ├── backtest.py          # GET /api/backtest/{ticker}, /market, POST /explain
│   │   │   ├── qualitative.py       # GET /api/v2/company/{ticker}/qualitative
│   │   │   ├── v2_portfolio.py      # ★ 주가/시뮬레이터/트랙레코드 API
│   │   │   ├── v2_committee.py      # GET /api/v2/committee/{ticker}
│   │   │   ├── v2_news.py           # GET /api/v2/news/sentiment/{ticker} 등
│   │   │   ├── v2_dashboard.py      # GET /api/v2/dashboard/*
│   │   │   ├── admin.py             # GET /api/admin/status 등
│   │   │   ├── auth.py              # 카카오 OAuth + JWT
│   │   │   ├── kakao.py             # 카카오 챗봇 웹훅
│   │   │   ├── payment.py           # 토스페이먼츠
│   │   │   ├── watchlist.py         # 관심종목 CRUD
│   │   │   └── search.py            # 종목 검색
│   │   ├── domain/
│   │   │   ├── scoring.py           # ★ 핵심: 텐배거 스코어링 로직
│   │   │   ├── backtest.py          # 역사적 백테스트 엔진
│   │   │   └── qualitative_analysis.py  # OpenAI 정성 분석 (gpt-4o-mini)
│   │   ├── agents/
│   │   │   ├── advisor.py           # 일일 어드바이저 에이전트
│   │   │   └── committee.py         # ★ AI 투자위원회 (4개 에이전트 병렬)
│   │   ├── infra/
│   │   │   ├── clients/
│   │   │   │   ├── dart_client.py   # DART OpenAPI 클라이언트
│   │   │   │   └── news_client.py   # 네이버 뉴스 API 클라이언트
│   │   │   └── repositories/
│   │   │       └── company_repo.py  # DB CRUD + save_score()
│   │   ├── workers/
│   │   │   ├── etl.py               # 전종목 ETL (DART → PostgreSQL)
│   │   │   └── news_worker.py       # 뉴스 감성 분석 워커
│   │   └── core/
│   │       ├── config.py            # Settings (환경변수)
│   │       └── database.py          # SQLAlchemy SessionLocal
│   └── requirements.txt
├── frontend/
│   ├── index.html      # 종목 상세 분석 (정성분석 + AI 투자위원회)
│   ├── screener.html   # 전체 시장 스크리너
│   ├── backtest.html   # 백테스트 + 포트폴리오 트랙레코드 ★신규
│   ├── dashboard.html  # 워치리스트 + 시뮬레이터 + 신뢰도 배너 ★신규
│   ├── pricing.html    # 요금제 페이지
│   └── login.html      # 카카오 로그인
├── scripts/
│   └── init.sql        # DB 스키마 (전체 테이블 정의)
├── CLAUDE.md           # Claude Code 에이전트 가이드 (코드 규칙 포함)
└── CHANGELOG.md        # 버전별 변경사항
```

---

## 3. 스코어링 시스템

| 카테고리 | 가중치 | 핵심 지표 |
|---------|--------|----------|
| 성장성 (growth) | 30% | 매출 CAGR, EPS CAGR |
| 안정성 (stability) | 20% | ROE, 부채비율, 유동비율 |
| 현금흐름 (cashflow) | 20% | FCF 마진, CFO 안정성 |
| 배당 (dividend) | 15% | 배당수익률, 배당성장률 |
| 일관성 (consistency) | 15% | 흑자 연속성, 이익률 안정성 |

**등급 기준:**
- **TENBAGGER**: 총점 7.5+ AND 성장성 8.0+
- **COMPOUNDER**: 총점 6.5+
- **WATCHLIST**: 총점 5.0+
- **AVOID**: 그 외

---

## 4. 핵심 API 엔드포인트

```
# v1 (불변)
GET  /api/company/{ticker}              종목 전체 분석
GET  /api/screener                      전체 시장 스크리닝
GET  /api/backtest/{ticker}             개별 종목 백테스트
GET  /api/backtest/market               시장 전체 백테스트
POST /api/backtest/explain              AI 백테스트 설명
GET  /api/admin/status                  시스템 상태

# v2 (신규 기능)
GET  /api/v2/company/{ticker}/qualitative      정성 분석 (DART 기반, OpenAI)
GET  /api/v2/committee/{ticker}                AI 투자위원회 (4에이전트 병렬)
GET  /api/v2/news/sentiment/{ticker}           뉴스 감성 + 주가 모멘텀
GET  /api/v2/news/signals                      BUY_SIGNAL 종목 목록
GET  /api/v2/price/history/{ticker}            주가 히스토리 (pykrx → cache)
GET  /api/v2/price/sparkline/{ticker}          52주 스파크라인
GET  /api/v2/portfolio/simulate                가상 포트폴리오 시뮬레이션
GET  /api/v2/portfolio/track-record            ★ 시스템 신뢰도 트랙레코드 (신규)
GET  /api/v2/search                            종목 검색
```

---

## 5. 완료된 개발 (Phase 1~6 + α)

### Phase 1: ETL 안정화 ✅
- `etl_run_state` 테이블로 이어받기 로직
- `score_history` 스냅샷 (매 ETL 실행 시 저장)

### Phase 2: 주가 히스토리 ✅
- `GET /api/v2/price/history/{ticker}` — pykrx → `price_daily_cache` 테이블 캐시
- `backtest.html` — Lightweight Charts 주가 차트

### Phase 3: 포트폴리오 시뮬레이터 ✅
- `GET /api/v2/portfolio/simulate` — `scores + price_daily_cache` 기반
- `dashboard.html` — 시뮬레이터 탭, 기간 선택, 비중 방식 선택

### Phase 4: 스파크라인 ✅
- `dashboard.html` 워치리스트 카드에 52주 스파크라인 표시

### Phase 5: 뉴스 감성 분석 ✅
- 네이버 뉴스 API → 종목별 감성 점수 (-1 ~ +1)
- `news_articles`, `news_sentiment`, `sector_sentiment` 테이블
- **주가 모멘텀 연동**: 1M/3M/6M 수익률 계산 + 뉴스-주가 alignment 분석

### Phase 6: AI 투자위원회 ✅
- `backend/app/agents/committee.py` — 4개 에이전트 asyncio.gather 병렬 실행
  - 재무 분석가 (DART 재무 기반)
  - 뉴스 분석가 (뉴스 감성 + 주가 모멘텀)
  - 업종 분석가 (섹터 환경)
  - 투자 전략가 (종합 의견 → BUY/HOLD/AVOID)
- `GET /api/v2/committee/{ticker}`

### 정성분석 vs 투자위원회 역할 분리 ✅
- **정성분석 (Layer 1)**: DART 기반 기업 본질 평가, 시간 무관
  - 결과: `long_term_potential` = STRONG / MODERATE / WEAK
- **투자위원회 (Layer 2)**: 현재 시점 매수·관망·회피 판단
  - 결과: `committee_decision` = BUY / HOLD / AVOID + confidence

### 포트폴리오 트랙레코드 ✅ (최신, 2026-05-10)
- `GET /api/v2/portfolio/track-record` — 시스템 신뢰도 검증
  - base_year 당시 DART 재무로 스코어 재계산
  - 당시 TENBAGGER 종목 equal-weight 포트폴리오
  - hold_years 후 실제 주가 수익률 집계
  - KOSPI 연말 종가 대비 알파 계산
  - asyncio.Semaphore(5)로 DART 과부하 방지 + 인메모리 캐시
- `backtest.html` — "📈 포트폴리오 검증" 탭 신규 추가
- `dashboard.html` — 시뮬레이터 탭 상단 신뢰도 배너

---

## 6. 미완료 (다음 개발 대상)

| Phase | 기능 | 상태 | 예상 작업 |
|-------|------|------|-----------|
| Phase 7 | 구독 가치 지표 대시보드 | ⬜ 미착수 | score_history 30일 이상 누적 후 의미 |
| Phase 8 | 프리미엄 게이팅 (위원회 API) | ⬜ 미착수 | JWT 구독 tier 확인 로직 |
| - | ETL 완료 (전종목 2,000개) | 🔄 진행 중 | 현재 일부 종목만 완료 |
| - | cron-job.org 자동화 | ⬜ 미착수 | 외부 스케줄러 등록 필요 |

---

## 7. DB 주요 테이블

```sql
scores          -- 현재 스코어 (ETL 결과)
score_history   -- 일별 스냅샷 (2026-05-09~ 쌓이는 중)
price_daily_cache  -- pykrx 주가 캐시 (ticker, trade_date, ohlcv)
news_articles   -- 뉴스 기사 + 감성 점수 (article_hash 중복 방지)
news_sentiment  -- 종목별 일간 뉴스 감성 집계
sector_sentiment-- 업종별 일간 감성 집계
watchlists      -- 사용자 관심종목
users           -- 카카오 OAuth 사용자
etl_run_state   -- ETL 이어받기 상태
```

---

## 8. 인프라 & 환경변수

```bash
# Railway 환경변수 (설정 완료)
DART_API_KEY          # DART OpenAPI
OPENAI_API_KEY        # gpt-4o-mini 사용
NAVER_CLIENT_ID       # 네이버 뉴스 API
NAVER_CLIENT_SECRET
DATABASE_URL          # PostgreSQL (Railway 내부)
JWT_SECRET_KEY
KAKAO_REST_API_KEY
TOSS_CLIENT_KEY
```

---

## 9. 자주 쓰는 커맨드

```bash
# ETL 실행 (Railway 배포된 서버 기준 → docker 없음, Railway CLI 사용)
# 로컬에서는:
docker exec -it tenbagger_api python -m app.workers.etl --market ALL --skip-existing

# 특정 종목 분석
curl https://tenbagger-production.up.railway.app/api/company/005930

# 트랙레코드 테스트 (2019년 3년 보유)
curl "https://tenbagger-production.up.railway.app/api/v2/portfolio/track-record?base_year=2019&hold_years=3&limit=30"

# 포트폴리오 시뮬레이션
curl "https://tenbagger-production.up.railway.app/api/v2/portfolio/simulate?grade_filter=TENBAGGER&period=3m"

# AI 투자위원회
curl "https://tenbagger-production.up.railway.app/api/v2/committee/005930"
```

---

## 10. 코드 작성 규칙 (CLAUDE.md 요약)

1. **v1 API 절대 불변**: `/api/*` 시그니처 변경 금지
2. **신기능은 `/api/v2/*`**: 항상 v2 prefix
3. **CHANGELOG.md 업데이트**: 모든 변경사항 기록
4. **Python AST 검증**: 수정 후 반드시 `python3 -c "import ast; ast.parse(open('파일', encoding='utf-8').read())"` 실행
5. **uvicorn --reload 자동 반영**: docker restart 불필요

---

## 11. 최근 이슈 & 해결

| 이슈 | 원인 | 해결 |
|------|------|------|
| 시뮬레이터 "insufficient_data" | `entry_row.trade_date < sim_start` strict `<` 버그 | `entry_row is not None` 으로 변경 |
| screener.html 한글 깨짐 | UTF-8 바이트 손상 (0x3F 대체) | 파일 전체 재작성 |
| 스크리너 필터 미작동 | toggleGrade() 등에 runScreener() 호출 누락 | 각 함수에 runScreener() 추가 |
| 투자위원회 역할 중복 | ai_investment_opinion이 BUY/HOLD/AVOID 반환 | long_term_potential (STRONG/MODERATE/WEAK)로 분리 |

---

## 12. 현재 프론트 화면 구조

```
index.html      종목코드 입력 → 재무지표 + 스코어 + 정성분석 (DART 기반) + AI 투자위원회 + 뉴스 감성
screener.html   좌: 필터패널(sticky) / 우: 스크리닝 결과 테이블 (등급/정렬/점수 필터)
backtest.html   탭1: 개별 종목 / 탭2: 시장 전체 / 탭3: 포트폴리오 검증(트랙레코드) ★신규
dashboard.html  탭: 텐배거랭킹 / 관심종목(스파크라인) / 시뮬레이터(신뢰도배너★) / 공시 / 종목분석 / 뉴스
```

---

## 13. 다음 세션에서 바로 시작하는 방법

```
새 Claude 세션에서:
1. 이 ONBOARDING.md를 읽어 맥락 파악
2. 작업할 기능 선택 (위 섹션 6 참고)
3. 필요한 파일을 Read 도구로 확인 후 수정
4. AST 검증 → git commit → git push → Railway 자동 배포
```

**Railway 자동 배포**: `git push origin main` 후 약 90초 대기.
