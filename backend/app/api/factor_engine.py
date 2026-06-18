"""
요소 발굴 엔진 API (v4.5)

GET  /api/v2/factors/ic               — IC 리더보드 조회
POST /api/v2/factors/ic/baseline      — 기준선(total_score_v1) IC 측정
POST /api/v2/factors/ic/search-volume — 검색량 요소 IC 측정 (Naver DataLab)
"""
from datetime import date
from fastapi import APIRouter, Query, BackgroundTasks

router = APIRouter(prefix="/api/v2/factors", tags=["factors"])

# train/test 기간 분리 (walk-forward)
_TRAIN_YEARS = [2018, 2019]
_TEST_YEARS  = [2020]


@router.get("/ic")
def get_ic_leaderboard():
    """저장된 요소별 IC 리더보드. train/test 분리로 과적합 탐지."""
    from app.domain.factors.ic_engine import get_ic_leaderboard
    return get_ic_leaderboard()


@router.post("/ic/baseline")
def measure_baseline_ic():
    """기준선(v1 total_score) IC 측정 후 저장.

    train IC와 test IC를 모두 측정해 walk-forward 결과 저장.
    """
    from app.domain.factors.ic_engine import baseline_ic, save_factor_ic

    train = baseline_ic(train_years=_TRAIN_YEARS)
    test  = baseline_ic(train_years=_TEST_YEARS)

    save_factor_ic(
        factor_name="total_score_v1",
        train_ic=train.get("ic"),
        test_ic=test.get("ic"),
        n_train=train.get("n", 0),
        n_test=test.get("n", 0),
    )

    return {
        "factor": "total_score_v1",
        "train": train,
        "test":  test,
        "note":  "walk-forward: train=2018~2019 / test=2020",
    }


@router.post("/ic/search-volume")
async def measure_search_volume_ic(background_tasks: BackgroundTasks):
    """검색량 요소(Naver DataLab) IC 측정 — 백그라운드.

    backfill_results 종목 목록을 가져와 각 종목의
    base_year 직전 3개월 검색량 추세를 수집 후 IC 계산.
    """
    background_tasks.add_task(_run_search_volume_ic)
    return {"message": "검색량 IC 측정 시작 (백그라운드). GET /api/v2/factors/ic 로 결과 확인."}


async def _run_search_volume_ic():
    import asyncio
    from sqlalchemy import text
    from app.core.database import SessionLocal
    from app.domain.factors.search_volume import get_search_trend
    from app.domain.factors.ic_engine import factor_ic, save_factor_ic

    with SessionLocal() as s:
        rows = s.execute(text("""
            SELECT DISTINCT b.ticker, c.name
            FROM backfill_results b
            LEFT JOIN companies c ON c.ticker = b.ticker
            WHERE b.return_pct IS NOT NULL
        """)).fetchall()

    # base_year별로 검색량 점수 수집
    scores_by_year: dict[int, dict[str, float]] = {y: {} for y in _TRAIN_YEARS + _TEST_YEARS}

    for ticker, name in rows:
        label = name or ticker
        for base_year in _TRAIN_YEARS + _TEST_YEARS:
            result = get_search_trend(label, date(base_year, 12, 31))
            if result:
                scores_by_year[base_year][ticker] = result["trend_score"]
        await asyncio.sleep(0.2)

    # train/test IC 측정
    train_scores: dict[str, float] = {}
    for y in _TRAIN_YEARS:
        train_scores.update(scores_by_year[y])

    test_scores: dict[str, float] = {}
    for y in _TEST_YEARS:
        test_scores.update(scores_by_year[y])

    train_result = factor_ic(train_scores, train_years=_TRAIN_YEARS)
    test_result  = factor_ic(test_scores,  train_years=_TEST_YEARS)

    save_factor_ic(
        factor_name="search_volume_naver",
        train_ic=train_result.get("ic"),
        test_ic=test_result.get("ic"),
        n_train=train_result.get("n", 0),
        n_test=test_result.get("n", 0),
    )
