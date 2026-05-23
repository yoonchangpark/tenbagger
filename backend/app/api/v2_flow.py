"""
GET /api/v2/flow/{ticker}  — 기관·외국인 수급 데이터 (pykrx 기반)
  최근 20거래일 기관/외국인/개인 순매수 추이 + 요약 신호
"""
import datetime
from fastapi import APIRouter, HTTPException, Query
from app.core.database import SessionLocal
from sqlalchemy import text

router = APIRouter(prefix="/api/v2/flow", tags=["flow"])

_CACHE: dict = {}          # {ticker: {"ts": datetime, "data": ...}}
_CACHE_TTL_H = 4           # 4시간 캐시


def _fetch_flow(ticker: str, days: int) -> dict | None:
    """pykrx로 기관·외국인 순매수 데이터 수집"""
    try:
        from pykrx import stock
        end   = datetime.date.today()
        start = end - datetime.timedelta(days=days + 10)  # 영업일 여유

        df = stock.get_market_trading_value_by_date(
            start.strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
            ticker,
        )
        if df is None or df.empty:
            return None

        # pykrx 컬럼명 정규화
        df = df.rename(columns={
            "기관합계": "institution",
            "외국인합계": "foreign",
            "개인": "retail",
            "기타법인": "other",
        })
        needed = [c for c in ["institution", "foreign", "retail"] if c in df.columns]
        if not needed:
            return None

        df = df[needed].tail(days)
        dates = [str(d.date()) for d in df.index]

        result = {"dates": dates}
        for col in needed:
            vals = [int(v) for v in df[col].fillna(0).tolist()]
            result[col] = vals

        # 최근 5·20거래일 합계
        summary = {}
        for col in needed:
            vals = result[col]
            summary[f"{col}_5d"]  = sum(vals[-5:])  if len(vals) >= 5  else sum(vals)
            summary[f"{col}_20d"] = sum(vals[-20:]) if len(vals) >= 20 else sum(vals)

        # 수급 신호
        f5  = summary.get("foreign_5d", 0)
        i5  = summary.get("institution_5d", 0)
        f20 = summary.get("foreign_20d", 0)
        i20 = summary.get("institution_20d", 0)

        smart_5d  = f5 + i5    # 외국인+기관 합산 (5일)
        smart_20d = f20 + i20  # 외국인+기관 합산 (20일)

        if smart_5d > 0 and smart_20d > 0:
            signal, signal_color = "매수 우위", "#10b981"
        elif smart_5d < 0 and smart_20d < 0:
            signal, signal_color = "매도 우위", "#ef4444"
        elif smart_5d > 0:
            signal, signal_color = "단기 유입", "#3b82f6"
        elif smart_5d < 0:
            signal, signal_color = "단기 이탈", "#f59e0b"
        else:
            signal, signal_color = "중립", "#9ca3af"

        result["summary"]      = summary
        result["signal"]       = signal
        result["signal_color"] = signal_color
        result["smart_5d"]     = smart_5d
        result["smart_20d"]    = smart_20d
        return result

    except Exception as e:
        print(f"[FLOW] {ticker} pykrx 오류: {e}")
        return None


def _fmt_bil(v: int) -> str:
    """억원 단위 포맷"""
    bil = v / 1e8
    if abs(bil) >= 1000:
        return f"{bil/1000:.1f}천억"
    return f"{bil:.0f}억"


@router.get("/{ticker}")
def get_flow(
    ticker: str,
    days: int = Query(20, ge=5, le=60, description="조회 거래일 수 (5~60)"),
):
    """기관·외국인 수급 데이터. 4시간 캐시."""
    cache_key = f"{ticker}_{days}"
    cached = _CACHE.get(cache_key)
    if cached and (datetime.datetime.now() - cached["ts"]).seconds < _CACHE_TTL_H * 3600:
        return cached["data"]

    data = _fetch_flow(ticker, days)
    if data is None:
        # pykrx 실패 시 DB 스코어에서 종목 존재 여부만 확인
        with SessionLocal() as session:
            row = session.execute(text(
                "SELECT name FROM scores WHERE ticker = :t LIMIT 1"
            ), {"t": ticker}).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="종목을 찾을 수 없습니다.")
        return {
            "ticker":  ticker,
            "no_data": True,
            "message": "수급 데이터를 가져올 수 없습니다 (KRX 서버 불안정 가능성)",
        }

    resp = {
        "ticker":       ticker,
        "days":         days,
        "dates":        data["dates"],
        "institution":  data.get("institution", []),
        "foreign":      data.get("foreign", []),
        "retail":       data.get("retail", []),
        "summary":      data["summary"],
        "signal":       data["signal"],
        "signal_color": data["signal_color"],
        "smart_money": {
            "5d_label":  _fmt_bil(data["smart_5d"]),
            "20d_label": _fmt_bil(data["smart_20d"]),
            "5d":  data["smart_5d"],
            "20d": data["smart_20d"],
        },
    }
    _CACHE[cache_key] = {"ts": datetime.datetime.now(), "data": resp}
    return resp
