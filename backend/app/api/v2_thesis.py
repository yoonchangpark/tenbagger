"""
GET /api/v2/company/{ticker}/thesis-checklist — 텐배거 내러티브 5고리 체크리스트

정의: agent_prompts/narrative_thesis_structure.md
숫자 항목만 판정하고, 서술 항목(리서치 근거 필요)은 '미확인'으로 돌려준다. GPT 호출 없음.
"""
import time
from fastapi import APIRouter, HTTPException

from app.domain.thesis_checklist import evaluate, fetch_thesis_financials
from app.infra.clients.dart_client import get_corp_code

router = APIRouter(prefix="/api/v2/company", tags=["thesis"])

# 사업보고서는 1년에 한 번 바뀌므로 하루 캐시로 DART 쿼터를 아낀다
_CACHE: dict[str, tuple[float, dict]] = {}
_TTL = 86400


@router.get("/{ticker}/thesis-checklist")
async def thesis_checklist(ticker: str):
    hit = _CACHE.get(ticker)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]

    corp_code, resolved = await get_corp_code(ticker)
    if not corp_code:
        raise HTTPException(404, "DART에서 종목을 찾을 수 없습니다.")

    rows, report_year = await fetch_thesis_financials(corp_code)
    result = evaluate(rows)
    result.update({"ticker": resolved, "report_year": report_year,
                   "definition": "agent_prompts/narrative_thesis_structure.md"})
    if result.get("available"):
        _CACHE[ticker] = (time.time(), result)
    return result
