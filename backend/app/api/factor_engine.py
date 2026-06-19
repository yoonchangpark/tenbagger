"""
요소 발굴 엔진 API (v4.5)

GET  /api/v2/factors/ic                   — IC 리더보드 조회
POST /api/v2/factors/ic/baseline          — 기준선(total_score_v1) IC 측정
POST /api/v2/factors/ic/search-volume     — 검색량 요소 IC 측정 (Naver DataLab)
POST /api/v2/factors/ic/momentum          — 모멘텀 요소 IC 측정 (pykrx 6개월 수익률)
POST /api/v2/factors/ic/value             — 가치 요소 IC 측정 (저PBR/저PER)
POST /api/v2/factors/ic/disclosure        — 공시 활동 요소 IC 측정 (DART 공시 건수)
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


@router.post("/ic/momentum")
async def measure_momentum_ic(background_tasks: BackgroundTasks):
    """모멘텀 요소(pykrx 6개월 수익률 - 1개월 반전) IC 측정 — 백그라운드."""
    background_tasks.add_task(_run_momentum_ic)
    return {"message": "모멘텀 IC 측정 시작 (백그라운드). GET /api/v2/factors/ic 로 결과 확인."}


async def _run_momentum_ic():
    import asyncio
    from sqlalchemy import text
    from app.core.database import SessionLocal
    from app.domain.factors.price_momentum import get_momentum_score
    from app.domain.factors.ic_engine import factor_ic, save_factor_ic

    with SessionLocal() as s:
        rows = s.execute(text("""
            SELECT DISTINCT ticker FROM backfill_results WHERE return_pct IS NOT NULL
        """)).fetchall()
    tickers = [r[0] for r in rows]

    scores_by_year: dict[int, dict[str, float]] = {y: {} for y in _TRAIN_YEARS + _TEST_YEARS}
    for ticker in tickers:
        for base_year in _TRAIN_YEARS + _TEST_YEARS:
            result = get_momentum_score(ticker, date(base_year, 12, 31))
            if result:
                scores_by_year[base_year][ticker] = result["momentum_score"]
        await asyncio.sleep(0.1)

    train_scores = {t: s for y in _TRAIN_YEARS for t, s in scores_by_year[y].items()}
    test_scores  = {t: s for y in _TEST_YEARS  for t, s in scores_by_year[y].items()}
    train_result = factor_ic(train_scores, train_years=_TRAIN_YEARS)
    test_result  = factor_ic(test_scores,  train_years=_TEST_YEARS)
    save_factor_ic("price_momentum_6m", train_result.get("ic"), test_result.get("ic"),
                   train_result.get("n", 0), test_result.get("n", 0))


@router.post("/ic/value")
async def measure_value_ic(background_tasks: BackgroundTasks):
    """가치 요소(저PBR/저PER) IC 측정 — 백그라운드."""
    background_tasks.add_task(_run_value_ic)
    return {"message": "가치 IC 측정 시작 (백그라운드). GET /api/v2/factors/ic 로 결과 확인."}


async def _run_value_ic():
    import asyncio
    from sqlalchemy import text
    from app.core.database import SessionLocal
    from app.domain.factors.value_factor import get_value_score
    from app.domain.factors.ic_engine import factor_ic, save_factor_ic

    with SessionLocal() as s:
        rows = s.execute(text("""
            SELECT DISTINCT ticker FROM backfill_results WHERE return_pct IS NOT NULL
        """)).fetchall()
    tickers = [r[0] for r in rows]

    scores_by_year: dict[int, dict[str, float]] = {y: {} for y in _TRAIN_YEARS + _TEST_YEARS}
    for ticker in tickers:
        for base_year in _TRAIN_YEARS + _TEST_YEARS:
            result = get_value_score(ticker, base_year)
            if result:
                scores_by_year[base_year][ticker] = result["value_score"]
        await asyncio.sleep(0)

    train_scores = {t: s for y in _TRAIN_YEARS for t, s in scores_by_year[y].items()}
    test_scores  = {t: s for y in _TEST_YEARS  for t, s in scores_by_year[y].items()}
    train_result = factor_ic(train_scores, train_years=_TRAIN_YEARS)
    test_result  = factor_ic(test_scores,  train_years=_TEST_YEARS)
    save_factor_ic("value_pbr_per", train_result.get("ic"), test_result.get("ic"),
                   train_result.get("n", 0), test_result.get("n", 0))


@router.post("/ic/disclosure")
async def measure_disclosure_ic(background_tasks: BackgroundTasks):
    """공시 활동 요소(DART 연간 공시 건수) IC 측정 — 백그라운드."""
    background_tasks.add_task(_run_disclosure_ic)
    return {"message": "공시 활동 IC 측정 시작 (백그라운드). GET /api/v2/factors/ic 로 결과 확인."}


async def _run_disclosure_ic():
    import asyncio
    from sqlalchemy import text
    from app.core.database import SessionLocal
    from app.domain.factors.disclosure_activity import get_disclosure_score, get_corp_code
    from app.domain.factors.ic_engine import factor_ic, save_factor_ic

    with SessionLocal() as s:
        rows = s.execute(text("""
            SELECT DISTINCT ticker FROM backfill_results WHERE return_pct IS NOT NULL
        """)).fetchall()
    tickers = [r[0] for r in rows]

    scores_by_year: dict[int, dict[str, float]] = {y: {} for y in _TRAIN_YEARS + _TEST_YEARS}
    for ticker in tickers:
        corp_code = get_corp_code(ticker)
        if not corp_code:
            continue
        for base_year in _TRAIN_YEARS + _TEST_YEARS:
            result = get_disclosure_score(corp_code, base_year)
            if result:
                scores_by_year[base_year][ticker] = result["disclosure_score"]
        await asyncio.sleep(0.3)

    train_scores = {t: s for y in _TRAIN_YEARS for t, s in scores_by_year[y].items()}
    test_scores  = {t: s for y in _TEST_YEARS  for t, s in scores_by_year[y].items()}
    train_result = factor_ic(train_scores, train_years=_TRAIN_YEARS)
    test_result  = factor_ic(test_scores,  train_years=_TEST_YEARS)
    save_factor_ic("disclosure_activity", train_result.get("ic"), test_result.get("ic"),
                   train_result.get("n", 0), test_result.get("n", 0))
