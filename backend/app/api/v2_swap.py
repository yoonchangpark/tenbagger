"""
포트폴리오 교체 분석 API
POST /api/v2/holdings/swap-analysis

공시 정보 + 산업 뉴스 + 텐배거 스코어를 GPT-4o로 종합해
이상 감지 종목에 대한 교체 타이밍·후보·예상 수익을 반환한다.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import text
from app.core.database import SessionLocal
from app.core.auth import get_current_user
from app.core.config import settings
from app.infra.clients.dart_client import _get, get_corp_code
import datetime
import json

router = APIRouter(prefix="/api/v2/holdings", tags=["swap"])

# 등급별 장기 CAGR 기대치 (보수 추정)
_GRADE_CAGR = {
    "TENBAGGER":  {"price": 12, "div": 7},
    "COMPOUNDER": {"price": 9,  "div": 5},
    "WATCHLIST":  {"price": 5,  "div": 3},
    "AVOID":      {"price": 2,  "div": 1},
    "UNKNOWN":    {"price": 4,  "div": 2},
}


class SwapRequest(BaseModel):
    ticker: str
    qty: int = 0
    avg_price: float = 0
    current_price: Optional[float] = None


def _fmt_won(v) -> str:
    if v is None:
        return "-"
    if abs(v) >= 1e8:
        return f"{v / 1e8:.0f}억원"
    if abs(v) >= 1e4:
        return f"{v / 1e4:.0f}만원"
    return f"{int(v):,}원"


def _sim_10y(price_cagr: float, div_cagr: float, invest: float, years: int = 10) -> dict:
    """배당 재투자 복리 시뮬레이션"""
    value = invest
    for _ in range(years):
        div = value * (div_cagr / 100)
        value = value * (1 + price_cagr / 100) + div
    effective_cagr = round((value / invest) ** (1 / years) - 1, 3) * 100 if invest > 0 else 0
    return {"value_10y": int(value), "cagr": round(effective_cagr, 1)}


@router.post("/swap-analysis")
async def swap_analysis(
    body: SwapRequest,
    current_user: dict = Depends(get_current_user),
):
    ticker = body.ticker.upper()
    invest = body.qty * body.avg_price

    # ── 1. 현재 종목 스코어 조회 ─────────────────────────────────────────
    company_info = {}
    with SessionLocal() as db:
        row = db.execute(text("""
            SELECT s.grade, s.total_score, s.growth_score, s.stability_score,
                   s.cashflow_score, s.dividend_yield, s.avg_roe_5y,
                   s.revenue_cagr_5y, s.market, s.name, s.close,
                   s.per, s.pbr
            FROM scores s
            WHERE s.ticker = :t
            ORDER BY s.analyzed_at DESC LIMIT 1
        """), {"t": ticker}).fetchone()
        if row:
            company_info = dict(row._mapping)

    grade = company_info.get("grade", "UNKNOWN")
    corp_name = company_info.get("name", ticker)

    # ── 2. 뉴스 감성 (종목별) ────────────────────────────────────────────
    news_signal = None
    news_summary = ""
    news_topics = []
    with SessionLocal() as db:
        ns = db.execute(text("""
            SELECT avg_score, signal, summary, key_topics
            FROM news_sentiment
            WHERE ticker = :t
            ORDER BY sentiment_date DESC LIMIT 1
        """), {"t": ticker}).fetchone()
        if ns:
            news_signal  = ns.signal
            news_summary = ns.summary or ""
            news_topics  = list(ns.key_topics or [])

    # ── 3. 업종 뉴스 감성 (최근 ticker가 포함된 업종 추론) ──────────────
    sector_name = None
    sector_signal = None
    sector_summary = ""
    with SessionLocal() as db:
        sec_row = db.execute(text("""
            SELECT sector FROM news_articles
            WHERE ticker = :t AND sector IS NOT NULL
            GROUP BY sector ORDER BY COUNT(*) DESC LIMIT 1
        """), {"t": ticker}).fetchone()
        if sec_row:
            sector_name = sec_row.sector
            ss = db.execute(text("""
                SELECT signal, summary FROM sector_sentiment
                WHERE sector = :s
                ORDER BY sentiment_date DESC LIMIT 1
            """), {"s": sector_name}).fetchone()
            if ss:
                sector_signal  = ss.signal
                sector_summary = ss.summary or ""

    # ── 4. DART 최근 공시 (90일) ─────────────────────────────────────────
    recent_disclosures = []
    try:
        corp_code, _ = await get_corp_code(ticker)
        if corp_code:
            today = datetime.date.today()
            bgn_de = (today - datetime.timedelta(days=90)).strftime("%Y%m%d")
            result = await _get("list.json", {
                "corp_code": corp_code,
                "bgn_de": bgn_de,
                "end_de": today.strftime("%Y%m%d"),
                "page_count": "10",
            })
            recent_disclosures = result.get("list", [])[:8]
    except Exception:
        pass

    disc_lines = [
        f"  - {d.get('report_nm', '')} ({d.get('rcept_dt', '')[:4]}.{d.get('rcept_dt', '')[4:6]}.{d.get('rcept_dt', '')[6:]})"
        for d in recent_disclosures
    ]
    disc_context = ("최근 90일 주요 공시:\n" + "\n".join(disc_lines)) if disc_lines else "최근 90일 중요 공시 없음"

    # ── 5. 이상 감지 조건 ────────────────────────────────────────────────
    pnl_pct = None
    if body.current_price and body.avg_price > 0:
        pnl_pct = (body.current_price - body.avg_price) / body.avg_price * 100

    anomalies = []
    if grade in ("WATCHLIST", "AVOID", "UNKNOWN"):
        anomalies.append({"type": "grade", "severity": "HIGH",
                          "msg": f"등급 {grade} — TENBAGGER/COMPOUNDER 미달"})
    elif grade == "COMPOUNDER":
        anomalies.append({"type": "grade", "severity": "LOW",
                          "msg": "COMPOUNDER 등급 — TENBAGGER 미달"})

    if pnl_pct is not None and pnl_pct < -10:
        anomalies.append({"type": "loss", "severity": "HIGH",
                          "msg": f"손실 {pnl_pct:.1f}% — 투자 논거 재검토 필요"})

    if news_signal in ("SELL_SIGNAL", "CAUTION"):
        anomalies.append({"type": "news", "severity": "MEDIUM",
                          "msg": f"종목 뉴스 감성 {news_signal} — {', '.join(news_topics[:3])}"})

    if sector_signal in ("SELL_SIGNAL", "CAUTION"):
        anomalies.append({"type": "sector", "severity": "MEDIUM",
                          "msg": f"업종({sector_name}) 감성 {sector_signal}"})

    # ── 6. 교체 후보 (TENBAGGER 상위 5개, 현재 종목 제외) ───────────────
    candidates = []
    with SessionLocal() as db:
        rows = db.execute(text("""
            SELECT s.ticker, s.name, s.grade, s.total_score,
                   s.close, s.revenue_cagr_5y, s.dividend_yield, s.market
            FROM scores s
            WHERE s.grade = 'TENBAGGER'
              AND s.ticker != :t
            ORDER BY s.total_score DESC NULLS LAST
            LIMIT 5
        """), {"t": ticker}).fetchall()
        candidates = [dict(r._mapping) for r in rows]

    # ── 7. 시뮬레이션 비교 ───────────────────────────────────────────────
    current_sim = None
    best_swap   = None

    if invest > 0:
        cur_cagr  = _GRADE_CAGR.get(grade, _GRADE_CAGR["UNKNOWN"])
        current_sim = _sim_10y(cur_cagr["price"], cur_cagr["div"], invest)

        if candidates:
            best = candidates[0]
            sw_cagr = _GRADE_CAGR["TENBAGGER"]
            swap_sim = _sim_10y(sw_cagr["price"], sw_cagr["div"], invest)
            best_swap = {
                "ticker":        best["ticker"],
                "name":          best["name"],
                "grade":         best["grade"],
                "score":         round(float(best["total_score"] or 0), 1),
                "current_price": int(best["close"] or 0),
                "current_sim":   current_sim,
                "swap_sim":      swap_sim,
                "diff_10y":      swap_sim["value_10y"] - current_sim["value_10y"],
            }

    # ── 8. GPT-4o 종합 판단 ──────────────────────────────────────────────
    timing = {"advice": "추가 데이터 수집 후 판단 권장", "urgency": "LOW", "reason": ""}
    comprehensive_opinion = ""
    key_signals: list[str] = []
    action = "MONITOR"
    action_reason = ""

    if settings.openai_api_key and anomalies:
        try:
            score_str = f"총점 {company_info['total_score']:.1f}" if company_info.get("total_score") else "점수 미산출"
            pnl_str   = f"{pnl_pct:+.1f}%" if pnl_pct is not None else "미확인"
            swap_str  = ""
            if best_swap:
                swap_str = (
                    f"\n[교체 후보]\n"
                    f"  {best_swap['name']} ({best_swap['ticker']}) — TENBAGGER {best_swap['score']}점\n"
                    f"  10년 후 예상: 현재 보유 {_fmt_won(best_swap['current_sim']['value_10y'])} "
                    f"→ 교체 시 {_fmt_won(best_swap['swap_sim']['value_10y'])} "
                    f"(차이 +{_fmt_won(best_swap['diff_10y'])})"
                )

            anomaly_str = "\n".join(f"  - [{a['severity']}] {a['msg']}" for a in anomalies)
            news_str = "\n".join(filter(None, [
                f"종목 뉴스: {news_signal} — {news_summary}" if news_signal else None,
                f"업종({sector_name}) 뉴스: {sector_signal} — {sector_summary}" if sector_signal else None,
            ])) or "뉴스 감성 데이터 없음"

            prompt = f"""한국 장기투자 포트폴리오 관리 전문가로서 아래 종목의 교체 여부를 종합 판단하세요.

[종목 정보]
회사명: {corp_name} ({ticker}) | 등급: {grade} | {score_str}
평균매수가: {body.avg_price:,.0f}원 | 보유수량: {body.qty}주 | 총투자금: {_fmt_won(invest)}
현재 손익: {pnl_str}

[이상 감지 신호]
{anomaly_str}

[최근 공시]
{disc_context}

[뉴스·감성]
{news_str}
{swap_str}

아래 JSON으로만 응답하세요:
{{
  "timing_urgency": "HIGH" | "MEDIUM" | "LOW",
  "timing_advice": "교체 타이밍 구체적 조언 1~2문장 (수치·날짜 포함)",
  "timing_reason": "공시·뉴스·이벤트 기반 타이밍 판단 근거",
  "comprehensive_opinion": "공시·뉴스·스코어 종합 판단 3~4문장. 장기투자 관점. 수치 포함.",
  "key_signals": ["핵심신호1", "핵심신호2", "핵심신호3"],
  "action": "SWAP_NOW" | "MONITOR" | "HOLD",
  "action_reason": "최종 행동 권고 근거 1문장"
}}"""

            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=settings.openai_api_key)
            resp = await client.chat.completions.create(
                model="gpt-4o",
                max_tokens=900,
                temperature=0.2,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": (
                        "당신은 한국 주식 장기투자 포트폴리오 관리 전문가입니다. "
                        "공시·뉴스·스코어를 종합해 교체 판단을 내립니다. "
                        "반드시 JSON으로만 응답하세요."
                    )},
                    {"role": "user", "content": prompt},
                ],
            )
            gpt = json.loads(resp.choices[0].message.content)
            timing = {
                "advice":  gpt.get("timing_advice", ""),
                "urgency": gpt.get("timing_urgency", "LOW"),
                "reason":  gpt.get("timing_reason", ""),
            }
            comprehensive_opinion = gpt.get("comprehensive_opinion", "")
            key_signals           = gpt.get("key_signals", [])
            action                = gpt.get("action", "MONITOR")
            action_reason         = gpt.get("action_reason", "")

        except Exception as e:
            comprehensive_opinion = f"AI 분석 오류: {e}"

    # ── 9. 데이터 소스 태그 ──────────────────────────────────────────────
    data_sources = [s for s in [
        f"DART 공시 {len(recent_disclosures)}건" if recent_disclosures else None,
        f"종목 뉴스 ({news_signal})" if news_signal else None,
        f"업종 뉴스 ({sector_name})" if sector_name else None,
        "텐배거 스코어" if company_info else None,
    ] if s]

    return {
        "ticker":                ticker,
        "corp_name":             corp_name,
        "grade":                 grade,
        "score":                 company_info.get("total_score"),
        "anomalies":             anomalies,
        "timing":                timing,
        "best_swap":             best_swap,
        "key_signals":           key_signals,
        "action":                action,
        "action_reason":         action_reason,
        "comprehensive_opinion": comprehensive_opinion,
        "data_sources":          data_sources,
    }
