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


def _fetch_market_indices_sync() -> dict:
    data = {}
    try:
        from pykrx import stock as pykrx_stock
        today = datetime.date.today()
        date_str = None
        for i in range(5):
            d = today - datetime.timedelta(days=i)
            if d.weekday() < 5:
                date_str = d.strftime("%Y%m%d")
                break
        if not date_str:
            return data
        for code, key in [("1001", "kospi"), ("2001", "kosdaq")]:
            try:
                df = pykrx_stock.get_index_ohlcv(date_str, date_str, code)
                if df is not None and not df.empty:
                    row = df.iloc[-1]
                    close_val = row.get("종가", row.get("Close", 0))
                    chg_val = row.get("등락률", row.get("Change", None))
                    data[key] = {
                        "close": int(close_val) if close_val else 0,
                        "change_pct": round(float(chg_val), 2) if chg_val is not None else None,
                    }
            except Exception as e:
                print(f"[MARKET] {key} 조회 오류: {e}")
    except Exception as e:
        print(f"[MARKET] pykrx import 오류: {e}")
    return data


def _fetch_usdkrw_sync() -> float | None:
    try:
        import requests
        resp = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/USDKRW=X",
            params={"interval": "1d", "range": "2d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=4,
        )
        data = resp.json()
        price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
        return round(float(price), 1)
    except Exception as e:
        print(f"[MARKET] 환율 조회 오류: {e}")
        return None


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


def _format_market_text_fallback(indices: dict, usdkrw: float | None,
                                  disclosures: list) -> str:
    today = datetime.date.today().strftime("%Y.%m.%d")
    lines = [f"📊 오늘의 시장 현황 ({today})\n"]
    kospi = indices.get("kospi")
    kosdaq = indices.get("kosdaq")
    if kospi:
        chg = f" ({kospi['change_pct']:+.2f}%)" if kospi.get("change_pct") is not None else ""
        lines.append(f"🔵 KOSPI: {kospi['close']:,}{chg}")
    if kosdaq:
        chg = f" ({kosdaq['change_pct']:+.2f}%)" if kosdaq.get("change_pct") is not None else ""
        lines.append(f"🟢 KOSDAQ: {kosdaq['close']:,}{chg}")
    if usdkrw:
        lines.append(f"💵 USD/KRW: {usdkrw:,.1f}원")
    if disclosures:
        lines.append("\n📋 최근 주요 공시")
        for d in disclosures[:4]:
            lines.append(f"• {d['corp']}: {d['title']}")
    return "\n".join(lines)


async def _gpt_market_summary(indices: dict, usdkrw: float | None,
                               disclosures: list) -> str:
    from app.core.config import settings
    openai_key = settings.openai_api_key or os.environ.get("OPENAI_API_KEY", "")
    if not openai_key:
        return _format_market_text_fallback(indices, usdkrw, disclosures)

    today = datetime.date.today().strftime("%Y년 %m월 %d일")
    kospi = indices.get("kospi", {})
    kosdaq = indices.get("kosdaq", {})

    def idx_str(d):
        if not d:
            return "N/A"
        chg = f" ({d['change_pct']:+.2f}%)" if d.get("change_pct") is not None else ""
        return f"{d['close']:,}{chg}"

    disc_str = "\n".join(
        f"• [{d['date']}] {d['corp']}: {d['title']}" for d in disclosures
    ) or "없음"

    usdkrw_str = f"{usdkrw:,.1f}원" if usdkrw else "N/A"

    prompt = (
        f"오늘({today}) 한국 주식시장 현황을 카카오톡 메시지로 요약해줘.\n\n"
        f"[시장 지표]\n"
        f"- KOSPI: {idx_str(kospi)}\n"
        f"- KOSDAQ: {idx_str(kosdaq)}\n"
        f"- USD/KRW: {usdkrw_str}\n\n"
        f"[최근 주요 공시 (주요사항보고)]\n"
        f"{disc_str}\n\n"
        f"장기투자자 관점에서 오늘의 시장을 간결하게 요약하고, 주목할 포인트 1~2개를 짧게 알려줘.\n"
        f"이모지를 적절히 사용하고 카카오톡에서 읽기 좋게 작성해줘. 전체 150자 이내로.\n\n"
        f"형식:\n"
        f"📊 [날짜] 시장 현황\n"
        f"[지표 한 줄 요약]\n\n"
        f"💡 주목 포인트\n"
        f"[핵심 내용]"
    )

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=openai_key)
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=350,
                temperature=0.4,
                messages=[
                    {"role": "system", "content": "한국 주식시장 장기투자 전문가. 카카오톡 메시지용으로 간결하게 답변."},
                    {"role": "user", "content": prompt},
                ],
            ),
            timeout=8.0,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[GPT_MARKET] 오류: {e}")
        return _format_market_text_fallback(indices, usdkrw, disclosures)


async def _handle_market_ai() -> dict:
    cache_key = f"market_{datetime.date.today().isoformat()}_{datetime.datetime.now().hour}"
    if cache_key in _market_cache:
        return _market_cache[cache_key]

    try:
        indices, usdkrw, disclosures = await asyncio.wait_for(
            asyncio.gather(
                asyncio.to_thread(_fetch_market_indices_sync),
                asyncio.to_thread(_fetch_usdkrw_sync),
                asyncio.to_thread(_fetch_dart_disclosures_sync),
            ),
            timeout=5.0,
        )
    except asyncio.TimeoutError:
        print("[MARKET_AI] 데이터 수집 타임아웃, 폴백 사용")
        indices, usdkrw, disclosures = {}, None, []

    analysis = await _gpt_market_summary(indices, usdkrw, disclosures)
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
