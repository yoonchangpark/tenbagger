# AI 투자위원회 Agent 프롬프트 (Phase 6)

## 역할
4명의 전문가 에이전트가 독립 분석 후 투자전략가가 합의 판단을 내리는 멀티에이전트 시스템.
온디맨드 실행 (API 직접 호출 또는 `committee.py` 모듈 임포트).

## 구성 에이전트

| 에이전트 | 역할 | 출력 verdict |
|---------|------|-------------|
| financial_analyst | 재무 건전성·성장성 평가 | STRONG / SOLID / MIXED / WEAK |
| news_sentiment_analyst | 뉴스 심리·주가 모멘텀 분석 | MOMENTUM_UP / POSITIVE / NEUTRAL / CAUTION / MOMENTUM_DOWN |
| sector_researcher | 업종 사이클·경쟁 위치 판단 | TAILWIND / STABLE / HEADWIND / DECLINING |
| investment_strategist | 위 3개 종합 → 최종 투자 판단 | STRONG_BUY / BUY / HOLD / REDUCE / AVOID |

## API 사용법

```bash
# 단일 종목 위원회 분석 (POST)
curl -s -X POST http://localhost:8000/api/v2/company/005930/committee | python3 -m json.tool

# 응답 핵심 필드만 추출
curl -s -X POST http://localhost:8000/api/v2/company/005930/committee | \
  python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f\"종목: {d['name']}({d['ticker']})\")
print(f\"결정: {d['committee_decision']} (신뢰도: {d.get('confidence',0)*100:.0f}%)\")
print(f\"종합점수: {d.get('consensus_score','N/A')}/10\")
ctx = d.get('context', {})
pm = ctx.get('price_momentum', {})
print(f\"주가 모멘텀: {pm.get('momentum','N/A')} | 1M: {pm.get('return_1m_pct','N/A')}% | 3M: {pm.get('return_3m_pct','N/A')}%\")
strat = d['agents']['investment_strategist']
print(f\"전략: {strat.get('entry_strategy','')}\")
print(f\"기간: {strat.get('time_horizon','')}\")
print(f\"핵심 리스크: {strat.get('key_risks','')}\")
"
```

## Python 직접 호출

```python
docker exec -it tenbagger_api python3 << 'EOF'
import asyncio
from app.agents.committee import run_committee

result = asyncio.run(run_committee("005930", "삼성전자"))
print(result)
EOF
```

## 배치: 상위 TENBAGGER 전체 위원회 분석

```bash
# 상위 10개 TENBAGGER를 위원회 분석
docker exec -it tenbagger_api python3 << 'EOF'
import asyncio
from app.core.database import SessionLocal
from sqlalchemy import text
from app.agents.committee import run_committee

with SessionLocal() as db:
    rows = db.execute(text("""
        SELECT ticker, name FROM scores
        WHERE grade = 'TENBAGGER'
        ORDER BY total_score DESC LIMIT 10
    """)).fetchall()

async def batch():
    results = []
    for ticker, name in rows:
        try:
            r = await run_committee(ticker, name)
            decision = r.get("committee_decision", "N/A")
            conf = r.get("confidence", 0)
            score = r.get("consensus_score", 0)
            pm = r.get("context", {}).get("price_momentum", {})
            print(f"{name}({ticker}): {decision} ({conf*100:.0f}%) | 점수={score:.1f} | 1M={pm.get('return_1m_pct','N/A')}%")
            results.append(r)
        except Exception as e:
            print(f"{ticker} 오류: {e}")
        await asyncio.sleep(1)  # API 부하 방지
    return results

asyncio.run(batch())
EOF
```

## 응답 구조 상세

```json
{
  "ticker": "005930",
  "name": "삼성전자",
  "analyzed_at": "2026-05-10T00:00:00Z",
  "committee_decision": "BUY",
  "confidence": 0.75,
  "consensus_score": 7.2,
  "agents": {
    "financial_analyst": {
      "verdict": "STRONG",
      "score": 8.5,
      "key_strengths": ["높은 FCF", "ROE 20%+"],
      "key_weaknesses": ["반도체 사이클 의존"],
      "rationale": "...",
      "growth_outlook": "..."
    },
    "news_sentiment_analyst": {
      "verdict": "POSITIVE",
      "score": 7.0,
      "key_themes": ["HBM 수요", "AI 인프라"],
      "rationale": "...",
      "price_news_alignment": "aligned",
      "price_analysis": "...",
      "sentiment_trend": "improving",
      "risk_signals": []
    },
    "sector_researcher": {
      "verdict": "TAILWIND",
      "score": 7.5,
      "sector_cycle": "expansion",
      "competitive_position": "leader",
      "rationale": "...",
      "tailwinds": ["AI 반도체 수요 급증"],
      "headwinds": ["중국 경쟁 심화"]
    },
    "investment_strategist": {
      "decision": "BUY",
      "confidence": 0.75,
      "score": 7.2,
      "summary": "...",
      "entry_strategy": "분할 매수 (2~3회)",
      "time_horizon": "중기(1-2년)",
      "key_risks": ["반도체 업황 급락", "환율 리스크"],
      "monitoring_points": ["다음 분기 HBM 수주 잔고", "경쟁사 공급 계획"]
    }
  },
  "context": {
    "current_grade": "TENBAGGER",
    "total_score": 8.2,
    "per": 15.3,
    "pbr": 1.8,
    "sector": "반도체",
    "has_news_data": true,
    "price_momentum": {
      "return_1m_pct": 5.2,
      "return_3m_pct": -3.1,
      "return_6m_pct": 12.4,
      "momentum": "UP",
      "latest_price": 85000
    }
  }
}
```

## 결과 해석 가이드

### committee_decision 해석
| 결정 | 조건 | 행동 |
|------|------|------|
| STRONG_BUY | 모든 에이전트 긍정 + PER 합리적 | 적극 매수 |
| BUY | 전반적 긍정, 일부 유보 | 분할 매수 |
| HOLD | 혼조 신호 | 관망, 다음 실적 확인 |
| REDUCE | 리스크 우세, 일부 긍정 | 비중 축소 |
| AVOID | 전반적 부정 합의 | 진입 회피 |

### confidence 해석
- 0.8 이상: 에이전트 합의 강함 → 결정에 높은 가중치
- 0.5~0.79: 의견 일부 엇갈림 → 추가 조사 권장
- 0.5 미만: 강한 의견 불일치 → 보수적 판단 필요

### price_news_alignment 해석
- `aligned`: 주가 방향 ↔ 뉴스 감성 일치 (신뢰할 만한 신호)
- `divergent`: 불일치 → 저평가 기회 또는 거품 경고
- `no_data`: 뉴스 데이터 부족, 재무 지표 위주로 판단

## 비용 참고
- 종목당 GPT 호출 4회 (병렬 3 + 순차 1)
- 예상 비용: 종목당 ~$0.04 (gpt-4o-mini 기준)
- 10종목 배치: ~$0.40, 약 2~3분 소요

## 주의사항
- 위원회 결과는 **당일 데이터 기준** → 실적 발표 직전·직후에는 특히 주의
- `news_sentiment_analyst`는 뉴스 DB가 없는 종목은 `NEUTRAL` 반환
- `sector_researcher`는 섹터 자동 추정 (키워드 기반) → 복합 사업 종목은 부정확할 수 있음

## CHANGELOG.md 업데이트
위원회 로직 변경 시 반드시 기록:
```bash
# CHANGELOG.md에 변경 내용 추가 후 커밋
git add /home/user/tenbagger/backend/app/agents/committee.py
git commit -m "feat(committee): [변경 내용 요약]"
```
