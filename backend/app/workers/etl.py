"""
전체 시장 ETL 워커
KOSPI + KOSDAQ 전체 종목을 순차 분석 후 DB에 저장

실행 방법:
  docker exec -it tenbagger_api python -m app.workers.etl
  docker exec -it tenbagger_api python -m app.workers.etl --market KOSPI
  docker exec -it tenbagger_api python -m app.workers.etl --limit 100 --skip-existing
"""
import asyncio
import datetime
import argparse
import sys
from pykrx import stock

from app.infra.clients.dart_client import (
    get_corp_code, fetch_yearly_financials, get_all_financial_indices
)
from app.domain.scoring import calculate_tenbagger_score, calculate_score_from_indices
from app.infra.repositories.company_repo import (
    save_company, save_financials, save_score, get_score_cached
)


async def process_ticker(ticker: str, name: str, market: str, skip_existing: bool = False) -> dict:
    """단일 종목 분석 후 DB 저장"""
    # 이미 분석된 종목이면 건너뜀
    if skip_existing and get_score_cached(ticker):
        return {"ticker": ticker, "status": "skipped", "score": None}

    try:
        # DART corp_code 조회
        corp_code, resolved_ticker = await get_corp_code(ticker)
        if not corp_code:
            return {"ticker": ticker, "status": "no_dart_code", "score": None}

        score = None
        financials = []
        current_year = datetime.datetime.today().year

        # ── 전략 1: DART 재무지표 API (빠름, 전처리 완료) ──────────────────────
        try:
            idx_data = await get_all_financial_indices(corp_code, current_year - 1)
            idx_list = idx_data.get("list", [])
            if len(idx_list) >= 5:  # 최소 5개 지표가 있어야 유효
                score = calculate_score_from_indices(idx_list)
                print(f"  [IDX] {name} → {score.get('total_score', 0):.1f}점")
        except Exception as idx_err:
            print(f"  [IDX] {ticker} 지표 API 실패: {idx_err}")

        # ── 전략 2: 원시 재무제표 파싱 (fallback) ──────────────────────────────
        if score is None or "error" in score:
            financials = await fetch_yearly_financials(corp_code, resolved_ticker, years=8)
            if not financials:
                return {"ticker": ticker, "status": "no_financials", "score": None}

            per, pbr, dividend_yield = None, None, None
            try:
                today = datetime.datetime.today().strftime("%Y%m%d")
                past  = (datetime.datetime.today() - datetime.timedelta(days=10)).strftime("%Y%m%d")
                df_fund = stock.get_market_fundamental(past, today, resolved_ticker)
                if not df_fund.empty:
                    per           = float(df_fund['PER'].iloc[-1])
                    pbr           = float(df_fund['PBR'].iloc[-1])
                    dividend_yield= float(df_fund['DIV'].iloc[-1])
            except Exception:
                pass

            score = calculate_tenbagger_score(financials, dividend_yield, per, pbr)

        # ── 주가 / 시가총액 수집 (공통) ────────────────────────────────────────
        close, market_cap = None, None
        try:
            today = datetime.datetime.today().strftime("%Y%m%d")
            past  = (datetime.datetime.today() - datetime.timedelta(days=10)).strftime("%Y%m%d")
            df_ohlcv = stock.get_market_ohlcv(past, today, resolved_ticker)
            if not df_ohlcv.empty:
                close = int(df_ohlcv['종가'].iloc[-1])
            df_cap = stock.get_market_cap(past, today, resolved_ticker)
            if not df_cap.empty:
                market_cap = int(df_cap['시가총액'].iloc[-1])
        except Exception:
            pass

        # DB 저장
        company_id = save_company(
            ticker=resolved_ticker,
            name=name,
            market=market,
            dart_code=corp_code,
        )
        save_financials(company_id, financials)
        save_score(
            company_id=company_id,
            ticker=resolved_ticker,
            name=name,
            market=market,
            score=score,
            close=close,
            market_cap=market_cap,
        )

        return {
            "ticker": resolved_ticker,
            "name": name,
            "status": "ok",
            "score": score.get("total_score"),
            "grade": score.get("grade"),
        }

    except Exception as e:
        return {"ticker": ticker, "status": "error", "error": str(e), "score": None}


def _load_market_tickers_fdr(markets: list[str]) -> list[tuple[str, str, str]]:
    """FinanceDataReader로 KOSPI/KOSDAQ 종목 목록 조회"""
    try:
        import FinanceDataReader as fdr
        results = []
        for mkt in markets:
            df = fdr.StockListing(mkt)
            if df is None or df.empty:
                print(f"[ETL] FinanceDataReader: {mkt} 종목 없음")
                continue
            # 컬럼명 통일 (버전별 차이 대응)
            code_col = next((c for c in df.columns if c in ('Code', 'Symbol', 'code', 'symbol')), None)
            name_col = next((c for c in df.columns if c in ('Name', 'name', 'Corp', 'corp')), None)
            if not code_col or not name_col:
                print(f"[ETL] FinanceDataReader: {mkt} 컬럼 파싱 실패 → {list(df.columns)}")
                continue
            for _, row in df.iterrows():
                code = str(row[code_col]).zfill(6)
                name = str(row[name_col])
                results.append((code, name, mkt))
            print(f"[ETL] FinanceDataReader: {mkt} {len(df)}개 로드 완료")
        return results
    except ImportError:
        print("[ETL] FinanceDataReader 미설치 → pip install finance-datareader")
        return []
    except Exception as e:
        print(f"[ETL] FinanceDataReader 실패: {e}")
        return []


async def run_etl(markets: list[str], limit: int = None, skip_existing: bool = False):
    """전체 종목 ETL 실행"""
    print(f"\n{'='*60}")
    print(f"  텐배거 헌터 ETL 시작")
    print(f"  대상 시장: {', '.join(markets)}")
    print(f"  스킵 옵션: {'기존 분석 종목 건너뜀' if skip_existing else '전체 재분석'}")
    print(f"{'='*60}\n")

    # 1순위: FinanceDataReader로 KOSPI/KOSDAQ 종목 로드
    all_tickers = _load_market_tickers_fdr(markets)

    # 2순위: FDR 실패 시 pykrx 시도
    if not all_tickers:
        print("[ETL] FinanceDataReader 실패, pykrx 시도...")
        try:
            past_str = (datetime.datetime.today() - datetime.timedelta(days=5)).strftime("%Y%m%d")
            for mkt in markets:
                tickers = stock.get_market_ticker_list(date=past_str, market=mkt)
                if tickers:
                    for t in tickers:
                        name = stock.get_market_ticker_name(t) or t
                        all_tickers.append((t, name, mkt))
            print(f"[ETL] pykrx 로드 완료: {len(all_tickers)}개")
        except Exception as e:
            print(f"[ETL] pykrx 실패: {e}")

    # 3순위: 둘 다 실패 시 DART 캐시 사용
    if not all_tickers:
        print("[ETL] pykrx도 실패, DART 캐시에서 종목 로드...")
        from app.infra.clients.dart_client import _load_corp_cache
        dart_items = await _load_corp_cache()
        for item in dart_items:
            all_tickers.append((item["stock_code"], item["corp_name"], markets[0]))
        print(f"[ETL] DART 캐시 로드: {len(all_tickers)}개")

    if not all_tickers:
        print("[ETL] 종목 로드 실패. 종료합니다.")
        return

    if limit:
        all_tickers = all_tickers[:limit]

    total = len(all_tickers)
    print(f"[ETL] 총 {total}개 종목 분석 시작...\n")

    success = skip = error = 0
    start_time = datetime.datetime.now()

    for i, (ticker, name, market) in enumerate(all_tickers, 1):
        result = await process_ticker(ticker, name, market, skip_existing)

        status = result["status"]
        score_str = f"{result['score']:.1f}점" if result.get("score") is not None else "N/A"
        grade_str = f" [{result.get('grade', '')}]" if result.get("grade") else ""

        if status == "ok":
            success += 1
            print(f"[{i:4d}/{total}] ✅ {name:<15} {ticker}  →  {score_str}{grade_str}")
        elif status == "skipped":
            skip += 1
            print(f"[{i:4d}/{total}] ⏭️  {name:<15} {ticker}  →  캐시 히트, 스킵")
        else:
            error += 1
            err_msg = result.get("error", status)
            print(f"[{i:4d}/{total}] ❌ {name:<15} {ticker}  →  {err_msg[:50]}")

        # DART rate limit 방지
        await asyncio.sleep(0.4)

        # 진행률 표시 (100개마다)
        if i % 100 == 0:
            elapsed = (datetime.datetime.now() - start_time).seconds
            eta = int(elapsed / i * (total - i))
            print(f"\n  ── 진행률: {i}/{total} | 성공:{success} 스킵:{skip} 실패:{error} | "
                  f"경과:{elapsed//60}분 | 예상 잔여:{eta//60}분 ──\n")

    elapsed_total = (datetime.datetime.now() - start_time).seconds
    print(f"\n{'='*60}")
    print(f"  ETL 완료!")
    print(f"  성공: {success}개 | 스킵: {skip}개 | 실패: {error}개")
    print(f"  총 소요 시간: {elapsed_total//60}분 {elapsed_total%60}초")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="텐배거 헌터 ETL 워커")
    parser.add_argument("--market", choices=["KOSPI", "KOSDAQ", "ALL"], default="ALL",
                        help="분석 대상 시장 (default: ALL)")
    parser.add_argument("--limit", type=int, default=None,
                        help="분석 종목 수 제한 (테스트용)")
    parser.add_argument("--skip-existing", action="store_true",
                        help="24시간 내 분석된 종목 건너뜀")
    args = parser.parse_args()

    markets = ["KOSPI", "KOSDAQ"] if args.market == "ALL" else [args.market]

    asyncio.run(run_etl(
        markets=markets,
        limit=args.limit,
        skip_existing=args.skip_existing,
    ))
