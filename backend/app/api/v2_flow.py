"""
GET /api/v2/flow/{ticker}  — 기관·외국인 수급 데이터 (Naver Finance 기반)
  최근 N거래일 기관/외국인/개인 순매수 추이 + 요약 신호
  pykrx 대신 Naver Finance investors.naver 사용 (Railway IP 차단 우회)
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
        start = end - datetime.timedelta(days=days * 2 + 20)  # 주말·공휴일 여유

        url = "https://finance.naver.com/item/investors.naver"
        params = {
            "code":      ticker,
            "startDate": start.strftime("%Y%m%d"),
            "endDate":   end.strftime("%Y%m%d"),
            "search":    "",
        }

        resp = requests.get(url, params=params, headers=_HEADERS, timeout=12)
        resp.encoding = "euc-kr"

        soup  = BeautifulSoup(resp.text, "html.parser")
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
                "retail":      _parse_int(tds[1]),   # 개인
                "foreign":     _parse_int(tds[2]),   # 외국인
                "institution": _parse_int(tds[3]),   # 기관합계
            })

        if not rows:
            return None

        rows = rows[:days]   # 최신 N건 (Naver는 최신순 반환)
        rows.reverse()       # 오래된 순서로 정렬

        dates            = [r["date"]        for r in rows]
        foreign_vals     = [r["foreign"]     for r in rows]
        institution_vals = [r["institution"] for r in rows]
        retail_vals      = [r["retail"]      for r in rows]

        # 5일·20일 합산
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


def _fmt_shares(v: int) -> str:
    """만주 단위 포맷 (단위: 주)"""
    man = v / 10_000
    if abs(man) >= 10_000:
        return f"{man/10_000:.1f}억주"
    if abs(man) >= 1_000:
        return f"{man/1_000:.1f}천만주"
    return f"{man:.0f}만주"


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

    resp = {
        "ticker":      ticker,
        "days":        days,
        "dates":       data["dates"],
        "institution": data.get("institution", []),
        "foreign":     data.get("foreign", []),
        "retail":      data.get("retail", []),
        "summary":     data["summary"],
        "signal":      data["signal"],
        "signal_color": data["signal_color"],
        "smart_money": {
            "5d_label":  _fmt_shares(data["smart_5d"]),
            "20d_label": _fmt_shares(data["smart_20d"]),
            "5d":        data["smart_5d"],
            "20d":       data["smart_20d"],
        },
    }
    _CACHE[cache_key] = {"ts": datetime.datetime.now(), "data": resp}
    return resp
