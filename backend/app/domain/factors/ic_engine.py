"""
IC (Information Coefficient) 측정 엔진

IC = 예측 점수와 실제 수익률 간 스피어만 순위 상관계수.
backfill_results 테이블을 기반으로 각 요소(factor)의 예측력을 측정한다.

Walk-forward 방지: train/test 기간을 분리해 과적합을 막는다.
  - train: base_year 2018~2019  (요소 개발·선택)
  - test:  base_year 2020       (홀드아웃 검증)
"""
import math
from collections import defaultdict
from typing import Optional
from sqlalchemy import text
from app.core.database import SessionLocal


def _spearman(xs: list[float], ys: list[float]) -> Optional[float]:
    """순수 구현 스피어만 상관. scipy 없이 동작."""
    n = len(xs)
    if n < 3:
        return None

    def _rank(vals):
        sorted_idx = sorted(range(n), key=lambda i: vals[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n and vals[sorted_idx[j]] == vals[sorted_idx[i]]:
                j += 1
            avg_rank = (i + j - 1) / 2.0 + 1
            for k in range(i, j):
                ranks[sorted_idx[k]] = avg_rank
            i = j
        return ranks

    rx, ry = _rank(xs), _rank(ys)
    mean_rx = sum(rx) / n
    mean_ry = sum(ry) / n
    num = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
    den_x = math.sqrt(sum((rx[i] - mean_rx) ** 2 for i in range(n)))
    den_y = math.sqrt(sum((ry[i] - mean_ry) ** 2 for i in range(n)))
    if den_x == 0 or den_y == 0:
        return None
    return round(num / (den_x * den_y), 4)



def baseline_ic(train_years: list[int] | None = None) -> dict:
    """v1 기준선 IC: total_score → actual_return_pct.

    train_years를 지정하면 해당 base_year만 사용 (walk-forward train set).
    """
    params: dict = {}
    where = "WHERE return_pct IS NOT NULL AND total_score IS NOT NULL"
    if train_years:
        placeholders = ", ".join(f":y{i}" for i in range(len(train_years)))
        where += f" AND base_year IN ({placeholders})"
        for i, y in enumerate(train_years):
            params[f"y{i}"] = y

    with SessionLocal() as session:
        rows = session.execute(text(f"""
            SELECT total_score, return_pct, base_year, grade
            FROM backfill_results {where}
        """), params).fetchall()

    if not rows:
        return {"ic": None, "n": 0, "years": [], "note": "backfill 데이터 없음"}

    scores = [float(r[0]) for r in rows]
    returns = [float(r[1]) for r in rows]
    ic = _spearman(scores, returns)

    by_grade: dict = defaultdict(lambda: {"scores": [], "returns": []})
    for r in rows:
        g = r[3]
        by_grade[g]["scores"].append(float(r[0]))
        by_grade[g]["returns"].append(float(r[1]))

    grade_ic = {}
    for g, d in by_grade.items():
        grade_ic[g] = _spearman(d["scores"], d["returns"])

    return {
        "factor": "total_score_v1",
        "ic": ic,
        "n": len(rows),
        "grade_ic": grade_ic,
        "years": sorted({r[2] for r in rows}),
    }


def factor_ic(factor_scores: dict[str, float], train_years: list[int] | None = None) -> dict:
    """임의 요소의 IC 측정.

    factor_scores: {ticker: score} 딕셔너리 (기준 연도 고정 가정)
    backfill_results에서 해당 ticker의 actual_return_pct와 대조.
    """
    params: dict = {}
    where = "WHERE return_pct IS NOT NULL"
    if train_years:
        placeholders = ", ".join(f":y{i}" for i in range(len(train_years)))
        where += f" AND base_year IN ({placeholders})"
        for i, y in enumerate(train_years):
            params[f"y{i}"] = y

    with SessionLocal() as session:
        rows = session.execute(text(f"""
            SELECT ticker, return_pct, base_year
            FROM backfill_results {where}
        """), params).fetchall()

    pairs = [(factor_scores[r[0]], float(r[1])) for r in rows if r[0] in factor_scores]
    if len(pairs) < 3:
        return {"ic": None, "n": len(pairs), "years": [], "note": "표본 부족"}

    xs, ys = zip(*pairs)
    ic = _spearman(list(xs), list(ys))
    return {
        "ic": ic,
        "n": len(pairs),
        "years": sorted({r[2] for r in rows if r[0] in factor_scores}),
    }


def get_ic_leaderboard() -> dict:
    """저장된 요소별 IC를 조회 (factor_ic_results 테이블).

    train IC와 test IC를 분리해 과적합 탐지.
    """
    with SessionLocal() as session:
        exists = session.execute(text("""
            SELECT EXISTS (SELECT 1 FROM information_schema.tables
                           WHERE table_name = 'factor_ic_results')
        """)).scalar()
        if not exists:
            return {"leaderboard": [], "note": "IC 측정 결과 없음. /api/v2/factors/ic 실행 필요"}

        rows = session.execute(text("""
            SELECT factor_name, train_ic, test_ic, n_train, n_test, status, measured_at
            FROM factor_ic_results
            ORDER BY COALESCE(test_ic, train_ic) DESC NULLS LAST
        """)).fetchall()

    leaderboard = []
    for r in rows:
        d = dict(r._mapping)
        d["measured_at"] = d["measured_at"].isoformat() if d["measured_at"] else None
        d["overfit_flag"] = (
            d["train_ic"] is not None and d["test_ic"] is not None
            and abs(d["train_ic"] - d["test_ic"]) > 0.1
        )
        leaderboard.append(d)

    return {"leaderboard": leaderboard}


def save_factor_ic(factor_name: str, train_ic: Optional[float], test_ic: Optional[float],
                   n_train: int, n_test: int, session=None) -> None:
    """IC 결과를 factor_ic_results 테이블에 저장 (upsert)."""
    _ensure_ic_table(session)
    upsert = text("""
        INSERT INTO factor_ic_results
            (factor_name, train_ic, test_ic, n_train, n_test, measured_at)
        VALUES (:name, :train_ic, :test_ic, :n_train, :n_test, NOW())
        ON CONFLICT (factor_name) DO UPDATE
        SET train_ic = EXCLUDED.train_ic, test_ic = EXCLUDED.test_ic,
            n_train = EXCLUDED.n_train, n_test = EXCLUDED.n_test,
            measured_at = NOW()
    """)
    with SessionLocal() as s:
        s.execute(upsert, {"name": factor_name, "train_ic": train_ic, "test_ic": test_ic,
                           "n_train": n_train, "n_test": n_test})
        s.commit()


def update_factor_status(factor_name: str, status: str) -> bool:
    """요소 상태 변경 (testing → adopted/rejected).

    status: 'testing' | 'adopted' | 'rejected'
    Returns True if row was found and updated.
    """
    valid = {"testing", "adopted", "rejected"}
    if status not in valid:
        raise ValueError(f"status must be one of {valid}")
    with SessionLocal() as s:
        result = s.execute(text("""
            UPDATE factor_ic_results SET status = :status
            WHERE factor_name = :name
        """), {"name": factor_name, "status": status})
        s.commit()
        return result.rowcount > 0


def _ensure_ic_table(_=None):
    with SessionLocal() as s:
        s.execute(text("""
            CREATE TABLE IF NOT EXISTS factor_ic_results (
                factor_name VARCHAR(100) PRIMARY KEY,
                train_ic    NUMERIC(6,4),
                test_ic     NUMERIC(6,4),
                n_train     INT,
                n_test      INT,
                status      VARCHAR(20) DEFAULT 'testing',
                measured_at TIMESTAMP DEFAULT NOW()
            )
        """))
        # 기존 테이블에 status 컬럼 추가 (이미 있으면 무시)
        s.execute(text("""
            ALTER TABLE factor_ic_results
            ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'testing'
        """))
        s.commit()
