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
from app.core.database import SessionLocal
from sqlalchemy import text

router = APIRouter(prefix="/api/v2", tags=["v2-qualitative"])

# KSIC 코드 앞 2~3자 → (업종명, scores.name 검색 키워드)
_KSIC_SECTOR_MAP: list = [
    ("C20", "화학·소재",     ["화학", "케미칼", "소재"]),
    ("C21", "제약·바이오",   ["제약", "바이오", "의약", "헬스케어"]),
    ("C26", "반도체·전자",   ["반도체", "전자", "디스플레이"]),
    ("C27", "2차전지·전기",  ["배터리", "2차전지", "전기"]),
    ("C28", "기계·장비",     ["기계", "장비", "설비"]),
    ("C29", "자동차·부품",   ["자동차", "부품"]),
    ("C30", "조선·항공",     ["조선", "항공", "선박"]),
    ("G4",  "유통·소매",     ["마트", "백화점", "유통"]),
    ("J",   "IT·소프트웨어", ["소프트웨어", "게임", "인터넷"]),
    ("K",   "금융·보험",     ["은행", "금융", "증권", "보험"]),
    ("F",   "건설·부동산",   ["건설", "건축"]),
    ("H",   "물류·운송",     ["운송", "물류", "택배"]),
]


def _get_sector_peer_valuation(induty_code: str) -> dict:
    """KSIC 업종코드 기반 동종업종 PER 분포 (scores.name ILIKE 검색)"""
    if not induty_code:
        return {}

    sector_name, keywords = None, []
    for prefix, sname, kws in _KSIC_SECTOR_MAP:
        if induty_code.startswith(prefix):
            sector_name, keywords = sname, kws
            break

    if not keywords:
        return {}

    try:
        with SessionLocal() as db:
            # 키워드는 하드코딩된 상수라 SQL injection 위험 없음
            cond = " OR ".join(f"name ILIKE '%{k}%'" for k in keywords)
            row = db.execute(text(f"""
                SELECT
                    COUNT(*)                                                              AS peer_count,
                    ROUND(AVG(per)::numeric, 1)                                           AS avg_per,
                    ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY per)::numeric, 1) AS per_q25,
                    ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY per)::numeric, 1) AS per_q75
                FROM scores
                WHERE ({cond})
                  AND per > 0 AND per < 200
                  AND analyzed_at > NOW() - INTERVAL '60 days'
            """)).fetchone()
            if row and row.avg_per and int(row.peer_count) >= 3:
                return {
                    "sector_name": sector_name,
                    "peer_count": int(row.peer_count),
                    "avg_per": float(row.avg_per),
                    "per_q25": float(row.per_q25) if row.per_q25 else None,
                    "per_q75": float(row.per_q75) if row.per_q75 else None,
                }
    except Exception as e:
        print(f"[QUALITATIVE] 섹터 PER 조회 실패: {e}")
    return {}


def _compute_dcf(financials: list, score: dict) -> dict:
    """단순 DCF 내재가치 계산 — 시가총액 기준 상승여력 추산"""
    if not financials or not score:
        return {}

    sorted_fins = sorted(financials, key=lambda x: x.get("year", 0))
    recent = sorted_fins[-3:]

    fcfs = [f.get("fcf") or f.get("free_cash_flow") for f in recent]
    fcfs = [f for f in fcfs if f is not None and f > 0]
    if not fcfs:
        return {"error": "양의 FCF 데이터 없음 — DCF 계산 불가"}

    fcf_base = sum(fcfs) / len(fcfs)

    grade = score.get("grade", "WATCHLIST")
    stability = score.get("stability_score") or 5.0
    wacc = {"TENBAGGER": 0.09, "COMPOUNDER": 0.10, "WATCHLIST": 0.12, "AVOID": 0.14}.get(grade, 0.11)
    if stability < 4.0:
        wacc += 0.02  # 재무 안정성 낮으면 리스크 프리미엄 가산

    rev_cagr = score.get("revenue_cagr_5y") or 0
    growth_rate = min(max(rev_cagr / 100, 0.02), 0.20)  # 2%~20% 캡
    terminal_g = 0.03

    if wacc <= terminal_g:
        return {"error": "WACC ≤ 영구성장률 — DCF 계산 불가"}

    pv = 0.0
    fcf_t = fcf_base
    for t in range(1, 6):
        fcf_t *= (1 + growth_rate)
        pv += fcf_t / (1 + wacc) ** t

    terminal_fcf = fcf_t * (1 + terminal_g)
    pv += (terminal_fcf / (wacc - terminal_g)) / (1 + wacc) ** 5

    dcf_eok = pv / 1e8
    market_cap = score.get("market_cap")

    res = {
        "fcf_base_eok": round(fcf_base / 1e8, 1),
        "growth_rate_pct": round(growth_rate * 100, 1),
        "wacc_pct": round(wacc * 100, 1),
        "terminal_g_pct": 3.0,
        "dcf_value_eok": round(dcf_eok, 0),
    }
    if market_cap and market_cap > 0:
        upside = (dcf_eok - market_cap) / market_cap * 100
        res["market_cap_eok"] = round(market_cap, 0)
        res["upside_pct"] = round(upside, 1)
        res["verdict"] = "저평가" if upside > 20 else "고평가" if upside < -20 else "적정"
    else:
        res["error"] = "시가총액 없어 비교 불가"
    return res


def _get_market_valuation(market: str) -> dict:
    """동일 시장(KOSPI/KOSDAQ) PER 분포 조회 — 밸류에이션 비교용"""
    try:
        with SessionLocal() as db:
            row = db.execute(text("""
                SELECT
                    ROUND(AVG(per)::numeric, 1)                              AS avg_per,
                    ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY per)::numeric, 1) AS per_q25,
                    ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY per)::numeric, 1) AS per_q75,
                    ROUND(AVG(pbr)::numeric, 2)                              AS avg_pbr
                FROM scores
                WHERE market = :market
                  AND per > 0 AND per < 200
                  AND analyzed_at > NOW() - INTERVAL '60 days'
            """), {"market": market}).fetchone()
            if row and row.avg_per:
                return {
                    "avg_per": float(row.avg_per),
                    "per_q25": float(row.per_q25) if row.per_q25 else None,
                    "per_q75": float(row.per_q75) if row.per_q75 else None,
                    "avg_pbr": float(row.avg_pbr) if row.avg_pbr else None,
                }
    except Exception as e:
        print(f"[QUALITATIVE] 시장 PER 조회 실패: {e}")
    return {}


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

    # 시장 평균 PER/PBR (밸류에이션 비교용)
    market = score.get("market", "KOSPI") if score else "KOSPI"
    market_valuation = _get_market_valuation(market)

    # 동종업종 PER 비교 (KSIC 코드 기반)
    induty_code = (company_info.get("induty_code") or "") if isinstance(company_info, dict) else ""
    sector_peer = _get_sector_peer_valuation(induty_code)

    # DCF 내재가치 계산 (Python 자체 계산)
    dcf_result = _compute_dcf(
        financials if isinstance(financials, list) else [],
        score or {},
    )

    # AI 분석 생성
    result = await generate_qualitative_analysis(
        ticker=ticker,
        name=name,
        financials=financials if isinstance(financials, list) else [],
        company_info=company_info if isinstance(company_info, dict) else {},
        shareholders=shareholders if isinstance(shareholders, dict) else {},
        disclosures=disclosures if isinstance(disclosures, dict) else {},
        score=score,
        market_valuation=market_valuation,
        sector_peer=sector_peer,
    )

    # 밸류에이션 메타 첨부
    if market_valuation:
        result["market_valuation_context"] = market_valuation
    if sector_peer:
        result["sector_peer_valuation"] = sector_peer
    if dcf_result:
        result["dcf_analysis"] = dcf_result

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
