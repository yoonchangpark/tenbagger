"""
트랙 레코드 API — 과거 TENBAGGER 예측 vs 실제 수익률 공개
GET /api/v2/track-record   전체 큐레이션 종목 일괄 백테스트
"""
import asyncio
from fastapi import APIRouter

router = APIRouter(prefix="/api/v2/track-record", tags=["TrackRecord"])

# 공개 큐레이션 목록 — 대본 생성에 쓰이는 aiva/data/tenbagger_history.json 과 동기화
CURATED = [
    {"name": "에코프로",    "ticker": "086520", "base_year": 2019, "hold_years": 5,
     "note": "2차전지 양극재 광풍의 상징. 2023년 검색량·주가 정점 직후 2024년 영업적자 전환."},
    {"name": "에코프로비엠", "ticker": "247540", "base_year": 2019, "hold_years": 5,
     "note": "하이니켈 양극재 글로벌 선두. 2023년 광풍 정점 → 2024년 영업적자 전환. 모회사 에코프로와 같은 광풍 사례."},
    {"name": "한미반도체",  "ticker": "042700", "base_year": 2019, "hold_years": 5,
     "note": "HBM용 TC본더 독점. AI 붐 이전엔 그저 그런 반도체 장비주."},
    {"name": "HMM",        "ticker": "011200", "base_year": 2019, "hold_years": 4,
     "note": "2019년 적자 해운사 → 2021년 운임 슈퍼사이클. 사이클주 사례."},
    {"name": "위메이드",   "ticker": "112040", "base_year": 2019, "hold_years": 3,
     "note": "미르4 + P2E로 2021년 폭등 후 급락. 네러티브 주도주의 위험성 사례."},
]


@router.get("")
async def get_track_record():
    """
    큐레이션 종목들의 백테스트 결과를 병렬로 조회해 반환한다.
    주가 조회(pykrx/FDR)가 포함되므로 응답까지 최대 30초 소요 가능.
    """
    from app.domain.backtest import run_backtest

    async def _safe_run(item: dict) -> dict:
        try:
            bt = await run_backtest(
                ticker=item["ticker"],
                base_year=item["base_year"],
                hold_years=item["hold_years"],
            )
            if "error" in bt:
                return {**item, "status": "error", "error": bt["error"]}
            score = bt.get("score_at_base_year") or {}
            return {
                "name": item["name"],
                "ticker": item["ticker"],
                "base_year": item["base_year"],
                "end_year": bt.get("end_year"),
                "hold_years": item["hold_years"],
                "note": item.get("note", ""),
                "predicted_grade": score.get("grade"),
                "total_score": score.get("total_score"),
                "growth_score": score.get("growth_score"),
                "price_at_base_year": bt.get("price_at_base_year"),
                "price_at_end_year": bt.get("price_at_end_year"),
                "actual_return_pct": bt.get("actual_return_pct"),
                "is_tenbagger_actual": bt.get("is_tenbagger_actual"),
                "prediction_correct": bt.get("prediction_correct"),
                "status": "ok",
            }
        except Exception as e:
            return {**item, "status": "error", "error": str(e)}

    results = await asyncio.gather(*[_safe_run(item) for item in CURATED])
    results = list(results)

    ok = [r for r in results if r.get("status") == "ok"]
    correct = [r for r in ok if r.get("prediction_correct")]
    returns = [r["actual_return_pct"] for r in ok if r.get("actual_return_pct") is not None]

    return {
        "records": results,
        "summary": {
            "total": len(CURATED),
            "resolved": len(ok),
            "correct_count": len(correct),
            "accuracy_pct": round(len(correct) / len(ok) * 100, 1) if ok else None,
            "avg_return_pct": round(sum(returns) / len(returns), 1) if returns else None,
        },
    }
