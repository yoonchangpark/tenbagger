# 텐배거 헌터 - 버전 기록

---

## v4.9 — 홈 배당/투자 섹션 혼선 정리 (순서·라벨 차별화) (2026-06-25)

### 문제 (UX/PM 합동 검토)
- "💰 배당 계산기"(목표 역산)와 "🔥 실제로 투자했다면"(과거 적립식 사례)이 둘 다 '배당' 간판·'시뮬레이션' 명칭·동일 CTA 목적지(`dashboard.html#dividend`)를 써서 첫 방문자가 **중복으로 오인**.

### 수정 (구조 변경 없음, 프론트 카피·순서)
- **순서 교체**: 감정 후킹이 강한 "🔥 실제로 투자했다면"을 위로, "💰 배당 계산기"를 아래로 (증거 → 유틸 흐름)
- **역할 부제 추가**: 각 섹션 제목 아래 한 줄 설명 (과거 적립식 사례 / 목표 자본 역산)
- **CTA 라벨 분리**: "적립 투자 시뮬레이션 더 보기 →" / "목표 배당 시뮬레이터 열기 →"

---

## v4.8 — '오늘의 텐배거 후보' 카드에 종목 유튜브 자리 추가 (2026-06-25)

### 홈 (`index.html`)
- 피처드 카드 헤더(점수·등급 배지) 바로 아래에 **해당 종목 유튜브 슬롯** 추가
  - `VIDEOS[ticker]`에 영상이 있으면 빨간 유튜브 바(제목·길이·바로보기 ↗)로 표시, 카드 클릭과 분리(`stopPropagation`)되어 새 탭으로 열림
  - 영상이 없으면 "○○ 분석 영상 준비 중" 점선 플레이스홀더로 **자리만 예약** (레이아웃 흔들림 방지)

---

## v4.7 — 트랙레코드 상·하단 시간축 통일 (10년 보유 기준) (2026-06-25)

### 문제
- 홈 "예측 정확도" 위젯(상단)은 **2년 보유 백필**(+11%/0%/표본4), 그 아래 "2016→2026" 사례 카드(하단)는 **10년 보유 큐레이션**(+621%/+358%/…)으로 시간축이 달랐다.
- 상단 "검증 표본 4"가 우연히 하단 카드 4개와 같아 **같은 데이터처럼 오인**되고, "+50% 달성률 0%"가 +621% 텐배거 카드 바로 위에 놓여 **정면 모순**으로 보였다.

### 수정 — 상단을 10년 보유 기준으로 통일
- `get_backfill_summary(hold_years)` — 보유기간 필터 추가 (2년·10년 결과 혼입 방지), 응답에 `hold_years`·`meta.requested_hold_years` 노출 (`backfill.py`)
- `GET /api/v2/accuracy/backfill?hold_years=` 필터 파라미터 추가, `POST`의 `hold_years` 상한 6→10·기본 보유 10년·기본 base_years `2016`(하단 사례와 동일한 2016→2026 창) (`accuracy.py`)
- 홈/트랙레코드 위젯이 `?hold_years=10`만 조회하도록 변경, 범위 라벨 "시장 전수 · 10년 보유" 명시, 10년 집계 부재 시 "준비 중" 정직 안내 (2년 수치 오인 방지)

> ⚠️ 실제 10년 백필 데이터는 배포 백엔드에서 재실행 필요 (2016→2026 단일 창):
> `POST /api/v2/accuracy/backfill?base_years=2016&hold_years=10`

---

## v4.6 — 종목별 DCA 시뮬레이션 (홈 티저 + 대시보드 거치식/적립식) (2026-06-25)

### 홈 (`index.html`)
- "💰 배당 계산기" 다음에 **"🔥 실제로 투자했다면" DCA 티저 카드** 추가
  - 두산에너빌리티 고정 노출(추후 "오늘의 텐배거 후보" 연동 예정), 매월 50만원·10년 적립·배당 재투자 가정
  - 지금 평가자산 + 총 납입 + 수익률 + 평가자산/원금 라인 차트(Chart.js), "대시보드에서 자세히 보기 →" CTA
  - ⚠️ 현재 mock 데이터 — 실제로는 pykrx 종가 + DART 배당으로 배당 재투자 누적 계산 예정

### 대시보드 (`dashboard.html`)
- DCA 시뮬레이터를 **거치식(1,000만원) / 적립식(월 60만원) 2탭** 으로 확장
  - 거치식: 일시금 거치 후 연 복리 성장 / 적립식: 매년 원금 추가 + 누적 복리
- **단위 버그 수정**: 기존 적립식 계산이 `/10000`으로 값이 0에 수렴하던 문제 → 만원 단위 일관 처리

---

## v4.5 — 요소 발굴 엔진 v1 (2026-06-18)

### 요소 발굴 엔진 (`app/domain/factors/`, `/api/v2/factors`)
- **IC(Information Coefficient) 측정 엔진** (`ic_engine.py`): 스피어만 순위 상관으로 요소 예측력 수치화
- **Walk-forward 검증**: train(2018~2019) / test(2020) 분리 — 과적합 탐지 자동화 (train IC와 test IC 차이 >0.1 → overfit_flag)
- **기준선 IC** (`POST /api/v2/factors/ic/baseline`): v1 total_score의 IC 측정 → 이후 요소 개선 기준점
- **검색량 요소** (`search_volume.py`, `POST /api/v2/factors/ic/search-volume`): Naver DataLab 기준일 직전 3개월 검색 추세 → 0~10 점수 → IC 측정
- **IC 리더보드** (`GET /api/v2/factors/ic`): 요소별 train/test IC + overfit_flag 조회
- **모니터링 워크플로우** (`.github/workflows/backfill-completion-monitor.yml`): 30분마다 백필 완료 체크 → 완료 시 이슈 자동 생성

---

## v4.4 — 검증 정확도 백필 v1 (2026-06-14)

### 백필 엔진 (`app/domain/backfill.py`, `/api/v2/accuracy/backfill`)
- 점-인-타임 재구성: (종목 × 과거연도) 격자를 돌며 그 시점 재무로 등급 산출 → 실제 이후 수익률과 대조 → `backfill_results` 저장
- 등급별 집계: 중간값 수익률·적중률·표본수 (요소 발굴 엔진의 v1 기준선)
- **GET /api/v2/accuracy/backfill**: 집계 + 진행상황 / **POST**: 백그라운드 실행(연도·보유기간·종목수 파라미터)
- 룩어헤드 방지(시점 이전 재무만) + 정직성 한계 명시(상폐 제외 표본 = 낙관 편향 가능)
- trackrecord.html: 편향 없는 실시간 추적이 비면 백필 v1으로 보완 표시('백필 기준선 v1' 라벨 + 편향 경고)

---

## v4.3 — 백테스트 사례 유형 자동분류 + 트랙레코드 (2026-06-14)

### 백테스트 사례 유형 자동 판정 (`/api/backtest/{ticker}`)
- **`classify_case_type()` 신규**: 보유기간(base_year~end_year) 재무 추세를 DART에서 가져와 `success`/`frenzy`를 데이터로 자동 판정
  - 최종 연도 영업적자 OR 매출 정점대비 30%+ 하락 OR 영업이익 정점대비 70%+ 하락 → `frenzy`
  - 그 외 견조한 성장 → `success`
- 백테스트 응답에 `financial_trajectory`(연도별 매출·영업이익) + `case_analysis`(case_type·적자여부·하락률·근거) 추가
- 목적: 콘텐츠(쇼츠 대본) 생성 시 사람이 손으로 라벨링하던 success/frenzy 오분류를 구조적으로 제거 (예: 에코프로비엠 — 수동 success였으나 2024년 영업적자로 실제 frenzy)

### 트랙 레코드 공개 페이지 (`/api/v2/track-record`, `trackrecord.html`)
- 큐레이션 종목의 과거 예측 등급 vs 실제 수익률을 DART 공시 기반으로 투명 공개
- **생존자 편향 제거**: 큐레이션 승자 기반 적중률 집계를 폐기하고, 편향 없는 `accuracy_validator`(예측 시점 등급 전수 추적)를 '검증된 정확도'로 분리 표기. 큐레이션은 '주요 분석 사례(예시)'로 명확히 라벨
- 스크리너 CTA (시청자→서비스 유입 펀넬)

---

## v4.2 — ETL 외부 스케줄 트리거 (2026-06-14)

### GitHub Actions 일일 ETL (신규: `.github/workflows/daily-etl.yml`)
- **매일 02:00 KST(UTC 17:00) Railway ETL 엔드포인트 자동 호출** — GitHub 서버가 외부에서 발동하므로 Railway 컨테이너 재시작·로컬 PC 전원과 무관하게 100% 실행
- **workflow_dispatch**: Actions 탭에서 수동 즉시 트리거 가능
- 기존 in-process APScheduler(02:00 KST)와 동일 시각이나, v2_etl 가드가 중복 실행 차단 → 이중 안전장치
- 대상 주소는 repo Variable `TENBAGGER_API_BASE`로 덮어쓰기 가능 (기본값: 운영 주소)

---

## v4.1 — ETL 원격 트리거 + 실행 상태 가시화 (2026-06-11)

### ETL API (신규: `/api/v2/etl`)
- **POST /api/v2/etl/run**: ETL 백그라운드 즉시 실행 (market=ALL|KOSPI|KOSDAQ, skip_existing 기본 true). 실행 중이면 중복 거부
- **GET /api/v2/etl/status**: 실행 중 여부·시작/종료 시각·트리거 종류(manual/schedule)·에러·다음 스케줄 시각 조회
- **02:00 KST 일일 스케줄 잡 개선**: subprocess 방식 → v2_etl 가드 러너로 교체. 수동/스케줄 실행이 같은 가드를 공유해 중복 실행 방지 + status로 실행 기록 확인 가능

---

## v4.0 — 인증 강화 + UX 개선 + 어드바이저 고도화 (2026-05-10)

### 인증 시스템 개선
- **JWT TTL 연장**: 60분 → 8시간 (`ACCESS_TOKEN_EXPIRE_MINUTES=480`)
- **자동 토큰 갱신**: `POST /api/v2/auth/refresh` 엔드포인트 신규 추가
- **authFetch() 래퍼**: 401 응답 시 refresh_token으로 자동 재발급 후 재요청
- **카카오 닉네임**: 로그인마다 DB 갱신 (기존: 최초 1회만)

### 관심종목 & 검색 수정
- **오너 권한 bypass**: `admin_email` 설정으로 관심종목 Premium 제한 없이 사용
- **검색 fallback 체인**: `companies` 테이블 → `scores` 테이블 → pykrx (기존: companies → pykrx)
- **관심종목 카드 강화**: 5개 서브점수 미니 바 + PER·PBR·배당수익률 태그 추가
- **watchlist API**: 서브점수(cashflow/dividend/consistency) + 밸류에이션 필드 추가

### 포트폴리오 시뮬레이터
- **관심종목 시뮬레이션**: `tickers=` 파라미터로 관심종목 직접 시뮬레이션 지원
- **UI**: 관심종목 탭에 "📊 시뮬레이션" 원클릭 버튼 추가
- **pfGrade select**: "⭐ 내 관심종목" 옵션 추가

### 대시보드 UI
- **헤더**: 로그인/로그아웃 버튼 + 사용자 이름 표시
- **로고**: `<a>` 태그로 변경 → 클릭 시 `index.html` 이동
- **모바일 반응형** (`@media max-width:768px`):
  - 헤더 패딩 축소, 탭버튼 크기 조정
  - 2열 그리드(차트·시나리오·SWOT·AI분석) → 1열 전환
  - 섹션 헤더 세로 배치

### 어드바이저 (advisor.py) 고도화
- **GPT 모델**: `gpt-4o-mini` → `gpt-4o` (실패 시 mini 자동 폴백)
- **프롬프트 데이터**: 5개 서브점수 + PER·PBR·배당수익률·시총 + EPS CAGR + FCF마진 전달
- **시장 온도 판단**: TENBAGGER 비율 기반 과열/적정/저평가 자동 분류
- **리포트 구조**: 5개 섹션 (시장온도·신규스포트라이트·Top3심층·신뢰도·행동제안)
- **`_fmt_candidate_row()`**: 후보 종목 포맷 헬퍼 함수 추출

### ETL 수정
- **`etl_run_state` 자동 생성**: 최초 실행 시 테이블 없으면 `CREATE TABLE IF NOT EXISTS` 자동 실행

### 정확도 검증
- **`accuracy_validator.py`** 신규 작성: TENBAGGER/COMPOUNDER 예측 실제 수익률 주간 검증
- **crontab**: 매주 일요일 오전 7시 정확도 검증 자동 실행 추가

### API (신규)
| 엔드포인트 | 설명 |
|-----------|------|
| POST /api/v2/auth/refresh | refresh_token → 새 access_token 발급 |

---

## v3.0 — MCP 연동 + 아티팩트 대시보드 (2026-05-05)

### 핵심 변경
- **Cowork 라이브 아티팩트**: opendart MCP + NaverSearch MCP 직접 호출
  - 탭1: KOSPI/KOSDAQ 신규 공시 실시간 모니터 (최근 5일, 필터: 전체/시장/유형)
  - 탭2: 종목명 입력 → DART 재무지표 4종 자동 수집 → 텐배거 점수 실시간 계산
  - 탭3: 네이버 뉴스 실시간 검색

- **백엔드 ETL 이중 전략**:
  - 1순위: `DART fnlttCmpnyIndx.json` (전처리된 재무지표 직접 사용 → 빠름)
  - 2순위: 원시 재무제표 파싱 (fallback)

- **새 함수 추가**:
  - `dart_client.get_financial_index()` — DART 재무지표 API 직접 호출
  - `dart_client.get_all_financial_indices()` — 4개 카테고리 병렬 수집
  - `dart_client.parse_idx()` — 지표 리스트 키워드 검색
  - `scoring.calculate_score_from_indices()` — 전처리된 지표로 점수 계산

### 아키텍처 변화
```
Before: Browser → FastAPI → DART HTTP 클라이언트 (원시 재무제표 파싱)
After:  Cowork Artifact → opendart MCP (전처리 지표) + NaverSearch MCP
        ETL → DART fnlttCmpnyIndx (우선) → 원시 재무제표 (fallback)
```

---

## v1.0 — 정량적 분석 시스템 (완성)

### 핵심 기능
- **DART 재무 데이터 수집**: 8년치 매출/영업이익/순이익/자산/부채/FCF 자동 수집
- **텐배거 스코어링 엔진**: 성장성(30%) + 안정성(20%) + 현금흐름(20%) + 배당(15%) + 일관성(15%)
- **4단계 등급**: TENBAGGER / COMPOUNDER / WATCHLIST / AVOID
- **종목 검색**: 종목명/코드 자동완성, 분석 이력 저장 (localStorage)
- **전체 시장 스크리너**: KOSPI/KOSDAQ 전체 종목 필터링 + 정렬
- **5년 시나리오 시뮬레이터**: 주가 CAGR × 수익 시나리오 (낙관/기본/보수) 계산
- **역사적 백테스트**: 특정 연도 기준 매수 → N년 보유 수익률 검증

### API 엔드포인트 (prefix: /api)
| 엔드포인트 | 설명 |
|-----------|------|
| GET /api/company/{ticker} | 종목 분석 + 스코어 |
| GET /api/company/{ticker}/simulate | 5년 시뮬레이터 |
| GET /api/search?q= | 종목명 검색 |
| GET /api/screener | 전체 시장 스크리너 |
| GET /api/backtest/{ticker} | 역사적 백테스트 |

### 인프라
- FastAPI + PostgreSQL + Docker Compose
- FinanceDataReader: KOSPI/KOSDAQ 종목 목록
- pykrx: 주가/PER/PBR 데이터
- ETL 워커: `docker exec -it tenbagger_api python -m app.workers.etl`

---

## v2.0 — AI 정성적 분석 (개발 중)

### 핵심 기능 (신규)
- **Claude AI 정성 분석**: DART 기업정보 + 재무 트렌드 → AI가 자동 분석
  - 사업모델 요약 및 수익구조
  - 경쟁우위(경제적 해자) 분석
  - SWOT 분석 (강점/약점/기회/위협)
  - 향후 5년 CAGR 추산 근거 (산업성장률 + 기업경쟁력 + 리스크)
  - 미래 사업 전망 및 핵심 관전포인트
  - AI 투자의견 (BUY/HOLD/AVOID)
- **주요주주 분석**: DART majorstock API 연동
- **최근 공시 목록**: 사업보고서 등 최근 5건 표시

### API 엔드포인트 (prefix: /api/v2)
| 엔드포인트 | 설명 |
|-----------|------|
| GET /api/v2/company/{ticker}/qualitative | AI 정성 분석 (신규) |

### 설정 필요
```
# .env 파일에 추가
ANTHROPIC_API_KEY=sk-ant-...
```

### 향후 계획 (v2.x)
- [ ] 이익수익률 (Earnings Yield) 지표 추가
- [ ] 자사주 제외 시가총액 보정
- [ ] 20년 배당 복리 시뮬레이터
- [ ] 배당분리과세 필터
- [ ] 포트폴리오 트래킹
