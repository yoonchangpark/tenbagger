"""
v2 대시보드 API - DART 공시 목록 조회 + AI 공시 분석 + 분기 비교 분석
"""
from fastapi import APIRouter, Query, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
from app.infra.clients.dart_client import _get
from app.core.config import settings
from app.core.database import SessionLocal
from sqlalchemy import text
import datetime
import json

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
    corp_code: Optional[str] = None
    rcept_dt: Optional[str] = None


def _classify_disclosure(report_nm: str) -> str:
    """공시 제목 키워드 → 유형별 분석 힌트 반환"""
    nm = report_nm.replace(" ", "")
    if any(k in nm for k in ["유상증자", "주식발행"]):
        return "유상증자 공시입니다. 발행 규모(기존 주식 대비 비율), 발행 목적(운영자금/시설투자/부채상환), 발행가 할인율을 중점 분석하세요. 희석 효과와 목적의 질이 핵심입니다."
    if any(k in nm for k in ["자기주식취득", "자사주매입"]):
        return "자사주 매입 공시입니다. 매입 규모(시가총액 대비 %), 재원, 취득 후 소각 여부를 분석하세요. 주주환원 신호의 강도가 핵심입니다."
    if any(k in nm for k in ["전환사채", "신주인수권부사채", "CB발행", "BW발행"]):
        return "CB/BW 발행 공시입니다. 발행 규모, 전환가격(현재 주가 대비 할인율), 만기, 발행 대상(기관/특수관계인)을 분석하세요. 잠재적 희석 리스크가 핵심입니다."
    if any(k in nm for k in ["합병", "분할", "인수", "양수도"]):
        return "M&A/구조조정 공시입니다. 거래 금액, 합병 비율, 피인수 기업의 재무 상태, 시너지 가능성을 분석하세요."
    if any(k in nm for k in ["횡령", "배임", "사기"]):
        return "횡령·배임 공시입니다. 피해 금액, 당사자(임원/직원), 회사 전체 자산 대비 비율, 경영진 신뢰도 훼손 가능성을 중점 분석하세요."
    if any(k in nm for k in ["소송", "분쟁", "제소"]):
        return "소송·분쟁 공시입니다. 청구 금액, 회사 자기자본 대비 비율, 승소 가능성 추정 근거, 최종 판결 시 재무 영향을 분석하세요."
    if any(k in nm for k in ["사업보고서", "분기보고서", "반기보고서"]):
        return "정기 보고서입니다. 실적 변화(매출·영업이익·순이익), 부채비율 추이, 배당 정책 변화, 경영진 코멘트의 톤을 분석하세요."
    if any(k in nm for k in ["임원변경", "대표이사", "이사선임", "이사해임"]):
        return "임원 변경 공시입니다. 변경 임원의 경력, 전 직책, 변경 시기(실적 발표 전후), 내부 승진 vs 외부 영입 여부를 분석하세요."
    if any(k in nm for k in ["배당", "현금배당", "주식배당"]):
        return "배당 공시입니다. 배당 금액, 배당수익률(현재 주가 대비), 전년 대비 증감률, 배당성향을 분석하세요."
    if any(k in nm for k in ["대규모내부거래", "특수관계인"]):
        return "대규모 내부거래 공시입니다. 거래 상대방(계열사명), 거래 금액, 거래 조건이 시장 가격과 공정한지, 소액주주 이익 침해 가능성을 분석하세요."
    return "공시 원문을 바탕으로 장기투자 관점에서 투자 임팩트를 분석하세요."


@router.post("/disclosures/analyze")
async def analyze_disclosure(body: DisclosureAnalyzeRequest):
    """
    DART 공시 원문(document.json) + 회사 스코어 기반 GPT-4o 심층 분석.
    원문 접근 불가 시 bzSummary + 재무지표로 보완.
    """
    if not settings.openai_api_key:
        return {"error": "OpenAI API 키가 설정되지 않았습니다."}

    from app.infra.clients.dart_client import fetch_document_text, get_corp_code
    import asyncio as _asyncio

    # 1. 원문 텍스트 병렬 수집 시작 (30,000자 전문)
    doc_text_task = _asyncio.create_task(fetch_document_text(body.rcept_no, max_chars=30000))

    # 2. corp_code 확보
    corp_code = body.corp_code
    if not corp_code:
        query = body.ticker or body.corp_name
        corp_code, _ = await get_corp_code(query)

    # 3. DB 스코어 조회
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
                score_context = (
                    f"\n[텐배거 시스템 평가]\n"
                    f"등급: {row.grade} | 총점: {row.total_score}/10\n"
                    f"성장성: {row.growth_score} | 안정성: {row.stability_score} | 현금흐름: {row.cashflow_score}\n"
                    f"매출CAGR(5y): {row.revenue_cagr_5y}% | ROE: {row.avg_roe_5y}% | FCF마진: {row.avg_fcf_margin}%\n"
                    f"PER: {row.per} | PBR: {row.pbr}"
                )
        except Exception:
            pass

    # 4. 원문 결과 대기
    doc_text = await doc_text_task

    # 5. 원문 없으면 bzSummary + 재무지표로 보완
    fallback_context = ""
    if not doc_text and corp_code:
        try:
            from app.agents.dart_report_parser import fetch_bz_summary, fetch_financial_indices
            import datetime as _dt
            year = str(_dt.date.today().year - 1)
            bz, indices = await _asyncio.gather(
                fetch_bz_summary(corp_code, year, "11011"),
                fetch_financial_indices(corp_code, year, "11011"),
                return_exceptions=True,
            )
            if isinstance(bz, str) and bz:
                fallback_context += f"\n[사업 개요]\n{bz[:2000]}"
            if isinstance(indices, dict) and indices:
                lines = [f"  {k}: {v}%" for k, v in list(indices.items())[:8]]
                fallback_context += f"\n[주요 재무지표]\n" + "\n".join(lines)
        except Exception:
            pass

    date_str = ""
    if body.rcept_dt and len(body.rcept_dt) == 8:
        date_str = f"{body.rcept_dt[:4]}년 {body.rcept_dt[4:6]}월 {body.rcept_dt[6:]}일"

    disclosure_hint = _classify_disclosure(body.report_nm)
    has_doc = bool(doc_text)

    if has_doc:
        content_section = f"\n[공시 원문 전문 — {len(doc_text):,}자]\n{doc_text}"
        analysis_instruction = (
            f"위 공시 원문 전문을 직접 읽고 분석하세요. {disclosure_hint}\n"
            "원문에 등장하는 구체적 수치(금액·주식수·비율·날짜·가격 등)를 반드시 key_points에 인용하세요."
        )
        confidence_note = "HIGH"
    else:
        content_section = fallback_context or "\n(원문 데이터 없음 — 제목 및 회사 프로필 기반 분석)"
        analysis_instruction = f"공시 제목과 회사 프로필을 기반으로 분석하세요. {disclosure_hint}"
        confidence_note = "제목만으로 판단 가능하면 MEDIUM, 내용 확인 필요하면 LOW"

    prompt = f"""다음 DART 공시를 장기투자 관점에서 심층 분석해주세요.

[공시 기본 정보]
- 회사명: {body.corp_name}
- 공시 제목: {body.report_nm}
- 공시 날짜: {date_str or '미상'}
- 접수번호: {body.rcept_no}
{score_context}
{content_section}

[분석 지시]
{analysis_instruction}

아래 JSON 형식으로만 응답하세요:
{{
  "summary": "공시의 핵심 내용 3~4문장. 원문에서 추출한 구체적 수치(금액, 주식수, 비율, 날짜, 가격)를 반드시 포함할 것.",
  "impact": "POSITIVE 또는 NEUTRAL 또는 NEGATIVE 또는 CAUTION 중 하나",
  "impact_reason": "투자 임팩트 판단 근거 1~2문장. 원문 수치 근거 필수.",
  "key_points": [
    "수치가 포함된 핵심 포인트 1",
    "수치가 포함된 핵심 포인트 2",
    "수치가 포함된 핵심 포인트 3",
    "투자자가 취해야 할 구체적 행동 또는 모니터링 포인트"
  ],
  "action": "장기투자자 관점에서 지금 당장 취할 행동 1문장 (보유/매수/매도/관망 + 이유)",
  "confidence": "{confidence_note}"
}}

impact 기준:
- POSITIVE: 실적 호전, 자사주 매입, 배당 증가, 신사업 성과 등 장기 가치 상승
- NEGATIVE: 실적 악화, 횡령, 소송, 대규모 손실, 희석성 유상증자
- CAUTION: 합병, 대규모 투자, 임원 변경, 공개매수 등 모니터링 필요
- NEUTRAL: 정기 보고서, 소액 공시 등 통상 공시"""

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        resp = await client.chat.completions.create(
            model="gpt-4o",
            max_tokens=1200,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "당신은 한국 주식 DART 공시 전문 분석가입니다. 공시 원문을 직접 읽고 구체적인 수치와 함께 장기투자자에게 의미 있는 인사이트를 제공합니다. 반드시 JSON 형식으로만 응답하세요."},
                {"role": "user", "content": prompt},
            ],
        )
        result = json.loads(resp.choices[0].message.content)
        result["corp_name"] = body.corp_name
        result["report_nm"] = body.report_nm
        result["rcept_no"] = body.rcept_no
        result["has_doc"] = has_doc  # 원문 기반 여부 프론트에 전달

        impact_emoji = {"POSITIVE": "✅", "NEGATIVE": "🔴", "CAUTION": "⚠️", "NEUTRAL": "ℹ️"}
        result["impact_emoji"] = impact_emoji.get(result.get("impact", "NEUTRAL"), "ℹ️")

        return result
    except Exception as e:
        return {"error": f"분석 실패: {e}"}


# ── 분기 비교 분석 ──────────────────────────────────────────────────────────

def _save_compare_cache(result: dict):
    with SessionLocal() as session:
        session.execute(text("""
            INSERT INTO quarter_compare_cache
                (ticker, corp_name, prev_label, curr_label, prev_indices, curr_indices, analysis_md, updated_at)
            VALUES
                (:ticker, :corp_name, :prev_label, :curr_label,
                 :prev_indices::jsonb, :curr_indices::jsonb, :analysis_md, NOW())
            ON CONFLICT (ticker) DO UPDATE SET
                corp_name   = EXCLUDED.corp_name,
                prev_label  = EXCLUDED.prev_label,
                curr_label  = EXCLUDED.curr_label,
                prev_indices = EXCLUDED.prev_indices,
                curr_indices = EXCLUDED.curr_indices,
                analysis_md = EXCLUDED.analysis_md,
                updated_at  = NOW()
        """), {
            "ticker": result["ticker"],
            "corp_name": result["corp_name"],
            "prev_label": result["prev_label"],
            "curr_label": result["curr_label"],
            "prev_indices": json.dumps(result["prev_indices"], ensure_ascii=False),
            "curr_indices": json.dumps(result["curr_indices"], ensure_ascii=False),
            "analysis_md": result["analysis_md"],
        })
        session.commit()


def _load_compare_cache(ticker: str) -> Optional[dict]:
    with SessionLocal() as session:
        row = session.execute(text("""
            SELECT ticker, corp_name, prev_label, curr_label,
                   prev_indices, curr_indices, analysis_md, updated_at
            FROM quarter_compare_cache WHERE ticker = :t
        """), {"t": ticker}).fetchone()
    if not row:
        return None
    return {
        "ticker": row.ticker,
        "corp_name": row.corp_name,
        "prev_label": row.prev_label,
        "curr_label": row.curr_label,
        "prev_indices": row.prev_indices or {},
        "curr_indices": row.curr_indices or {},
        "analysis_md": row.analysis_md,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "cached": True,
    }


@router.get("/company/{ticker}/quarter-compare")
async def get_quarter_compare(
    ticker: str,
    refresh: bool = Query(False, description="True이면 캐시 무시 후 재분석"),
    background_tasks: BackgroundTasks = None,
):
    """
    분기 비교 분석 조회.
    - 캐시 있으면 즉시 반환
    - 캐시 없거나 refresh=True이면 DART + GPT 실시간 분석 (10~20초)
    """
    if not refresh:
        cached = _load_compare_cache(ticker)
        if cached:
            return cached

    try:
        from app.agents.dart_report_parser import fetch_quarter_compare
        result = await fetch_quarter_compare(ticker)
        _save_compare_cache(result)
        result["cached"] = False
        result["updated_at"] = datetime.datetime.utcnow().isoformat()
        return result
    except Exception as e:
        return {"error": str(e), "ticker": ticker}


async def _precache_one(ticker: str):
    try:
        from app.agents.dart_report_parser import fetch_quarter_compare
        result = await fetch_quarter_compare(ticker)
        _save_compare_cache(result)
        print(f"[QC] {ticker} 캐시 완료")
    except Exception as e:
        print(f"[QC] {ticker} 실패: {e}")


@router.post("/admin/quarter-compare/precache")
async def precache_quarter_compare(background_tasks: BackgroundTasks):
    """
    관심종목 + TENBAGGER 전종목 분기비교 사전 캐시 (백그라운드 실행).
    """
    tickers = []
    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT DISTINCT ticker FROM (
                SELECT ticker FROM watchlist
                UNION
                SELECT ticker FROM scores WHERE grade = 'TENBAGGER'
            ) t
        """)).fetchall()
        tickers = [r[0] for r in rows]

    async def run_all():
        import asyncio
        for ticker in tickers:
            await _precache_one(ticker)
            await asyncio.sleep(2)  # DART API 레이트 리밋 배려

    background_tasks.add_task(run_all)
    return {"message": f"{len(tickers)}개 종목 백그라운드 캐시 시작", "tickers": tickers}
