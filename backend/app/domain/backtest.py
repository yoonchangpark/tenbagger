"""
백테스트 엔진
base_year 기준 당시 재무데이터로 스코어 계산 → 실제 이후 수익률 비교
"""
import datetime
import asyncio
from typing import Optional
from app.infra.clients.dart_client import get_corp_code, fetch_yearly_financials
from app.domain.scoring import calculate_tenbagger_score


async def _get_price_at(ticker: str, year: int) -> Optional[int]:
    """특정 연도 말 주가 조회 (FinanceDataReader 우선 → pykrx fallback)"""
    def _fdr_fetch() -> Optional[int]:
        try:
            import FinanceDataReader as fdr
            df = fdr.DataReader(ticker, f"{year}-12-01", f"{year}-12-31")
            if df is not None and not df.empty:
                closes = df["Close"].dropna()
                if len(closes) > 0:
                    return int(closes.iloc[-1])
        except Exception as e:
            print(f"[Backtest] {ticker} {year}년 FDR 조회 실패: {e}")
        return None

    def _pykrx_fetch() -> Optional[int]:
        try:
            from pykrx import stock
            df = stock.get_market_ohlcv(f"{year}1201", f"{year}1231", ticker)
            if df is not None and not df.empty:
                return int(df["종가"].iloc[-1])
        except Exception as e:
            print(f"[Backtest] {ticker} {year}년 pykrx 조회 실패: {e}")
        return None

    # FinanceDataReader 먼저 시도 (더 안정적)
    price = await asyncio.to_thread(_fdr_fetch)
    if price:
        return price

    # pykrx fallback (1초 대기 후)
    await asyncio.sleep(1)
    return await asyncio.to_thread(_pykrx_fetch)


async def run_backtest(
    ticker: str,
    base_year: int,
    hold_years: int = 5,
) -> dict:
    """
    1. base_year 기준으로 당시 재무데이터(base_year 이전)만 사용해서 스코어 계산
    2. base_year 당시 주가 조회
    3. base_year + hold_years 주가 조회
    4. 실제 수익률 계산
    5. 당시 스코어 vs 실제 수익률 비교
    """
    current_year = datetime.date.today().year

    if base_year >= current_year:
        return {"error": "base_year는 현재 연도보다 이전이어야 합니다."}
    if base_year + hold_years > current_year:
        return {"error": f"보유 기간({hold_years}년) 종료 시점({base_year + hold_years})이 아직 미래입니다."}

    # corp_code 조회
    corp_code, resolved_ticker = await get_corp_code(ticker)
    if not corp_code:
        return {"error": "DART에서 종목을 찾을 수 없습니다."}

    ticker = resolved_ticker

    # base_year 기준 과거 재무데이터 수집 (end_year 지정으로 해당 연도 기준 12년치)
    historical_fins = await fetch_yearly_financials(corp_code, ticker, years=12, end_year=base_year)

    if not historical_fins:
        return {"error": f"{base_year}년 이전 재무 데이터가 없습니다."}

    # 당시 스코어 계산 (당시 밸류에이션 없으므로 per/pbr/dividend 생략)
    historical_score = calculate_tenbagger_score(historical_fins)

    # 주가 조회 (병렬)
    price_base, price_end = await asyncio.gather(
        _get_price_at(ticker, base_year),
        _get_price_at(ticker, base_year + hold_years),
    )

    # 수익률 계산
    actual_return = None
    if price_base and price_end and price_base > 0:
        actual_return = round((price_end / price_base - 1) * 100, 1)

    is_tenbagger_actual = actual_return is not None and actual_return >= 900

    return {
        "ticker": ticker,
        "base_year": base_year,
        "hold_years": hold_years,
        "end_year": base_year + hold_years,
        "historical_financials_count": len(historical_fins),
        "score_at_base_year": historical_score,
        "price_at_base_year": price_base,
        "price_at_end_year": price_end,
        "actual_return_pct": actual_return,
        "is_tenbagger_actual": is_tenbagger_actual,
        "predicted_grade": historical_score.get("grade"),
        "prediction_correct": (
            (historical_score.get("grade") == "TENBAGGER" and actual_return >= 20)
            or (historical_score.get("grade") == "COMPOUNDER" and actual_return >= 10)
            or (historical_score.get("grade") == "WATCHLIST" and actual_return >= 0)
            or (historical_score.get("grade") == "AVOID" and actual_return < 0)
        ) if actual_return is not None else None,
    }


async def run_market_backtest(
    base_year: int,
    hold_years: int = 5,
    limit: int = 50,
) -> dict:
    """
    DB에 저장된 종목들을 대상으로 시장 전체 백테스트
    (ETL이 먼저 실행된 이후에 의미있는 결과 출력)
    """
    from sqlalchemy import text
    from app.core.database import SessionLocal

    sql = text("SELECT DISTINCT ticker FROM scores ORDER BY total_score DESC LIMIT :limit")
    with SessionLocal() as session:
        rows = session.execute(sql, {"limit": limit}).fetchall()
        tickers = [r[0] for r in rows]

    if not tickers:
        return {"error": "스크리너 데이터가 없습니다. ETL을 먼저 실행해 주세요."}

    results = []
    for t in tickers:
        try:
            r = await run_backtest(t, base_year, hold_years)
            if "error" not in r:
                results.append(r)
            await asyncio.sleep(0.3)
        except Exception as e:
            print(f"[Backtest] {t} 실패: {e}")

    if not results:
        return {"error": "백테스트 결과가 없습니다."}

    from collections import defaultdict
    grade_stats: dict = defaultdict(lambda: {"returns": [], "tenbagger_count": 0, "count": 0})

    for r in results:
        grade = r.get("predicted_grade", "UNKNOWN")
        ret = r.get("actual_return_pct")
        grade_stats[grade]["count"] += 1
        if ret is not None:
            grade_stats[grade]["returns"].append(ret)
        if r.get("is_tenbagger_actual"):
            grade_stats[grade]["tenbagger_count"] += 1

    summary = {}
    for grade, stat in grade_stats.items():
        rets = stat["returns"]
        summary[grade] = {
            "count": stat["count"],
            "avg_return": round(sum(rets) / len(rets), 1) if rets else None,
            "tenbagger_count": stat["tenbagger_count"],
            "tenbagger_rate": round(stat["tenbagger_count"] / stat["count"] * 100, 1) if stat["count"] > 0 else 0,
        }

    return {
        "base_year": base_year,
        "hold_years": hold_years,
        "total_companies": len(results),
        "grade_summary": summary,
        "details": results,
    }

async def generate_backtest_explanation(
    ticker: str,
    name: str,
    base_year: int,
    end_year: int,
    hold_years: int,
    predicted_grade: str,
    actual_return_pct,
    score_detail: dict,
    prediction_correct,
) -> dict:
    """GPT-4o-mini로 백테스트 결과(적중/빗나감) AI 설명 생성"""
    import os as _os
    from app.core.config import settings
    openai_key = settings.openai_api_key or _os.environ.get("OPENAI_API_KEY", "")
    if not openai_key:
        return {"explanation": "OPENAI_API_KEY가 설정되지 않아 AI 설명을 제공할 수 없습니다.", "improvement_hint": ""}

    ret_str = f"{actual_return_pct:+.1f}%" if actual_return_pct is not None else "데이터 없음"
    correct_str = "적중" if prediction_correct else "빗나감" if prediction_correct is False else "미확인"

    grade_map = {
        "TENBAGGER": "텐배거 후보(최우량)",
        "COMPOUNDER": "우량 성장주",
        "WATCHLIST": "관심 종목",
        "AVOID": "회피(저평가 위험)",
    }
    grade_label = grade_map.get(predicted_grade, predicted_grade)

    score_lines = []
    for k, label in [
        ("growth_score", "성장성"),
        ("stability_score", "안정성"),
        ("cashflow_score", "현금흐름"),
        ("dividend_score", "배당정책"),
        ("consistency_score", "일관성"),
    ]:
        v = score_detail.get(k)
        if v is not None:
            score_lines.append(f"- {label}: {v:.1f}/10")

    prompt = (
        f"한국 주식 백테스트 결과를 분석해줘.\n\n"
        f"[종목] {name or ticker} ({ticker})\n"
        f"[분석 기준] {base_year}년 재무데이터 기준 → {end_year}년까지 {hold_years}년 보유\n"
        f"[시스템 예측] {grade_label} (총점 {score_detail.get('total_score', '-')}/10)\n"
        f"[카테고리 점수]\n" + "\n".join(score_lines) + "\n"
        f"[실제 수익률] {ret_str}\n"
        f"[예측 결과] {correct_str}\n\n"
        f"아래 두 가지를 간결하게 한국어로 설명해줘:\n"
        f"1. **왜 {base_year}년 당시 이 스코어가 나왔는가** (점수 구성 해석)\n"
        f"2. **왜 실제 수익률이 예측과 달랐는가** (산업 사이클, 정책 수혜, 테마, 경기 등 재무 외 요인 포함)\n"
        f"마지막에 **이 케이스에서 스코어링 시스템이 배울 점** 한 줄 추가.\n"
        f"전체 250자 이내, 이모지 적절히 사용, 투자자가 읽기 좋게."
    )

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=openai_key, timeout=20.0)
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=600,
                temperature=0.4,
                messages=[
                    {"role": "system", "content": "한국 주식 장기투자 전문 애널리스트. 백테스트 결과를 명확하고 간결하게 설명."},
                    {"role": "user", "content": prompt},
                ],
            ),
            timeout=25.0,
        )
        text = resp.choices[0].message.content.strip()
        return {"explanation": text, "ticker": ticker, "base_year": base_year}
    except Exception as e:
        print(f"[BACKTEST_EXPLAIN] AI 실패: {type(e).__name__}: {e}")
        return {
            "explanation": f"AI 설명 생성 실패: {type(e).__name__}",
            "ticker": ticker,
            "base_year": base_year,
        }
