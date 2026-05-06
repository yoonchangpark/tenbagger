"""
DART OpenAPI 클라이언트
공식 문서: https://opendart.fss.or.kr/guide/main.do
"""
import httpx
import asyncio
import datetime
from typing import Optional
from app.core.config import settings

DART_BASE = "https://opendart.fss.or.kr/api"

# ── 인메모리 캐시: DART 전체 종목 목록 (24시간 TTL) ──────────────────────────
_corp_cache: list = []
_corp_cache_at: Optional[datetime.datetime] = None
_corp_cache_lock = asyncio.Lock()


async def _get(path: str, params: dict) -> dict:
    params["crtfc_key"] = settings.dart_api_key
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{DART_BASE}/{path}", params=params)
        r.raise_for_status()
        return r.json()


async def _load_corp_cache() -> list:
    """DART corpCode.xml 다운로드 후 파싱 (메모리 캐싱, 24hr TTL)"""
    global _corp_cache, _corp_cache_at
    now = datetime.datetime.utcnow()

    async with _corp_cache_lock:
        if _corp_cache and _corp_cache_at and (now - _corp_cache_at).total_seconds() < 86400:
            return _corp_cache

        import zipfile, io, xml.etree.ElementTree as ET
        async with httpx.AsyncClient(timeout=30.0) as client:
            params = {"crtfc_key": settings.dart_api_key}
            r = await client.get(f"{DART_BASE}/corpCode.xml", params=params)
            r.raise_for_status()

        zf = zipfile.ZipFile(io.BytesIO(r.content))
        xml_data = zf.read("CORPCODE.xml")
        root = ET.fromstring(xml_data)

        items = []
        for item in root.findall("list"):
            code = item.findtext("stock_code", "").strip()
            if not code:
                continue
            items.append({
                "corp_code": item.findtext("corp_code", "").strip(),
                "stock_code": code,
                "corp_name": item.findtext("corp_name", "").strip(),
            })

        _corp_cache = items
        _corp_cache_at = now
        print(f"[DART] 종목 캐시 로드 완료: {len(items)}개")
        return _corp_cache


async def get_corp_code(query: str) -> tuple:
    """종목코드 또는 기업명 → DART 고유번호(corp_code), 종목코드(stock_code) 변환"""
    items = await _load_corp_cache()
    query_clean = query.replace(" ", "")

    # 1. Exact match
    for item in items:
        code = item["stock_code"]
        name = item["corp_name"].replace(" ", "")
        if query == code or query_clean == name:
            return item["corp_code"], code

    # 2. Substring match
    for item in items:
        code = item["stock_code"]
        name = item["corp_name"].replace(" ", "")
        if query_clean in name or name in query_clean:
            return item["corp_code"], code

    # 3. Prefix fuzzy (앞 4글자)
    if len(query_clean) >= 4:
        prefix = query_clean[:4]
        for item in items:
            name = item["corp_name"].replace(" ", "")
            if prefix in name:
                return item["corp_code"], item["stock_code"]

    return None, None


async def get_financial_statements(corp_code: str, year: int, report_type: str = "11011") -> dict:
    """단일 재무제표 조회 (연결 우선, 없으면 별도)"""
    params = {
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": report_type,
        "fs_div": "CFS",
    }
    data = await _get("fnlttSinglAcntAll.json", params)

    if data.get("status") != "000" or not data.get("list"):
        params["fs_div"] = "OFS"
        data = await _get("fnlttSinglAcntAll.json", params)

    return data


async def get_company_info(corp_code: str) -> dict:
    """기업 기본정보 조회 (업종, 대표이사, 설립일, 임직원수, 홈페이지)"""
    try:
        return await _get("company.json", {"corp_code": corp_code})
    except Exception as e:
        print(f"[DART] company.json 조회 실패: {e}")
        return {}


async def get_major_shareholders(corp_code: str, year: int) -> dict:
    """최대주주 및 특수관계인 주식소유 현황"""
    try:
        return await _get("majorstock.json", {
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": "11011",
        })
    except Exception as e:
        print(f"[DART] majorstock.json 조회 실패: {e}")
        return {}


async def get_recent_disclosures(corp_code: str, count: int = 5) -> dict:
    """최근 공시 목록 (정기공시: 사업보고서, 분기보고서 등)"""
    try:
        return await _get("list.json", {
            "corp_code": corp_code,
            "pblntf_ty": "A",
            "last_reprt_at": "N",
            "page_count": str(count),
        })
    except Exception as e:
        print(f"[DART] list.json 조회 실패: {e}")
        return {}


async def get_financial_index(corp_code: str, year: int, idx_cl_code: str = None) -> dict:
    """
    기업 주요 재무지표 조회 (DART fnlttCmpnyIndx.json)
    idx_cl_code:
      M210000 = 수익성지표 (ROE, ROA, 영업이익률, 순이익률)
      M220000 = 안정성지표 (부채비율, 유동비율, 당좌비율)
      M230000 = 성장성지표 (매출액증가율, 영업이익증가율, 순이익증가율)
      M240000 = 활동성지표 (총자산회전율, 매출채권회전율)
      None    = 전체 (4개 카테고리 모두)
    """
    params = {
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": "11011",
    }
    if idx_cl_code:
        params["idx_cl_code"] = idx_cl_code

    try:
        return await _get("fnlttCmpnyIndx.json", params)
    except Exception as e:
        print(f"[DART] fnlttCmpnyIndx.json 조회 실패 ({corp_code}, {year}): {e}")
        return {"status": "error", "list": []}


async def get_all_financial_indices(corp_code: str, year: int) -> dict:
    """
    4개 카테고리 재무지표 병렬 수집 후 단일 리스트로 합산 반환
    returns: {"list": [...모든 지표...], "year": year}
    """
    codes = ["M210000", "M220000", "M230000", "M240000"]
    results = await asyncio.gather(
        *[get_financial_index(corp_code, year, code) for code in codes],
        return_exceptions=True
    )
    merged = []
    for r in results:
        if isinstance(r, dict) and r.get("list"):
            merged.extend(r["list"])
    return {"list": merged, "year": year}


def parse_idx(items: list, keyword: str) -> Optional[float]:
    """재무지표 리스트에서 키워드로 값 추출"""
    for it in items:
        if it.get("idx_nm", "").replace(" ", "").find(keyword.replace(" ", "")) >= 0:
            val = it.get("idx_val", "")
            if val not in ("", None, "-"):
                try:
                    return float(val)
                except (ValueError, TypeError):
                    continue
    return None


async def get_dividend_info(corp_code: str, year: int) -> dict:
    """배당 정보 조회"""
    params = {
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": "11011",
    }
    return await _get("alotMatter.json", params)


def parse_amount(value_str: str) -> Optional[int]:
    """DART 금액 문자열 → 정수 (단위: 원)"""
    if not value_str or value_str.strip() in ("", "-", "N/A"):
        return None
    try:
        cleaned = value_str.replace(",", "").strip()
        return int(float(cleaned))
    except (ValueError, TypeError):
        return None


def extract_account(items: list, account_names: list) -> Optional[int]:
    """재무제표 항목 리스트에서 특정 계정 추출"""
    for name in account_names:
        for item in items:
            if item.get("account_nm", "").strip() == name:
                val = parse_amount(item.get("thstrm_amount", ""))
                if val is not None:
                    return val
    return None


async def fetch_yearly_financials(corp_code: str, ticker: str, years: int = 8, end_year: int = None) -> list:
    """최근 N년치 재무데이터 수집. end_year 지정 시 해당 연도 기준으로 과거 데이터 수집 (백테스트용)"""
    import datetime
    if end_year is None:
        end_year = datetime.date.today().year - 1
    results = []

    for year in range(end_year, end_year - years, -1):
        try:
            data = await get_financial_statements(corp_code, year)
            items = data.get("list", [])
            if not items:
                continue

            row = {"year": year, "ticker": ticker}

            # 손익계산서
            row["revenue"] = extract_account(items, ["매출액", "수익(매출액)", "영업수익", "매출"])
            row["operating_profit"] = extract_account(items, ["영업이익", "영업이익(손실)"])
            row["net_income"] = extract_account(items, ["당기순이익", "당기순이익(손실)", "분기순이익"])

            # 재무상태표
            row["total_assets"]   = extract_account(items, ["자산총계"])
            row["total_equity"]   = extract_account(items, ["자본총계"])
            row["total_debt"]     = extract_account(items, ["부채총계"])
            row["cash"]           = extract_account(items, ["현금및현금성자산"])
            row["current_assets"] = extract_account(items, ["유동자산"])
            row["current_liab"]   = extract_account(items, ["유동부채"])

            # 현금흐름표
            row["cfo"]   = extract_account(items, ["영업활동 현금흐름", "영업활동으로 인한 현금흐름"])
            row["capex"] = extract_account(items, ["유형자산의 취득", "유형자산 취득", "유형자산취득"])
            if row.get("cfo") and row.get("capex"):
                row["fcf"] = row["cfo"] - abs(row["capex"])

            results.append(row)
            await asyncio.sleep(0.3)

        except Exception as e:
            print(f"[DART] {ticker} {year}년 수집 실패: {e}")
            continue

    return results
