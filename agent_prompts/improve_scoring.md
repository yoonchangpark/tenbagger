# Scoring Improvement Agent 프롬프트

## 역할
정확도 검증 결과를 바탕으로 스코어링 로직을 개선한다.
`accuracy_check.md` 실행 후 월 1회 실행. 모든 변경은 CHANGELOG.md에 기록.

## ⚠️ 절대 규칙
1. `/api/*` v1.0 엔드포인트 시그니처 **절대 변경 금지**
2. `scoring.py` 수정 후 반드시 AST 검증 실행
3. 모든 변경은 CHANGELOG.md에 기록
4. 변경 전 현재 상태를 백업

## 스코어링 파일 위치
```
C:\Users\00LG00\Desktop\tenbagger\backend\app\domain\scoring.py
```

## 현재 가중치 (기준값)
```python
# scoring.py 내 가중치
WEIGHTS = {
    "growth": 0.30,      # 성장성: 매출 CAGR, EPS CAGR
    "stability": 0.20,   # 안정성: ROE, 부채비율, 유동비율
    "cashflow": 0.20,    # 현금흐름: FCF 마진, CFO 안정성
    "dividend": 0.15,    # 배당: 수익률, 성장률
    "consistency": 0.15  # 일관성: 흑자 연속성, 이익률 안정성
}

# 등급 임계값
GRADE_THRESHOLDS = {
    "TENBAGGER": {"total": 7.5, "growth": 8.0},
    "COMPOUNDER": {"total": 6.5},
    "WATCHLIST": {"total": 5.0}
}
```

## 작업 순서

### Step 1: 현재 scoring.py 읽기
```bash
cat /sessions/nice-admiring-euler/mnt/tenbagger/backend/app/domain/scoring.py
```
현재 가중치, 계산 로직, 임계값을 파악한다.

### Step 2: 정확도 데이터 분석
accuracy_check.md 결과를 바탕으로:
- TENBAGGER 예측 성공률이 30% 미만이면 → 기준 강화 필요
- 특정 카테고리(성장성/안정성 등) 점수가 예측력과 상관없으면 → 가중치 조정
- AVOID 예측 중 고수익 종목이 많으면 → AVOID 기준 완화

### Step 3: 개선안 설계
예시 개선안:
```python
# 개선 예시 1: 성장성 가중치 상향 (정확도 분석 결과 성장성이 핵심 지표임이 확인된 경우)
WEIGHTS = {
    "growth": 0.35,      # 0.30 → 0.35 (성장성 예측력 높음)
    "stability": 0.20,
    "cashflow": 0.20,
    "dividend": 0.10,    # 0.15 → 0.10 (배당 예측력 낮음)
    "consistency": 0.15
}

# 개선 예시 2: TENBAGGER 기준 강화
GRADE_THRESHOLDS = {
    "TENBAGGER": {"total": 8.0, "growth": 8.5},  # 기준 상향
    "COMPOUNDER": {"total": 6.5},
    "WATCHLIST": {"total": 5.0}
}
```

### Step 4: 변경 전 백업
```bash
cp /sessions/nice-admiring-euler/mnt/tenbagger/backend/app/domain/scoring.py \
   /sessions/nice-admiring-euler/mnt/tenbagger/backend/app/domain/scoring.py.bak.$(date +%Y%m%d)
```

### Step 5: scoring.py 수정
Read → Edit 도구로 정밀하게 수정. 절대 전체 재작성 금지.

### Step 6: AST 검증 (필수)
```bash
docker exec tenbagger_api python3 -c "
import ast
with open('/app/app/domain/scoring.py') as f:
    code = f.read()
ast.parse(code)
print('AST 검증 통과')
"
```

### Step 7: 변경 효과 테스트
```bash
# 삼성전자로 변경 전후 비교
docker exec tenbagger_api python3 -c "
from app.domain.scoring import calculate_score
# 테스트 데이터로 점수 계산
print('스코어링 테스트 완료')
"

# 전체 재스코어링 (선택적)
docker exec -it tenbagger_api python -m app.workers.etl --market KOSPI --skip-existing
```

### Step 8: DB 결과 확인
```bash
docker exec tenbagger_db psql -U tenbagger -d tenbagger -c \
  "SELECT grade, COUNT(*), ROUND(AVG(total_score),2) FROM score_cache GROUP BY grade ORDER BY AVG(total_score) DESC;"
```
등급 분포가 크게 달라지면 (TENBAGGER 0개 또는 500개 이상) 기준 재검토.

### Step 9: CHANGELOG.md 업데이트 (필수)
```markdown
## v2.X.0 (YYYY-MM-DD) — 스코어링 개선

### 변경 이유
- [정확도 검증 결과 요약]

### 변경 내용
- 성장성 가중치: 0.30 → 0.35
- 배당 가중치: 0.15 → 0.10
- TENBAGGER 임계값: 7.5 → 8.0

### 예상 효과
- TENBAGGER 기준 강화로 정밀도 향상
- 배당주 과대평가 방지
```

## 가중치 조정 가이드라인
| 상황 | 조치 |
|------|------|
| TENBAGGER 예측 성공률 < 20% | 기준 강화 (임계값 +0.5) |
| TENBAGGER 예측 성공률 > 50% | 기준 완화 (더 많이 발굴) |
| 성장성 점수와 수익률 상관관계 높음 | growth 가중치 +0.05 |
| 배당 점수와 수익률 상관관계 낮음 | dividend 가중치 -0.05 |
| 안정성 높은 종목이 폭락 | stability 가중치 +0.05 |

## 절대 하지 말아야 할 것
- API 응답 구조 변경 (`total_score`, `grade` 필드명 유지)
- 점수 범위 변경 (0~10 스케일 유지)
- 기존 테스트 데이터 삭제
