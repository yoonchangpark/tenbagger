"""
v2 대시보드 API - DART 공시 목록 조회 + AI 공시 분석
"""
from fastapi import APIRouter, Query
from pydantic import BaseModel
from typing import Optional
from app.infra.clients.dart_client import _get
from app.core.config import settings
from app.core.database import SessionLocal
from sqlalchemy import text
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


# ── 공시 AI 분석 ────────────────────────────────────────────────────────────

class DisclosureAnalyzeRequest(BaseModel):
    rcept_no: str
    corp_name: str
    report_nm: str
    ticker: Optional[str] = None
    rcept_dt: Optional[str] = None


@router.post("/disclosures/analyze")
async def analyze_disclosure(body: DisclosureAnalyzeRequest):
    """
    공시 제목 + 회사 스코어 데이터를 기반으로 GPT-4o가 투자 임팩트를 즉시 분석.
    """
    if not settings.openai_api_key:
        return {"error": "OpenAI API 키가 설정되지 않았습니다."}

    # DB에서 회사 스코어 조회 (ticker 있을 때)
    score_context = ""
    if body.ticker:
        try:
            with SessionLocal() as session:
                row = session.execute(text("""
                    SELECT grade, total_score, growth_score, stability_score,
                           cashflow_score, revenue_cagr_5y, avg_roe_5y,
                           avg_fcf_margin, per, pbr
                    FROM scores WHERE ticker = :t LIMIT 1
                """), {"t": body.ticker}).fetchone()
            if row:
                score_context = f"""
회사 텐배거 시스템 평가:
- 등급: {row.grade} | 총점: {row.total_score}/10
- 성장성: {row.growth_score} | 안정성: {row.stability_score} | 현금흐름: {row.cashflow_score}
- 매출CAGR(5y): {row.revenue_cagr_5y}% | ROE: {row.avg_roe_5y}% | FCF마진: {row.avg_fcf_margin}%
- PER: {row.per} | PBR: {row.pbr}"""
        except Exception:
            pass

    date_str = ""
    if body.rcept_dt and len(body.rcept_dt) == 8:
        date_str = f"{body.rcept_dt[:4]}년 {body.rcept_dt[4:6]}월 {body.rcept_dt[6:]}일"

    prompt = f"""다음 DART 공시를 장기투자 관점에서 분석해주세요.

공시 정보:
- 회사명: {body.corp_name}
- 공시 제목: {body.report_nm}
- 공시 날짜: {date_str or '미상'}
- DART 접수번호: {body.rcept_no}
{score_context}

아래 JSON 형식으로만 응답하세요:
{{
  "summary": "이 공시가 무엇인지 2문장으로 설명 (공시 종류와 의미)",
  "impact": "POSITIVE 또는 NEUTRAL 또는 NEGATIVE 또는 CAUTION 중 하나",
  "impact_reason": "투자 임팩트 판단 근거 1~2문장",
  "key_points": ["핵심 포인트 1", "핵심 포인트 2", "핵심 포인트 3"],
  "action": "장기투자자 관점에서 취할 행동 1문장",
  "confidence": "HIGH 또는 MEDIUM 또는 LOW"
}}

impact 판단 기준:
- POSITIVE: 실적 호전, 자사주 매입, 배당 증가, 신사업 진출 등 장기 가치 상승 신호
- NEGATIVE: 실적 악화, 횡령, 소송, 대규모 손실, 유상증자 등 가치 훼손 신호
- CAUTION: 임원 변경, 대규모 투자, 합병 등 중립이나 모니터링 필요
- NEUTRAL: 정기 보고서, 소액 공시 등 통상적인 공시
confidence: 공시 제목만으로 판단 가능하면 HIGH, 내용 확인 필요하면 LOW"""

    try:
        from openai import AsyncOpenAI
        import json
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        resp = await client.chat.completions.create(
            model="gpt-4o",
            max_tokens=600,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "당신은 한국 주식 DART 공시 전문 분석가입니다. 공시 제목과 맥락을 보고 장기투자자에게 의미 있는 인사이트를 제공합니다. 반드시 JSON 형식으로만 응답하세요."},
                {"role": "user", "content": prompt},
            ],
        )
        result = json.loads(resp.choices[0].message.content)
        result["corp_name"] = body.corp_name
        result["report_nm"] = body.report_nm
        result["rcept_no"] = body.rcept_no

        impact_emoji = {"POSITIVE": "✅", "NEGATIVE": "🔴", "CAUTION": "⚠️", "NEUTRAL": "ℹ️"}
        result["impact_emoji"] = impact_emoji.get(result.get("impact", "NEUTRAL"), "ℹ️")

        return result
    except Exception as e:
        return {"error": f"분석 실패: {e}"}
