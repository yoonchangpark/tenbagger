"""
Claude AI 기반 정성적 기업 분석 엔진
DART 기업정보 + 재무 트렌드 -> AI가 사업모델/SWOT/CAGR근거/미래전망 생성
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
        roe = None
        if f.get("net_income") and f.get("total_equity") and f.get("total_equity") > 0:
            roe = round(f["net_income"] / f["total_equity"] * 100, 1)

        rev_str = f"{revenue/1e8:.0f}억" if revenue else "N/A"
        op_str = f"{op/1e8:.0f}억" if op else "N/A"
        net_str = f"{net/1e8:.0f}억" if net else "N/A"
        roe_str = f"{roe}%" if roe else "N/A"
        lines.append(f"  {year}년: 매출 {rev_str}, 영업이익 {op_str}, 순이익 {net_str}, ROE {roe_str}")

    if len(sorted_fins) >= 4:
        first = sorted_fins[0]
        last = sorted_fins[-1]
        n = last.get("year", 0) - first.get("year", 0)
        if n > 0 and first.get("revenue") and last.get("revenue") and first["revenue"] > 0:
            cagr = ((last["revenue"] / first["revenue"]) ** (1 / n) - 1) * 100
            lines.append(f"  -> {n}년 매출 CAGR: {cagr:.1f}%")
        if n > 0 and first.get("net_income") and last.get("net_income") and first["net_income"] > 0:
            eps_cagr = ((last["net_income"] / first["net_income"]) ** (1 / n) - 1) * 100
            lines.append(f"  -> {n}년 순이익 CAGR: {eps_cagr:.1f}%")

    return "\n".join(lines)


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
) -> dict:
    """OpenAI API로 정성적 분석 생성 (캐시 적용)"""
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

    fin_summary = _format_financials_summary(financials)
    shareholder_str = _format_shareholders(shareholders)
    company_str = _format_company_info(company_info)
    disc_str = _format_disclosures(disclosures)

    total_score = score.get("total_score", 0) if score else 0
    grade = score.get("grade", "UNKNOWN") if score else "UNKNOWN"
    rev_cagr = score.get("revenue_cagr_5y") if score else None
    eps_cagr = score.get("eps_cagr_5y") if score else None
    avg_roe = score.get("avg_roe_5y") if score else None
    op_margin = score.get("avg_operating_margin") if score else None

    rev_cagr_str = f"{rev_cagr:.1f}%" if rev_cagr else "N/A"
    eps_cagr_str = f"{eps_cagr:.1f}%" if eps_cagr else "N/A"
    avg_roe_str = f"{avg_roe:.1f}%" if avg_roe else "N/A"
    op_margin_str = f"{op_margin:.1f}%" if op_margin else "N/A"

    prompt = (
        f"당신은 한국 주식 장기투자 전문 애널리스트입니다. 다음 데이터를 바탕으로 {name}({ticker})의 기업 리서치 리포트를 작성하세요.\n\n"
        f"## 기업 기본정보\n{company_str}\n\n"
        f"## 재무 현황 (최근 6년)\n{fin_summary}\n"
        f"- 현재 시스템 점수: {total_score}/10 ({grade})\n"
        f"- 5년 매출 CAGR: {rev_cagr_str}\n"
        f"- 5년 EPS CAGR: {eps_cagr_str}\n"
        f"- 평균 ROE: {avg_roe_str}\n"
        f"- 평균 영업이익률: {op_margin_str}\n\n"
        f"## 주요주주 현황\n{shareholder_str}\n\n"
        f"## 최근 공시\n{disc_str}\n\n"
        "위 데이터를 바탕으로 다음 JSON 형식으로 분석하세요. JSON 외 다른 텍스트는 절대 포함하지 마세요.\n"
        "estimated_forward_cagr는 반드시 실제 숫자(예: 8.5)로만 입력하세요:\n\n"
        "{\n"
        '  "business_model": "핵심 사업모델과 수익구조 요약 (150자 이내)",\n'
        '  "competitive_advantage": "핵심 경쟁우위 및 해자(moat) 분석 (150자 이내)",\n'
        '  "major_shareholders_analysis": "주요주주 구성과 지배구조 특징 (100자 이내)",\n'
        '  "swot": {\n'
        '    "strength": "핵심 강점 2~3가지 (쉼표 구분)",\n'
        '    "weakness": "핵심 약점 2~3가지 (쉼표 구분)",\n'
        '    "opportunity": "성장 기회 2~3가지 (쉼표 구분)",\n'
        '    "threat": "위협 요인 2~3가지 (쉼표 구분)"\n'
        "  },\n"
        '  "cagr_rationale": "향후 5년 EPS 성장률 추산 근거 (250자 이내)",\n'
        '  "estimated_forward_cagr": 8.5,\n'
        '  "future_outlook": "향후 3~5년 사업 전망과 장기투자자 관전 포인트 (300자 이내)",\n'
        '  "key_risks": "핵심 리스크 2~3가지 (쉼표 구분)",\n'
        '  "long_term_potential": "STRONG / MODERATE / WEAK 중 하나와 장기 사업 잠재력 평가 한 줄 (현재 주가/타이밍 무관, 사업 본질만 평가)"\n'
        "}"
    )

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=openai_key, timeout=30.0)
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=1500,
                temperature=0.3,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "당신은 한국 주식 장기투자 전문 애널리스트입니다. 요청된 JSON 형식으로만 응답하세요."},
                    {"role": "user", "content": prompt},
                ],
            ),
            timeout=35.0,
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
        print(f"[QUALITATIVE] {ticker} AI 분석 완료")
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
    return {
        "ticker": ticker,
        "name": name,
        "business_model": "AI 분석을 불러오지 못했습니다. 잠시 후 다시 시도해주세요.",
        "competitive_advantage": "-",
        "major_shareholders_analysis": "-",
        "swot": {
            "strength": "데이터 분석 필요",
            "weakness": "데이터 분석 필요",
            "opportunity": "데이터 분석 필요",
            "threat": "데이터 분석 필요",
        },
        "cagr_rationale": "AI 분석을 불러오지 못했습니다.",
        "estimated_forward_cagr": None,
        "future_outlook": "잠시 후 다시 시도하거나 OPENAI_API_KEY 설정을 확인하세요.",
        "key_risks": "-",
        "long_term_potential": "MODERATE - AI 분석 불가",
        "generated_at": datetime.datetime.now().isoformat(),
        "error": "AI analysis unavailable",
    }
