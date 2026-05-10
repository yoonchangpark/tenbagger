"""
텐배거 헌터 — 정확도 검증 에이전트
매주 일요일 자동 실행:
  1. TENBAGGER/COMPOUNDER 등급 종목의 분석 시점 이후 실제 수익률 계산
  2. 목표 지표 대비 달성률 평가
  3. 결과를 DB 저장 + 콘솔 리포트 출력

실행:
  docker exec -it tenbagger_api python -m app.agents.accuracy_validator
  docker exec -it tenbagger_api python -m app.agents.accuracy_validator --grade TENBAGGER
  docker exec -it tenbagger_api python -m app.agents.accuracy_validator --min-days 180
"""

import argparse
import asyncio
import datetime
import statistics
from typing import Optional

from sqlalchemy import text

from app.core.database import SessionLocal


# ── 목표 지표 ───────────────────────────────────────────────────────────────
TARGETS = {
    "TENBAGGER":  {"days": 365 * 2, "target_return_pct": 50.0,  "label": "2년 +50%"},
    "COMPOUNDER": {"days": 365 * 1, "target_return_pct": 20.0,  "label": "1년 +20%"},
    "WATCHLIST":  {"days": 365 * 1, "target_return_pct": 10.0,  "label": "1년 +10%"},
}


# ── DB 유틸 ─────────────────────────────────────────────────────────────────

def get_graded_stocks(grade_filter: Optional[str] = None) -> list[dict]:
    """등급이 매겨진 종목 목록 조회"""
    with SessionLocal() as session:
        where = "WHERE grade IN ('TENBAGGER','COMPOUNDER','WATCHLIST')"
        if grade_filter:
            grades = [g.strip().upper() for g in grade_filter.split(",")]
            grade_list = ",".join(f"'{g}'" for g in grades)
            where = f"WHERE grade IN ({grade_list})"

        rows = session.execute(text(f"""
            SELECT ticker, name, grade, total_score, analyzed_at
            FROM scores
            {where}
            ORDER BY grade, total_score DESC
        """)).fetchall()
    return [dict(r._mapping) for r in rows]


def get_price_on_date(ticker: str, target_date: datetime.date) -> Optional[float]:
    """특정 날짜에 가장 가까운 종가 조회 (이전 최대 7 영업일)"""
    with SessionLocal() as session:
        row = session.execute(text("""
            SELECT close_price FROM price_daily_cache
            WHERE ticker = :t
              AND trade_date <= :d
              AND trade_date >= :d_minus7
              AND close_price > 0
            ORDER BY trade_date DESC LIMIT 1
        """), {
            "t": ticker,
            "d": target_date,
            "d_minus7": target_date - datetime.timedelta(days=7),
        }).fetchone()
    return float(row.close_price) if row else None


def get_latest_price(ticker: str) -> Optional[float]:
    """최신 종가 조회"""
    with SessionLocal() as session:
        row = session.execute(text("""
            SELECT close_price FROM price_daily_cache
            WHERE ticker = :t AND close_price > 0
            ORDER BY trade_date DESC LIMIT 1
        """), {"t": ticker}).fetchone()
    return float(row.close_price) if row else None


def save_accuracy_result(ticker: str, grade: str, analyzed_at: datetime.date,
                         entry_price: float, current_price: float,
                         return_pct: float, hold_days: int) -> None:
    """정확도 결과 DB 저장 (accuracy_results 테이블)"""
    with SessionLocal() as session:
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS accuracy_results (
                id           SERIAL PRIMARY KEY,
                ticker       VARCHAR(20) NOT NULL,
                grade        VARCHAR(20) NOT NULL,
                analyzed_at  DATE NOT NULL,
                entry_price  NUMERIC(12,2),
                current_price NUMERIC(12,2),
                return_pct   NUMERIC(8,2),
                hold_days    INTEGER,
                checked_at   TIMESTAMP DEFAULT NOW(),
                UNIQUE (ticker, analyzed_at)
            )
        """))
        session.execute(text("""
            INSERT INTO accuracy_results
                (ticker, grade, analyzed_at, entry_price, current_price, return_pct, hold_days)
            VALUES (:ticker, :grade, :analyzed_at, :entry, :current, :return_pct, :days)
            ON CONFLICT (ticker, analyzed_at) DO UPDATE
                SET current_price = EXCLUDED.current_price,
                    return_pct    = EXCLUDED.return_pct,
                    hold_days     = EXCLUDED.hold_days,
                    checked_at    = NOW()
        """), {
            "ticker": ticker, "grade": grade,
            "analyzed_at": analyzed_at,
            "entry": entry_price, "current": current_price,
            "return_pct": return_pct, "days": hold_days,
        })
        session.commit()


# ── 검증 메인 ────────────────────────────────────────────────────────────────

def run_validation(grade_filter: Optional[str] = None, min_days: int = 30) -> dict:
    """정확도 검증 실행. 결과 딕셔너리 반환"""
    today = datetime.date.today()
    stocks = get_graded_stocks(grade_filter)

    print(f"\n{'='*60}")
    print(f"  텐배거 헌터 정확도 검증 — {today.isoformat()}")
    print(f"  대상: {len(stocks)}개 종목 | 최소 보유기간: {min_days}일")
    print(f"{'='*60}\n")

    results_by_grade: dict[str, list[float]] = {}
    skipped = 0

    for s in stocks:
        ticker = s["ticker"]
        grade  = s["grade"]
        analyzed_at = s["analyzed_at"]

        if not analyzed_at:
            skipped += 1
            continue

        analyzed_date = analyzed_at.date() if hasattr(analyzed_at, "date") else analyzed_at
        hold_days = (today - analyzed_date).days

        if hold_days < min_days:
            skipped += 1
            continue

        entry_price   = get_price_on_date(ticker, analyzed_date)
        current_price = get_latest_price(ticker)

        if not entry_price or not current_price:
            skipped += 1
            continue

        return_pct = round((current_price - entry_price) / entry_price * 100, 2)
        save_accuracy_result(ticker, grade, analyzed_date,
                             entry_price, current_price, return_pct, hold_days)

        if grade not in results_by_grade:
            results_by_grade[grade] = []
        results_by_grade[grade].append(return_pct)

        print(f"  {grade:12} {s['name'][:10]:10} ({ticker}) "
              f"| {hold_days:4}일 | {return_pct:+.1f}%")

    # ── 집계 ──────────────────────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print(f"  {'등급':12} {'종목수':>6} {'중간값':>8} {'평균':>8} {'목표':>10} {'달성률':>8}")
    print(f"{'─'*60}")

    summary = {}
    for grade, returns in sorted(results_by_grade.items()):
        target_info = TARGETS.get(grade, {})
        target_pct  = target_info.get("target_return_pct", 0)
        target_label = target_info.get("label", "-")
        median_ret  = statistics.median(returns)
        mean_ret    = statistics.mean(returns)
        hit_rate    = sum(1 for r in returns if r >= target_pct) / len(returns) * 100

        print(f"  {grade:12} {len(returns):6} {median_ret:+8.1f}% {mean_ret:+8.1f}% "
              f"{target_label:>10} {hit_rate:7.1f}%")

        summary[grade] = {
            "count":      len(returns),
            "median_pct": round(median_ret, 2),
            "mean_pct":   round(mean_ret, 2),
            "target_pct": target_pct,
            "hit_rate":   round(hit_rate, 1),
        }

    print(f"{'─'*60}")
    print(f"  스킵(가격 데이터 없음 / 보유기간 미달): {skipped}개\n")

    # ── 시스템 평가 ────────────────────────────────────────────────────────
    tb = summary.get("TENBAGGER", {})
    if tb:
        hit = tb["hit_rate"]
        if hit >= 30:
            verdict = f"✅ 목표 달성 ({hit:.1f}% ≥ 30%)"
        else:
            verdict = f"⚠️  개선 필요 ({hit:.1f}% < 30% 목표)"
        print(f"  TENBAGGER 2년 목표(+50%) 달성률: {verdict}")

    return {"summary": summary, "skipped": skipped, "checked_at": today.isoformat()}


# ── 진입점 ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="텐배거 헌터 정확도 검증")
    parser.add_argument("--grade", default=None,
                        help="검증할 등급 (쉼표 구분, 예: TENBAGGER,COMPOUNDER)")
    parser.add_argument("--min-days", type=int, default=30,
                        help="최소 보유 기간(일), 미달 종목은 스킵 (기본: 30)")
    args = parser.parse_args()
    run_validation(grade_filter=args.grade, min_days=args.min_days)
