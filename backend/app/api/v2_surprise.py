"""
GET /api/v2/surprise/{ticker}  — 분기 실적 서프라이즈 감지
  컨센서스 대신 전분기 대비 증감률을 기반으로 실적 서프라이즈 근사값 계산
  DART quarter_compare_cache 또는 financials_annual 활용
"""
import datetime
from fastapi import APIRouter, HTTPException
from app.core.database import SessionLocal
from sqlalchemy import text

router = APIRouter(prefix="/api/v2/surprise", tags=["surprise"])


def _calc_surprise(prev: float | None, curr: float | None) -> dict | None:
    """전분기 대비 증감률 및 서프라이즈 레벨 반환"""
    if prev is None or curr is None or prev == 0:
        return None
    chg = (curr - prev) / abs(prev) * 100
    if chg >= 20:
        level, color, label = "BEAT",    "#10b981", "서프라이즈 (큰 폭 증가)"
    elif chg >= 5:
        level, color, label = "BEAT",    "#3b82f6", "서프라이즈 (소폭 증가)"
    elif chg >= -5:
        level, color, label = "IN_LINE", "#9ca3af", "부합 (±5% 이내)"
    elif chg >= -20:
        level, color, label = "MISS",    "#f59e0b", "미스 (소폭 감소)"
    else:
        level, color, label = "MISS",    "#ef4444", "쇼크 (큰 폭 감소)"
    return {"chg_pct": round(chg, 1), "level": level, "color": color, "label": label}


@router.get("/{ticker}")
def get_surprise(ticker: str):
    """
    연간 재무 데이터 기준 최근 2개년 비교 + 서프라이즈 판정.
    DART 분기 데이터가 있으면 quarter_compare_cache 우선 사용.
    """
    with SessionLocal() as session:
        # 1) quarter_compare_cache 우선 (최신 분기 비교)
        qrow = session.execute(text("""
            SELECT corp_name, prev_label, curr_label,
                   prev_indices, curr_indices, updated_at
            FROM quarter_compare_cache
            WHERE ticker = :t
        """), {"t": ticker}).fetchone()

        # 2) 연간 재무 폴백
        ann_rows = session.execute(text("""
            SELECT f.year,
                   f.revenue, f.operating_profit, f.net_income, f.eps
            FROM financials_annual f
            JOIN companies c ON c.id = f.company_id
            WHERE c.ticker = :t
            ORDER BY f.year DESC
            LIMIT 3
        """), {"t": ticker}).fetchall()

        name_row = session.execute(text(
            "SELECT name FROM scores WHERE ticker = :t LIMIT 1"
        ), {"t": ticker}).fetchone()

    name = name_row.name if name_row else ticker

    surprises = []

    # ── 분기 데이터 (quarter_compare_cache) ────────────────────────────────
    if qrow and qrow.prev_indices and qrow.curr_indices:
        pi = qrow.prev_indices  # dict
        ci = qrow.curr_indices

        def _v(d, key):
            v = d.get(key)
            if v is None:
                return None
            try:
                return float(str(v).replace(",", "").replace("%", ""))
            except Exception:
                return None

        for metric, label_ko in [
            ("영업이익", "영업이익"),
            ("매출액",   "매출액"),
            ("당기순이익", "당기순이익"),
        ]:
            sp = _calc_surprise(_v(pi, metric), _v(ci, metric))
            if sp:
                surprises.append({
                    "period":     f"{qrow.prev_label} → {qrow.curr_label}",
                    "metric":     label_ko,
                    "prev_val":   _v(pi, metric),
                    "curr_val":   _v(ci, metric),
                    **sp,
                })

        return {
            "ticker":        ticker,
            "name":          qrow.corp_name or name,
            "data_source":   "quarter",
            "period_label":  f"{qrow.prev_label} → {qrow.curr_label}",
            "surprises":     surprises,
            "updated_at":    qrow.updated_at.isoformat() if qrow.updated_at else None,
            "overall_signal": _overall_signal(surprises),
        }

    # ── 연간 폴백 ───────────────────────────────────────────────────────────
    if len(ann_rows) < 2:
        return {
            "ticker":  ticker,
            "name":    name,
            "no_data": True,
            "message": "재무 데이터 부족 (최소 2개년 필요)",
        }

    curr_y = ann_rows[0]
    prev_y = ann_rows[1]

    for metric_key, label_ko in [
        ("revenue",          "매출액"),
        ("operating_profit", "영업이익"),
        ("net_income",       "당기순이익"),
        ("eps",              "EPS"),
    ]:
        sp = _calc_surprise(
            float(getattr(prev_y, metric_key)) if getattr(prev_y, metric_key) else None,
            float(getattr(curr_y, metric_key)) if getattr(curr_y, metric_key) else None,
        )
        if sp:
            surprises.append({
                "period":   f"{prev_y.year}년 → {curr_y.year}년",
                "metric":   label_ko,
                "prev_val": getattr(prev_y, metric_key),
                "curr_val": getattr(curr_y, metric_key),
                **sp,
            })

    return {
        "ticker":         ticker,
        "name":           name,
        "data_source":    "annual",
        "period_label":   f"{prev_y.year}년 → {curr_y.year}년",
        "surprises":      surprises,
        "overall_signal": _overall_signal(surprises),
    }


def _overall_signal(surprises: list[dict]) -> dict:
    if not surprises:
        return {"label": "데이터 없음", "color": "#6b7280"}
    beats = sum(1 for s in surprises if s["level"] == "BEAT")
    misses = sum(1 for s in surprises if s["level"] == "MISS")
    if beats > misses:
        return {"label": "전반적 서프라이즈 ↑", "color": "#10b981"}
    if misses > beats:
        return {"label": "전반적 미스 ↓",       "color": "#ef4444"}
    return {"label": "혼조세",                  "color": "#9ca3af"}
