"""
월가 애널리스트 관점 심층 분석 엔진 (v2.0)
DART 기업정보 + 재무 트렌드 + 시장 밸류에이션 → GPT-4o 종합 리포트
7개 분석 프레임워크: 비즈니스·해자·트렌드·밸류에이션·Bull/Bear·단기전망·최종판단
"""
import json
import asyncio
import datetime
from typing import Optional

# 인메모리 캐시 (24시간 TTL)
_qualitative_cache: dict = {}
_qualitative_cache_lock = asyncio.Lock()


def _make_cache_key(ticker: str) -> str:
    date_str = datetime.date.today().isoformat()
    return f"{ticker}:{date_str}"


async def _get_cached(ticker: str) -> Optional[dict]:
    async with _qualitative_cache_lock:
        return _qualitative_cache.get(_make_cache_key(ticker))


async def _set_cached(ticker: str, data: dict):
    async with _qualitative_cache_lock:
        _qualitative_cache[_make_cache_key(ticker)] = data


def _format_financials_summary(financials: list) -> str:
    if not financials:
        return "재무 데이터 없음"

    lines = []
    sorted_fins = sorted(financials, key=lambda x: x.get("year", 0))

    for f in sorted_fins[-6:]:
        year = f.get("year", "?")
        revenue = f.get("revenue")
        op = f.get("operating_profit")
        net = f.get("net_income")
        fcf = f.get("free_cash_flow") or f.get("fcf")
        roe = None
        if f.get("net_income") and f.get("total_equity") and f.get("total_equity") > 0:
            roe = round(f["net_income"] / f["total_equity"] * 100, 1)

        rev_str = f"{revenue/1e8:.0f}억" if revenue else "N/A"
        op_str  = f"{op/1e8:.0f}억"      if op      else "N/A"
        net_str = f"{net/1e8:.0f}억"     if net     else "N/A"
        fcf_str = f"{fcf/1e8:.0f}억"     if fcf     else "N/A"
        roe_str = f"{roe}%"              if roe     else "N/A"
        lines.append(
            f"  {year}년: 매출 {rev_str}, 영업이익 {op_str}, "
            f"순이익 {net_str}, FCF {fcf_str}, ROE {roe_str}"
        )

    if len(sorted_fins) >= 4:
        first, last = sorted_fins[0], sorted_fins[-1]
        n = last.get("year", 0) - first.get("year", 0)
        if n > 0 and first.get("revenue") and last.get("revenue") and first["revenue"] > 0:
            cagr = ((last["revenue"] / first["revenue"]) ** (1 / n) - 1) * 100
            lines.append(f"  → {n}년 매출 CAGR: {cagr:.1f}%")
        if n > 0 and first.get("net_income") and last.get("net_income") and first["net_income"] > 0:
            ec = ((last["net_income"] / first["net_income"]) ** (1 / n) - 1) * 100
            lines.append(f"  → {n}년 순이익 CAGR: {ec:.1f}%")

    return "\n".join(lines)


def _compute_financial_trend(financials: list) -> str:
    """최근 3년 재무 방향성 사전 계산 (GPT 가이드용)"""
    if len(financials) < 3:
        return "데이터 부족"
    sorted_fins = sorted(financials, key=lambda x: x.get("year", 0))
    recent = sorted_fins[-3:]

    revenues   = [f.get("revenue")    for f in recent if f.get("revenue")]
    net_incomes = [f.get("net_income") for f in recent if f.get("net_income")]
    ops        = [f.get("operating_profit") for f in recent if f.get("operating_profit")]

    rev_up  = len(revenues) >= 2   and revenues[-1]    > revenues[0]
    net_up  = len(net_incomes) >= 2 and net_incomes[-1] > net_incomes[0]
    op_up   = len(ops) >= 2        and ops[-1]         > ops[0]

    positives = sum([rev_up, net_up, op_up])
    if positives == 3:
        return "강화 (매출·영업이익·순이익 모두 상승)"
    elif positives == 0:
        return "약화 (매출·영업이익·순이익 모두 하락)"
    else:
        parts = []
        if rev_up:  parts.append("매출↑")
        else:       parts.append("매출↓")
        if op_up:   parts.append("영업이익↑")
        else:       parts.append("영업이익↓")
        if net_up:  parts.append("순이익↑")
        else:       parts.append("순이익↓")
        return f"혼조 ({', '.join(parts)})"


def _format_shareholders(shareholders_data: dict) -> str:
    items = shareholders_data.get("list", [])
    if not items:
        return "주주 데이터 없음"

    lines = []
    seen = set()
    for item in items[:10]:
        name = (item.get("nm") or item.get("shreholder_nm") or item.get("name") or "").strip()
        relation = (item.get("relate") or item.get("spcfmtt_rltn") or "").strip()
        ratio = (item.get("trmend_posesn_stock_qota_rt") or item.get("bsis_posesn_stock_qota_rt") or "").strip()

        if name and name not in seen:
            seen.add(name)
            ratio_str = f" ({ratio}%)" if ratio else ""
            lines.append(f"  {name}{ratio_str} [{relation}]")

    return "\n".join(lines) if lines else "주주 데이터 없음"


def _format_company_info(info: dict) -> str:
    if not info or info.get("status") != "000":
        return "기업정보 없음"

    fields = {
        "업종": info.get("induty_code"),
        "설립일": info.get("est_dt"),
        "결산월": info.get("acc_mt"),
        "대표이사": info.get("ceo_nm"),
        "홈페이지": info.get("hm_url"),
        "임직원수": info.get("empl_no"),
        "주소": info.get("adres"),
    }
    return "\n".join(f"  {k}: {v}" for k, v in fields.items() if v)


def _format_disclosures(disc_data: dict) -> str:
    items = disc_data.get("list", [])
    if not items:
        return "공시 데이터 없음"

    lines = []
    for item in items[:5]:
        date = item.get("rcept_dt", "")
        title = item.get("report_nm", "")
        lines.append(f"  {date}: {title}")
    return "\n".join(lines)


async def generate_qualitative_analysis(
    ticker: str,
    name: str,
    financials: list,
    company_info: dict,
    shareholders: dict,
    disclosures: dict,
    score: dict,
    market_valuation: Optional[dict] = None,
    sector_peer: Optional[dict] = None,
) -> dict:
    """GPT-4o 기반 월가 애널리스트 수준 심층 분석 (캐시 24h)"""
    cached = await _get_cached(ticker)
    if cached:
        print(f"[QUALITATIVE] {ticker} 캐시 히트")
        return cached

    from app.core.config import settings
    import os

    openai_key = settings.openai_api_key or os.environ.get("OPENAI_API_KEY", "")
    if not openai_key:
        try:
            with open("/app/.env") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("OPENAI_API_KEY="):
                        openai_key = line.split("=", 1)[1].strip()
                        break
        except Exception:
            pass

    if not openai_key:
        print(f"[QUALITATIVE] {ticker} OPENAI_API_KEY 미설정")
        return _fallback_analysis(ticker, name, financials, score)

    # ── 데이터 포맷팅 ──────────────────────────────────────────────────────
    fin_summary    = _format_financials_summary(financials)
    fin_trend_hint = _compute_financial_trend(financials)
    shareholder_str = _format_shareholders(shareholders)
    company_str    = _format_company_info(company_info)
    disc_str       = _format_disclosures(disclosures)

    s = score or {}
    total_score  = s.get("total_score", 0)
    grade        = s.get("grade", "UNKNOWN")
    rev_cagr     = s.get("revenue_cagr_5y")
    eps_cagr     = s.get("eps_cagr_5y")
    avg_roe      = s.get("avg_roe_5y")
    op_margin    = s.get("avg_operating_margin")
    fcf_margin   = s.get("avg_fcf_margin")
    debt_ratio   = s.get("debt_ratio")
    current_ratio = s.get("current_ratio")
    growth_score = s.get("growth_score")
    stability_sc = s.get("stability_score")
    cashflow_sc  = s.get("cashflow_score")
    per          = s.get("per")
    pbr          = s.get("pbr")
    div_yield    = s.get("dividend_yield")
    market       = s.get("market", "KOSPI")

    def _pct(v): return f"{v:.1f}%" if v is not None else "N/A"
    def _num(v, d=1): return f"{v:.{d}f}" if v is not None else "N/A"

    # 시장 평균 PER 컨텍스트
    mkt_per_ctx = ""
    if market_valuation and market_valuation.get("avg_per"):
        mkt_per_ctx = (
            f"\n- {market} 시장 평균 PER: {market_valuation['avg_per']:.1f}배"
            f" (상위 25%: {market_valuation.get('per_q75', 'N/A')}배)"
        )
    if sector_peer and sector_peer.get("avg_per"):
        mkt_per_ctx += (
            f"\n- {sector_peer['sector_name']} 동종업종 평균 PER: {sector_peer['avg_per']:.1f}배"
            f" (Q25: {sector_peer.get('per_q25','N/A')} | Q75: {sector_peer.get('per_q75','N/A')})"
            f" — 비교 종목 {sector_peer.get('peer_count', '?')}개 기준"
        )

    prompt = f"""당신은 월가 베테랑 애널리스트입니다. {name}({ticker})에 대해 기관급 심층 투자 리포트를 작성하세요.

## 기업 기본정보
{company_str}

## 재무 현황 (최근 6년)
{fin_summary}

## 정량 스코어 요약
- 시스템 종합점수: {_num(total_score)}/10 ({grade})
- 성장성: {_num(growth_score)} | 안정성: {_num(stability_sc)} | 현금흐름: {_num(cashflow_sc)}
- 5년 매출 CAGR: {_pct(rev_cagr)} | 5년 EPS CAGR: {_pct(eps_cagr)}
- 평균 ROE: {_pct(avg_roe)} | 평균 영업이익률: {_pct(op_margin)} | 평균 FCF마진: {_pct(fcf_margin)}
- 부채비율: {_pct(debt_ratio)} | 유동비율: {_num(current_ratio, 2)}배
- PER: {_num(per, 1)}배 | PBR: {_num(pbr, 2)}배 | 배당수익률: {_pct(div_yield)}{mkt_per_ctx}
- 재무 트렌드 사전계산: {fin_trend_hint}

## 주요주주
{shareholder_str}

## 최근 공시
{disc_str}

---
아래 JSON 형식으로만 응답하세요. JSON 외 텍스트 절대 금지. estimated_forward_cagr는 반드시 숫자(예: 8.5):

{{
  "business_model": "핵심 사업모델·수익구조·고객군 (200자)",
  "competitive_advantage": "경쟁우위 핵심 요약 (150자)",
  "moat_score": 7,
  "moat_detail": {{
    "brand": 8,
    "network_effect": 3,
    "switching_cost": 6,
    "cost_advantage": 5,
    "tech_patent": 4,
    "summary": "해자 강도 종합 근거 1문장"
  }},
  "major_shareholders_analysis": "지배구조·주주 구성 특징 (100자)",
  "financial_trend": {{
    "direction": "강화",
    "reason": "최근 3년 매출·이익·FCF 흐름 기반 트렌드 판단 근거 1문장"
  }},
  "swot": {{
    "strength": "강점 2~3가지 (쉼표 구분)",
    "weakness": "약점 2~3가지 (쉼표 구분)",
    "opportunity": "기회 2~3가지 (쉼표 구분)",
    "threat": "위협 2~3가지 (쉼표 구분)"
  }},
  "valuation_view": {{
    "assessment": "저평가",
    "per_vs_market": "종목 PER vs 시장평균 비교 설명 1문장",
    "dcf_logic": "FCF·성장률 기반 DCF 논리 요약 1문장",
    "reason": "밸류에이션 종합 판단 근거 1문장"
  }},
  "bull_bear": {{
    "bull": ["강세 논리 1", "강세 논리 2", "강세 논리 3"],
    "bear": ["약세 논리 1", "약세 논리 2", "약세 논리 3"],
    "verdict": "중립 결론 — 핵심 변수와 투자자가 모니터링해야 할 포인트 1문장"
  }},
  "short_term_outlook": "향후 12~24개월 전망 — 실적 촉매·리스크 이벤트 중심 (150자)",
  "cagr_rationale": "향후 5년 EPS 성장률 추산 근거 (200자)",
  "estimated_forward_cagr": 8.5,
  "future_outlook": "향후 3~5년 사업 전망·장기투자 포인트 (250자)",
  "key_risks": "핵심 리스크 2~3가지 (쉼표 구분)",
  "investment_verdict": {{
    "action": "BUY",
    "catalysts": ["촉매요인 1", "촉매요인 2"],
    "risks_to_watch": ["모니터링 리스크 1", "모니터링 리스크 2"],
    "horizon": "적합 투자 기간 (예: 3~5년 장기)"
  }},
  "long_term_potential": "STRONG / MODERATE / WEAK 중 하나와 장기 사업 잠재력 평가 한 줄"
}}"""

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=openai_key, timeout=45.0)
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model="gpt-4o",
                max_tokens=2500,
                temperature=0.3,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "당신은 월가 베테랑 투자 애널리스트입니다. "
                            "기관급 리서치 리포트 수준으로 분석하되 "
                            "한국 주식 시장 특성(DART 공시·재벌 지배구조·업종 특성)을 반영하세요. "
                            "반드시 JSON으로만 응답하세요."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
            ),
            timeout=50.0,
        )

        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw.strip())
        result["generated_at"] = datetime.datetime.now().isoformat()
        result["ticker"] = ticker
        result["name"] = name

        await _set_cached(ticker, result)
        print(f"[QUALITATIVE] {ticker} AI 분석 완료 (gpt-4o 7개 프레임워크)")
        return result

    except json.JSONDecodeError as e:
        print(f"[QUALITATIVE] JSON 파싱 실패: {e}")
        result = _fallback_analysis(ticker, name, financials, score)
        result["error"] = f"JSON 파싱 실패: {str(e)[:100]}"
        return result
    except Exception as e:
        print(f"[QUALITATIVE] AI 분석 실패: {type(e).__name__}: {e}")
        result = _fallback_analysis(ticker, name, financials, score)
        result["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        return result


def _fallback_analysis(ticker: str, name: str, financials: list, score: dict) -> dict:
    fin_trend = _compute_financial_trend(financials)
    return {
        "ticker": ticker,
        "name": name,
        "business_model": "AI 분석을 불러오지 못했습니다. 잠시 후 다시 시도해주세요.",
        "competitive_advantage": "-",
        "moat_score": None,
        "moat_detail": {
            "brand": None, "network_effect": None, "switching_cost": None,
            "cost_advantage": None, "tech_patent": None, "summary": "-",
        },
        "major_shareholders_analysis": "-",
        "financial_trend": {"direction": fin_trend.split(" ")[0], "reason": fin_trend},
        "swot": {
            "strength": "데이터 분석 필요", "weakness": "데이터 분석 필요",
            "opportunity": "데이터 분석 필요", "threat": "데이터 분석 필요",
        },
        "valuation_view": {
            "assessment": "-", "per_vs_market": "-", "dcf_logic": "-", "reason": "-",
        },
        "bull_bear": {
            "bull": [], "bear": [], "verdict": "AI 분석 불가",
        },
        "short_term_outlook": "잠시 후 다시 시도하거나 OPENAI_API_KEY 설정을 확인하세요.",
        "cagr_rationale": "AI 분석을 불러오지 못했습니다.",
        "estimated_forward_cagr": None,
        "future_outlook": "잠시 후 다시 시도하거나 OPENAI_API_KEY 설정을 확인하세요.",
        "key_risks": "-",
        "investment_verdict": {
            "action": "-", "catalysts": [], "risks_to_watch": [], "horizon": "-",
        },
        "long_term_potential": "MODERATE - AI 분석 불가",
        "generated_at": datetime.datetime.now().isoformat(),
        "error": "AI analysis unavailable",
    }
