"""
정성적 AI 분석 API (v2.0)
GET /api/v2/company/{ticker}/qualitative
"""
import datetime
from fastapi import APIRouter, HTTPException
from app.infra.clients.dart_client import (
    get_corp_code,
    get_company_info,
    get_major_shareholders,
    get_recent_disclosures,
    fetch_yearly_financials,
)
from app.domain.qualitative_analysis import generate_qualitative_analysis
from app.infra.repositories.company_repo import get_score_cached, get_financials_cached

router = APIRouter(prefix="/api/v2", tags=["v2-qualitative"])


@router.get("/company/{ticker}/qualitative")
async def get_qualitative_analysis(ticker: str):
    """
    AI 기반 정성적 기업 분석
    - 사업모델 요약
    - 경쟁우위(해자) 분석
    - 주요주주 구성 분석
    - SWOT 분석
    - 향후 CAGR 추산 근거
    - 미래 사업 전망
    - AI 투자의견
    """
    ticker = ticker.upper().strip()

    # DART corp_code 조회
    corp_code, resolved_ticker = await get_corp_code(ticker)
    if not corp_code:
        raise HTTPException(status_code=404, detail=f"DART에서 종목을 찾을 수 없습니다: {ticker}")

    ticker = resolved_ticker or ticker
    current_year = datetime.date.today().year

    # 병렬로 DART 데이터 수집
    import asyncio
    company_info, shareholders, disclosures, financials = await asyncio.gather(
        get_company_info(corp_code),
        get_major_shareholders(corp_code, current_year - 1),
        get_recent_disclosures(corp_code, count=5),
        fetch_yearly_financials(corp_code, ticker, years=8),
        return_exceptions=True,
    )

    # 예외 처리
    if isinstance(company_info, Exception):
        company_info = {}
    if isinstance(shareholders, Exception):
        shareholders = {}
    if isinstance(disclosures, Exception):
        disclosures = {}
    if isinstance(financials, Exception):
        financials = []

    # 기존 스코어 캐시에서 로드 (없으면 None)
    score = get_score_cached(ticker)

    # 기업명 추출
    name = company_info.get("corp_name") or ticker
    if score and score.get("name"):
        name = score.get("name")

    # AI 분석 생성
    result = await generate_qualitative_analysis(
        ticker=ticker,
        name=name,
        financials=financials if isinstance(financials, list) else [],
        company_info=company_info if isinstance(company_info, dict) else {},
        shareholders=shareholders if isinstance(shareholders, dict) else {},
        disclosures=disclosures if isinstance(disclosures, dict) else {},
        score=score,
    )

    # 추가 메타데이터
    result["dart_company_info"] = {
        "ceo": company_info.get("ceo_nm") if isinstance(company_info, dict) else None,
        "industry": company_info.get("induty_code") if isinstance(company_info, dict) else None,
        "est_date": company_info.get("est_dt") if isinstance(company_info, dict) else None,
        "employees": company_info.get("empl_no") if isinstance(company_info, dict) else None,
        "homepage": company_info.get("hm_url") if isinstance(company_info, dict) else None,
        "address": company_info.get("adres") if isinstance(company_info, dict) else None,
    }

    # 주요주주 리스트 (상위 5명)
    shareholder_list = []
    if isinstance(shareholders, dict):
        seen_names = set()
        for item in shareholders.get("list", [])[:10]:
            name = (item.get("nm") or item.get("shreholder_nm") or "").strip()
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            shareholder_list.append({
                "name": name,
                "relation": (item.get("relate") or item.get("spcfmtt_rltn") or "").strip(),
                "shares": (item.get("trmend_posesn_stock_co") or item.get("bsis_posesn_stock_co") or "").strip(),
                "ratio": (item.get("trmend_posesn_stock_qota_rt") or item.get("bsis_posesn_stock_qota_rt") or "").strip(),
            })
            if len(shareholder_list) >= 5:
                break
    result["shareholders"] = shareholder_list

    # 최근 공시 리스트
    disclosure_list = []
    if isinstance(disclosures, dict):
        for item in disclosures.get("list", [])[:5]:
            disclosure_list.append({
                "date": item.get("rcept_dt", ""),
                "title": item.get("report_nm", ""),
                "company": item.get("corp_name", ""),
            })
    result["recent_disclosures"] = disclosure_list

    return result
