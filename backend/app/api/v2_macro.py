"""
매크로 환경 스냅샷 API
GET /api/v2/macro/snapshot — 실데이터 기반 매크로 지표 자동 산출
"""
import os
import datetime
import threading
from fastapi import APIRouter

router = APIRouter(prefix="/api/v2/macro", tags=["Macro v2"])

# 인메모리 캐시 (1시간 TTL) — KRX/야후 호출 최소화
_cache: dict = {}
_cache_at: datetime.datetime = None
_cache_lock = threading.Lock()
CACHE_TTL_SEC = 3600


def _score_by_change(pct: float, strong: float, mild: float) -> int:
    """변화율 → -2~+2 스코어 변환"""
    if pct >= strong:
        return 2
    if pct >= mild:
        return 1
    if pct <= -strong:
        return -2
    if pct <= -mild:
        return -1
    return 0


def _fetch_dollar() -> dict:
    """USD/KRW 3개월 변화율 → 달러 강세/약세"""
    import FinanceDataReader as fdr
    end = datetime.date.today()
    start = end - datetime.timedelta(days=95)
    df = fdr.DataReader("USD/KRW", start.isoformat(), end.isoformat())
    if df.empty or len(df) < 10:
        raise ValueError("USD/KRW 데이터 부족")
    first, last = float(df["Close"].iloc[0]), float(df["Close"].iloc[-1])
    pct = (last - first) / first * 100
    value = _score_by_change(pct, strong=5, mild=2)
    return {
        "value": value,
        "auto": True,
        "evidence": f"USD/KRW {last:,.0f}원 · 3개월 {pct:+.1f}%",
    }


def _fetch_economy() -> dict:
    """KOSPI 3개월 수익률 → 경기사이클"""
    import FinanceDataReader as fdr
    end = datetime.date.today()
    start = end - datetime.timedelta(days=95)
    df = fdr.DataReader("KS11", start.isoformat(), end.isoformat())
    if df.empty or len(df) < 10:
        raise ValueError("KOSPI 데이터 부족")
    first, last = float(df["Close"].iloc[0]), float(df["Close"].iloc[-1])
    pct = (last - first) / first * 100
    value = _score_by_change(pct, strong=8, mild=3)
    return {
        "value": value,
        "auto": True,
        "evidence": f"KOSPI {last:,.0f} · 3개월 {pct:+.1f}%",
        "_df": df,  # 유동성 계산 재사용 (응답 전 제거)
    }


def _calc_liquidity(kospi_df) -> dict:
    """KOSPI 거래량: 최근 1개월 vs 이전 2개월 평균 → 유동성"""
    if "Volume" not in kospi_df.columns:
        raise ValueError("거래량 데이터 없음")
    vol = kospi_df["Volume"].dropna()
    if len(vol) < 30:
        raise ValueError("거래량 데이터 부족")
    recent = float(vol.iloc[-20:].mean())     # 최근 ~1개월 (거래일 20일)
    prior = float(vol.iloc[:-20].mean())      # 이전 ~2개월
    if prior <= 0:
        raise ValueError("거래량 기준값 0")
    pct = (recent - prior) / prior * 100
    value = _score_by_change(pct, strong=30, mild=10)
    return {
        "value": value,
        "auto": True,
        "evidence": f"거래량 최근1개월 vs 이전 {pct:+.1f}%",
    }


def _fetch_rate() -> dict:
    """한국은행 기준금리 — ECOS API 키 있을 때만 자동 (BOK_API_KEY)"""
    bok_key = os.environ.get("BOK_API_KEY", "")
    if not bok_key:
        return {"value": 0, "auto": False, "evidence": "자동 조회 불가 (BOK_API_KEY 미설정) — 직접 조정"}

    import httpx
    end = datetime.date.today()
    start = end - datetime.timedelta(days=200)
    # ECOS 722Y001: 한국은행 기준금리 (월간)
    url = (
        f"https://ecos.bok.or.kr/api/StatisticSearch/{bok_key}/json/kr/1/12/"
        f"722Y001/M/{start.strftime('%Y%m')}/{end.strftime('%Y%m')}/0101000"
    )
    r = httpx.get(url, timeout=15.0)
    r.raise_for_status()
    rows = r.json().get("StatisticSearch", {}).get("row", [])
    if len(rows) < 2:
        raise ValueError("기준금리 데이터 부족")
    rates = [float(row["DATA_VALUE"]) for row in rows]
    current = rates[-1]
    # 최근 6개월 내 변동 횟수·방향
    recent6 = rates[-7:]
    cuts = sum(1 for i in range(1, len(recent6)) if recent6[i] < recent6[i - 1])
    hikes = sum(1 for i in range(1, len(recent6)) if recent6[i] > recent6[i - 1])
    if cuts >= 2:
        value = -2
    elif cuts == 1:
        value = -1
    elif hikes >= 2:
        value = 2
    elif hikes == 1:
        value = 1
    else:
        value = 0
    direction = "인하" if cuts > hikes else ("인상" if hikes > cuts else "동결")
    return {
        "value": value,
        "auto": True,
        "evidence": f"기준금리 {current:.2f}% · 최근 6개월 {direction} 기조",
    }


def _build_snapshot() -> dict:
    indicators = {}

    # 금리
    try:
        indicators["rate"] = _fetch_rate()
    except Exception as e:
        print(f"[MACRO] 금리 조회 실패: {e}")
        indicators["rate"] = {"value": 0, "auto": False, "evidence": "조회 실패 — 직접 조정"}

    # 경기 + 유동성 (KOSPI 데이터 공유)
    kospi_df = None
    try:
        eco = _fetch_economy()
        kospi_df = eco.pop("_df")
        indicators["economy"] = eco
    except Exception as e:
        print(f"[MACRO] 경기 조회 실패: {e}")
        indicators["economy"] = {"value": 0, "auto": False, "evidence": "조회 실패 — 직접 조정"}

    try:
        if kospi_df is None:
            raise ValueError("KOSPI 데이터 없음")
        indicators["liquidity"] = _calc_liquidity(kospi_df)
    except Exception as e:
        print(f"[MACRO] 유동성 조회 실패: {e}")
        indicators["liquidity"] = {"value": 0, "auto": False, "evidence": "조회 실패 — 직접 조정"}

    # 달러
    try:
        indicators["dollar"] = _fetch_dollar()
    except Exception as e:
        print(f"[MACRO] 달러 조회 실패: {e}")
        indicators["dollar"] = {"value": 0, "auto": False, "evidence": "조회 실패 — 직접 조정"}

    # 지정학: 항상 수동
    indicators["geo"] = {"value": 0, "auto": False, "evidence": "정량화 불가 — 직접 판단 후 조정"}

    return {
        "as_of": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(),
        "indicators": indicators,
        "auto_count": sum(1 for v in indicators.values() if v["auto"]),
    }


@router.get("/snapshot")
def macro_snapshot(refresh: bool = False):
    """
    매크로 5대 지표 스냅샷 (1시간 캐시).
    rate(금리)·dollar(달러)·economy(경기)·liquidity(유동성)는 실데이터 자동 산출,
    geo(지정학)는 항상 수동. 조회 실패 지표는 auto=false + 중립(0) 반환.
    refresh=true 시 캐시 무시하고 재조회.
    """
    global _cache, _cache_at
    now = datetime.datetime.utcnow()

    with _cache_lock:
        if (
            not refresh
            and _cache
            and _cache_at
            and (now - _cache_at).total_seconds() < CACHE_TTL_SEC
        ):
            return _cache

        snapshot = _build_snapshot()
        _cache = snapshot
        _cache_at = now
        return snapshot
