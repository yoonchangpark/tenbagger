"""
weight_tuner.py — 정확도 데이터 기반 스코어링 가중치 자동 조정 (뼈대)

동작 원리:
  1. accuracy_results 테이블에서 등급별 실제 수익률 분포 분석
  2. 수익률이 높은 종목들의 공통 지표 패턴 추출
  3. scoring.py의 가중치 조정 제안 생성 (현재는 제안만, 자동 적용은 미구현)

실행:
  docker exec -it tenbagger_api python -m app.agents.weight_tuner
  docker exec -it tenbagger_api python -m app.agents.weight_tuner --apply  (승인 후 활성화)
"""
import argparse
import datetime
import statistics
from sqlalchemy import text
from app.core.database import SessionLocal

# 현재 가중치 (scoring.py 와 동기화)
CURRENT_WEIGHTS = {
    "growth":      0.30,
    "stability":   0.20,
    "cashflow":    0.20,
    "dividend":    0.15,
    "consistency": 0.15,
}

MIN_SAMPLES = 20   # 통계 의미를 갖기 위한 최소 샘플 수


def _load_accuracy_data(min_hold_days: int = 180) -> list[dict]:
    """accuracy_results + score_history 조인으로 수익률·지표 데이터 수집"""
    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT
                ar.ticker, ar.grade, ar.return_pct, ar.hold_days,
                sh.growth_score, sh.total_score
            FROM accuracy_results ar
            LEFT JOIN score_history sh
                ON sh.ticker = ar.ticker
               AND sh.snapshot_date = ar.analyzed_at
            WHERE ar.hold_days >= :min_days
              AND ar.grade IN ('TENBAGGER','COMPOUNDER','WATCHLIST')
            ORDER BY ar.return_pct DESC
        """), {"min_days": min_hold_days}).fetchall()
    return [dict(r._mapping) for r in rows]


def _analyze_patterns(records: list[dict]) -> dict:
    """수익률 상위/하위 종목 지표 패턴 비교"""
    if len(records) < MIN_SAMPLES:
        return {"insufficient_data": True, "sample_count": len(records)}

    sorted_r = sorted(records, key=lambda x: x["return_pct"], reverse=True)
    top_n    = max(5, len(sorted_r) // 4)
    top      = sorted_r[:top_n]
    bottom   = sorted_r[-top_n:]

    def avg(lst, key):
        vals = [x[key] for x in lst if x.get(key) is not None]
        return round(statistics.mean(vals), 2) if vals else None

    return {
        "sample_count":     len(records),
        "top_n":            top_n,
        "top_avg_return":   avg(top,    "return_pct"),
        "bottom_avg_return":avg(bottom, "return_pct"),
        "top_avg_growth":   avg(top,    "growth_score"),
        "bottom_avg_growth":avg(bottom, "growth_score"),
        "top_avg_total":    avg(top,    "total_score"),
        "bottom_avg_total": avg(bottom, "total_score"),
    }


def _suggest_weights(patterns: dict) -> dict | None:
    """패턴 분석 결과를 바탕으로 가중치 조정 제안"""
    if patterns.get("insufficient_data"):
        return None

    suggestions = dict(CURRENT_WEIGHTS)
    notes = []

    top_g    = patterns.get("top_avg_growth")
    bottom_g = patterns.get("bottom_avg_growth")

    if top_g is not None and bottom_g is not None:
        diff = top_g - bottom_g
        if diff > 1.5:
            # 상위 수익 종목이 성장성 점수가 유의하게 높음 → 가중치 상향
            delta = min(0.05, diff * 0.02)
            suggestions["growth"]    = round(min(0.45, CURRENT_WEIGHTS["growth"] + delta), 2)
            suggestions["stability"] = round(max(0.10, CURRENT_WEIGHTS["stability"] - delta / 2), 2)
            suggestions["dividend"]  = round(max(0.08, CURRENT_WEIGHTS["dividend"] - delta / 2), 2)
            notes.append(f"성장성 점수 차이 {diff:.1f}점 → growth 가중치 +{delta:.2f}")
        elif diff < -1.0:
            # 상위 종목이 안정성이 더 중요한 경우
            delta = min(0.03, abs(diff) * 0.015)
            suggestions["stability"] = round(min(0.35, CURRENT_WEIGHTS["stability"] + delta), 2)
            suggestions["growth"]    = round(max(0.20, CURRENT_WEIGHTS["growth"] - delta), 2)
            notes.append(f"안정성 패턴 감지 → stability 가중치 +{delta:.2f}")

    # 합계 1.0 보정
    total = sum(suggestions.values())
    if abs(total - 1.0) > 0.001:
        diff_adj = 1.0 - total
        suggestions["consistency"] = round(suggestions["consistency"] + diff_adj, 3)
        notes.append(f"합계 보정: consistency {diff_adj:+.3f}")

    return {"weights": suggestions, "notes": notes, "based_on": patterns["sample_count"]}


def run_weight_tuner(apply: bool = False) -> dict:
    today = datetime.date.today()
    print(f"\n{'='*60}")
    print(f"  텐배거 헌터 가중치 자동 조정 분석 — {today}")
    print(f"{'='*60}\n")

    records  = _load_accuracy_data(min_hold_days=180)
    patterns = _analyze_patterns(records)

    print(f"📊 데이터: {patterns.get('sample_count', 0)}개 종목 (180일+ 보유)")

    if patterns.get("insufficient_data"):
        print(f"⚠️  최소 {MIN_SAMPLES}개 샘플 필요 — 현재 {patterns.get('sample_count', 0)}개")
        print("   accuracy_results 데이터가 더 쌓이면 자동으로 의미 있는 분석이 가능합니다.")
        return {"status": "insufficient_data", "sample_count": patterns.get("sample_count", 0)}

    suggestion = _suggest_weights(patterns)

    print(f"\n현재 가중치:")
    for k, v in CURRENT_WEIGHTS.items():
        print(f"  {k:15s}: {v:.2f}")

    if suggestion:
        print(f"\n제안 가중치 (근거: {suggestion['based_on']}개 종목):")
        changed = False
        for k, v in suggestion["weights"].items():
            delta = v - CURRENT_WEIGHTS[k]
            marker = f"  {'▲' if delta > 0 else '▼' if delta < 0 else ' '} {delta:+.3f}" if abs(delta) > 0.001 else ""
            print(f"  {k:15s}: {v:.2f}{marker}")
            if abs(delta) > 0.001:
                changed = True
        print(f"\n조정 이유:")
        for note in suggestion["notes"]:
            print(f"  - {note}")

        if apply and changed:
            print("\n⚠️  --apply 모드: 실제 가중치 변경은 scoring.py 수동 수정 필요")
            print("   (자동 적용은 충분한 검증 후 활성화 예정)")
    else:
        print("\n변경 필요 없음 (현재 가중치 최적)")

    return {
        "status":     "ok",
        "patterns":   patterns,
        "suggestion": suggestion,
        "current":    CURRENT_WEIGHTS,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="제안 가중치 적용 (현재 미구현)")
    args = parser.parse_args()
    run_weight_tuner(apply=args.apply)
