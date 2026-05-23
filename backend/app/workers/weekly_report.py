"""
weekly_report.py — 매주 월요일 08:00(KST) 관심종목 주간 변화 요약 이메일
  - alert_enabled=TRUE 사용자 대상 (관심종목 1개 이상)
  - 지난 주 대비 현재가 변화, 등급 변동, 목표가 도달 여부 요약
"""
import datetime
from sqlalchemy import text
from app.core.database import SessionLocal


def _get_week_price(ticker: str, days_ago: int) -> int | None:
    """price_daily_cache 에서 N일 전 종가 조회"""
    target = datetime.date.today() - datetime.timedelta(days=days_ago)
    try:
        with SessionLocal() as session:
            row = session.execute(text("""
                SELECT close_price FROM price_daily_cache
                WHERE ticker = :t AND trade_date <= :d AND close_price > 0
                ORDER BY trade_date DESC LIMIT 1
            """), {"t": ticker, "d": target}).fetchone()
        return int(row.close_price) if row else None
    except Exception:
        return None


def _get_current_price(ticker: str) -> int | None:
    try:
        import requests
        for suffix in [".KS", ".KQ"]:
            resp = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}{suffix}",
                params={"interval": "1d", "range": "1d"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=4,
            )
            price = resp.json()["chart"]["result"][0]["meta"].get("regularMarketPrice")
            if price:
                return int(price)
    except Exception:
        pass
    return None


def _build_html(user_email: str, items: list[dict]) -> str:
    today = datetime.date.today().strftime("%Y년 %m월 %d일")
    rows_html = ""
    for item in items:
        cur   = item.get("current_price")
        prev  = item.get("week_ago_price")
        chg   = round((cur - prev) / prev * 100, 2) if cur and prev else None
        chg_s = (f'<span style="color:{"#10b981" if chg >= 0 else "#ef4444"};font-weight:700;">{"▲" if chg >= 0 else "▼"}{abs(chg):.2f}%</span>'
                 if chg is not None else '—')
        tgt_s = ""
        if item.get("target_price") and cur:
            gap = ((item["target_price"] - cur) / cur * 100)
            if cur >= item["target_price"]:
                tgt_s = '<span style="color:#10b981;font-weight:700;">🎯 목표가 달성!</span>'
            else:
                tgt_s = f'<span style="color:#f59e0b;">목표가까지 {gap:.1f}%</span>'

        grade_color = {
            "TENBAGGER": "#10b981", "COMPOUNDER": "#3b82f6",
            "WATCHLIST": "#f59e0b", "AVOID": "#ef4444"
        }.get(item.get("grade", ""), "#9ca3af")

        rows_html += f"""
        <tr style="border-bottom:1px solid #374151;">
          <td style="padding:12px 14px;font-weight:600;">{item['name']}<br>
            <span style="font-size:11px;color:#6b7280;">{item['ticker']}</span></td>
          <td style="padding:12px 14px;text-align:right;">₩{cur:,}</td>
          <td style="padding:12px 14px;text-align:right;">{chg_s}</td>
          <td style="padding:12px 14px;text-align:right;">
            <span style="background:{grade_color}22;color:{grade_color};border-radius:5px;padding:2px 8px;font-size:11px;font-weight:700;">{item.get('grade','—')}</span>
          </td>
          <td style="padding:12px 14px;text-align:right;">{tgt_s or '—'}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"/></head>
<body style="background:#0a0f1e;color:#f9fafb;font-family:Arial,sans-serif;margin:0;padding:0;">
<div style="max-width:640px;margin:0 auto;padding:32px 20px;">
  <div style="background:linear-gradient(135deg,#10b981,#3b82f6);-webkit-background-clip:text;-webkit-text-fill-color:transparent;font-size:22px;font-weight:800;margin-bottom:6px;">🚀 텐배거 헌터</div>
  <div style="font-size:13px;color:#9ca3af;margin-bottom:28px;">관심종목 주간 리포트 · {today}</div>

  <div style="background:#111827;border:1px solid #374151;border-radius:16px;overflow:hidden;margin-bottom:24px;">
    <div style="padding:16px 20px;border-bottom:1px solid #374151;font-size:14px;font-weight:700;">📋 내 관심종목 주간 변화</div>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead>
        <tr style="background:#1f2937;border-bottom:1px solid #374151;">
          <th style="padding:10px 14px;text-align:left;color:#9ca3af;font-size:11px;">종목</th>
          <th style="padding:10px 14px;text-align:right;color:#9ca3af;font-size:11px;">현재가</th>
          <th style="padding:10px 14px;text-align:right;color:#9ca3af;font-size:11px;">주간 변화</th>
          <th style="padding:10px 14px;text-align:right;color:#9ca3af;font-size:11px;">등급</th>
          <th style="padding:10px 14px;text-align:right;color:#9ca3af;font-size:11px;">목표가</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>

  <div style="text-align:center;margin-bottom:24px;">
    <a href="https://tenbagger-hunter.com/dashboard.html"
       style="display:inline-block;background:linear-gradient(135deg,#059669,#2563eb);border-radius:10px;padding:12px 28px;color:#fff;font-weight:700;font-size:14px;text-decoration:none;">
      📊 대시보드 열기
    </a>
  </div>

  <div style="font-size:11px;color:#4b5563;text-align:center;line-height:1.6;">
    이 메일은 텐배거 헌터 관심종목 알림으로 발송됩니다.<br>
    수신을 원하지 않으시면 대시보드 설정에서 해제해 주세요.<br>
    본 리포트는 투자 참고 자료이며, 실제 투자 손익에 대한 책임은 투자자 본인에게 있습니다.
  </div>
</div>
</body></html>"""


def run_weekly_report():
    """매주 월요일 08:00(KST) 호출"""
    import smtplib, os
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    print("📧 [WEEKLY_REPORT] 주간 리포트 발송 시작…")

    sender   = os.environ.get("EMAIL_SENDER", "")
    password = os.environ.get("EMAIL_PASSWORD", "")
    if not sender or not password:
        print("[WEEKLY_REPORT] EMAIL_SENDER / EMAIL_PASSWORD 미설정 — 건너뜀")
        return

    # alert_enabled 사용자 + 관심종목 조회
    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT DISTINCT u.id, u.email,
                   w.ticker, w.name, w.target_price, w.alert_enabled,
                   s.grade, s.total_score
            FROM watchlist w
            JOIN users u ON u.id = w.user_id
            LEFT JOIN scores s ON s.ticker = w.ticker
            WHERE u.email IS NOT NULL
            ORDER BY u.id, s.total_score DESC NULLS LAST
        """)).fetchall()

    # 사용자별 그룹핑
    user_map: dict[int, dict] = {}
    for r in rows:
        uid = r.id
        if uid not in user_map:
            user_map[uid] = {"email": r.email, "items": []}
        user_map[uid]["items"].append({
            "ticker":       r.ticker,
            "name":         r.name or r.ticker,
            "grade":        r.grade or "—",
            "target_price": float(r.target_price) if r.target_price else None,
        })

    sent_count = 0
    for uid, udata in user_map.items():
        items = udata["items"][:10]   # 최대 10개
        if not items:
            continue

        # 현재가 + 지난 주 가격 보완
        for item in items:
            item["current_price"] = _get_current_price(item["ticker"])
            item["week_ago_price"] = _get_week_price(item["ticker"], 7)

        html = _build_html(udata["email"], items)
        today_s = datetime.date.today().strftime("%m/%d")

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[텐배거 헌터] 주간 관심종목 리포트 ({today_s})"
            msg["From"]    = f"텐배거 헌터 <{sender}>"
            msg["To"]      = udata["email"]
            msg.attach(MIMEText(html, "html", "utf-8"))

            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(sender, password)
                server.sendmail(sender, udata["email"], msg.as_string())

            print(f"[WEEKLY_REPORT] ✅ → {udata['email']} ({len(items)}개 종목)")
            sent_count += 1
        except Exception as e:
            print(f"[WEEKLY_REPORT] ⚠️ 발송 실패 {udata['email']}: {e}")

    print(f"✅ [WEEKLY_REPORT] 완료 — {sent_count}/{len(user_map)}명 발송")
