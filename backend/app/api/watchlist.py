"""
backend/app/api/watchlist.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
관심종목 CRUD + 현재가 조회
  GET    /api/v2/watchlist          관심종목 목록 (점수 + 현재가 포함)
  POST   /api/v2/watchlist          종목 추가
  DELETE /api/v2/watchlist/{ticker} 종목 삭제
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, get_db
from app.core.config import settings

router = APIRouter(prefix="/api/v2/watchlist", tags=["watchlist"])

# 구독 등급별 관심종목 한도
WATCHLIST_LIMITS = {"free": 0, "pro": 20, "premium": -1}  # -1 = 무제한


def _get_user_tier(user_id: int, db: Session, user_email: str = "") -> str:
    # 오너/관리자는 항상 premium
    if user_email and settings.admin_email and user_email == settings.admin_email:
        return "premium"
    row = db.execute(text("""
        SELECT tier FROM subscriptions
        WHERE user_id = :uid AND status = 'active'
        ORDER BY id DESC LIMIT 1
    """), {"uid": user_id}).fetchone()
    return row.tier if row else "free"


def _fetch_current_price(ticker: str) -> dict | None:
    """Yahoo Finance로 현재가 + 등락률 조회"""
    try:
        import requests
        symbol = ticker + ".KS" if len(ticker) == 6 else ticker
        resp = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"interval": "1d", "range": "2d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=4,
        )
        meta = resp.json()["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice", 0)
        prev = meta.get("chartPreviousClose") or meta.get("previousClose", price)
        chg = round((price - prev) / prev * 100, 2) if prev else 0
        return {"price": int(price), "change_pct": chg}
    except Exception:
        # KOSDAQ 시도
        try:
            import requests
            symbol = ticker + ".KQ"
            resp = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                params={"interval": "1d", "range": "2d"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=4,
            )
            meta = resp.json()["chart"]["result"][0]["meta"]
            price = meta.get("regularMarketPrice", 0)
            prev = meta.get("chartPreviousClose") or meta.get("previousClose", price)
            chg = round((price - prev) / prev * 100, 2) if prev else 0
            return {"price": int(price), "change_pct": chg}
        except Exception:
            return None


# ── 목록 조회 ─────────────────────────────────────────────────────
@router.get("")
def list_watchlist(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.execute(text("""
        SELECT w.ticker, w.name, w.added_at, w.alert_enabled,
               w.entry_price, w.entry_date, w.target_price, w.alert_sent_at,
               s.total_score, s.grade,
               s.growth_score, s.stability_score, s.cashflow_score,
               s.dividend_score, s.consistency_score,
               s.revenue_cagr_5y, s.eps_cagr_5y, s.avg_roe_5y,
               s.avg_fcf_margin, s.per, s.pbr, s.dividend_yield,
               s.market_cap, s.close
        FROM watchlist w
        LEFT JOIN scores s ON s.ticker = w.ticker
        WHERE w.user_id = :uid
        ORDER BY w.added_at DESC
    """), {"uid": current_user["id"]}).fetchall()

    items = []
    for r in rows:
        item = dict(r._mapping)
        # 현재가 실시간 조회 (DB 값 있으면 보완)
        live = _fetch_current_price(r.ticker)
        if live:
            item["live_price"] = live["price"]
            item["change_pct"] = live["change_pct"]
        else:
            item["live_price"] = int(r.close) if r.close else None
            item["change_pct"] = None
        item["added_at"] = r.added_at.isoformat() if r.added_at else None
        item["entry_date"] = r.entry_date.isoformat() if r.entry_date else None
        item["entry_price"]  = float(r.entry_price)  if r.entry_price  else None
        item["target_price"] = float(r.target_price) if r.target_price else None
        item["alert_enabled"] = bool(r.alert_enabled) if r.alert_enabled is not None else False
        item["alert_sent_at"] = r.alert_sent_at.isoformat() if r.alert_sent_at else None

        # 목표가 도달 여부
        if item["target_price"] and item["live_price"]:
            item["target_reached"] = item["live_price"] >= item["target_price"]
        else:
            item["target_reached"] = False

        # 매수가 대비 수익률 계산
        if item["entry_price"] and item["live_price"]:
            item["return_pct"] = round(
                (item["live_price"] - item["entry_price"]) / item["entry_price"] * 100, 2
            )
        else:
            item["return_pct"] = None

        items.append(item)

    tier = _get_user_tier(current_user["id"], db, current_user.get("email", ""))
    limit = WATCHLIST_LIMITS.get(tier, 0)

    return {
        "items": items,
        "count": len(items),
        "tier": tier,
        "limit": limit,
    }


# ── 종목 추가 ─────────────────────────────────────────────────────
class AddWatchlistRequest(BaseModel):
    ticker: str
    name: str = ""


@router.post("", status_code=201)
def add_watchlist(
    body: AddWatchlistRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tier = _get_user_tier(current_user["id"], db, current_user.get("email", ""))
    limit = WATCHLIST_LIMITS.get(tier, 0)

    if limit == 0:
        raise HTTPException(
            status_code=403,
            detail="관심종목은 Pro 이상 구독자만 사용 가능합니다. (Pro: 20개, Premium: 무제한)"
        )

    if limit > 0:
        count = db.execute(text(
            "SELECT COUNT(*) FROM watchlist WHERE user_id = :uid"
        ), {"uid": current_user["id"]}).scalar()
        if count >= limit:
            raise HTTPException(
                status_code=403,
                detail=f"{tier.capitalize()} 플랜 관심종목 한도({limit}개)에 도달했습니다. Premium으로 업그레이드하세요."
            )

    ticker = body.ticker.upper().strip()

    # 종목명 자동 조회 (DB에 있으면)
    name = body.name
    if not name:
        row = db.execute(text(
            "SELECT name FROM scores WHERE ticker = :t LIMIT 1"
        ), {"t": ticker}).fetchone()
        if row:
            name = row.name

    try:
        db.execute(text("""
            INSERT INTO watchlist (user_id, ticker, name, added_at)
            VALUES (:uid, :ticker, :name, NOW())
        """), {"uid": current_user["id"], "ticker": ticker, "name": name or ticker})
        db.commit()
    except Exception:
        raise HTTPException(status_code=409, detail="이미 관심종목에 추가된 종목입니다.")

    # Lazy 뉴스 분석 트리거 (백그라운드, 비차단)
    # 기존 데이터 있으면 24시간 룰에 따라 갱신, 없으면 신규 분석
    try:
        import asyncio
        from app.api.v2_news import _lazy_analyze_ticker, _needs_refresh
        existing = db.execute(text("""
            SELECT sentiment_date FROM news_sentiment
            WHERE ticker = :t ORDER BY sentiment_date DESC LIMIT 1
        """), {"t": ticker}).fetchone()
        if not existing or _needs_refresh(existing.sentiment_date):
            asyncio.create_task(_lazy_analyze_ticker(ticker, name or ticker))
    except Exception as e:
        print(f"[WATCHLIST] lazy 트리거 실패 (무시): {e}")

    return {"success": True, "ticker": ticker, "name": name}


# ── 매수가/매수일 업데이트 ──────────────────────────────────────────
class UpdateWatchlistRequest(BaseModel):
    entry_price:   float | None = None
    entry_date:    str | None   = None   # "YYYY-MM-DD"
    target_price:  float | None = None
    alert_enabled: bool | None  = None


@router.patch("/{ticker}")
def update_watchlist(
    ticker: str,
    body: UpdateWatchlistRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """매수가·매수일·목표가·알림 ON/OFF 저장"""
    import datetime
    entry_date = None
    if body.entry_date:
        try:
            entry_date = datetime.date.fromisoformat(body.entry_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="entry_date 형식 오류 (YYYY-MM-DD)")

    set_clauses = ["entry_price = :price", "entry_date = :date"]
    params: dict = {
        "uid":    current_user["id"],
        "ticker": ticker.upper(),
        "price":  body.entry_price,
        "date":   entry_date,
    }
    if body.target_price is not None:
        set_clauses.append("target_price = :target_price")
        params["target_price"] = body.target_price
        # 목표가 변경 시 발송 이력 초기화 (재알림 허용)
        set_clauses.append("alert_sent_at = NULL")
    if body.alert_enabled is not None:
        set_clauses.append("alert_enabled = :alert_enabled")
        params["alert_enabled"] = body.alert_enabled

    result = db.execute(text(f"""
        UPDATE watchlist SET {', '.join(set_clauses)}
        WHERE user_id = :uid AND ticker = :ticker
    """), params)
    db.commit()

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="관심종목에 없는 종목입니다.")

    return {"success": True, "ticker": ticker.upper()}


# ── 종목 삭제 ─────────────────────────────────────────────────────
@router.delete("/{ticker}")
def remove_watchlist(
    ticker: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = db.execute(text("""
        DELETE FROM watchlist WHERE user_id = :uid AND ticker = :ticker
    """), {"uid": current_user["id"], "ticker": ticker.upper()})
    db.commit()

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="관심종목에 없는 종목입니다.")

    return {"success": True}
