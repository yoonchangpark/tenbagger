"""
v2 대시보드 API - DART 공시 목록 조회
"""
from fastapi import APIRouter, Query
from app.infra.clients.dart_client import _get
import datetime

router = APIRouter(prefix="/api/v2", tags=["dashboard"])

@router.get("/disclosures")
async def get_disclosures(
    corp_cls: str = Query("", description="Y=KOSPI, K=KOSDAQ, 빈값=전체"),
    bgn_de: str = Query(None, description="시작일 YYYYMMDD"),
    end_de: str = Query(None, description="종료일 YYYYMMDD"),
    page_count: int = Query(40, le=100),
):
    today = datetime.date.today()
    if end_de is None:
        end_de = today.strftime("%Y%m%d")
    if bgn_de is None:
        bgn_de = (today - datetime.timedelta(days=7)).strftime("%Y%m%d")

    params = {
        "bgn_de": bgn_de,
        "end_de": end_de,
        "page_count": str(page_count),
        "sort": "date",
        "sort_mth": "desc",
    }
    if corp_cls:
        params["corp_cls"] = corp_cls

    try:
        result = await _get("list.json", params)
        return result
    except Exception as e:
        return {"status": "error", "message": str(e), "list": []}
