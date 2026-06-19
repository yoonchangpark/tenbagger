"""
공시 활동 요소 (Factor v5)

가설: 공시 활동이 활발한 기업 = 주주 소통 적극적 → 거버넌스 프리미엄
데이터: DART 공시 검색 API (search_disclosures)
출력: 0~10 점수 (연간 공시 건수 기반)

주의: 불성실 공시(정정·취소)가 많으면 오히려 부정적 → 정기 공시 위주로 카운트
"""
import os
import httpx
from datetime import date
from typing import Optional

_DART_API_KEY = os.getenv("DART_API_KEY", "")
_DART_BASE = "https://opendart.fss.or.kr/api"

# 정기 공시 유형 (긍정적 신호)
_POSITIVE_REPORT_TYPES = {
    "사업보고서", "반기보고서", "분기보고서",
    "주주총회소집공고", "주주총회결과", "감사보고서",
}


def get_disclosure_score(corp_code: str, base_year: int) -> Optional[dict]:
    """base_year 기준 직전 12개월 DART 공시 건수 → 0~10 점수.

    corp_code: DART 기업고유번호 (8자리)
    Returns: {"total_count": int, "positive_count": int, "disclosure_score": float}
    """
    if not _DART_API_KEY or not corp_code:
        return None

    bgn = f"{base_year - 1}0101"
    end = f"{base_year}1231"

    try:
        resp = httpx.get(
            f"{_DART_BASE}/list.json",
            params={
                "crtfc_key": _DART_API_KEY,
                "corp_code": corp_code,
                "bgn_de": bgn,
                "end_de": end,
                "page_count": 100,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("status") != "000":
            return None

        items = data.get("list", [])
        total = len(items)
        positive = sum(
            1 for it in items
            if any(t in it.get("report_nm", "") for t in _POSITIVE_REPORT_TYPES)
        )

        # 연간 20건 이상 = 10점, 0건 = 0점
        # 단, 정기 공시 3건 이상(사업·반기·분기) 있어야 신뢰 가능 기업
        if positive < 2:
            score = min(3.0, total / 5 * 3)  # 최대 3점 (정기공시 미달)
        else:
            score = min(10.0, total / 20 * 10)

        return {
            "total_count": total,
            "positive_count": positive,
            "disclosure_score": round(score, 2),
        }
    except Exception:
        return None


def get_corp_code(ticker: str) -> Optional[str]:
    """티커 → DART corp_code 조회."""
    if not _DART_API_KEY:
        return None
    try:
        resp = httpx.get(
            f"{_DART_BASE}/company.json",
            params={"crtfc_key": _DART_API_KEY, "stock_code": ticker},
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data.get("corp_code")
    except Exception:
        return None
