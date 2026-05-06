# 텐배거 헌터 — Claude Code 에이전트 가이드

## 프로젝트 개요
한국 주식 장기투자 분석 시스템. KOSPI/KOSDAQ 전종목을 DART 재무데이터로 분석해
10배 수익 가능성이 있는 "텐배거" 종목을 자동 발굴한다.

**오너**: yoonchang.park@gmail.com  
**목표**: 시스템을 지속 고도화하고, 투자 정확도를 높이며, 오너에게 실시간 투자 조언 제공

---

## 시스템 구조

```
tenbagger/
├── backend/
│   ├── app/
│   │   ├── api/           # FastAPI 엔드포인트
│   │   │   ├── company.py      # /api/company/{ticker}
│   │   │   ├── screener.py     # /api/screener
│   │   │   ├── backtest.py     # /api/backtest/{ticker}
│   │   │   └── qualitative.py  # /api/v2/company/{ticker}/qualitative (AI 분석)
│   │   ├── domain/
│   │   │   ├── scoring.py      # ★ 핵심: 텐배거 스코어링 로직
│   │   │   ├── backtest.py     # 역사적 백테스트
│   │   │   └── qualitative_analysis.py  # OpenAI 정성 분석
│   │   ├── infra/
│   │   │   ├── clients/dart_client.py   # DART OpenAPI 클라이언트
│   │   │   └── repositories/company_repo.py  # DB CRUD
│   │   ├── workers/
│   │   │   └── etl.py          # 전종목 ETL 워커
│   │   └── agents/
│   │       └── advisor.py      # 일일 어드바이저 에이전트
│   └── requirements.txt
├── frontend/
│   ├── index.html         # 종목 분석 메인
│   ├── screener.html      # 전체 시장 스크리너
│   └── backtest.html      # 백테스트 UI
├── CHANGELOG.md           # v1.0 / v2.0 기능 목록
└── agent_prompts/         # 에이전트별 프롬프트
```

---

## 스코어링 시스템 (scoring.py)

### 가중치 구성
| 카테고리 | 가중치 | 핵심 지표 |
|---------|--------|----------|
| 성장성 (growth) | 30% | 매출 CAGR, EPS CAGR |
| 안정성 (stability) | 20% | ROE, 부채비율, 유동비율 |
| 현금흐름 (cashflow) | 20% | FCF 마진, CFO 안정성 |
| 배당 (dividend) | 15% | 배당수익률, 배당성장률 |
| 일관성 (consistency) | 15% | 흑자 연속성, 이익률 안정성 |

### 등급 기준
- **TENBAGGER**: 총점 7.5+ AND 성장성 8.0+
- **COMPOUNDER**: 총점 6.5+
- **WATCHLIST**: 총점 5.0+
- **AVOID**: 그 외

---

## 인프라

- **백엔드**: FastAPI + PostgreSQL + Docker Compose
- **포트**: API=8000, DB=5432
- **DART API 키**: settings.dart_api_key (환경변수 DART_API_KEY)
- **OpenAI API 키**: settings.openai_api_key (환경변수 OPENAI_API_KEY)
- **ETL 실행**: `docker exec -it tenbagger_api python -m app.workers.etl --market ALL`
- **어드바이저**: `docker exec -it tenbagger_api python -m app.agents.advisor`

---

## 에이전트 역할 정의

### 1. Daily Advisor Agent (매일)
- ETL 실행 → 신규 텐배거 탐지 → GPT 투자의견 → 이메일 발송
- 실행: `python -m app.agents.advisor`

### 2. Accuracy Validator (주 1회)
- 과거 TENBAGGER 예측 → 실제 수익률 비교 → 정확도 리포트
- 목표 정확도: TENBAGGER 등급 종목 중 2년 내 +50% 이상 비율 > 30%

### 3. Scoring Improvement Agent (월 1회)
- 정확도 데이터 분석 → 가중치/임계값 조정 제안 → scoring.py 수정
- 변경 시 반드시 CHANGELOG.md 업데이트

### 4. Investment Advisor (온디맨드)
- 오너 질문에 답변: "XX 종목 지금 사도 될까?" → 시스템 데이터 + AI 분석 종합

---

## 코드 개선 시 규칙

1. **v1.0 API 절대 불변**: `/api/*` 엔드포인트 시그니처 변경 금지
2. **신기능은 `/api/v2/*`**: 새 기능은 항상 v2 prefix
3. **CHANGELOG.md 업데이트**: 모든 변경사항 기록
4. **Python AST 검증**: 파일 수정 후 반드시 `python3 -c "import ast; ast.parse(open('파일').read())"` 실행
5. **docker restart 불필요**: uvicorn --reload로 자동 반영

---

## 자주 쓰는 명령어

```bash
# ETL 실행 (전체, skip-existing)
docker exec -it tenbagger_api python -m app.workers.etl --market ALL --skip-existing

# 일일 리포트 (dry-run = 이메일 미발송)
docker exec -it tenbagger_api python -m app.agents.advisor --dry-run --skip-etl

# 특정 종목 분석
curl http://localhost:8000/api/company/005930

# AI 정성 분석
curl http://localhost:8000/api/v2/company/005930/qualitative

# 백테스트
curl "http://localhost:8000/api/backtest/005930?base_year=2018"

# DB 스코어 현황
docker exec tenbagger_db psql -U tenbagger -d tenbagger -c \
  "SELECT grade, COUNT(*), ROUND(AVG(total_score),2) FROM score_cache GROUP BY grade ORDER BY AVG(total_score) DESC;"

# 컨테이너 로그
docker logs tenbagger_api --tail 30 -f
```

---

## 알려진 이슈

- pykrx `get_market_fundamental` → KRX 서버 불안정. PER/PBR/주가 미수집 가능 (스코어에 영향 없음)
- DART `majorstock.json` → 대형주 주주 데이터 불안정
- ETL 소요 시간: KOSPI 949개 ≈ 30분, KOSDAQ 포함 ≈ 2시간

---

## 정확도 목표 (2026 기준)

| 지표 | 현재 | 목표 |
|------|------|------|
| TENBAGGER 2년 수익률 중간값 | 측정 중 | +50% 이상 |
| AVOID 2년 수익률 중간값 | 측정 중 | +10% 이하 |
| 시스템 커버리지 | KOSPI 949개 | KOSPI+KOSDAQ 전체 |
