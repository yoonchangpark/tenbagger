"""
카카오 i 오픈빌더 웹훅 엔드포인트 v2.0
"""

import os
import re
import datetime
import asyncio
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text
from app.core.database import SessionLocal

router = APIRouter()

BASE_URL = os.environ.get("SERVICE_URL", "http://localhost:8000")
_market_cache: dict = {}


class KakaoUserRequest(BaseModel):
    utterance: str = ""

class KakaoRequest(BaseModel):
    userRequest: KakaoUserRequest = KakaoUserRequest()


def find_stock_by_query(query: str) -> dict | None:
    with SessionLocal() as session:
        row = session.execute(text("""
            SELECT ticker, name, market, total_score, grade,
                   growth_score, stability_score, cashflow_score,
                   revenue_cagr_5y, avg_roe_5y, close
            FROM scores
            WHERE LOWER(name) LIKE LOWER(:q) OR ticker = :q2
            ORDER BY total_score DESC
            LIMIT 1
        """), {"q": f"%{query}%", "q2": query.upper()}).fetchone()
        return dict(row._mapping) if row else None


def get_top_tenbaggers(limit: int = 5) -> list[dict]:
    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT ticker, name, total_score, grade,
                   growth_score, revenue_cagr_5y, avg_roe_5y
            FROM scores
            WHERE grade IN ('TENBAGGER', 'COMPOUNDER')
            ORDER BY total_score DESC
            LIMIT :limit
        """), {"limit": limit}).fetchall()
        return [dict(r._mapping) for r in rows]


def _main_quick_replies() -> list:
    return [
        {"action": "message", "label": "📌 종목분석", "messageText": "종목분석"},
        {"action": "message", "label": "⭐ 텐배거 추천", "messageText": "텐배거추천"},
        {"action": "message", "label": "📊 시장 현황", "messageText": "시장현황"},
    ]


def _simple_text(msg: str, quick_replies: list = None) -> dict:
    # 카카오 simpleText 최대 1000자 제한
    if len(msg) > 990:
        msg = msg[:987] + "..."
    result = {
        "version": "2.0",
        "template": {"outputs": [{"simpleText": {"text": msg}}]},
    }
    if quick_replies:
        result["template"]["quickReplies"] = quick_replies
    return result


def _basic_card(title: str, desc: str, btn_label: str, btn_url: str,
                quick_replies: list = None) -> dict:
    result = {
        "version": "2.0",
        "template": {
            "outputs": [{
                "basicCard": {
                    "title": title,
                    "description": desc,
                    "buttons": [{"action": "webLink", "label": btn_label, "webLinkUrl": btn_url}],
                }
            }]
        },
    }
    if quick_replies:
        result["template"]["quickReplies"] = quick_replies
    return result


def _list_card(header: str, items: list[dict], btn_label: str, btn_url: str,
               quick_replies: list = None) -> dict:
    result = {
        "version": "2.0",
        "template": {
            "outputs": [{
                "listCard": {
                    "header": {"title": header},
                    "items": items,
                    "buttons": [{"action": "webLink", "label": btn_label, "webLinkUrl": btn_url}],
                }
            }]
        },
    }
    if quick_replies:
        result["template"]["quickReplies"] = quick_replies
    return result


def _handle_help() -> dict:
    msg = (
        "🚀 텐배거 헌터 챗봇입니다!\n\n"
        "아래 메뉴를 선택하거나\n"
        "직접 종목명을 입력하세요.\n\n"
        "예) 삼성전자 / 005930"
    )
    return _simple_text(msg, _main_quick_replies())


def _handle_stock_analysis_prompt() -> dict:
    msg = (
        "📌 종목 분석\n\n"
        "분석할 종목명 또는 티커를\n"
        "입력해주세요.\n\n"
        "예)\n"
        "• 삼성전자\n"
        "• 005930\n"
        "• LG화학\n"
        "• 051910"
    )
    return _simple_text(msg)


def _handle_stock_query(utterance: str) -> dict:
    query = re.sub(r'(분석해줘|분석해|어때|알려줘|검색|조회|스코어|점수|보여줘)', '', utterance).strip()
    if not query:
        return _handle_stock_analysis_prompt()

    stock = find_stock_by_query(query)
    if not stock:
        return _simple_text(
            f"'{query}' 종목을 찾지 못했습니다.\n"
            "정확한 종목명이나 티커(예: 005930)로\n"
            "다시 입력해주세요.",
            _main_quick_replies()
        )

    grade_emoji = {"TENBAGGER": "⭐", "COMPOUNDER": "🟢", "WATCHLIST": "🟡", "AVOID": "🔴"}
    emoji = grade_emoji.get(stock["grade"], "")
    close_str = f"₩{int(stock['close']):,}" if stock.get("close") else "-"
    cagr = f"{stock['revenue_cagr_5y']:.1f}%" if stock.get("revenue_cagr_5y") else "-"
    roe = f"{stock['avg_roe_5y']:.1f}%" if stock.get("avg_roe_5y") else "-"

    # 카카오 basicCard 제한: title 35자, description 230자
    title = f"{stock['name']} ({stock['ticker']})"[:35]
    desc = (
        f"{emoji} {stock['grade']} | {stock['total_score']:.2f}점\n"
        f"성장 {stock['growth_score']:.1f} | 안정 {stock['stability_score']:.1f}\n"
        f"매출CAGR: {cagr} | ROE: {roe}\n"
        f"현재가: {close_str}"
    )[:230]
    url = f"{BASE_URL}/?ticker={stock['ticker']}"
    return _basic_card(title, desc, "📊 상세 분석 보기", url, _main_quick_replies())


def _handle_tenbagger_list() -> dict:
    stocks = get_top_tenbaggers(5)
    if not stocks:
        return _simple_text("현재 TENBAGGER 등급 종목이 없습니다.", _main_quick_replies())

    grade_emoji = {"TENBAGGER": "⭐", "COMPOUNDER": "🟢"}
    items = []
    for s in stocks:
        emoji = grade_emoji.get(s["grade"], "")
        cagr = f"+{s['revenue_cagr_5y']:.1f}%" if s.get("revenue_cagr_5y") else "-"
        # 카카오 listCard 제한: title 35자, description 16자
        title = f"{emoji} {s['name']} ({s['ticker']})"[:35]
        desc = f"{s['total_score']:.1f}점 | {cagr}"[:16]
        items.append({
            "title": title,
            "description": desc,
        })

    return _list_card(
        "⭐ 텐배거 TOP 5",
        items,
        "📈 TOP10 스크리너",
        f"{BASE_URL}/screener.html",
        _main_quick_replies()
    )


def _fetch_yahoo(symbol: str) -> dict | None:
    """Yahoo Finance에서 지수/종목 현재가 + 등락률 조회"""
    try:
        import requests
        resp = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"interval": "1d", "range": "2d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=4,
        )
        meta = resp.json()["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice", 0)
        prev  = meta.get("chartPreviousClose") or meta.get("previousClose", price)
        chg   = round((price - prev) / prev * 100, 2) if prev else None
        return {"close": price, "change_pct": chg}
    except Exception as e:
        print(f"[YAHOO] {symbol} 오류: {e}")
        return None


def _fetch_market_indices_sync() -> dict:
    """KOSPI, KOSDAQ, NASDAQ, S&P500, USD/KRW 한번에 조회"""
    import concurrent.futures
    symbols = {
        "kospi":  "^KS11",
        "kosdaq": "^KQ11",
        "nasdaq": "^IXIC",
        "sp500":  "^GSPC",
        "usdkrw": "USDKRW=X",
    }
    result = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(_fetch_yahoo, sym): key for key, sym in symbols.items()}
        for f in concurrent.futures.as_completed(futures):
            key = futures[f]
            val = f.result()
            if val:
                result[key] = val
    return result


def _fetch_usdkrw_sync() -> float | None:
    data = _fetch_yahoo("USDKRW=X")
    return round(data["close"], 1) if data else None


def _get_top_stocks_sync() -> list[dict]:
    """DB에서 오늘 스코어 상위 3개 종목 조회"""
    try:
        with SessionLocal() as session:
            rows = session.execute(text("""
                SELECT name, ticker, total_score, grade
                FROM scores
                WHERE grade IN ('TENBAGGER','COMPOUNDER')
                ORDER BY total_score DESC
                LIMIT 3
            """)).fetchall()
            return [dict(r._mapping) for r in rows]
    except Exception:
        return []


def _fetch_dart_disclosures_sync() -> list:
    try:
        import requests
        from app.core.config import settings
        dart_key = settings.dart_api_key or os.environ.get("DART_API_KEY", "")
        if not dart_key:
            return []
        today = datetime.date.today()
        week_ago = today - datetime.timedelta(days=7)
        resp = requests.get(
            "https://opendart.fss.or.kr/api/list.json",
            params={
                "crtfc_key": dart_key,
                "bgn_de": week_ago.strftime("%Y%m%d"),
                "end_de": today.strftime("%Y%m%d"),
                "pblntf_ty": "B",
                "page_count": 10,
            },
            timeout=5,
        )
        items = resp.json().get("list", [])
        return [
            {
                "date": i.get("rcept_dt", ""),
                "corp": i.get("corp_name", ""),
                "title": i.get("report_nm", ""),
            }
            for i in items[:5]
        ]
    except Exception as e:
        print(f"[DART] 공시 조회 오류: {e}")
        return []


def _fmt_idx(d: dict | None, is_price: bool = False) -> str:
    if not d: return "N/A"
    price = d["close"]
    chg = d.get("change_pct")
    arrow = ("▲" if chg >= 0 else "▼") if chg is not None else ""
    chg_str = f" {arrow}{chg:+.2f}%" if chg is not None else ""
    if is_price:
        return f"{price:,.1f}{chg_str}"
    return f"{int(price):,}{chg_str}"


def _format_market_text_fallback(indices: dict, usdkrw: float | None,
                                  disclosures: list, top_stocks: list) -> str:
    today = datetime.date.today().strftime("%Y.%m.%d")
    lines = [f"📊 {today} 시장 현황\n"]

    # 국내 지수
    kospi  = indices.get("kospi")
    kosdaq = indices.get("kosdaq")
    if kospi:  lines.append(f"🔵 KOSPI  {_fmt_idx(kospi)}")
    if kosdaq: lines.append(f"🟢 KOSDAQ {_fmt_idx(kosdaq)}")

    # 해외 지수
    nasdaq = indices.get("nasdaq")
    sp500  = indices.get("sp500")
    if nasdaq or sp500:
        lines.append("")
        if nasdaq: lines.append(f"🌐 NASDAQ {_fmt_idx(nasdaq)}")
        if sp500:  lines.append(f"🌐 S&P500 {_fmt_idx(sp500)}")

    # 환율
    usd = indices.get("usdkrw") or ({"close": usdkrw} if usdkrw else None)
    if usd: lines.append(f"💵 USD/KRW {_fmt_idx(usd, is_price=True)}원")

    # 추천 종목 TOP3
    if top_stocks:
        lines.append("\n⭐ 주목 종목 TOP3")
        for s in top_stocks:
            lines.append(f"• {s['name']} ({s['ticker']}) {s['total_score']:.1f}점")

    # 주요 공시
    if disclosures:
        lines.append("\n📋 주요 공시")
        for d in disclosures[:3]:
            lines.append(f"• {d['corp']}: {d['title'][:20]}")

    return "\n".join(lines)


async def _gpt_market_summary(indices: dict, disclosures: list, top_stocks: list) -> str:
    """GPT는 시장 코멘트만 생성. 종목 섹션은 실제 DB 데이터로 직접 추가 (환각 방지)."""
    from app.core.config import settings
    openai_key = settings.openai_api_key or os.environ.get("OPENAI_API_KEY", "")
    if not openai_key:
        return _format_market_text_fallback(indices, None, disclosures, top_stocks)

    today = datetime.date.today().strftime("%Y년 %m월 %d일")
    today_short = datetime.date.today().strftime("%Y.%m.%d")

    idx_lines = []
    for key, label in [("kospi","KOSPI"),("kosdaq","KOSDAQ"),("nasdaq","NASDAQ"),("sp500","S&P500")]:
        d = indices.get(key)
        if d:
            chg = f"{d['change_pct']:+.2f}%" if d.get("change_pct") is not None else ""
            idx_lines.append(f"- {label}: {int(d['close']):,} ({chg})")
    usd = indices.get("usdkrw")
    if usd:
        idx_lines.append(f"- USD/KRW: {usd['close']:,.1f}원")

    disc_str = "\n".join(f"• {d['corp']}: {d['title']}" for d in disclosures) or "없음"

    # ★ GPT에게 종목 추천 절대 금지 — 실제 데이터만 표시
    prompt = (
        f"오늘({today}) 주식시장 현황을 카카오톡 메시지로 요약해줘.\n\n"
        f"[지수]\n" + "\n".join(idx_lines) + "\n\n"
        f"[주요 공시]\n{disc_str}\n\n"
        f"장기투자자 관점에서 핵심만 요약. 이모지 활용. 전체 250자 이내.\n"
        f"종목명은 절대 언급하지 마. 지수 흐름과 시장 코멘트만 작성.\n\n"
        f"형식 (정확히 따를 것):\n"
        f"📊 {today_short} 시장\n"
        f"🔵 KOSPI: X,XXX (±X.XX%) / 🟢 KOSDAQ: X,XXX (±X.XX%) / 🌐 NASDAQ: XX,XXX (±X.XX%)\n"
        f"💵 USD/KRW: X,XXX원\n\n"
        f"💡 시장 코멘트\n"
        f"2~3줄 흐름 해석"
    )

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=openai_key)
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=350,
                temperature=0.3,
                messages=[
                    {"role": "system", "content": "한국 주식 장기투자 전문가. 카카오톡 형식으로 간결하게 답변. 종목명 언급 금지."},
                    {"role": "user", "content": prompt},
                ],
            ),
            timeout=10.0,
        )
        gpt_text = resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[GPT_MARKET] 오류: {e}")
        gpt_text = _format_market_text_fallback(indices, None, disclosures, [])

    # ★ 종목 섹션은 실제 DB 데이터로만 직접 붙임 (GPT 환각 방지)
    if top_stocks:
        stock_lines = ["\n⭐ 주목 종목 (텐배거 스코어 TOP3)"]
        for s in top_stocks:
            score = f"{s['total_score']:.1f}점"
            stock_lines.append(f"• {s['name']} ({s['ticker']}) {score}")
        gpt_text = gpt_text + "\n" + "\n".join(stock_lines)

    return gpt_text


async def _handle_market_ai() -> dict:
    cache_key = f"market_{datetime.date.today().isoformat()}_{datetime.datetime.now().hour}"
    if cache_key in _market_cache:
        return _market_cache[cache_key]

    try:
        indices, disclosures, top_stocks = await asyncio.wait_for(
            asyncio.gather(
                asyncio.to_thread(_fetch_market_indices_sync),
                asyncio.to_thread(_fetch_dart_disclosures_sync),
                asyncio.to_thread(_get_top_stocks_sync),
            ),
            timeout=8.0,
        )
    except asyncio.TimeoutError:
        print("[MARKET_AI] 데이터 수집 타임아웃, 폴백 사용")
        indices, disclosures, top_stocks = {}, [], []

    analysis = await _gpt_market_summary(indices, disclosures, top_stocks)
    result = _simple_text(analysis, _main_quick_replies())
    _market_cache[cache_key] = result
    return result


@router.post("/webhook")
async def kakao_webhook(body: KakaoRequest):
    try:
        utterance = body.userRequest.utterance.strip()
        utt_lower = utterance.lower()

        if utt_lower in ["종목분석", "종목 분석"]:
            return _handle_stock_analysis_prompt()

        # 텐배거/추천: 단독 키워드만 매칭 (흔한 단어 "추천" 단독 제외)
        if any(kw in utt_lower for kw in ["텐배거추천", "텐배거 추천", "텐배거", "top5", "top10", "상위종목"]):
            return _handle_tenbagger_list()

        # 시장현황: 단독 키워드만 매칭 (흔한 단어 "시장" 단독 제외)
        if any(kw in utt_lower for kw in ["시장현황", "시장 현황", "오늘시장", "오늘 시장"]):
            return await _handle_market_ai()

        if any(kw in utt_lower for kw in ["도움말", "help", "사용법", "어떻게", "안녕", "시작", "hi", "ㅎㅇ"]):
            return _handle_help()

        if any(kw in utt_lower for kw in ["분석", "어때", "알려줘", "보여줘", "조회", "검색", "스코어", "점수"]) \
                or re.search(r'\d{6}', utterance):
            return _handle_stock_query(utterance)

        if len(utterance) >= 2:
            stock = find_stock_by_query(utterance)
            if stock:
                return _handle_stock_query(utterance)

        return _handle_help()

    except Exception as e:
        print(f"[KAKAO_WEBHOOK] 오류: {type(e).__name__}: {e}")
        return _simple_text(
            "일시적인 오류가 발생했습니다.\n잠시 후 다시 시도해주세요.",
            _main_quick_replies()
        )
