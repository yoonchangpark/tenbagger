# Accuracy Validator Agent 프롬프트

## 역할
과거 TENBAGGER 예측의 실제 수익률을 검증하고, 시스템 정확도 리포트를 생성한다.
매주 월요일 실행. 스코어링 개선의 근거 데이터를 제공한다.

## 목표 지표
| 지표 | 목표 |
|------|------|
| TENBAGGER 2년 수익률 중간값 | +50% 이상 |
| AVOID 2년 수익률 중간값 | +10% 이하 |
| COMPOUNDER 1년 수익률 중간값 | +20% 이상 |

## 작업 순서

### Step 1: 현재 TENBAGGER 예측 데이터 조회
```python
docker exec -it tenbagger_api python3 << 'EOF'
from app.core.database import SessionLocal
from sqlalchemy import text

with SessionLocal() as db:
    result = db.execute(text("""
        SELECT ticker, name, grade, total_score, analyzed_at
        FROM scores
        WHERE grade = 'TENBAGGER'
        ORDER BY total_score DESC
    """)).fetchall()
    for r in result:
        print(r)
EOF
```

### Step 2: 현재 주가 수익률 계산
```bash
docker exec -it tenbagger_api python3 << 'EOF'
import asyncio
import FinanceDataReader as fdr
from app.core.database import SessionLocal
from sqlalchemy import text
from datetime import datetime, timedelta

with SessionLocal() as db:
    tenbaggers = db.execute(text(
        "SELECT ticker, name, grade, total_score FROM scores WHERE grade='TENBAGGER'"
    )).fetchall()

results = []
for t in tenbaggers[:20]:  # 상위 20개만 샘플
    try:
        ticker = t[0]
        # 1년 전 가격 vs 현재
        df = fdr.DataReader(ticker, datetime.now() - timedelta(days=365))
        if len(df) > 0:
            old_price = df.iloc[0]['Close']
            cur_price = df.iloc[-1]['Close']
            ret = (cur_price - old_price) / old_price * 100
            results.append((ticker, t[1], t[3], ret))
            print(f"{ticker} {t[1]}: 1년 수익률 {ret:.1f}%")
    except Exception as e:
        print(f"{ticker} 오류: {e}")

# 요약
positive = [r for r in results if r[3] > 20]
print(f"\n+20% 이상: {len(positive)}/{len(results)} = {len(positive)/len(results)*100:.0f}%")
EOF
```

### Step 3: 백테스트 API 활용
```bash
# 상위 종목 백테스트 (base_year는 2년 전)
YEAR=$(($(date +%Y) - 2))
for TICKER in 005930 000660 035420 051910 006400; do
    echo "=== $TICKER ==="
    curl -s "http://localhost:8000/api/backtest/$TICKER?base_year=$YEAR" | \
      python3 -c "import sys,json; d=json.load(sys.stdin); print(f'수익률: {d.get(\"return_pct\",\"N/A\")}%')"
done
```

### Step 4: 정확도 리포트 생성
```python
docker exec -it tenbagger_api python3 << 'EOF'
from app.core.database import SessionLocal
from sqlalchemy import text

with SessionLocal() as db:
    # 등급별 분포
    dist = db.execute(text("""
        SELECT grade, COUNT(*) as cnt, ROUND(AVG(total_score),2) as avg_score,
               ROUND(MIN(total_score),2) as min_score, ROUND(MAX(total_score),2) as max_score
        FROM scores
        GROUP BY grade
        ORDER BY avg_score DESC
    """)).fetchall()

    print("=== 등급별 분포 ===")
    for d in dist:
        print(f"{d[0]}: {d[1]}종목, 평균={d[2]}, 범위={d[3]}~{d[4]}")

    # 세부 점수 분포
    growth = db.execute(text("""
        SELECT
            ROUND(AVG(growth_score),2) as avg_growth,
            ROUND(AVG(stability_score),2) as avg_stability,
            ROUND(AVG(cashflow_score),2) as avg_cashflow,
            ROUND(AVG(dividend_score),2) as avg_dividend,
            ROUND(AVG(consistency_score),2) as avg_consistency
        FROM scores
        WHERE total_score IS NOT NULL
    """)).fetchone()
    if growth:
        print(f"\n=== 평균 세부 점수 ===")
        print(f"성장성: {growth[0]}, 안정성: {growth[1]}, 현금흐름: {growth[2]}")
        print(f"배당: {growth[3]}, 일관성: {growth[4]}")

    # 뉴스 감성 vs 등급 교차 분석
    cross = db.execute(text("""
        SELECT s.grade, ns.signal, COUNT(*) as cnt
        FROM scores s
        JOIN (
            SELECT DISTINCT ON (ticker) ticker, signal
            FROM news_sentiment
            ORDER BY ticker, sentiment_date DESC
        ) ns ON ns.ticker = s.ticker
        WHERE s.grade IN ('TENBAGGER','COMPOUNDER')
        GROUP BY s.grade, ns.signal
        ORDER BY s.grade, cnt DESC
    """)).fetchall()
    if cross:
        print(f"\n=== 등급별 뉴스 감성 분포 ===")
        for c in cross:
            print(f"{c[0]} × {c[1]}: {c[2]}종목")
EOF
```

### Step 5: 개선 제안 도출
수집된 데이터를 바탕으로:
1. TENBAGGER 예측 중 실제 수익률 낮은 종목의 공통 특징 파악
2. AVOID 예측 중 실제 수익률 높은 종목의 공통 특징 파악
3. 가중치 조정 필요 여부 판단 → `improve_scoring.md` 프롬프트로 이어짐

## 보고 형식
```
=== 주간 정확도 리포트 (YYYY-MM-DD) ===

📊 등급 분포
  TENBAGGER: N종목
  COMPOUNDER: N종목
  WATCHLIST: N종목
  AVOID: N종목

📈 1년 수익률 샘플 (TENBAGGER 상위 20종목)
  +20% 이상: X종목 (X%)
  0~+20%: X종목 (X%)
  손실: X종목 (X%)

⚠️ 개선 필요 사항
  - [발견된 패턴]

✅ 시스템 상태
  - 전체 커버리지: N종목
  - 마지막 ETL: YYYY-MM-DD
```

## CHANGELOG.md 업데이트 필수
정확도 데이터가 의미있게 쌓이면 반드시 기록:
```bash
echo "## $(date +%Y-%m-%d) 정확도 검증" >> /home/user/tenbagger/CHANGELOG.md
```
