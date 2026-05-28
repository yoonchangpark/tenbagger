"""
GET /api/v2/flow/{ticker}  — 기관·외국인 수급 데이터 (Naver Finance 기반)
  최근 N거래일 기관/외국인/개인 순매수 추이 + 요약 신호
  단위: 주(株) → 현재가 × 주수 / 1억 = 억원 자동환산
"""
import datetime
import re
import requests
from fastapi import APIRouter, HTTPException, Query
from app.core.database import SessionLocal
from sqlalchemy import text

router = APIRouter(prefix="/api/v2/flow", tags=["flow"])

_CACHE: dict = {}
_CACHE_TTL_H = 4

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}


def _fetch_current_price(ticker: str) -> float | None:
    """
    현재가 조회. 순서대로 시도, 성공하면 즉시 반환.
    1. price_daily_cache DB  2. frgn.naver  3. Yahoo Finance  4. FinanceDataReader
    """
    # 1순위: DB price_daily_cache 최근 종가
    try:
        with SessionLocal() as session:
            row = session.execute(text("""
                SELECT close_price FROM price_daily_cache
                WHERE ticker = :t AND close_price > 0
                ORDER BY trade_date DESC LIMIT 1
            """), {"t": ticker}).fetchone()
        if row and row[0]:
            print(f"[FLOW] price DB hit: {ticker} = {row[0]}")
            return float(row[0])
    except Exception as e:
        print(f"[FLOW] DB price 실패 ({ticker}): {e}")

    # 2순위: frgn.naver 최신 종가 파싱
    try:
        from bs4 import BeautifulSoup
        resp = requests.get(
            "https://finance.naver.com/item/frgn.naver",
            params={"code": ticker}, headers=_HEADERS, timeout=8,
        )
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table", {"class": "type2"})
        if table:
            for tr in table.find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) >= 2:
                    price_txt = tds[1].get_text(strip=True).replace(",", "")
                    if re.match(r"^\d+$", price_txt) and int(price_txt) > 0:
                        print(f"[FLOW] price frgn.naver: {ticker} = {price_txt}")
                        return float(price_txt)
    except Exception as e:
        print(f"[FLOW] frgn.naver price 실패 ({ticker}): {e}")

    # 3순위: Yahoo Finance (KOSPI → .KS, KOSDAQ → .KQ 순서로 시도)
    for suffix in (".KS", ".KQ"):
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}{suffix}"
            resp = requests.get(url, params={"interval": "1d", "range": "1d"},
                                headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
            if resp.status_code == 200:
                price = resp.json()["chart"]["result"][0]["meta"]["regularMarketPrice"]
                if price and price > 0:
                    print(f"[FLOW] price Yahoo({suffix}): {ticker} = {price}")
                    return float(price)
        except Exception as e:
            print(f"[FLOW] Yahoo{suffix} price 실패 ({ticker}): {e}")

    # 4순위: FinanceDataReader
    try:
        import FinanceDataReader as fdr
        import datetime
        end   = datetime.date.today()
        start = end - datetime.timedelta(days=7)
        df = fdr.DataReader(ticker, start, end)
        if not df.empty:
            price = float(df["Close"].iloc[-1])
            if price > 0:
                print(f"[FLOW] price FDR: {ticker} = {price}")
                return price
    except Exception as e:
        print(f"[FLOW] FDR price 실패 ({ticker}): {e}")

    print(f"[FLOW] price 조회 전체 실패 ({ticker}) → 만주 표시로 fallback")
    return None


def _parse_int(cell) -> int:
    """네이버 파이낸스 셀 → 정수 (부호 포함, 단위: 주)"""
    text = cell.get_text(strip=True).replace(",", "").strip()
    if not text or text == "-":
        return 0
    negative = text.startswith("-")
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return 0
    return -int(digits) if negative else int(digits)


def _fetch_flow_naver(ticker: str, days: int) -> dict | None:
    """
    Naver Finance investors.naver에서 기관·외국인·개인 순매수 스크래핑.
    단위: 주(株). 충분한 날짜 범위를 요청해 영업일 기준 days개를 확보.
    """
    try:
        from bs4 import BeautifulSoup

        end   = datetime.date.today()
        start = end - datetime.timedelta(days=days * 2 + 20)

        url = "https://finance.naver.com/item/investors.naver"
        params = {
            "code":      ticker,
            "startDate": start.strftime("%Y%m%d"),
            "endDate":   end.strftime("%Y%m%d"),
            "search":    "",
        }

        resp = requests.get(url, params=params, headers=_HEADERS, timeout=12)
        resp.encoding = "euc-kr"

        soup  = __import__("bs4").BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table", {"class": "type2"})
        if table is None:
            return None

        rows = []
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue
            date_txt = tds[0].get_text(strip=True)
            if not re.match(r"\d{4}\.\d{2}\.\d{2}", date_txt):
                continue
            rows.append({
                "date":        date_txt,
                "retail":      _parse_int(tds[1]),
                "foreign":     _parse_int(tds[2]),
                "institution": _parse_int(tds[3]),
            })

        if not rows:
            return None

        rows = rows[:days]
        rows.reverse()

        dates            = [r["date"]        for r in rows]
        foreign_vals     = [r["foreign"]     for r in rows]
        institution_vals = [r["institution"] for r in rows]
        retail_vals      = [r["retail"]      for r in rows]

        f5  = sum(foreign_vals[-5:])      if len(foreign_vals)     >= 5  else sum(foreign_vals)
        i5  = sum(institution_vals[-5:])  if len(institution_vals) >= 5  else sum(institution_vals)
        f20 = sum(foreign_vals[-20:])     if len(foreign_vals)     >= 20 else sum(foreign_vals)
        i20 = sum(institution_vals[-20:]) if len(institution_vals) >= 20 else sum(institution_vals)

        smart_5d  = f5 + i5
        smart_20d = f20 + i20

        if smart_5d > 0 and smart_20d > 0:
            signal, signal_color = "매수 우위", "#10b981"
        elif smart_5d < 0 and smart_20d < 0:
            signal, signal_color = "매도 우위", "#ef4444"
        elif smart_5d > 0:
            signal, signal_color = "단기 유입", "#3b82f6"
        elif smart_5d < 0:
            signal, signal_color = "단기 이탈", "#f59e0b"
        else:
            signal, signal_color = "중립", "#9ca3af"

        return {
            "dates":       dates,
            "foreign":     foreign_vals,
            "institution": institution_vals,
            "retail":      retail_vals,
            "summary": {
                "foreign_5d":     f5,  "foreign_20d":     f20,
                "institution_5d": i5,  "institution_20d": i20,
            },
            "signal":       signal,
            "signal_color": signal_color,
            "smart_5d":     smart_5d,
            "smart_20d":    smart_20d,
        }

    except Exception as e:
        print(f"[FLOW] {ticker} Naver 스크래핑 오류: {e}")
        return None


def _to_bil(shares: int, price: float | None) -> float | None:
    """주수 × 현재가 / 1억 = 억원. price 없으면 None."""
    if price is None or price <= 0:
        return None
    return round(shares * price / 1e8, 1)


def _fmt_bil(v: float) -> str:
    """억원 단위 포맷"""
    if abs(v) >= 1000:
        return f"{v/1000:.1f}천억"
    return f"{v:+.0f}억"


@router.get("/{ticker}")
def get_flow(
    ticker: str,
    days: int = Query(20, ge=5, le=60, description="조회 거래일 수 (5~60)"),
):
    """기관·외국인 수급 데이터. 4시간 캐시."""
    cache_key = f"{ticker}_{days}"
    cached    = _CACHE.get(cache_key)
    if cached and (datetime.datetime.now() - cached["ts"]).seconds < _CACHE_TTL_H * 3600:
        return cached["data"]

    data = _fetch_flow_naver(ticker, days)

    if data is None:
        with SessionLocal() as session:
            row = session.execute(
                text("SELECT name FROM scores WHERE ticker = :t LIMIT 1"),
                {"t": ticker},
            ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="종목을 찾을 수 없습니다.")
        return {
            "ticker":  ticker,
            "no_data": True,
            "message": "수급 데이터를 가져올 수 없습니다 (Naver Finance 응답 없음)",
        }

    # 현재가로 억원 환산
    price = _fetch_current_price(ticker)

    def bil(shares: int) -> str | None:
        v = _to_bil(shares, price)
        return _fmt_bil(v) if v is not None else None

    summ = data["summary"]
    resp = {
        "ticker":        ticker,
        "days":          days,
        "dates":         data["dates"],
        "institution":   data.get("institution", []),
        "foreign":       data.get("foreign", []),
        "retail":        data.get("retail", []),
        "current_price": price,
        "summary": {
            # 주수 원본
            "foreign_5d":     summ["foreign_5d"],
            "foreign_20d":    summ["foreign_20d"],
            "institution_5d": summ["institution_5d"],
            "institution_20d":summ["institution_20d"],
            # 억원 환산 (price 없으면 null)
            "foreign_5d_bil":      bil(summ["foreign_5d"]),
            "foreign_20d_bil":     bil(summ["foreign_20d"]),
            "institution_5d_bil":  bil(summ["institution_5d"]),
            "institution_20d_bil": bil(summ["institution_20d"]),
        },
        "signal":       data["signal"],
        "signal_color": data["signal_color"],
        "smart_money": {
            "5d":        data["smart_5d"],
            "20d":       data["smart_20d"],
            "5d_label":  bil(data["smart_5d"])  or f"{data['smart_5d']//10000:+d}만주",
            "20d_label": bil(data["smart_20d"]) or f"{data['smart_20d']//10000:+d}만주",
        },
    }
    _CACHE[cache_key] = {"ts": datetime.datetime.now(), "data": resp}
    return resp
