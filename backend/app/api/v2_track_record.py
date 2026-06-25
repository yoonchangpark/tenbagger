"""
트랙 레코드 API — 과거 TENBAGGER 예측 vs 실제 수익률 공개
GET /api/v2/track-record   전체 큐레이션 종목 일괄 백테스트
"""
import asyncio
from fastapi import APIRouter

router = APIRouter(prefix="/api/v2/track-record", tags=["TrackRecord"])

# 공개 큐레이션 목록 — 2020년 시스템 예측 → 2022년 실제 결과
CURATED = [
    {"name": "SK하이닉스", "ticker": "000660", "base_year": 2020, "hold_years": 2,
     "note": "2020년 TENBAGGER 선정. 메모리 반도체 슈퍼사이클 수혜로 2022년까지 급등."},
    {"name": "삼성SDI",    "ticker": "006400", "base_year": 2020, "hold_years": 2,
     "note": "2020년 TENBAGGER 선정. EV 배터리 폭증 수요로 2022년까지 강세."},
    {"name": "카카오",     "ticker": "035720", "base_year": 2020, "hold_years": 2,
     "note": "2020년 TENBAGGER 선정. 플랫폼 고성장 이후 규제·금리 역풍으로 상승 제한."},
    {"name": "셀트리온",   "ticker": "068270", "base_year": 2020, "hold_years": 2,
     "note": "2020년 WATCHLIST 선정. 바이오시밀러 성장 기대에도 실제 주가 횡보·하락."},
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

    # ⚠️ 생존자 편향 주의: 아래 records는 '이미 텐배거가 된 것을 아는' 종목을 손으로 고른
    # 큐레이션 사례다. 이걸로 '적중률/평균수익률'을 집계하면 승자만 본 편향된 수치가 된다.
    # 따라서 여기서는 종합 적중률을 계산하지 않는다.
    # 편향 없는 시스템 정확도는 /api/v2/accuracy/results (예측 시점 등급 기준 전수 추적)를 써라.
    return {
        "records": results,
        "summary": {
            "total": len(CURATED),
            "resolved": len(ok),
            "is_curated_examples": True,
            "note": ("이 목록은 설명을 위해 선별한 대표 사례입니다. 전체 예측의 표본이 아니므로 "
                     "여기서 적중률을 집계하지 않습니다. 편향 없는 검증 정확도는 '검증된 정확도' 섹션을 보세요."),
        },
    }
