"""
실제 보유 종목 (내 종목) CRUD
  GET    /api/v2/holdings          보유 목록
  POST   /api/v2/holdings          종목 추가/수정
  DELETE /api/v2/holdings/{ticker} 종목 삭제
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, get_db

router = APIRouter(prefix="/api/v2/holdings", tags=["holdings"])


class HoldingBody(BaseModel):
    ticker: str
    name: Optional[str] = None
    quantity: int = 0
    avg_price: Optional[float] = None


@router.get("")
def list_holdings(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.execute(text("""
        SELECT h.ticker, h.name, h.quantity, h.avg_price, h.added_at,
               s.total_score, s.grade, s.close_price
        FROM user_holdings h
        LEFT JOIN score_cache s ON s.ticker = h.ticker
        WHERE h.user_id = :uid
        ORDER BY h.added_at DESC
    """), {"uid": current_user["id"]}).fetchall()

    result = []
    for r in rows:
        cur_price = r.close_price or 0
        avg = float(r.avg_price or 0)
        qty = r.quantity or 0
        pnl_pct = round((cur_price - avg) / avg * 100, 2) if avg > 0 and cur_price > 0 else None
        result.append({
            "ticker": r.ticker,
            "name": r.name,
            "quantity": qty,
            "avg_price": avg,
            "current_price": cur_price,
            "pnl_pct": pnl_pct,
            "total_score": r.total_score,
            "grade": r.grade,
            "added_at": str(r.added_at)[:10] if r.added_at else None,
        })
    return {"holdings": result, "count": len(result)}


@router.post("")
def upsert_holding(
    body: HoldingBody,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ticker = body.ticker.upper()
    db.execute(text("""
        INSERT INTO user_holdings (user_id, ticker, name, quantity, avg_price)
        VALUES (:uid, :ticker, :name, :qty, :price)
        ON CONFLICT (user_id, ticker)
        DO UPDATE SET name = EXCLUDED.name,
                      quantity = EXCLUDED.quantity,
                      avg_price = EXCLUDED.avg_price
    """), {
        "uid": current_user["id"],
        "ticker": ticker,
        "name": body.name or ticker,
        "qty": body.quantity,
        "price": body.avg_price,
    })
    db.commit()
    return {"ok": True, "ticker": ticker}


@router.delete("/{ticker}")
def delete_holding(
    ticker: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db.execute(text("""
        DELETE FROM user_holdings WHERE user_id = :uid AND ticker = :ticker
    """), {"uid": current_user["id"], "ticker": ticker.upper()})
    db.commit()
    return {"ok": True}
