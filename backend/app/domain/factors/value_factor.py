"""
가치 요소 (Factor v4)

가설: 저PBR · 저PER 종목이 장기적으로 초과 수익 (Fama-French 검증)
데이터: scores 테이블 (ETL이 수집한 PBR/PER), 없으면 pykrx
출력: 0~10 점수 (PBR/PER 낮을수록 높음 — 단, 적자 제외)

주의: PBR=0 또는 PER=0 은 결측 처리
"""
from typing import Optional
from sqlalchemy import text
from app.core.database import SessionLocal


def get_value_score(ticker: str, base_year: int) -> Optional[dict]:
    """base_year 시점 PBR/PER 기반 가치 점수 → 0~10.

    scores 테이블에서 해당 연도 데이터를 먼저 조회한다.
    Returns: {"pbr": float, "per": float, "value_score": float}
    """
    with SessionLocal() as session:
        row = session.execute(text("""
            SELECT pbr, per, total_score
            FROM scores
            WHERE ticker = :ticker
            ORDER BY ABS(EXTRACT(YEAR FROM updated_at) - :year)
            LIMIT 1
        """), {"ticker": ticker, "year": base_year}).fetchone()

    if not row:
        return None

    pbr = float(row[0]) if row[0] and float(row[0]) > 0 else None
    per = float(row[1]) if row[1] and float(row[1]) > 0 else None

    if pbr is None and per is None:
        return None

    # PBR 점수: 0.3~3.0 범위를 역수 정규화 (낮을수록 높은 점수)
    if pbr is not None:
        # PBR 0.3 = 10점, PBR 3.0 = 0점
        s_pbr = max(0.0, min(10.0, (3.0 - pbr) / 2.7 * 10))
    else:
        s_pbr = 5.0  # 결측은 중간값

    # PER 점수: 5~30 범위 역수 정규화 (낮을수록 높은 점수, 단 음수는 0점)
    if per is not None and per > 0:
        s_per = max(0.0, min(10.0, (30.0 - per) / 25.0 * 10))
    else:
        s_per = 0.0 if (per is not None and per < 0) else 5.0

    value_score = round(s_pbr * 0.5 + s_per * 0.5, 2)

    return {"pbr": pbr, "per": per, "value_score": value_score}
