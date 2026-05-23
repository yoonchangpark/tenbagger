"""
price_alert.py — 관심종목 목표가 도달 시 카카오 알림 발송
  - alert_enabled=TRUE AND target_price IS NOT NULL 인 watchlist 항목 체크
  - 현재가 >= 목표가 이면 해당 사용자에게 카카오 메시지 발송
  - alert_sent_at 업데이트로 중복 발송 방지 (24시간 이내 재발송 안 함)
"""
import datetime
from sqlalchemy import text
from app.core.database import SessionLocal


def _get_current_price(ticker: str) -> int | None:
    """Yahoo Finance 현재가 조회 (KS → KQ fallback)"""
    try:
        import requests
        for suffix in [".KS", ".KQ"]:
            resp = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}{suffix}",
                params={"interval": "1d", "range": "1d"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=4,
            )
            data = resp.json()
            price = data["chart"]["result"][0]["meta"].get("regularMarketPrice")
            if price:
                return int(price)
    except Exception:
        pass
    return None


def _send_kakao_to_user(user_id: int, message: str) -> bool:
    """특정 user_id 의 카카오 bot_user_key 로 개인 Push 발송"""
    try:
        import requests
        from app.core.config import settings

        bot_id = settings.kakao_bot_id
        if not bot_id:
            return False

        # 해당 유저의 카카오 bot_user_key 조회
        with SessionLocal() as session:
            row = session.execute(text("""
                SELECT kbs.bot_user_key
                FROM kakao_bot_subscribers kbs
                JOIN users u ON u.kakao_id = kbs.bot_user_key
                WHERE u.id = :uid AND kbs.push_enabled = TRUE
                LIMIT 1
            """), {"uid": user_id}).fetchone()

        if not row:
            return False

        # 앱 토큰 발급
        token_resp = requests.post(
            "https://kauth.kakao.com/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": settings.kakao_client_id,
                "client_secret": settings.kakao_client_secret,
            },
            timeout=5,
        )
        token = token_resp.json().get("access_token")
        if not token:
            return False

        url = f"https://kakao.com/v1/api/talk/bots/{bot_id}/messages"
        payload = {
            "user_key": row.bot_user_key,
            "template_object": {"object_type": "text", "text": message[:990], "link": {}},
        }
        resp = requests.post(url, json=payload, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json"
        }, timeout=5)
        return resp.status_code == 200
    except Exception as e:
        print(f"[PRICE_ALERT] 개인 발송 오류: {e}")
        return False


def run_price_alerts():
    """
    스케줄러에서 매일 장 마감 후 호출 (16:00 KST).
    목표가 도달 종목을 감지해 사용자에게 카카오 메시지 발송.
    """
    print("🔔 [PRICE_ALERT] 목표가 알림 체크 시작…")
    now = datetime.datetime.now()
    cooldown = datetime.timedelta(hours=24)

    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT w.id, w.user_id, w.ticker, w.name, w.target_price, w.alert_sent_at,
                   u.email
            FROM watchlist w
            JOIN users u ON u.id = w.user_id
            WHERE w.alert_enabled = TRUE
              AND w.target_price IS NOT NULL
              AND (w.alert_sent_at IS NULL OR w.alert_sent_at < NOW() - INTERVAL '24 hours')
        """)).fetchall()

    if not rows:
        print("[PRICE_ALERT] 알림 대상 없음")
        return

    triggered = 0
    for row in rows:
        price = _get_current_price(row.ticker)
        if price is None:
            continue
        if price < row.target_price:
            continue

        # 목표가 도달 — 카카오 알림 발송
        gap_pct = (price - row.target_price) / row.target_price * 100
        msg = (
            f"🎯 [텐배거 목표가 알림]\n\n"
            f"종목: {row.name} ({row.ticker})\n"
            f"목표가: ₩{int(row.target_price):,}\n"
            f"현재가: ₩{price:,} (+{gap_pct:.1f}%)\n\n"
            f"설정하신 목표가에 도달했습니다! 지금 바로 확인해보세요."
        )

        sent = _send_kakao_to_user(row.user_id, msg)
        status = "✅ 발송" if sent else "⚠️ 실패(카카오 미연결)"
        print(f"[PRICE_ALERT] {row.ticker} {row.name} → {status}")

        # alert_sent_at 업데이트 (발송 여부와 무관하게 갱신 → 과도한 재시도 방지)
        with SessionLocal() as session:
            session.execute(text("""
                UPDATE watchlist SET alert_sent_at = NOW() WHERE id = :id
            """), {"id": row.id})
            session.commit()

        triggered += 1

    print(f"✅ [PRICE_ALERT] 완료 — {triggered}/{len(rows)}개 알림 발송")
