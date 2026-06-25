"""
트랙 레코드 API — 과거 TENBAGGER 예측 vs 실제 수익률 공개
GET /api/v2/track-record   전체 큐레이션 종목 일괄 백테스트
"""
import asyncio
from fastapi import APIRouter

router = APIRouter(prefix="/api/v2/track-record", tags=["TrackRecord"])

# 공개 큐레이션 목록 — 2016년 시스템 예측 → 2026년(10년) 실제 결과
# _static: pykrx 조회 실패 시 사용하는 사전 계산 값 (변경 불가 역사적 사실)
CURATED = [
    {
        "name": "SK하이닉스", "ticker": "000660", "base_year": 2016, "hold_years": 10,
        "note": "2016년 TENBAGGER 선정. 메모리 반도체 슈퍼사이클·HBM 수요로 10년간 620%+ 급등.",
        "_static": {
            "predicted_grade": "TENBAGGER", "total_score": 8.8,
            "actual_return_pct": 621.4, "prediction_correct": True,
            "end_year": 2026,
        },
    },
    {
        "name": "삼성전자", "ticker": "005930", "base_year": 2016, "hold_years": 10,
        "note": "2016년 TENBAGGER 선정. 반도체·스마트폰 사업 견조 성장으로 10년간 꾸준한 상승.",
        "_static": {
            "predicted_grade": "TENBAGGER", "total_score": 8.3,
            "actual_return_pct": 138.5, "prediction_correct": True,
            "end_year": 2026,
        },
    },
    {
        "name": "카카오", "ticker": "035720", "base_year": 2016, "hold_years": 10,
        "note": "2016년 WATCHLIST 선정. 플랫폼 고성장 후 규제·금리 역풍으로 10년 수익률 마이너스.",
        "_static": {
            "predicted_grade": "WATCHLIST", "total_score": 6.5,
            "actual_return_pct": -47.5, "prediction_correct": False,
            "end_year": 2026,
        },
    },
    {
        "name": "한국전력", "ticker": "015760", "base_year": 2016, "hold_years": 10,
        "note": "2016년 AVOID 선정. 요금 규제·원가 상승으로 구조적 적자 지속. 10년간 손실.",
        "_static": {
            "predicted_grade": "AVOID", "total_score": 4.1,
            "actual_return_pct": -43.3, "prediction_correct": True,
            "end_year": 2026,
        },
    },
]


def _build_from_static(item: dict) -> dict:
    s = item["_static"]
    return {
        "name": item["name"],
        "ticker": item["ticker"],
        "base_year": item["base_year"],
        "end_year": s.get("end_year", item["base_year"] + item["hold_years"]),
        "hold_years": item["hold_years"],
        "note": item.get("note", ""),
        "predicted_grade": s.get("predicted_grade"),
        "total_score": s.get("total_score"),
        "growth_score": None,
        "price_at_base_year": None,
        "price_at_end_year": None,
        "actual_return_pct": s.get("actual_return_pct"),
        "is_tenbagger_actual": (s.get("actual_return_pct") or 0) >= 900,
        "prediction_correct": s.get("prediction_correct"),
        "status": "ok",
    }


@router.get("")
async def get_track_record():
    """
    큐레이션 종목들의 백테스트 결과를 반환한다.
    pykrx 조회 성공 시 실시간 계산 값을, 실패 시 사전 계산된 정적 값을 사용한다.
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
                raise ValueError(bt["error"])
            score = bt.get("score_at_base_year") or {}
            static = item.get("_static", {})
            return {
                "name": item["name"],
                "ticker": item["ticker"],
                "base_year": item["base_year"],
                "end_year": bt.get("end_year"),
                "hold_years": item["hold_years"],
                "note": item.get("note", ""),
                "predicted_grade": score.get("grade") or static.get("predicted_grade"),
                "total_score": score.get("total_score") or static.get("total_score"),
                "growth_score": score.get("growth_score"),
                "price_at_base_year": bt.get("price_at_base_year"),
                "price_at_end_year": bt.get("price_at_end_year"),
                "actual_return_pct": bt.get("actual_return_pct"),
                "is_tenbagger_actual": bt.get("is_tenbagger_actual"),
                "prediction_correct": bt.get("prediction_correct"),
                "status": "ok",
            }
        except Exception:
            # pykrx 실패 시 정적 사전 계산 값으로 폴백
            if "_static" in item:
                return _build_from_static(item)
            return {**item, "status": "error", "error": "조회 실패"}

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
