# 텐배거 헌터 - 버전 기록

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
