"""
Phase 6: AI 투자위원회 (멀티에이전트 합의 시스템)

4명의 전문가 에이전트가 독립 분석 → 투자전략가가 종합:
  1. financial_analyst    재무 건전성·성장성
  2. news_sentiment       뉴스 심리·모멘텀
  3. sector_researcher    업종 사이클·경쟁력
  4. investment_strategist 위 3개 + 종합 매수/관망/회피 판단

병렬 실행 (asyncio.gather), 한 종목 ~15초, GPT 비용 ~$0.04
"""
import asyncio
import json
import datetime
from typing import Any
from openai import AsyncOpenAI
from sqlalchemy import text

from app.core.config import settings
from app.core.database import SessionLocal


GPT_MODEL = "gpt-4o-mini"  # 비용 절약 (강의: gpt-4o로 업그레이드 가능)
GPT_TEMPERATURE = 0.3


# ── 에이전트 시스템 프롬프트 ─────────────────────────────────────────────

FINANCIAL_ANALYST_PROMPT = """당신은 한국 주식 재무분석 전문가입니다.
주어진 재무 데이터(매출/영업이익/순이익/FCF/ROE/부채비율/CAGR)와 종합 점수를 기반으로
이 회사의 재무 건전성과 성장 지속성을 평가합니다.

반드시 JSON 형식으로 응답:
{
  "verdict": "STRONG" | "SOLID" | "MIXED" | "WEAK",
  "score": 1.0 ~ 10.0,
  "key_strengths": ["강점1", "강점2"],
  "key_weaknesses": ["약점1", "약점2"],
  "rationale": "2-3 문장 종합 평가",
  "growth_outlook": "향후 3-5년 성장 전망 (한 문장)"
}

평가 기준:
- 매출/EPS CAGR 5년: 15%+ 강세, 5-15% 양호, 5% 미만 약세
- ROE: 15%+ 우수, 10-15% 양호, 10% 미만 미흡
- 부채비율: 100% 이하 양호, 200%+ 위험
- FCF 마진: 양수 안정, 변동성 큰지 체크"""


NEWS_SENTIMENT_AGENT_PROMPT = """당신은 한국 주식 뉴스 심리 분석 전문가입니다.
주어진 종목의 최근 14일 뉴스 감성 데이터(평균점수, 시그널, 키워드, 기사 제목들)를 분석하여
시장 심리와 단기 모멘텀을 판단합니다.

반드시 JSON 형식으로 응답:
{
  "verdict": "MOMENTUM_UP" | "POSITIVE" | "NEUTRAL" | "CAUTION" | "MOMENTUM_DOWN",
  "score": 1.0 ~ 10.0,
  "key_themes": ["테마1", "테마2"],
  "rationale": "2-3 문장 (어떤 뉴스가 주가에 우호적/비우호적인지)",
  "sentiment_trend": "improving" | "stable" | "deteriorating",
  "risk_signals": ["위험 신호 (있으면)"]
}

평가 기준:
- 평균점수 +0.4↑: 강한 긍정, +0.1~+0.4: 긍정, -0.1~+0.1: 중립
- 키워드에 '수주'/'실적'/'성장'/'신제품' → 긍정 모멘텀
- 키워드에 '리콜'/'소송'/'하락'/'적자' → 위험 신호
- 데이터 없으면 verdict='NEUTRAL', rationale에 '뉴스 데이터 부족' 명시"""


SECTOR_RESEARCHER_PROMPT = """당신은 한국 산업 분석 전문가입니다.
주어진 종목의 업종 정보, 업종 뉴스 감성, 동종 종목 평균 점수를 기반으로
이 종목이 속한 업종의 사이클과 종목의 경쟁 위치를 평가합니다.

반드시 JSON 형식으로 응답:
{
  "verdict": "TAILWIND" | "STABLE" | "HEADWIND" | "DECLINING",
  "score": 1.0 ~ 10.0,
  "sector_cycle": "expansion" | "peak" | "contraction" | "trough",
  "competitive_position": "leader" | "challenger" | "average" | "laggard",
  "rationale": "2-3 문장 (업종 사이클 + 종목 위치)",
  "tailwinds": ["호재1", "호재2"],
  "headwinds": ["악재1", "악재2"]
}

평가 기준:
- 업종 평균 감성 점수, 업종 키워드(예: 'AI인프라', 'K-배터리') 활용
- 동종 종목 평균 대비 이 종목 점수 우열로 경쟁 위치 판단"""


INVESTMENT_STRATEGIST_PROMPT = """당신은 한국 주식 투자전략가입니다. 3명의 전문가 의견(재무분석가, 뉴스감성가, 업종리서처)과
종목의 가격 모멘텀(PER/PBR/시가총액)을 종합하여 최종 투자 판단을 내립니다.

반드시 JSON 형식으로 응답:
{
  "decision": "STRONG_BUY" | "BUY" | "HOLD" | "REDUCE" | "AVOID",
  "confidence": 0.0 ~ 1.0,
  "score": 1.0 ~ 10.0,
  "summary": "3-4 문장 투자 의견 (왜 이 결정인지)",
  "entry_strategy": "분할 매수 / 일괄 매수 / 관망 / 매도 (한 줄)",
  "time_horizon": "단기(3-6개월) | 중기(1-2년) | 장기(3-5년)",
  "key_risks": ["주요 리스크 2-3개"],
  "monitoring_points": ["다음 분기 체크할 포인트 2-3개"]
}

판단 원칙:
- 재무 STRONG + 업종 TAILWIND + 뉴스 POSITIVE → STRONG_BUY (단 PER 50배 이상이면 BUY로 하향)
- 재무 SOLID + 업종 STABLE → BUY 또는 HOLD
- 어느 한 쪽이라도 WEAK/HEADWIND/MOMENTUM_DOWN → REDUCE 또는 AVOID
- 의견이 갈리면 confidence 낮춤 (0.4-0.6)"""


# ── 데이터 수집 ──────────────────────────────────────────────────────────

def _gather_financial_data(ticker: str) -> dict:
    """재무분석가용 데이터 수집"""
    with SessionLocal() as session:
        score_row = session.execute(text("""
            SELECT total_score, growth_score, stability_score, cashflow_score,
                   dividend_score, consistency_score, grade,
                   revenue_cagr_5y, eps_cagr_5y, avg_roe_5y, avg_operating_margin,
                   avg_fcf_margin, debt_ratio, current_ratio, avg_payout_ratio,
                   per, pbr, dividend_yield, close, market_cap
            FROM scores WHERE ticker = :t LIMIT 1
        """), {"t": ticker}).fetchone()

        # 5년 재무 추이
        fin_rows = session.execute(text("""
            SELECT f.year, f.revenue, f.operating_profit, f.net_income, f.fcf
            FROM financials_annual f
            JOIN companies c ON c.id = f.company_id
            WHERE c.ticker = :t
            ORDER BY f.year DESC LIMIT 5
        """), {"t": ticker}).fetchall()

    if not score_row:
        return {"error": "no_data"}

    return {
        "scores": {
            "total": score_row.total_score,
            "growth": score_row.growth_score,
            "stability": score_row.stability_score,
            "cashflow": score_row.cashflow_score,
            "dividend": score_row.dividend_score,
            "consistency": score_row.consistency_score,
            "grade": score_row.grade,
        },
        "metrics": {
            "revenue_cagr_5y": score_row.revenue_cagr_5y,
            "eps_cagr_5y": score_row.eps_cagr_5y,
            "avg_roe_5y": score_row.avg_roe_5y,
            "avg_operating_margin": score_row.avg_operating_margin,
            "avg_fcf_margin": score_row.avg_fcf_margin,
            "debt_ratio": score_row.debt_ratio,
            "current_ratio": score_row.current_ratio,
            "avg_payout_ratio": score_row.avg_payout_ratio,
        },
        "valuation": {
            "per": score_row.per, "pbr": score_row.pbr,
            "dividend_yield": score_row.dividend_yield,
            "close": score_row.close, "market_cap": score_row.market_cap,
        },
        "yearly_trend": [
            {
                "year": r.year,
                "revenue_eok": int(r.revenue / 1e8) if r.revenue else None,
                "op_profit_eok": int(r.operating_profit / 1e8) if r.operating_profit else None,
                "net_income_eok": int(r.net_income / 1e8) if r.net_income else None,
                "fcf_eok": int(r.fcf / 1e8) if r.fcf else None,
            }
            for r in reversed(fin_rows)
        ],
    }


def _gather_news_data(ticker: str) -> dict:
    """뉴스감성가용 데이터 수집"""
    with SessionLocal() as session:
        sent_rows = session.execute(text("""
            SELECT sentiment_date, avg_score, signal, article_count,
                   positive_count, negative_count, key_topics
            FROM news_sentiment
            WHERE ticker = :t
              AND sentiment_date >= CURRENT_DATE - INTERVAL '14 days'
            ORDER BY sentiment_date DESC
        """), {"t": ticker}).fetchall()

        articles = session.execute(text("""
            SELECT title, sentiment_label, sentiment_score, key_topics
            FROM news_articles
            WHERE ticker = :t
              AND published_at >= NOW() - INTERVAL '14 days'
            ORDER BY published_at DESC LIMIT 15
        """), {"t": ticker}).fetchall()

    if not sent_rows:
        return {"has_data": False}

    return {
        "has_data": True,
        "daily_sentiment": [
            {
                "date": str(r.sentiment_date),
                "avg_score": round(r.avg_score or 0, 3),
                "signal": r.signal,
                "article_count": r.article_count,
                "positive_count": r.positive_count,
                "negative_count": r.negative_count,
                "key_topics": r.key_topics or [],
            }
            for r in sent_rows
        ],
        "recent_articles": [
            {
                "title": a.title,
                "sentiment": a.sentiment_label,
                "score": round(a.sentiment_score or 0, 3),
                "topics": a.key_topics or [],
            }
            for a in articles
        ],
    }


def _infer_sector(ticker: str, name: str) -> str:
    """종목명/업종에서 키워드 매칭으로 업종 추정 (news_worker.py와 동일 로직 가벼운 버전)"""
    SECTOR_MAP = {
        "반도체": ["반도체", "메모리", "파운드리", "DRAM", "낸드"],
        "2차전지": ["배터리", "2차전지", "양극재", "음극재", "전해질", "셀"],
        "바이오": ["바이오", "제약", "신약", "헬스케어", "진단", "치료"],
        "자동차": ["자동차", "모빌리티", "EV", "완성차", "부품"],
        "금융": ["은행", "증권", "보험", "금융", "캐피탈"],
        "IT·소프트웨어": ["소프트웨어", "IT", "플랫폼", "클라우드", "AI", "게임"],
        "철강·소재": ["철강", "포스코", "화학", "소재", "비철"],
        "에너지": ["에너지", "발전", "원전", "태양광", "풍력", "가스", "석유"],
        "유통·소비재": ["유통", "백화점", "마트", "식품", "음료", "화장품"],
        "통신": ["통신", "5G", "텔레콤"],
    }
    for sector, kws in SECTOR_MAP.items():
        if any(kw in name for kw in kws):
            return sector
    return "기타"


def _gather_sector_data(ticker: str, name: str) -> dict:
    """업종리서처용 데이터 수집"""
    sector = _infer_sector(ticker, name)
    with SessionLocal() as session:
        sector_row = session.execute(text("""
            SELECT sentiment_date, avg_score, signal, article_count, key_topics, summary
            FROM sector_sentiment
            WHERE sector = :s
            ORDER BY sentiment_date DESC LIMIT 1
        """), {"s": sector}).fetchone()

        # 동종 업종 평균 점수와 이 종목 위치 (간이: scores 테이블에서 같은 시장의 상위/하위 비교)
        peer_stats = session.execute(text("""
            SELECT AVG(total_score) AS avg_score,
                   COUNT(*) AS peer_count
            FROM scores
            WHERE grade IN ('TENBAGGER', 'COMPOUNDER')
        """)).fetchone()

        my_score = session.execute(text(
            "SELECT total_score FROM scores WHERE ticker = :t LIMIT 1"
        ), {"t": ticker}).fetchone()

    return {
        "inferred_sector": sector,
        "sector_sentiment": {
            "avg_score": round(sector_row.avg_score or 0, 3) if sector_row else None,
            "signal": sector_row.signal if sector_row else "NO_DATA",
            "article_count": sector_row.article_count if sector_row else 0,
            "key_topics": sector_row.key_topics if sector_row else [],
            "summary": sector_row.summary if sector_row else None,
            "date": str(sector_row.sentiment_date) if sector_row else None,
        } if sector_row else {"signal": "NO_DATA"},
        "peer_comparison": {
            "tenbagger_compounder_avg": round(peer_stats.avg_score or 0, 2) if peer_stats else None,
            "this_stock_score": round(my_score.total_score or 0, 2) if my_score else None,
            "peer_count": peer_stats.peer_count if peer_stats else 0,
        },
    }


# ── 에이전트 호출 ────────────────────────────────────────────────────────

async def _call_agent(
    client: AsyncOpenAI,
    system_prompt: str,
    user_payload: dict,
    agent_name: str,
) -> dict:
    """단일 에이전트 GPT 호출 → JSON 파싱"""
    try:
        resp = await client.chat.completions.create(
            model=GPT_MODEL,
            temperature=GPT_TEMPERATURE,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            timeout=30,
        )
        content = resp.choices[0].message.content
        return json.loads(content)
    except Exception as e:
        print(f"[committee] {agent_name} 오류: {e}")
        return {"error": str(e), "verdict": "ERROR", "score": 0, "rationale": f"{agent_name} 분석 실패"}


# ── 메인 진입점 ──────────────────────────────────────────────────────────

async def run_committee(ticker: str, name: str = "") -> dict:
    """
    AI 투자위원회 실행 — 4개 에이전트 병렬 + 종합

    Args:
        ticker: 종목 코드 (예: "005930")
        name: 종목명 (없으면 DB에서 조회)

    Returns:
        committee 분석 결과 dict
    """
    if not settings.openai_api_key:
        return {"error": "openai_key_not_set"}

    # 종목명 보완
    if not name:
        with SessionLocal() as session:
            row = session.execute(text(
                "SELECT name FROM scores WHERE ticker = :t LIMIT 1"
            ), {"t": ticker}).fetchone()
            name = row.name if row else ticker

    # 데이터 수집 (병렬 안 해도 빠름, DB 로컬)
    fin_data = _gather_financial_data(ticker)
    if fin_data.get("error") == "no_data":
        return {"error": "ticker_not_in_scores", "ticker": ticker}

    news_data = _gather_news_data(ticker)
    sector_data = _gather_sector_data(ticker, name)

    client = AsyncOpenAI(api_key=settings.openai_api_key)

    # ── 1단계: 3개 전문가 병렬 호출 ───────────────────────────────────────
    fa_payload = {"ticker": ticker, "name": name, **fin_data}
    ns_payload = {"ticker": ticker, "name": name, **news_data}
    sr_payload = {"ticker": ticker, "name": name, **sector_data}

    fa_task = _call_agent(client, FINANCIAL_ANALYST_PROMPT, fa_payload, "재무분석가")
    ns_task = _call_agent(client, NEWS_SENTIMENT_AGENT_PROMPT, ns_payload, "뉴스감성가")
    sr_task = _call_agent(client, SECTOR_RESEARCHER_PROMPT, sr_payload, "업종리서처")

    fa_result, ns_result, sr_result = await asyncio.gather(fa_task, ns_task, sr_task)

    # ── 2단계: 투자전략가 (위 3개 결과 + 가격 데이터) ──────────────────────
    strategist_payload = {
        "ticker": ticker,
        "name": name,
        "valuation": fin_data.get("valuation", {}),
        "financial_analyst": fa_result,
        "news_sentiment_analyst": ns_result,
        "sector_researcher": sr_result,
    }
    strategist_result = await _call_agent(
        client, INVESTMENT_STRATEGIST_PROMPT, strategist_payload, "투자전략가"
    )

    return {
        "ticker": ticker,
        "name": name,
        "analyzed_at": datetime.datetime.utcnow().isoformat() + "Z",
        "committee_decision": strategist_result.get("decision", "HOLD"),
        "confidence": strategist_result.get("confidence", 0.5),
        "consensus_score": strategist_result.get("score", 5.0),
        "agents": {
            "financial_analyst": fa_result,
            "news_sentiment_analyst": ns_result,
            "sector_researcher": sr_result,
            "investment_strategist": strategist_result,
        },
        "context": {
            "current_grade": fin_data.get("scores", {}).get("grade"),
            "total_score": fin_data.get("scores", {}).get("total"),
            "per": fin_data.get("valuation", {}).get("per"),
            "pbr": fin_data.get("valuation", {}).get("pbr"),
            "sector": sector_data.get("inferred_sector"),
            "has_news_data": news_data.get("has_data", False),
        },
    }
