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
    # 발굴 점수 v2 / 모멘텀 점수 컬럼 (기존 테이블에도 안전하게 추가)
    session.execute(text(
        "ALTER TABLE backfill_results ADD COLUMN IF NOT EXISTS discovery_score NUMERIC(5,2)"
    ))
    session.execute(text(
        "ALTER TABLE backfill_results ADD COLUMN IF NOT EXISTS momentum_score NUMERIC(5,2)"
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


def _get_universe(limit: int, market: str | None, universe: str = "top_score") -> list[str]:
    """top_score: 현재 점수 상위 N (기존 동작). 구 공식이 AVOID로 찍은 실제 승자가 빠지므로
    팩터 검증에는 부적합하다. sample: 점수와 무관한 고정 해시 순서로 N개 — 재실행해도 같은 표본."""
    params: dict = {"limit": limit}
    order = "MD5(ticker)" if universe == "sample" else "MAX(total_score) DESC"
    sql = f"""
        SELECT ticker FROM scores
        GROUP BY ticker
        ORDER BY {order}
        LIMIT :limit
    """
    with SessionLocal() as session:
        rows = session.execute(text(sql), params).fetchall()
        return [r[0] for r in rows]


async def run_backfill(base_years: list[int], hold_years: int = 2,
                       limit: int = 200, market: str | None = None,
                       progress: dict | None = None,
                       universe: str = "top_score", skip_existing: bool = False) -> dict:
    """백필 실행 — (종목 × base_year) 격자를 돌며 점-인-타임 등급·이후 수익률을 기록.
    progress dict가 주어지면 진행 상황을 실시간 갱신한다.
    skip_existing이면 세 점수와 수익률이 이미 채워진 행은 건너뛴다 — 재배포·DART 쿼터로
    중단된 백필을 이어서 돌리기 위함."""
    tickers = _get_universe(limit, market, universe)
    if not tickers:
        return {"error": "스크리너 데이터가 없습니다. ETL을 먼저 실행해 주세요."}

    with SessionLocal() as session:
        _ensure_table(session)
        done_keys: set = set()
        if skip_existing:
            done_keys = {(r[0], r[1]) for r in session.execute(text("""
                SELECT ticker, base_year FROM backfill_results
                WHERE hold_years = :hy AND return_pct IS NOT NULL AND total_score IS NOT NULL
                  AND discovery_score IS NOT NULL AND momentum_score IS NOT NULL
            """), {"hy": hold_years}).fetchall()}

    total = len(tickers) * len(base_years)
    done = 0
    stored = 0
    if progress is not None:
        progress.update({"total": total, "done": 0, "stored": 0, "status": "running"})

    upsert = text("""
        INSERT INTO backfill_results
            (ticker, base_year, hold_years, grade, total_score, discovery_score, momentum_score, return_pct, hit_target)
        VALUES (:ticker, :base_year, :hold_years, :grade, :score, :disc, :mom, :ret, :hit)
        ON CONFLICT (ticker, base_year, hold_years) DO UPDATE
        SET grade = EXCLUDED.grade, total_score = EXCLUDED.total_score,
            discovery_score = EXCLUDED.discovery_score, momentum_score = EXCLUDED.momentum_score,
            return_pct = EXCLUDED.return_pct, hit_target = EXCLUDED.hit_target,
            created_at = NOW()
    """)

    for base_year in base_years:
        for ticker in tickers:
            if (ticker, base_year) in done_keys:
                done += 1
                continue
            try:
                r = await run_backtest(ticker, base_year, hold_years)
                if "error" not in r:
                    grade = r.get("predicted_grade")
                    ret = r.get("actual_return_pct")
                    score = (r.get("score_at_base_year") or {}).get("total_score")
                    disc = r.get("discovery_at_base_year")
                    mom = r.get("momentum_at_base_year")
                    hit = _is_hit(grade, ret)
                    with SessionLocal() as session:
                        session.execute(upsert, {
                            "ticker": ticker, "base_year": base_year, "hold_years": hold_years,
                            "grade": grade, "score": score, "disc": disc, "mom": mom, "ret": ret, "hit": hit,
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


# ── 검증 프로토콜 (scoring_v2_design.md §5) ─────────────────────────────────
# 세 기준을 backfill_results에서 그대로 계산한다. 읽기 전용.
#   ① Spearman(점수, 수익률) > quality(total_score)의 Spearman
#   ② 점수 상위 10% 중간 수익률 > TENBAGGER 등급 중간값, 그리고 > 시장 중간값
#   ③ 실제 승자(코호트 수익률 상위 5%)가 점수 분포의 상위 분위로 이동
# 연도가 섞이면 시장 국면이 순위를 왜곡하므로 전부 코호트(base_year) 안에서 계산하고,
# 코호트 값의 평균/중간값으로 요약한다.
_PROTOCOL_COLS = {"quality": "total_score", "discovery": "discovery_score", "momentum": "momentum_score"}


def _median(vals: list[float]):
    if not vals:
        return None
    s = sorted(vals)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def _round1(v):
    return round(v, 1) if v is not None else None


def _pct_rank(sorted_vals: list[float], v: float) -> float:
    """v가 분포에서 차지하는 백분위(0~100, 동점은 중간)."""
    below = sum(1 for x in sorted_vals if x < v)
    equal = sum(1 for x in sorted_vals if x == v)
    return (below + equal / 2) / len(sorted_vals) * 100


def get_protocol_report(hold_years: int = 5) -> dict:
    from app.domain.factors.ic_engine import _spearman

    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT base_year, grade, total_score, discovery_score, momentum_score, return_pct
            FROM backfill_results
            WHERE hold_years = :hy AND return_pct IS NOT NULL
        """), {"hy": hold_years}).fetchall()

    if not rows:
        return {"available": False, "hold_years": hold_years,
                "note": "해당 보유기간 백필 데이터가 없습니다."}

    by_year: dict = defaultdict(list)
    for r in rows:
        by_year[int(r[0])].append(r)

    tb_returns = [float(r[5]) for r in rows if r[1] == "TENBAGGER"]
    tb_median = _median(tb_returns)

    scores = {}
    for name, _ in _PROTOCOL_COLS.items():
        idx = {"quality": 2, "discovery": 3, "momentum": 4}[name]
        cohorts = []
        for year in sorted(by_year):
            pairs = [(float(r[idx]), float(r[5])) for r in by_year[year] if r[idx] is not None]
            if len(pairs) < 20:
                cohorts.append({"base_year": year, "n": len(pairs), "skipped": "표본 20 미만"})
                continue
            xs = [p[0] for p in pairs]
            ys = [p[1] for p in pairs]
            market = _median(ys)
            # 상위 10% (코호트 내 점수 순위)
            k = max(1, len(pairs) // 10)
            top = sorted(pairs, key=lambda p: p[0], reverse=True)[:k]
            top_median = _median([p[1] for p in top])
            # 실제 승자 = 코호트 수익률 상위 5% → 그들의 점수 백분위 중간값
            w = max(1, len(pairs) // 20)
            winners = sorted(pairs, key=lambda p: p[1], reverse=True)[:w]
            sx = sorted(xs)
            winner_pct = _median([_pct_rank(sx, p[0]) for p in winners])
            cohorts.append({
                "base_year": year, "n": len(pairs),
                "spearman": _spearman(xs, ys),
                "market_median": round(market, 1),
                "top10_n": k, "top10_median": round(top_median, 1),
                "top10_alpha": round(top_median - market, 1),
                "winners_n": w, "winner_score_pctile": round(winner_pct, 1),
            })
        valid = [c for c in cohorts if "skipped" not in c]
        ics = [c["spearman"] for c in valid if c["spearman"] is not None]
        scores[name] = {
            "column": _PROTOCOL_COLS[name],
            "cohorts": cohorts,
            "n_cohorts": len(valid),
            "mean_spearman": round(sum(ics) / len(ics), 4) if ics else None,
            "positive_ic_cohorts": sum(1 for v in ics if v > 0),
            "median_top10_alpha": _round1(_median([c["top10_alpha"] for c in valid])),
            "median_top10_return": _round1(_median([c["top10_median"] for c in valid])),
            "beat_market_cohorts": sum(1 for c in valid if c["top10_alpha"] > 0),
            "median_winner_pctile": _round1(_median([c["winner_score_pctile"] for c in valid])),
        }

    base = scores["quality"]
    verdicts = {}
    for name in ("discovery", "momentum"):
        s = scores[name]
        c1 = (s["mean_spearman"] is not None and base["mean_spearman"] is not None
              and s["mean_spearman"] > base["mean_spearman"])
        c2 = (s["median_top10_return"] is not None and s["median_top10_alpha"] is not None
              and (tb_median is None or s["median_top10_return"] > tb_median)
              and s["median_top10_alpha"] > 0)
        c3 = (s["median_winner_pctile"] is not None and base["median_winner_pctile"] is not None
              and s["median_winner_pctile"] > base["median_winner_pctile"]
              and s["median_winner_pctile"] >= 70)
        # 코호트 3개 미만이거나 절반 이상에서 시장에 지면 '로버스트'로 보지 않는다
        robust = s["n_cohorts"] >= 3 and s["beat_market_cohorts"] * 2 > s["n_cohorts"]
        verdicts[name] = {
            "spearman_beats_quality": c1,
            "top10_beats_tenbagger_and_market": c2,
            "winners_move_to_top": c3,
            "robust_across_cohorts": robust,
            "pass": bool(c1 and c2 and c3 and robust),
        }

    return {
        "available": True,
        "hold_years": hold_years,
        "cohorts": sorted(by_year),
        "tenbagger_grade_median": round(tb_median, 1) if tb_median is not None else None,
        "tenbagger_grade_n": len(tb_returns),
        "scores": scores,
        "verdicts": verdicts,
        "criteria": {
            "spearman_beats_quality": "코호트 평균 Spearman이 quality(total_score)보다 높음",
            "top10_beats_tenbagger_and_market": "코호트 상위 10% 중간수익률이 TENBAGGER 등급 중간값보다 높고, 시장 대비 초과(중간값) > 0",
            "winners_move_to_top": "실제 승자(코호트 수익률 상위 5%)의 점수 백분위 중간값이 quality보다 높고 70 이상",
            "robust_across_cohorts": "유효 코호트 3개 이상, 과반 코호트에서 상위 10%가 시장을 이김",
        },
        "caveats": [
            "유니버스가 현재 상장 종목이라 상폐 종목이 빠져 있다 — 트로프(discovery) 쪽이 낙관 편향될 수 있다.",
            "universe=top_score로 돌린 백필은 구 공식이 좋아한 종목만 담아 실제 승자가 빠진다. 판정에는 universe=sample 백필을 쓸 것.",
        ],
    }
