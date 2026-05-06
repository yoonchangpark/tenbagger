# 텐배거 헌터 - 버전 기록

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
