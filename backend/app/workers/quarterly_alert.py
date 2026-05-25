"""
quarterly_alert.py — 분기/반기/사업보고서 공시 감지 → 내 종목 보유자에게 카카오 알림
  - DART 최근 공시 조회 → 분기보고서 필터링
  - user_holdings와 교차 → 해당 사용자에게 카카오 개인 Push
  - 중복 방지: quarterly_alert_sent 테이블에 (user_id, rcept_no) 기록
  - APScheduler에서 매일 오전 7시 30분 KST에 실행 (어드바이저 직후)
"""
import datetime
from sqlalchemy import text
from app.core.database import SessionLocal


REPORT_KEYWORDS = ["분기보고서", "반기보고서", "사업보고서"]


def _is_quarterly_report(report_nm: str) -> bool:
    nm = report_nm.replace(" ", "")
    return any(k in nm for k in REPORT_KEYWORDS)


def _get_recent_quarterly_disclosures(days: int = 2) -> list[dict]:
    """DART API에서 최근 N일 분기보고서 공시 조회"""
    try:
        import requests
        from app.core.config import settings
        today = datetime.date.today()
        bgn = (today - datetime.timedelta(days=days)).strftime("%Y%m%d")
        end = today.strftime("%Y%m%d")
        resp = requests.get(
            "https://opendart.fss.or.kr/api/list.json",
            params={
                "crtfc_key": settings.dart_api_key,
                "bgn_de": bgn,
                "end_de": end,
                "page_count": "100",
                "sort": "date",
                "sort_mth": "desc",
            },
            timeout=10,
        )
        data = resp.json()
        items = data.get("list", [])
        return [
            {
                "rcept_no": item.get("rcept_no", ""),
                "corp_name": item.get("corp_name", ""),
                "stock_code": (item.get("stock_code") or "").upper(),
                "report_nm": item.get("report_nm", ""),
                "rcept_dt": item.get("rcept_dt", ""),
            }
            for item in items
            if _is_quarterly_report(item.get("report_nm", "")) and item.get("stock_code")
        ]
    except Exception as e:
        print(f"[QUARTERLY_ALERT] DART 조회 실패: {e}")
        return []


def _send_kakao_to_user(user_id: int, message: str) -> bool:
    """특정 user_id → 카카오 개인 Push (price_alert.py와 동일 패턴)"""
    try:
        import requests
        from app.core.config import settings

        bot_id = settings.kakao_bot_id
        if not bot_id:
            return False

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

        from app.api.kakao import _get_kakao_app_token
        token = _get_kakao_app_token()
        if not token:
            return False

        url = f"https://kakao.com/v1/api/talk/bots/{bot_id}/messages"
        resp = requests.post(url, json={
            "user_key": row.bot_user_key,
            "template_object": {"object_type": "text", "text": message[:990], "link": {}},
        }, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, timeout=5)
        return resp.status_code == 200
    except Exception as e:
        print(f"[QUARTERLY_ALERT] 카카오 발송 오류 (user={user_id}): {e}")
        return False


def _ensure_alert_table():
    """중복 방지 테이블 IF NOT EXISTS"""
    with SessionLocal() as session:
        session.execute(text("""
            CREATE TABLE IF NOT EXISTS quarterly_alert_sent (
                id         SERIAL PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                rcept_no   VARCHAR(20) NOT NULL,
                sent_at    TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (user_id, rcept_no)
            )
        """))
        session.commit()


def _already_sent(user_id: int, rcept_no: str) -> bool:
    with SessionLocal() as session:
        row = session.execute(text("""
            SELECT 1 FROM quarterly_alert_sent
            WHERE user_id = :uid AND rcept_no = :rn LIMIT 1
        """), {"uid": user_id, "rcept_no": rcept_no}).fetchone()
    return bool(row)


def _mark_sent(user_id: int, rcept_no: str):
    with SessionLocal() as session:
        session.execute(text("""
            INSERT INTO quarterly_alert_sent (user_id, rcept_no)
            VALUES (:uid, :rn) ON CONFLICT DO NOTHING
        """), {"uid": user_id, "rcept_no": rcept_no})
        session.commit()


def run_quarterly_alerts():
    """
    분기보고서 알림 메인.
    1. 최근 2일 분기보고서 공시 수집
    2. user_holdings와 교차
    3. 미발송 유저에게 카카오 Push
    """
    _ensure_alert_table()

    disclosures = _get_recent_quarterly_disclosures(days=2)
    if not disclosures:
        print("[QUARTERLY_ALERT] 최근 분기보고서 없음")
        return

    print(f"[QUARTERLY_ALERT] 분기보고서 {len(disclosures)}건 감지")

    # 내 종목 보유 유저 전체 조회 (ticker → user_id 매핑)
    ticker_to_users: dict[str, list[int]] = {}
    try:
        with SessionLocal() as session:
            rows = session.execute(text("""
                SELECT user_id, ticker FROM user_holdings
            """)).fetchall()
        for r in rows:
            ticker_to_users.setdefault(r.ticker.upper(), []).append(r.user_id)
    except Exception as e:
        print(f"[QUARTERLY_ALERT] user_holdings 조회 실패: {e}")
        return

    sent_total, skipped = 0, 0

    for disc in disclosures:
        ticker = disc["stock_code"]
        user_ids = ticker_to_users.get(ticker, [])
        if not user_ids:
            continue

        dt = disc["rcept_dt"]
        dt_fmt = f"{dt[:4]}.{dt[4:6]}.{dt[6:]}" if len(dt) == 8 else dt
        msg = (
            f"📋 [{disc['corp_name']}] {disc['report_nm']} 공시\n"
            f"📅 {dt_fmt}\n"
            f"보유 종목 실적 공시입니다. 원문을 확인하세요.\n"
            f"🔗 dart.fss.or.kr/dsaf001/main.do?rcpNo={disc['rcept_no']}"
        )

        for uid in user_ids:
            if _already_sent(uid, disc["rcept_no"]):
                skipped += 1
                continue
            ok = _send_kakao_to_user(uid, msg)
            if ok:
                _mark_sent(uid, disc["rcept_no"])
                sent_total += 1
                print(f"[QUARTERLY_ALERT] ✅ user={uid} {ticker} {disc['report_nm']}")
            else:
                print(f"[QUARTERLY_ALERT] ⚠️ user={uid} {ticker} 발송 실패 (카카오 미연동?)")

    print(f"[QUARTERLY_ALERT] 완료 — 발송:{sent_total} 중복스킵:{skipped}")


if __name__ == "__main__":
    run_quarterly_alerts()
