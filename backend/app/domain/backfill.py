"""
검증 정확도 백필 v1 (현재 스코어 공식 기준선)

지금 가진 DART 데이터로 '그 시점이었다면 시스템이 매겼을 등급'을 점-인-타임으로 재구성하고,
실제 이후 수익률과 대조해 '검증된 정확도'를 즉시 채운다.

⚠️ v1 한계 (페이지에 반드시 표기):
- 유니버스가 현재 상장 종목(scores 테이블)이라 상폐 종목이 빠진 '상폐 제외 표본' → 정확도가 낙관 쪽으로 편향될 수 있음.
- 현재 스코어 공식 1개만 검증한다(요소 발굴 엔진의 v1 기준선). 요소 추가는 다음 단계.
- 룩어헤드 방지: run_backtest가 end_year=base_year로 그 시점 이전 재무만 사용한다.
"""
import asyncio
import time
import datetime
from collections import defaultdict
from sqlalchemy import text

from app.core.database import SessionLocal
from app.domain.backtest import run_backtest

# 등급별 '적중' 목표 (보유기간 무관 단순 기준선). accuracy.py의 _TARGETS와 일관.
GRADE_TARGET_PCT = {
    "TENBAGGER": 50.0,
    "COMPOUNDER": 20.0,
    "WATCHLIST": 10.0,
    "AVOID": 0.0,   # AVOID는 '하락 회피'가 목표라 별도 해석 (return < 0 이면 적중)
}


def _ensure_table(session):
    session.execute(text("""
        CREATE TABLE IF NOT EXISTS backfill_results (
            id          SERIAL PRIMARY KEY,
            ticker      VARCHAR(10) NOT NULL,
            base_year   INT NOT NULL,
            hold_years  INT NOT NULL,
            grade       VARCHAR(20),
            total_score NUMERIC(5,2),
            return_pct  NUMERIC(10,2),
            hit_target  BOOLEAN,
            created_at  TIMESTAMP DEFAULT NOW(),
            UNIQUE (ticker, base_year, hold_years)
        )
    """))
    # 발굴 점수 v2 컬럼 (기존 테이블에도 안전하게 추가)
    session.execute(text(
        "ALTER TABLE backfill_results ADD COLUMN IF NOT EXISTS discovery_score NUMERIC(5,2)"
    ))
    session.commit()


def _is_hit(grade: str, ret: float) -> bool | None:
    if ret is None:
        return None
    if grade == "AVOID":
        return ret < 0          # 회피 등급은 실제로 하락(또는 부진)했으면 적중
    target = GRADE_TARGET_PCT.get(grade)
    if target is None:
        return None
    return ret >= target


def _get_universe(limit: int, market: str | None) -> list[str]:
    params: dict = {"limit": limit}
    sql = """
        SELECT ticker FROM scores
        GROUP BY ticker
        ORDER BY MAX(total_score) DESC
        LIMIT :limit
    """
    with SessionLocal() as session:
        rows = session.execute(text(sql), params).fetchall()
        return [r[0] for r in rows]


async def run_backfill(base_years: list[int], hold_years: int = 2,
                       limit: int = 200, market: str | None = None,
                       progress: dict | None = None) -> dict:
    """백필 실행 — (종목 × base_year) 격자를 돌며 점-인-타임 등급·이후 수익률을 기록.
    progress dict가 주어지면 진행 상황을 실시간 갱신한다."""
    tickers = _get_universe(limit, market)
    if not tickers:
        return {"error": "스크리너 데이터가 없습니다. ETL을 먼저 실행해 주세요."}

    with SessionLocal() as session:
        _ensure_table(session)

    total = len(tickers) * len(base_years)
    done = 0
    stored = 0
    if progress is not None:
        progress.update({"total": total, "done": 0, "stored": 0, "status": "running"})

    upsert = text("""
        INSERT INTO backfill_results
            (ticker, base_year, hold_years, grade, total_score, discovery_score, return_pct, hit_target)
        VALUES (:ticker, :base_year, :hold_years, :grade, :score, :disc, :ret, :hit)
        ON CONFLICT (ticker, base_year, hold_years) DO UPDATE
        SET grade = EXCLUDED.grade, total_score = EXCLUDED.total_score,
            discovery_score = EXCLUDED.discovery_score,
            return_pct = EXCLUDED.return_pct, hit_target = EXCLUDED.hit_target,
            created_at = NOW()
    """)

    for base_year in base_years:
        for ticker in tickers:
            try:
                r = await run_backtest(ticker, base_year, hold_years)
                if "error" not in r:
                    grade = r.get("predicted_grade")
                    ret = r.get("actual_return_pct")
                    score = (r.get("score_at_base_year") or {}).get("total_score")
                    disc = r.get("discovery_at_base_year")
                    hit = _is_hit(grade, ret)
                    with SessionLocal() as session:
                        session.execute(upsert, {
                            "ticker": ticker, "base_year": base_year, "hold_years": hold_years,
                            "grade": grade, "score": score, "disc": disc, "ret": ret, "hit": hit,
                        })
                        session.commit()
                    stored += 1
                await asyncio.sleep(0.3)
            except Exception as e:
                print(f"[Backfill] {ticker} {base_year} 실패: {e}")
            done += 1
            if progress is not None:
                progress.update({"done": done, "stored": stored})

    if progress is not None:
        progress["status"] = "completed"
    return get_backfill_summary()


def get_backfill_summary(hold_years: int | None = None) -> dict:
    """저장된 백필 결과를 등급별로 집계 (중간값 중심, 정직한 표본 수 명시).

    hold_years 지정 시 해당 보유기간 결과만 집계한다. 서로 다른 보유기간(예: 2년·10년)
    결과가 한 테이블에 섞여 있을 때 중간값/적중률이 뒤섞이는 것을 막기 위함이다.
    """
    where = "" if hold_years is None else "WHERE hold_years = :hy"
    params = {} if hold_years is None else {"hy": hold_years}
    with SessionLocal() as session:
        exists = session.execute(text("""
            SELECT EXISTS (SELECT 1 FROM information_schema.tables
                           WHERE table_name = 'backfill_results')
        """)).scalar()
        if not exists:
            return {"summary": {}, "meta": {"available": False,
                    "note": "백필 미실행. POST /api/v2/accuracy/backfill 로 실행하세요."}}

        rows = session.execute(text(f"""
            SELECT grade,
                   COUNT(*) AS n,
                   COUNT(return_pct) AS n_priced,
                   ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY return_pct)::numeric, 1) AS median_pct,
                   ROUND(AVG(return_pct)::numeric, 1) AS mean_pct,
                   ROUND(100.0 * SUM(CASE WHEN hit_target THEN 1 ELSE 0 END)
                         / NULLIF(COUNT(hit_target), 0), 1) AS hit_rate,
                   MIN(base_year) AS from_year, MAX(base_year) AS to_year,
                   MAX(hold_years) AS hold_years,
                   MAX(created_at) AS last_run
            FROM backfill_results
            {where}
            GROUP BY grade
        """), params).fetchall()

    order = {"TENBAGGER": 0, "COMPOUNDER": 1, "WATCHLIST": 2, "AVOID": 3}
    summary = {}
    last_run = None
    for r in sorted(rows, key=lambda x: order.get(x[0], 9)):
        d = r._mapping
        summary[d["grade"]] = {
            "count": int(d["n"]),
            "count_priced": int(d["n_priced"]) if d["n_priced"] is not None else 0,
            "median_pct": float(d["median_pct"]) if d["median_pct"] is not None else None,
            "mean_pct": float(d["mean_pct"]) if d["mean_pct"] is not None else None,
            "hit_rate": float(d["hit_rate"]) if d["hit_rate"] is not None else None,
            "target_pct": GRADE_TARGET_PCT.get(d["grade"]),
            "from_year": int(d["from_year"]) if d["from_year"] is not None else None,
            "to_year": int(d["to_year"]) if d["to_year"] is not None else None,
            "hold_years": int(d["hold_years"]) if d["hold_years"] is not None else None,
        }
        if d["last_run"]:
            last_run = d["last_run"].isoformat()

    return {
        "summary": summary,
        "meta": {
            "available": bool(summary),
            "method": "backfill_v1",
            "requested_hold_years": hold_years,
            "last_run": last_run,
            "caveats": [
                "현재 스코어 공식 1개를 검증한 기준선입니다(요소 발굴 엔진의 v1).",
                "유니버스가 현재 상장 종목이라 상폐 종목이 빠진 '상폐 제외 표본'입니다 — 정확도가 낙관 쪽으로 편향될 수 있습니다.",
                "룩어헤드 방지: 각 시점 이전에 공시된 재무만 사용했습니다.",
            ],
        },
    }
