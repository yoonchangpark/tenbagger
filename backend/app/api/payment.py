"""
backend/app/api/payment.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
토스페이먼츠 결제 연동
  GET  /api/v2/payment/plans          구독 플랜 목록
  POST /api/v2/payment/confirm        결제 승인 (토스 → 백엔드)
  POST /api/v2/payment/cancel         구독 취소
  GET  /api/v2/payment/history        결제 내역
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import base64
from datetime import datetime, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, get_db
from app.core.config import settings

router = APIRouter(prefix="/api/v2/payment", tags=["payment"])

# ── 구독 플랜 정의 ────────────────────────────────────────────
PLANS = {
    "free": {
        "name": "Free",
        "price": 0,
        "description": "기본 분석 무료 제공",
        "features": [
            "종목 분석 (기본)",
            "스크리너 상위 10개",
            "등급 조회",
        ],
        "limits": {"screener": 10, "watchlist": 0, "ai_analysis": False},
    },
    "pro": {
        "name": "Pro",
        "price": 9900,
        "description": "투자자를 위한 전문 분석",
        "features": [
            "종목 분석 (전체)",
            "스크리너 전체 무제한",
            "AI 정성 분석",
            "관심종목 20개",
            "월간 투자 리포트",
        ],
        "limits": {"screener": -1, "watchlist": 20, "ai_analysis": True},
    },
    "premium": {
        "name": "Premium",
        "price": 19900,
        "description": "기관급 분석 환경",
        "features": [
            "Pro 모든 기능",
            "관심종목 무제한",
            "카카오톡 알림",
            "백테스트 무제한",
            "API 접근",
        ],
        "limits": {"screener": -1, "watchlist": -1, "ai_analysis": True},
    },
}


# ── Pydantic 스키마 ──────────────────────────────────────────────
class PaymentConfirmRequest(BaseModel):
    paymentKey: str
    orderId: str
    amount: int
    tier: str  # pro / premium


class CancelRequest(BaseModel):
    cancel_reason: str = "사용자 요청"


# ── 플랜 목록 ────────────────────────────────────────────────────
@router.get("/plans", summary="구독 플랜 목록")
def get_plans():
    return {"plans": PLANS}


# ── 토스 클라이언트 키 (프론트용 공개 키) ────────────────────────
@router.get("/toss-client-key", summary="토스 클라이언트 키")
def toss_client_key():
    return {"client_key": settings.toss_client_key}


# ── 결제 승인 ────────────────────────────────────────────────────
@router.post("/confirm", summary="토스페이먼츠 결제 승인")
async def confirm_payment(
    body: PaymentConfirmRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    프론트에서 토스 결제 완료 후 호출.
    토스 API로 결제 승인 → DB에 구독 저장.
    """
    if not settings.toss_secret_key:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="TOSS_SECRET_KEY가 설정되지 않았습니다.",
        )

    if body.tier not in ("pro", "premium"):
        raise HTTPException(status_code=400, detail="유효하지 않은 플랜입니다.")

    expected_price = PLANS[body.tier]["price"]
    if body.amount != expected_price:
        raise HTTPException(status_code=400, detail="결제 금액이 일치하지 않습니다.")

    # 토스 결제 승인 API 호출
    secret_key = settings.toss_secret_key
    encoded = base64.b64encode(f"{secret_key}:".encode()).decode()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://api.tosspayments.com/v1/payments/confirm",
            headers={
                "Authorization": f"Basic {encoded}",
                "Content-Type": "application/json",
            },
            json={
                "paymentKey": body.paymentKey,
                "orderId": body.orderId,
                "amount": body.amount,
            },
        )

    if resp.status_code != 200:
        raise HTTPException(
            status_code=400,
            detail=f"결제 승인 실패: {resp.json().get('message', resp.text)}",
        )

    toss_data = resp.json()

    # 기존 구독 비활성화
    db.execute(
        text("UPDATE subscriptions SET status = 'cancelled' WHERE user_id = :uid AND status = 'active'"),
        {"uid": current_user["id"]},
    )

    # 새 구독 생성 (1개월)
    expires_at = datetime.utcnow() + timedelta(days=30)
    db.execute(
        text("""
            INSERT INTO subscriptions (user_id, tier, status, started_at, expires_at, payment_key)
            VALUES (:uid, :tier, 'active', NOW(), :expires_at, :payment_key)
        """),
        {
            "uid": current_user["id"],
            "tier": body.tier,
            "expires_at": expires_at,
            "payment_key": body.paymentKey,
        },
    )

    # 결제 내역 저장
    db.execute(
        text("""
            INSERT INTO payments (user_id, order_id, payment_key, amount, tier, status, paid_at)
            VALUES (:uid, :order_id, :payment_key, :amount, :tier, 'done', NOW())
        """),
        {
            "uid": current_user["id"],
            "order_id": body.orderId,
            "payment_key": body.paymentKey,
            "amount": body.amount,
            "tier": body.tier,
        },
    )
    db.commit()

    return {
        "success": True,
        "tier": body.tier,
        "expires_at": expires_at.isoformat(),
        "message": f"{PLANS[body.tier]['name']} 구독이 시작됐습니다!",
    }


# ── 구독 취소 ────────────────────────────────────────────────────
@router.post("/cancel", summary="구독 취소")
def cancel_subscription(
    body: CancelRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sub = db.execute(
        text("""
            SELECT id, tier, payment_key, expires_at
            FROM subscriptions
            WHERE user_id = :uid AND status = 'active' AND tier != 'free'
            ORDER BY id DESC LIMIT 1
        """),
        {"uid": current_user["id"]},
    ).fetchone()

    if not sub:
        raise HTTPException(status_code=404, detail="취소할 구독이 없습니다.")

    db.execute(
        text("UPDATE subscriptions SET status = 'cancelled' WHERE id = :id"),
        {"id": sub.id},
    )
    # free 구독으로 복귀
    db.execute(
        text("INSERT INTO subscriptions (user_id, tier, status, started_at) VALUES (:uid, 'free', 'active', NOW())"),
        {"uid": current_user["id"]},
    )
    db.commit()

    return {"success": True, "message": "구독이 취소됐습니다. Free 플랜으로 전환됩니다."}


# ── 결제 내역 ────────────────────────────────────────────────────
@router.get("/history", summary="결제 내역")
def payment_history(
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        text("""
            SELECT order_id, amount, tier, status, paid_at
            FROM payments
            WHERE user_id = :uid
            ORDER BY paid_at DESC
            LIMIT 20
        """),
        {"uid": current_user["id"]},
    ).fetchall()

    return {
        "history": [
            {
                "order_id": r.order_id,
                "amount": r.amount,
                "tier": r.tier,
                "status": r.status,
                "paid_at": r.paid_at.isoformat() if r.paid_at else None,
            }
            for r in rows
        ]
    }
