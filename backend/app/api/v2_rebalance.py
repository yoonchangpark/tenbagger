"""
POST /api/v2/rebalance/suggest
  positions: [{"ticker","name","weight_actual","weight_target"}]
  → 편차가 5%p 이상인 종목에 대해 매수/매도 액션 제안
"""
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/v2/rebalance", tags=["rebalance"])

DRIFT_THRESHOLD = 5.0   # 목표 비중 대비 ±5%p 이상이면 리밸런싱 대상


class Position(BaseModel):
    ticker: str
    name: str
    weight_actual: float    # 현재 비중 (%)
    weight_target: float    # 목표 비중 (%)
    value: float            # 현재 평가금액 (원)


class RebalanceRequest(BaseModel):
    positions: list[Position]
    total_value: float      # 포트폴리오 총 평가금액 (원)


@router.post("/suggest")
def suggest_rebalance(body: RebalanceRequest):
    """
    각 종목의 실제 비중 vs 목표 비중을 비교해 리밸런싱 액션 목록 반환.
    drift = weight_actual - weight_target
    drift > +THRESHOLD → 비중 초과 → 매도 제안
    drift < -THRESHOLD → 비중 부족 → 매수 제안
    """
    actions = []
    total = body.total_value

    for pos in body.positions:
        drift = pos.weight_actual - pos.weight_target
        if abs(drift) < DRIFT_THRESHOLD:
            continue

        target_value  = total * pos.weight_target / 100
        current_value = pos.value
        delta_value   = target_value - current_value   # 양수 → 매수, 음수 → 매도

        actions.append({
            "ticker":        pos.ticker,
            "name":          pos.name,
            "weight_actual": round(pos.weight_actual, 1),
            "weight_target": round(pos.weight_target, 1),
            "drift":         round(drift, 1),
            "action":        "매도" if drift > 0 else "매수",
            "action_color":  "#ef4444" if drift > 0 else "#10b981",
            "delta_value":   round(abs(delta_value)),
            "urgency":       "높음" if abs(drift) >= 15 else ("중간" if abs(drift) >= 10 else "낮음"),
        })

    # 편차 큰 순 정렬
    actions.sort(key=lambda x: abs(x["drift"]), reverse=True)

    total_drift = sum(abs(p.weight_actual - p.weight_target) for p in body.positions)
    balance_score = max(0, round(100 - total_drift * 2))

    return {
        "actions":       actions,
        "action_count":  len(actions),
        "balance_score": balance_score,
        "needs_rebalance": len(actions) > 0,
        "threshold_pct": DRIFT_THRESHOLD,
    }
