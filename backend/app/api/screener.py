"""
KOSPI/KOSDAQ 전체 종목 스크리너 API
GET /api/screener
"""
from fastapi import APIRouter, Query
from typing import Optional
from app.infra.repositories.company_repo import query_scores

router = APIRouter(prefix="/api/screener", tags=["Screener"])

# 섹터 키워드 매핑 (committee.py와 동일 로직)
_SECTOR_MAP = {
    "반도체":      ["반도체", "메모리", "파운드리", "DRAM", "낸드"],
    "2차전지":     ["배터리", "2차전지", "양극재", "음극재", "전해질", "셀"],
    "바이오":      ["바이오", "제약", "신약", "헬스케어", "진단", "치료"],
    "자동차":      ["자동차", "모빌리티", "EV", "완성차", "부품"],
    "금융":        ["은행", "증권", "보험", "금융", "캐피탈"],
    "IT·소프트웨어": ["소프트웨어", "IT", "플랫폼", "클라우드", "AI", "게임"],
    "철강·소재":   ["철강", "포스코", "화학", "소재", "비철"],
    "에너지":      ["에너지", "발전", "원전", "태양광", "풍력", "가스", "석유"],
    "유통·소비재": ["유통", "백화점", "마트", "식품", "음료", "화장품"],
    "통신":        ["통신", "5G", "텔레콤"],
    "조선·중공업": ["조선", "중공업", "해운", "선박"],
}


def _infer_sector(name: str) -> str:
    for sector, kws in _SECTOR_MAP.items():
        if any(kw in name for kw in kws):
            return sector
    return "기타"


def _growth_tag(r: dict) -> str:
    rcagr = r.get("revenue_cagr_5y") or 0
    gscore = r.get("growth_score") or 0
    if rcagr > 15 and gscore > 7.0:
        return "구조적성장"
    if rcagr < 5 and gscore < 6.0:
        return "사이클주"
    return ""


@router.get("")
def screen_companies(
    grade: Optional[str] = Query(None, description="등급 필터 (콤마 구분): TENBAGGER,COMPOUNDER"),
    min_score: Optional[float] = Query(None, description="최소 종합 점수 (0~10)"),
    min_growth: Optional[float] = Query(None, description="최소 성장성 점수 (0~10)"),
    min_dividend_yield: Optional[float] = Query(None, description="최소 배당수익률 (%)"),
    max_debt_ratio: Optional[float] = Query(None, description="최대 부채비율 (%)"),
    market: Optional[str] = Query(None, description="시장: KOSPI, KOSDAQ"),
    sort: str = Query("total_score", description="정렬 기준"),
    limit: int = Query(50, ge=1, le=200, description="결과 개수"),
):
    """
    DB에 저장된 스코어 중 조건에 맞는 종목 필터링 반환
    ETL 실행 후 데이터가 쌓여야 의미있는 결과가 나옵니다.
    각 종목에 sector(업종), growth_tag(구조적성장/사이클주) 필드가 포함됩니다.
    """
    grades = [g.strip() for g in grade.split(",")] if grade else None

    results = query_scores(
        grades=grades,
        min_score=min_score,
        min_growth=min_growth,
        min_dividend_yield=min_dividend_yield,
        max_debt_ratio=max_debt_ratio,
        market=market if market and market != "전체" else None,
        sort_by=sort,
        limit=limit,
    )

    for r in results:
        if r.get("analyzed_at"):
            r["analyzed_at"] = str(r["analyzed_at"])
        r["sector"] = _infer_sector(r.get("name", ""))
        r["growth_tag"] = _growth_tag(r)

    return {
        "total": len(results),
        "companies": results,
    }


@router.get("/summary")
def screener_summary():
    """등급별 통계 요약"""
    from sqlalchemy import text
    from app.core.database import SessionLocal

    sql = text("""
        SELECT grade, COUNT(*) as count,
               ROUND(AVG(total_score)::numeric, 2) as avg_score,
               ROUND(AVG(dividend_yield)::numeric, 2) as avg_div_yield
        FROM scores
        WHERE grade IS NOT NULL
        GROUP BY grade
        ORDER BY avg_score DESC
    """)
    with SessionLocal() as session:
        rows = session.execute(sql).fetchall()
        return {
            "grades": [
                {
                    "grade": r[0],
                    "count": r[1],
                    "avg_score": float(r[2]) if r[2] else None,
                    "avg_div_yield": float(r[3]) if r[3] else None,
                }
                for r in rows
            ],
            "total_analyzed": sum(r[1] for r in rows),
        }
