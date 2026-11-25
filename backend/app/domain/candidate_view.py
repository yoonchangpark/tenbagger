"""
종목 화면(② 확인한다) 한 번에 — 5고리 근거 + 재무 3년 + 시장 반영 1년

- 5고리 숫자 판정: domain/thesis_checklist.evaluate (DART 사업보고서 1건, GPT 없음)
- 5고리 서술 근거: product_link_evidence (출처 링크가 있는 것만 '확인')
- 시장 반영: 1년 주가 변화 + 같은 기간 외국인·기관 누적 순매수.
  '아직 덜 알려짐 / 반영 시작 / 가격에 반영 중'은 확인 신호일 뿐 매수·매도 신호가 아니다.
가져오지 못한 조각은 None과 '미확인'으로 돌려준다. 없는 값을 지어내지 않는다.
"""
import asyncio
import datetime

from sqlalchemy import text

from app.core.database import SessionLocal
from app.domain.thesis_checklist import evaluate, fetch_thesis_financials

LINK_ORDER = ("demand", "unique", "barrier", "imbalance", "margin")
LINK_NAMES = ("수요 폭발", "독자성", "진입장벽", "공급 부족", "마진 상승")

# 시장 반영 판정 기준. 화면에 이 문장을 그대로 보여 준다.
MARKET_RULE = ("1년 주가 +20% 미만이고 외국인+기관 누적 순매수가 0 이하면 '아직 덜 알려짐', "
               "주가 +50% 이상이고 순매수가 0 초과면 '가격에 반영 중', 그 사이는 '반영 시작'")


def market_label(price_change_pct, smart_net) -> str:
    if price_change_pct is None or smart_net is None:
        return "미확인"
    if price_change_pct < 20 and smart_net <= 0:
        return "아직 덜 알려짐"
    if price_change_pct >= 50 and smart_net > 0:
        return "가격에 반영 중"
    return "반영 시작"


def _market(ticker: str) -> dict:
    """동기 함수 — 기존 주가 캐시(pykrx)와 네이버 수급 스크래퍼를 재사용한다."""
    from app.api.v2_flow import _fetch_flow_naver
    from app.api.v2_portfolio import get_price_history

    price = {"now": None, "year_ago": None, "change_pct": None, "from": None, "to": None}
    try:
        data = [r for r in get_price_history(ticker, "1y")["data"] if r.get("close")]
        if len(data) >= 2:
            price.update(now=data[-1]["close"], year_ago=data[0]["close"],
                         change_pct=round((data[-1]["close"] / data[0]["close"] - 1) * 100, 1),
                         **{"from": data[0]["date"], "to": data[-1]["date"]})
    except Exception as e:
        print(f"[CANDIDATE] {ticker} 주가 조회 실패: {e}")

    flow = {"foreign": None, "institution": None, "days": 0, "from": None, "to": None}
    try:
        f = _fetch_flow_naver(ticker, 250)
        if f and f.get("dates"):
            # 응답이 1년보다 짧을 수 있으므로 실제 받은 기간을 함께 보여 준다
            flow.update(foreign=sum(f["foreign"]), institution=sum(f["institution"]),
                        days=len(f["dates"]), **{"from": f["dates"][0], "to": f["dates"][-1]})
    except Exception as e:
        print(f"[CANDIDATE] {ticker} 수급 조회 실패: {e}")

    smart = None if flow["foreign"] is None else flow["foreign"] + flow["institution"]
    return {"price": price, "flow": flow, "smart_net_shares": smart,
            "label": market_label(price["change_pct"], smart), "rule": MARKET_RULE}


def _db_parts(ticker: str) -> tuple[dict, list]:
    db = SessionLocal()
    try:
        ev = db.execute(text("""
            SELECT link, status, claim, source_url, source_type, checked_at
              FROM product_link_evidence WHERE ticker = :t ORDER BY link, id
        """), {"t": ticker}).fetchall()
        by_link: dict[int, list] = {}
        for r in ev:
            d = dict(r._mapping)
            d["checked_at"] = d["checked_at"].isoformat() if d["checked_at"] else None
            by_link.setdefault(d.pop("link"), []).append(d)
        themes = db.execute(text("""
            SELECT t.id, t.name, c.stage, c.survival_pass
              FROM product_theme_candidates c JOIN product_themes t ON t.id = c.theme_id
             WHERE c.ticker = :t AND t.status = 'approved' AND c.excluded_reason IS NULL
        """), {"t": ticker}).fetchall()
        return by_link, [dict(r._mapping) for r in themes]
    finally:
        db.close()


async def build_candidate(ticker: str) -> dict:
    from app.infra.clients.dart_client import get_corp_code

    corp_code, resolved = await get_corp_code(ticker)
    if not corp_code:
        return {"found": False}
    ticker = resolved or ticker

    (rows, report_year), market = await asyncio.gather(
        fetch_thesis_financials(corp_code),
        asyncio.to_thread(_market, ticker),
    )
    evidence, themes = _db_parts(ticker)
    checklist = evaluate(rows)

    links = []
    for i, (key, name) in enumerate(zip(LINK_ORDER, LINK_NAMES), 1):
        num = checklist.get("links", {}).get(key, {}) if checklist.get("available") else {}
        ev = evidence.get(i, [])
        links.append({
            "no": i, "name": name,
            "number_status": num.get("number_status", "미확인"),
            "number_checks": [c for c in num.get("checks", []) if c.get("kind") != "narrative"],
            "evidence": ev,
            "evidence_needed": [c["label"] for c in num.get("checks", []) if c.get("kind") == "narrative"],
            "evidence_status": ("확인" if any(e["status"] == "확인" for e in ev)
                                else "반증" if any(e["status"] == "반증" for e in ev) else "미확인"),
        })

    return {
        "found": True,
        "ticker": ticker,
        "themes": themes,
        "stage": checklist.get("stage"),
        "stage_label": checklist.get("stage_label"),
        "stage_warning": checklist.get("warning"),
        "links": links,
        "financials": [{"year": r["year"], "revenue": r.get("revenue"),
                        "operating_profit": r.get("operating_profit"),
                        "net_income": r.get("net_income")} for r in rows],
        "report_year": report_year,
        "market": market,
        "sources": {"financials": "DART 사업보고서", "price": "KRX(pykrx) 일별 종가",
                    "flow": "네이버 금융 투자자별 매매동향"},
        "as_of": datetime.date.today().isoformat(),
    }
