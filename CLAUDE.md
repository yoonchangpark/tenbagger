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

## 팀 역할 & 디버깅 워크플로우 (PM 감독)

### 팀 구성
| 역할 | 책임 |
|------|------|
| **PM (팀장)** | 변경 범위 승인, 디버깅 에이전트 트리거 최종 결정 |
| **시니어 개발자** | 구현 담당, CLAUDE.md 규칙 준수 |
| **UX 디자이너** | UI 변경 시 중간 점검 |
| **디버깅 에이전트** | `.agent.md` 기준으로 버그 원인 추적 및 최소 수정안 제시 |

### 자동 정적 분석 훅
`Edit` / `Write` 도구 실행 직후 `scripts/debug_check.ps1`이 자동으로 실행된다.
- HTML: script 태그 균형 · mobile-tab-bar 누락 · viewport 메타 · AbortController 체크
- Python: AST 구문 검증

훅 출력이 `⚠️` 경고를 포함하면 **PM이 디버깅 에이전트 심층 분석을 승인**해야 다음 작업으로 넘어간다.

### 디버깅 에이전트 강제 트리거 조건 (PM 판단)
아래 중 하나라도 해당하면 PM은 디버깅 에이전트를 호출한다.
1. 자동 훅에서 `⚠️` 경고 발생
2. 백엔드 API 엔드포인트 시그니처/응답 구조 변경
3. 여러 파일에 걸친 상태 흐름 수정 (검색 → 분석 → 시뮬레이션 등)
4. ETL·DB 연동 로직 변경
5. PM이 명시적으로 요청

### 변경·개선 작업 표준 절차 (완전 자동화)

모든 작업은 아래 파이프라인을 **사용자 별도 지시 없이 자동으로** 수행한다.

```
[PM] 요구사항 정의 및 범위 승인
  ↓
[시니어 개발자] 구현
  ↓
[자동] debug_check.ps1 — 정적 분석 (Edit/Write 즉시)
  ↓
[자동] 디버깅 에이전트 — 오류·버그·API 불일치 탐지 및 즉시 수정
  ↓
[자동] UX 디자이너 에이전트 — 사용자 관점 UI/UX 점검 및 즉시 수정
  · 버튼 동작, 빈 상태, 에러 메시지, 모바일 레이아웃, 탭 흐름 확인
  ↓
[자동] 개선 사항 분류
  · 즉시 수정 가능(버그·오탈자·스타일) → 자동 수정
  · 큰 개선(신기능·구조 변경·DB 스키마) → PM·시니어 개발자 에스컬레이션 후 별도 작업으로 진행
  ↓
[자동] git commit + push → Railway 자동 배포
```

#### 에이전트 역할 상세

| 에이전트 | 자동 실행 조건 | 처리 방식 |
|---------|--------------|----------|
| **디버깅 에이전트** | 항상 (모든 코드 변경 후) | AST 오류·JS 오류·API 불일치 → 즉시 수정 |
| **UX 디자이너 에이전트** | HTML/JS 변경 시 | 사용자 흐름 점검 → 즉시 수정 |
| **PM·시니어 개발자** | 큰 개선 필요 시 | 요구사항 정의 → 다음 작업으로 스케줄링 |

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

---

## AI 코딩 행동 원칙 (Karpathy Guidelines)

> 출처: https://github.com/forrestchang/andrej-karpathy-skills

**주의사항:** 이 원칙들은 속도보다 신중함을 우선시한다. 단순한 작업은 판단에 따라 유연하게 적용한다.

### 1. 코딩 전 먼저 생각하기

**가정하지 말 것. 혼란을 숨기지 말 것. 트레이드오프를 드러낼 것.**

구현 전에:
- 가정을 명시적으로 밝힐 것. 불확실하면 물어볼 것.
- 해석이 여러 가지라면 모두 제시할 것 — 조용히 하나를 선택하지 말 것.
- 더 단순한 접근이 있다면 말할 것. 필요하면 반대 의견을 낼 것.
- 불분명한 게 있으면 멈출 것. 무엇이 혼란스러운지 명확히 하고 물어볼 것.

### 2. 단순함 우선

**문제를 해결하는 최소한의 코드. 추측성 코드는 금지.**

- 요청받지 않은 기능은 추가하지 않는다.
- 단일 용도 코드에 추상화를 만들지 않는다.
- 요청받지 않은 "유연성"이나 "설정 가능성"을 추가하지 않는다.
- 불가능한 시나리오에 대한 에러 핸들링은 만들지 않는다.
- 200줄로 쓴 코드가 50줄로 가능하다면 다시 쓴다.

스스로 물어볼 것: "시니어 엔지니어가 이 코드를 복잡하다고 할까?" 그렇다면 단순화한다.

### 3. 외과적 변경

**반드시 필요한 것만 건드릴 것. 내가 만든 것만 정리할 것.**

기존 코드를 편집할 때:
- 인접한 코드, 주석, 포매팅을 "개선"하지 않는다.
- 고장나지 않은 것은 리팩토링하지 않는다.
- 내 방식이 다르더라도 기존 스타일을 맞춘다.
- 관련 없는 데드 코드를 발견하면 언급만 한다 — 삭제하지 않는다.

변경으로 인해 고아 코드가 생길 때:
- 내 변경으로 인해 쓰이지 않게 된 import/변수/함수는 제거한다.
- 기존에 있던 데드 코드는 요청받지 않으면 건드리지 않는다.

테스트: 변경된 모든 줄은 사용자의 요청으로 직접 추적 가능해야 한다.

### 4. 목표 중심 실행

**성공 기준을 정의할 것. 검증될 때까지 반복할 것.**

작업을 검증 가능한 목표로 전환한다:
- "유효성 검사 추가" → "잘못된 입력에 대한 테스트를 작성하고 통과시킨다"
- "버그 수정" → "버그를 재현하는 테스트를 작성하고 통과시킨다"
- "X 리팩토링" → "리팩토링 전후로 테스트가 통과하는지 확인한다"

여러 단계 작업은 간략한 계획을 먼저 밝힌다:
```
1. [단계] → 검증: [확인 방법]
2. [단계] → 검증: [확인 방법]
3. [단계] → 검증: [확인 방법]
```

명확한 성공 기준이 있으면 독립적으로 반복할 수 있다. 모호한 기준("작동하게 만들어")은 계속 확인을 요구하게 된다.

---

**이 원칙들이 잘 작동하고 있다면:** diff에 불필요한 변경이 없고, 과도한 복잡성으로 인한 재작성이 없으며, 구현 전에 먼저 명확한 질문이 나온다.
