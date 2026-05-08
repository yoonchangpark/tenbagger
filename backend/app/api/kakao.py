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


class KakaoUser(BaseModel):
    id: str = ""
    type: str = ""

class KakaoUserRequest(BaseModel):
    utterance: str = ""
    user: KakaoUser = KakaoUser()

class KakaoRequest(BaseModel):
    userRequest: KakaoUserRequest = KakaoUserRequest()


# ── 구독자 저장 ─────────────────────────────────────────────────────
def _upsert_subscriber(bot_user_key: str):
    """챗봇과 대화한 사용자 ID를 DB에 저장/갱신"""
    if not bot_user_key:
        return
    try:
        with SessionLocal() as session:
            session.execute(text("""
                INSERT INTO kakao_bot_subscribers (bot_user_key, last_seen)
                VALUES (:key, NOW())
                ON CONFLICT (bot_user_key)
                DO UPDATE SET last_seen = NOW()
            """), {"key": bot_user_key})
            session.commit()
    except Exception as e:
        print(f"[KAKAO] 구독자 저장 오류: {e}")


# ── Push 발송 ────────────────────────────────────────────────────────
def _get_kakao_app_token() -> str | None:
    """카카오 앱 액세스 토큰 발급 (24시간 유효)"""
    try:
        import requests
        from app.core.config import settings
        resp = requests.post(
            "https://kauth.kakao.com/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": settings.kakao_client_id,
                "client_secret": settings.kakao_client_secret,
            },
            timeout=5,
        )
        return resp.json().get("access_token")
    except Exception as e:
        print(f"[KAKAO_PUSH] 앱 토큰 발급 실패: {e}")
        return None


def send_kakao_push_to_all(message: str) -> dict:
    """
    Push 알림: DB의 모든 push_enabled 구독자에게 메시지 발송
    Returns: {"sent": N, "failed": M}
    """
    import requests
    from app.core.config import settings

    bot_id = settings.kakao_bot_id
    if not bot_id:
        print("[KAKAO_PUSH] KAKAO_BOT_ID 미설정 — Push 건너뜀")
        return {"sent": 0, "failed": 0, "error": "KAKAO_BOT_ID not set"}

    token = _get_kakao_app_token()
    if not token:
        return {"sent": 0, "failed": 0, "error": "token_failed"}

    # 구독자 목록 조회
    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT bot_user_key FROM kakao_bot_subscribers
            WHERE push_enabled = TRUE
        """)).fetchall()
    user_keys = [r[0] for r in rows]

    if not user_keys:
        print("[KAKAO_PUSH] 구독자 없음")
        return {"sent": 0, "failed": 0}

    # 카카오 i Open Builder Push API
    url = f"https://kakao.com/v1/api/talk/bots/{bot_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    sent, failed = 0, 0
    # 메시지 글자 제한 (simpleText 1000자)
    msg = message[:990]

    for key in user_keys:
        try:
            body = {
                "userKey": key,
                "response": {
                    "version": "2.0",
                    "template": {
                        "outputs": [{"simpleText": {"text": msg}}]
                    }
                }
            }
            r = requests.post(url, json=body, headers=headers, timeout=5)
            if r.status_code == 200:
                sent += 1
            else:
                print(f"[KAKAO_PUSH] 발송 실패 {key[:8]}…: {r.status_code} {r.text[:100]}")
                failed += 1
        except Exception as e:
            print(f"[KAKAO_PUSH] 오류 {key[:8]}…: {e}")
            failed += 1

    print(f"[KAKAO_PUSH] 발송 완료: 성공 {sent}건, 실패 {failed}건")
    return {"sent": sent, "failed": failed}


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


def _get_disclosure_highlight(title: str, corp: str) -> str | None:
    """공시 제목을 분석해 주목 포인트 한 줄 생성. 관심 없는 공시는 None 반환."""
    t = title.lower()
    # 자기주식
    if "자기주식" in t and "취득" in t:
        return f"📈 {corp}, 자기주식 취득 결정! (주가 방어)"
    if "자기주식" in t and "처분" in t:
        return f"🔍 {corp}, 자기주식 처분 결정"
    # 증자
    if "유상증자" in t:
        return f"💰 {corp}, 유상증자로 자금 확보"
    if "무상증자" in t:
        return f"🎁 {corp}, 무상증자 결정! (주주 환원)"
    # 합병·분할
    if "합병" in t:
        return f"🔗 {corp}, 합병 추진"
    if "분할" in t:
        return f"✂️ {corp}, 분할 결정"
    # 배당
    if "배당" in t:
        return f"💵 {corp}, 배당 결정"
    # 전환사채·신주인수권
    if "전환사채" in t or "cb" in t:
        return f"⚠️ {corp}, 전환사채 발행"
    if "신주인수권" in t or "bw" in t:
        return f"⚠️ {corp}, BW 발행"
    return None


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

    # 주목 포인트 (관심 공시만 필터링)
    highlights = []
    for d in disclosures:
        h = _get_disclosure_highlight(d.get("title", ""), d.get("corp", ""))
        if h:
            highlights.append(f"• {h}")
    if highlights:
        lines.append("\n💡 주목 포인트")
        lines.extend(highlights[:3])

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
                max_tokens=200,
                temperature=0.3,
                messages=[
                    {"role": "system", "content": "한국 주식 장기투자 전문가. 카카오톡 형식으로 간결하게 답변. 종목명 언급 금지."},
                    {"role": "user", "content": prompt},
                ],
            ),
            timeout=4.0,
        )
        gpt_text = resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[GPT_MARKET] 오류: {e}")
        gpt_text = _format_market_text_fallback(indices, None, disclosures, [])

    # ★ 주목 포인트 — 실제 공시 데이터로만 (GPT 환각 방지)
    highlights = []
    for d in disclosures:
        h = _get_disclosure_highlight(d.get("title", ""), d.get("corp", ""))
        if h:
            highlights.append(f"• {h}")
    if highlights:
        gpt_text += "\n\n💡 주목 포인트\n" + "\n".join(highlights[:3])

    # ★ 종목 섹션은 실제 DB 데이터로만 직접 붙임 (GPT 환각 방지)
    if top_stocks:
        stock_lines = ["\n⭐ 주목 종목 (텐배거 스코어 TOP3)"]
        for s in top_stocks:
            score = f"{s['total_score']:.1f}점"
            stock_lines.append(f"• {s['name']} ({s['ticker']}) {score}")
        gpt_text += "\n" + "\n".join(stock_lines)

    return gpt_text


async def _handle_market_ai() -> dict:
    cache_key = f"market_{datetime.date.today().isoformat()}_{datetime.datetime.now().hour}"
    if cache_key in _market_cache:
        return _market_cache[cache_key]

    # 카카오 타임아웃 5초 → 전체 4초 안에 완료
    try:
        # 1단계: 데이터 수집 (2.5초 제한)
        try:
            indices, disclosures, top_stocks = await asyncio.wait_for(
                asyncio.gather(
                    asyncio.to_thread(_fetch_market_indices_sync),
                    asyncio.to_thread(_fetch_dart_disclosures_sync),
                    asyncio.to_thread(_get_top_stocks_sync),
                ),
                timeout=2.5,
            )
        except asyncio.TimeoutError:
            print("[MARKET_AI] 데이터 수집 타임아웃, 폴백 사용")
            indices, disclosures, top_stocks = {}, [], []

        # 2단계: GPT 요약 (1.5초 제한) → 시간 초과 시 즉시 폴백
        try:
            analysis = await asyncio.wait_for(
                _gpt_market_summary(indices, disclosures, top_stocks),
                timeout=1.5,
            )
        except asyncio.TimeoutError:
            print("[MARKET_AI] GPT 타임아웃, 폴백 텍스트 사용")
            analysis = _format_market_text_fallback(indices, None, disclosures, top_stocks)

    except Exception as e:
        print(f"[MARKET_AI] 오류: {e}")
        analysis = "📊 시장 데이터를 불러오는 중입니다.\n잠시 후 다시 시도해주세요."

    result = _simple_text(analysis, _main_quick_replies())
    _market_cache[cache_key] = result
    return result


@router.post("/webhook")
async def kakao_webhook(body: KakaoRequest):
    try:
        utterance = body.userRequest.utterance.strip()
        utt_lower = utterance.lower()

        # 사용자 ID 저장 (Push 알림 구독자 관리)
        bot_user_key = body.userRequest.user.id
        if bot_user_key:
            asyncio.create_task(asyncio.to_thread(_upsert_subscriber, bot_user_key))

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


@router.get("/subscribers/count")
async def get_subscriber_count():
    """채널 구독자 수 조회 (관리용)"""
    with SessionLocal() as session:
        row = session.execute(text(
            "SELECT COUNT(*) FROM kakao_bot_subscribers WHERE push_enabled = TRUE"
        )).fetchone()
    return {"count": row[0]}


@router.post("/push/test")
async def push_test(msg: str = "📢 텐배거 헌터 Push 테스트 메시지입니다!"):
    """Push 발송 테스트 (관리용)"""
    result = await asyncio.to_thread(send_kakao_push_to_all, msg)
    return result
