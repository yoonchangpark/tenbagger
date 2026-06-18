"""
검색량 요소 (Factor v2)

가설: 기준 시점 직전 3개월 네이버 검색량 증가 추세 → 이후 주가 수익률과 양의 상관
데이터: Naver DataLab API (2016년부터 제공)
출력: 0~10 점수 (상승 추세 강할수록 높음)
"""
import os
import httpx
from datetime import date, timedelta
from typing import Optional


_DATALAB_URL = "https://openapi.naver.com/v1/datalab/search"
_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "")
_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")


def _month_str(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def get_search_trend(company_name: str, base_date: date, months: int = 3) -> Optional[dict]:
    """Naver DataLab에서 기준일 직전 `months`개월 검색량 반환.

    Returns: {"ratios": [float, ...], "trend_score": float 0~10}
    None if API unavailable or no data.
    """
    if not _CLIENT_ID or not _CLIENT_SECRET:
        return None

    end = base_date - timedelta(days=1)
    start = date(end.year, end.month, 1) - timedelta(days=30 * (months - 1))
    start = date(start.year, start.month, 1)

    payload = {
        "startDate": _month_str(start),
        "endDate": _month_str(end),
        "timeUnit": "month",
        "keywordGroups": [{"groupName": company_name, "keywords": [company_name]}],
    }
    try:
        resp = httpx.post(
            _DATALAB_URL,
            json=payload,
            headers={
                "X-Naver-Client-Id": _CLIENT_ID,
                "X-Naver-Client-Secret": _CLIENT_SECRET,
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        results = data.get("results", [])
        if not results:
            return None
        ratios = [p["ratio"] for p in results[0].get("data", []) if "ratio" in p]
        if len(ratios) < 2:
            return None
        trend_score = _calc_trend_score(ratios)
        return {"ratios": ratios, "trend_score": trend_score}
    except Exception:
        return None


def _calc_trend_score(ratios: list[float]) -> float:
    """검색량 추세를 0~10으로 변환.

    최근 절반 평균 / 전반 절반 평균 비율로 추세 강도 측정.
    1.0 = 변화 없음(5점), >1.0 상승, <1.0 하락.
    """
    if not ratios or max(ratios) == 0:
        return 5.0
    mid = len(ratios) // 2
    first_half = sum(ratios[:mid]) / max(mid, 1)
    second_half = sum(ratios[mid:]) / max(len(ratios) - mid, 1)
    if first_half == 0:
        return 5.0
    ratio = second_half / first_half
    # ratio: 0.5~2.0 범위를 0~10으로 선형 매핑
    score = (ratio - 0.5) / 1.5 * 10
    return round(max(0.0, min(10.0, score)), 2)
