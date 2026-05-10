"""
텐배거 헌터 — 일일 투자 어드바이저 에이전트
매일 새벽 자동 실행:
  1. ETL 전종목 갱신 (skip-existing)
  2. 신규 텐배거/컴파운더 후보 탐지
  3. 백테스트 정확도 검증
  4. GPT 투자 의견 생성
  5. HTML 이메일 리포트 발송

실행:
  docker exec -it tenbagger_api python -m app.agents.advisor
  docker exec -it tenbagger_api python -m app.agents.advisor --dry-run  (이메일 미발송, 리포트만 저장)
"""

import asyncio
import datetime
import json
import os
import smtplib
import argparse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from app.core.config import settings
from app.core.database import SessionLocal
from app.infra.repositories.company_repo import get_score_cached
from app.infra.clients.dart_client import get_corp_code, fetch_yearly_financials


# ── 날짜 ──────────────────────────────────────────────────────────────────
TODAY = datetime.date.today()
YESTERDAY = TODAY - datetime.timedelta(days=1)
REPORT_PATH = f"/app/reports/daily_{TODAY.isoformat()}.html"


# ── DB 조회 유틸 ───────────────────────────────────────────────────────────
def get_all_scores() -> list[dict]:
    """DB에서 전체 스코어 조회"""
    try:
        from sqlalchemy import text
        with SessionLocal() as session:
            rows = session.execute(text("""
                SELECT ticker, name, market, total_score, grade,
                       growth_score, stability_score, cashflow_score,
                       dividend_score, consistency_score,
                       revenue_cagr_5y, eps_cagr_5y, avg_roe_5y,
                       avg_operating_margin, avg_fcf_margin,
                       close, per, pbr, dividend_yield, market_cap,
                       analyzed_at
                FROM scores
                ORDER BY total_score DESC
            """)).fetchall()
            return [dict(row._mapping) for row in rows]
    except Exception as e:
        print(f"[ADVISOR] DB 조회 실패: {e}")
        return []


def get_new_candidates(all_scores: list[dict]) -> list[dict]:
    """오늘 새로 등장한 TENBAGGER/COMPOUNDER 후보"""
    new_ones = []
    for s in all_scores:
        if s.get("grade") not in ("TENBAGGER", "COMPOUNDER"):
            continue
        analyzed = s.get("analyzed_at")
        if analyzed:
            upd_date = analyzed.date() if hasattr(analyzed, "date") else TODAY
            if upd_date >= YESTERDAY:
                new_ones.append(s)
    return new_ones


def get_top_candidates(all_scores: list[dict], n: int = 10) -> list[dict]:
    """상위 N개 후보"""
    return [s for s in all_scores if s.get("grade") in ("TENBAGGER", "COMPOUNDER")][:n]


def get_accuracy_stats(all_scores: list[dict]) -> dict:
    """스코어 분포 및 기본 통계"""
    grades = {"TENBAGGER": 0, "COMPOUNDER": 0, "WATCHLIST": 0, "AVOID": 0}
    scores = []
    for s in all_scores:
        g = s.get("grade", "AVOID")
        grades[g] = grades.get(g, 0) + 1
        if s.get("total_score"):
            scores.append(float(s["total_score"]))

    total = len(all_scores)
    avg_score = sum(scores) / len(scores) if scores else 0
    return {
        "total_analyzed": total,
        "grades": grades,
        "avg_score": round(avg_score, 2),
        "tenbagger_rate": round(grades["TENBAGGER"] / total * 100, 1) if total else 0,
    }


async def run_backtest_sample(tickers: list[str], base_year: int = 2020) -> list[dict]:
    """상위 후보들의 백테스트 샘플 실행 (정확도 검증)"""
    from app.domain.backtest import run_backtest
    results = []
    for ticker in tickers[:5]:  # 상위 5개만 샘플
        try:
            result = await run_backtest(ticker, base_year=base_year)
            results.append({
                "ticker": ticker,
                "predicted_grade": result.get("predicted_grade"),
                "actual_return": result.get("actual_return_pct"),
                "base_year": base_year,
            })
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"[ADVISOR] 백테스트 샘플 실패 {ticker}: {e}")
    return results


def _fmt_candidate_row(s: dict) -> str:
    """후보 종목 데이터를 프롬프트용 텍스트로 포맷"""
    valuation = []
    if s.get("per") and float(s["per"]) > 0:
        valuation.append(f"PER={s['per']:.1f}")
    if s.get("pbr") and float(s["pbr"]) > 0:
        valuation.append(f"PBR={s['pbr']:.1f}")
    if s.get("dividend_yield") and float(s["dividend_yield"]) > 0:
        valuation.append(f"배당수익률={s['dividend_yield']:.1f}%")
    if s.get("market_cap"):
        mc = float(s["market_cap"])
        valuation.append(f"시총={mc/1e8:.0f}억" if mc < 1e12 else f"시총={mc/1e12:.1f}조")
    val_str = " | ".join(valuation) if valuation else "밸류에이션 미집계"

    sub = (
        f"성장={s.get('growth_score', 'N/A'):.1f} "
        f"안정={s.get('stability_score', 'N/A'):.1f} "
        f"현금={s.get('cashflow_score', 'N/A'):.1f} "
        f"배당={s.get('dividend_score', 'N/A'):.1f} "
        f"일관={s.get('consistency_score', 'N/A'):.1f}"
    )
    return (
        f"  [{s.get('grade')}] {s.get('name')}({s.get('ticker')}) — 총점 {s.get('total_score', 0):.1f}/10\n"
        f"    서브점수: {sub}\n"
        f"    재무: 매출CAGR={s.get('revenue_cagr_5y', 'N/A')}% | EPS CAGR={s.get('eps_cagr_5y', 'N/A')}% | "
        f"ROE={s.get('avg_roe_5y', 'N/A')}% | FCF마진={s.get('avg_fcf_margin', 'N/A')}%\n"
        f"    밸류에이션: {val_str}"
    )


async def generate_investment_report(
    top_candidates: list[dict],
    new_candidates: list[dict],
    stats: dict,
    backtest_samples: list[dict],
) -> str:
    """GPT로 투자 리포트 본문 생성"""
    if not settings.openai_api_key:
        return "OpenAI API 키가 설정되지 않아 AI 분석을 생성할 수 없습니다."

    today_str = TODAY.strftime("%Y년 %m월 %d일 (%A)")
    grades = stats["grades"]

    # 시장 온도 계산 (TENBAGGER 비율로 판단)
    tb_rate = stats["tenbagger_rate"]
    if tb_rate >= 5:
        market_temp = f"🔥 과열 구간 (TENBAGGER {tb_rate}% — 고밸류 주의)"
    elif tb_rate >= 2:
        market_temp = f"✅ 적정 구간 (TENBAGGER {tb_rate}%)"
    else:
        market_temp = f"❄️ 저평가 구간 (TENBAGGER {tb_rate}% — 기회 탐색)"

    # 상위 후보 상세 데이터
    top_detail = "\n\n".join([_fmt_candidate_row(s) for s in top_candidates[:6]])

    # 신규 후보
    if new_candidates:
        new_detail = "\n\n".join([_fmt_candidate_row(s) for s in new_candidates[:4]])
    else:
        new_detail = "  (오늘 새로 발굴된 종목 없음)"

    # 백테스트
    bt_lines = [
        f"  {r.get('ticker')}: {r.get('base_year')}년 매수 → "
        f"현재 수익률 {r.get('actual_return', 'N/A')}%"
        for r in backtest_samples
    ]
    bt_str = "\n".join(bt_lines) if bt_lines else "  (백테스트 샘플 없음)"

    system_msg = (
        "당신은 한국 주식 장기투자 전문 AI 애널리스트입니다.\n"
        "텐배거 헌터 스코어링 시스템(성장성 30%·안정성 20%·현금흐름 20%·배당 15%·일관성 15%)을 "
        "깊이 이해하고, 데이터에 근거한 예리하고 실용적인 투자 인사이트를 제공합니다.\n"
        "오너(yoonchang.park@gmail.com)는 KOSPI/KOSDAQ 장기투자자로 텐배거 발굴이 목표입니다.\n"
        "구체적 수치를 인용하고, 막연한 표현은 사용하지 마세요."
    )

    prompt = f"""{today_str} 텐배거 헌터 일일 투자 리포트를 작성해주세요.

━━━ 시스템 현황 ━━━
분석 종목: {stats['total_analyzed']}개 | 평균 점수: {stats['avg_score']}/10 | 시장 온도: {market_temp}
등급 분포: TENBAGGER {grades['TENBAGGER']}개 | COMPOUNDER {grades['COMPOUNDER']}개 | WATCHLIST {grades['WATCHLIST']}개 | AVOID {grades['AVOID']}개

━━━ 상위 후보 종목 (상세) ━━━
{top_detail}

━━━ 오늘 신규 발굴 ━━━
{new_detail}

━━━ 백테스트 실적 ━━━
{bt_str}

━━━ 리포트 작성 지침 ━━━
아래 순서로 작성하되, **각 섹션 제목을 굵게** 표시하세요. 총 600~900자.

1. **오늘의 시장 온도** (3~4줄)
   - TENBAGGER 비율과 평균 점수로 현재 시장이 과열·적정·저평가 중 어느 국면인지 판단
   - 이 국면에서 장기투자자가 취해야 할 전략 1가지 제시

2. **신규 발굴 종목 스포트라이트** (신규 있을 때만)
   - 왜 오늘 새로 TENBAGGER/COMPOUNDER에 진입했는지 핵심 지표 근거로 설명
   - 진입 고려 시 주의할 밸류에이션 포인트 1가지

3. **Top 3 심층 분석**
   - 각 종목마다: ① 핵심 강점 1줄 ② 잠재 리스크 1줄 ③ 장기 보유 thesis 1줄

4. **시스템 신뢰도**
   - 백테스트 결과 수치를 직접 인용해 시스템 예측 정확도 평가 (1~2줄)

5. **오늘의 행동 제안**
   - 오너가 오늘 실제로 확인해야 할 것 1~2가지 (구체적 종목코드 or 지표 포함)

⚠️ 마지막에 면책 고지 1줄 추가: "이 분석은 시스템 데이터 기반 참고 정보이며, 최종 투자 결정은 본인 판단으로 하시기 바랍니다."
"""

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        resp = await client.chat.completions.create(
            model="gpt-4o",
            max_tokens=1800,
            temperature=0.3,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt},
            ],
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        # gpt-4o 실패 시 mini로 폴백
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=settings.openai_api_key)
            resp = await client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=1800,
                temperature=0.3,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
            )
            return resp.choices[0].message.content.strip()
        except Exception as e2:
            return f"AI 분석 생성 실패: {e2}"


def build_html_report(
    top_candidates: list[dict],
    new_candidates: list[dict],
    stats: dict,
    ai_commentary: str,
    backtest_samples: list[dict],
) -> str:
    """HTML 이메일 리포트 생성"""
    today_str = TODAY.strftime("%Y년 %m월 %d일")
    grade_colors = {
        "TENBAGGER": ("#10b981", "⭐"),
        "COMPOUNDER": ("#3b82f6", "🟢"),
        "WATCHLIST": ("#f59e0b", "🟡"),
        "AVOID": ("#ef4444", "🔴"),
    }

    # 후보 종목 테이블
    rows = ""
    for s in top_candidates:
        color, icon = grade_colors.get(s.get("grade", "AVOID"), ("#888", ""))
        close_str = f"₩{int(s['close']):,}" if s.get("close") else "-"
        rows += f"""
        <tr>
          <td><strong>{s.get('name', '-')}</strong><br><span style="color:#888;font-size:11px;">{s.get('ticker')} · {s.get('market', '')}</span></td>
          <td style="text-align:center;font-size:18px;font-weight:800;color:{color};">{s.get('total_score', '-')}</td>
          <td style="text-align:center;"><span style="background:{color}22;color:{color};padding:3px 8px;border-radius:12px;font-size:11px;font-weight:700;">{icon} {s.get('grade', '-')}</span></td>
          <td style="text-align:right;">{f"{s['revenue_cagr_5y']:.1f}%" if s.get('revenue_cagr_5y') else "-"}</td>
          <td style="text-align:right;">{f"{s['avg_roe_5y']:.1f}%" if s.get('avg_roe_5y') else "-"}</td>
          <td style="text-align:right;">{close_str}</td>
        </tr>"""

    # 신규 발굴 알림
    new_alert = ""
    if new_candidates:
        names = ", ".join([f"{s['name']}({s['ticker']})" for s in new_candidates])
        new_alert = f"""
        <div style="background:#10b98122;border:1px solid #10b981;border-radius:12px;padding:16px 20px;margin-bottom:24px;">
          <div style="color:#10b981;font-weight:700;font-size:14px;margin-bottom:6px;">⭐ 오늘 신규 발굴된 텐배거 후보</div>
          <div style="color:#d1fae5;font-size:13px;">{names}</div>
        </div>"""

    # 백테스트 정확도
    bt_rows = ""
    for r in backtest_samples:
        ret = r.get("actual_return")
        ret_str = f"+{ret:.1f}%" if ret and ret >= 0 else (f"{ret:.1f}%" if ret else "N/A")
        color = "#10b981" if ret and ret > 0 else "#ef4444"
        bt_rows += f"<tr><td>{r.get('ticker')}</td><td>{r.get('base_year')}년 매수</td><td style='color:{color};font-weight:700;'>{ret_str}</td></tr>"

    # AI 코멘터리 → HTML 변환
    ai_html = ai_commentary.replace("\n", "<br>").replace("**", "<strong>").replace("**", "</strong>")
    import re
    ai_html = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', ai_commentary.replace("\n", "<br>"))

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>텐배거 헌터 — {today_str} 일일 리포트</title></head>
<body style="background:#0a0f1e;color:#f9fafb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;padding:0;">
<div style="max-width:680px;margin:0 auto;padding:32px 20px;">

  <!-- 헤더 -->
  <div style="background:linear-gradient(135deg,#1e3a8a,#4c1d95);border-radius:16px;padding:24px 28px;margin-bottom:24px;">
    <div style="font-size:22px;font-weight:800;margin-bottom:4px;">🚀 텐배거 헌터</div>
    <div style="font-size:13px;color:#93c5fd;">{today_str} · 일일 투자 인텔리전스 리포트</div>
  </div>

  <!-- 통계 요약 -->
  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:24px;">
    <div style="background:#111827;border:1px solid #374151;border-radius:12px;padding:16px;text-align:center;">
      <div style="font-size:10px;color:#9ca3af;margin-bottom:4px;text-transform:uppercase;">분석 종목</div>
      <div style="font-size:28px;font-weight:800;color:#60a5fa;">{stats['total_analyzed']}</div>
    </div>
    <div style="background:#111827;border:1px solid #10b98144;border-radius:12px;padding:16px;text-align:center;">
      <div style="font-size:10px;color:#9ca3af;margin-bottom:4px;text-transform:uppercase;">⭐ TENBAGGER</div>
      <div style="font-size:28px;font-weight:800;color:#10b981;">{stats['grades']['TENBAGGER']}</div>
    </div>
    <div style="background:#111827;border:1px solid #3b82f644;border-radius:12px;padding:16px;text-align:center;">
      <div style="font-size:10px;color:#9ca3af;margin-bottom:4px;text-transform:uppercase;">COMPOUNDER</div>
      <div style="font-size:28px;font-weight:800;color:#60a5fa;">{stats['grades']['COMPOUNDER']}</div>
    </div>
  </div>

  <!-- 신규 알림 -->
  {new_alert}

  <!-- AI 투자 의견 -->
  <div style="background:#111827;border:1px solid #374151;border-radius:14px;padding:22px 24px;margin-bottom:24px;">
    <div style="font-size:13px;font-weight:700;color:#a78bfa;margin-bottom:14px;">🤖 AI 투자 인사이트</div>
    <div style="font-size:13px;line-height:1.8;color:#d1d5db;">{ai_html}</div>
  </div>

  <!-- 상위 후보 테이블 -->
  <div style="background:#111827;border:1px solid #374151;border-radius:14px;overflow:hidden;margin-bottom:24px;">
    <div style="padding:16px 20px;border-bottom:1px solid #374151;font-size:13px;font-weight:700;color:#9ca3af;">📊 상위 텐배거 후보 종목</div>
    <table style="width:100%;border-collapse:collapse;font-size:12.5px;">
      <thead>
        <tr style="background:#1f2937;">
          <th style="padding:10px 14px;text-align:left;color:#6b7280;font-weight:600;font-size:11px;">종목</th>
          <th style="padding:10px 14px;text-align:center;color:#6b7280;font-weight:600;font-size:11px;">점수</th>
          <th style="padding:10px 14px;text-align:center;color:#6b7280;font-weight:600;font-size:11px;">등급</th>
          <th style="padding:10px 14px;text-align:right;color:#6b7280;font-weight:600;font-size:11px;">매출CAGR</th>
          <th style="padding:10px 14px;text-align:right;color:#6b7280;font-weight:600;font-size:11px;">ROE</th>
          <th style="padding:10px 14px;text-align:right;color:#6b7280;font-weight:600;font-size:11px;">주가</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </div>

  <!-- 백테스트 정확도 -->
  {"" if not bt_rows else f'''
  <div style="background:#111827;border:1px solid #374151;border-radius:14px;overflow:hidden;margin-bottom:24px;">
    <div style="padding:16px 20px;border-bottom:1px solid #374151;font-size:13px;font-weight:700;color:#9ca3af;">🧪 백테스트 정확도 샘플</div>
    <table style="width:100%;border-collapse:collapse;font-size:12.5px;">
      <tr style="background:#1f2937;"><th style="padding:8px 14px;text-align:left;color:#6b7280;font-size:11px;">종목</th><th style="color:#6b7280;font-size:11px;">기준</th><th style="color:#6b7280;font-size:11px;text-align:right;padding-right:14px;">실제 수익률</th></tr>
      {bt_rows}
    </table>
  </div>'''}

  <!-- 푸터 -->
  <div style="text-align:center;padding:20px;font-size:11px;color:#4b5563;border-top:1px solid #1f2937;">
    텐배거 헌터 자동 리포트 · {today_str}<br>
    이 리포트는 투자 참고용이며 투자 손실에 대한 책임을 지지 않습니다.
  </div>
</div>
</body>
</html>"""
    return html


def get_subscribers() -> list[str]:
    """구독자 목록 반환.
    환경변수 EMAIL_SUBSCRIBERS에 콤마 구분으로 입력.
    없으면 EMAIL_RECIPIENT 단일 수신자로 fallback.
    예) EMAIL_SUBSCRIBERS=a@gmail.com,b@naver.com
    """
    subscribers_env = os.environ.get("EMAIL_SUBSCRIBERS", "")
    if subscribers_env:
        return [e.strip() for e in subscribers_env.split(",") if e.strip()]
    fallback = os.environ.get("EMAIL_RECIPIENT", "yoonchang.park@gmail.com")
    return [fallback]


def _build_kakao_push_message(top_candidates: list, new_candidates: list, stats: dict) -> str:
    """카카오 Push 알림용 요약 메시지 (990자 이내)"""
    today_str = TODAY.strftime("%m/%d")
    lines = [f"📊 [{today_str}] 텐배거 헌터 일일 리포트\n"]

    tenbagger_cnt = stats["grades"].get("TENBAGGER", 0)
    total = stats.get("total_analyzed", 0)
    lines.append(f"분석 종목: {total}개 | TENBAGGER: {tenbagger_cnt}개\n")

    if new_candidates:
        lines.append(f"🆕 신규 발굴 {len(new_candidates)}개:")
        for s in new_candidates[:3]:
            lines.append(f"  ⭐ {s['name']} ({s['ticker']}) {s['total_score']:.1f}점")
        lines.append("")

    if top_candidates:
        lines.append("🏆 TOP 3 종목:")
        for s in top_candidates[:3]:
            cagr = f"+{s['revenue_cagr_5y']:.1f}%" if s.get("revenue_cagr_5y") else "-"
            lines.append(f"  • {s['name']} ({s['ticker']}) {s['total_score']:.1f}점 | 매출CAGR {cagr}")

    lines.append(f"\n🔗 상세 분석 보기:")
    base = os.environ.get("SERVICE_URL", "https://tenbagger-production.up.railway.app")
    lines.append(f"{base}/screener.html")

    msg = "\n".join(lines)
    return msg[:990]


def send_email(html_content: str, recipient: str, subject: str) -> bool:
    """Gmail SMTP로 HTML 이메일 발송"""
    sender = os.environ.get("EMAIL_SENDER", "")
    password = os.environ.get("EMAIL_PASSWORD", "")

    if not sender or not password:
        print("[ADVISOR] 이메일 설정 없음 (EMAIL_SENDER, EMAIL_PASSWORD 환경변수 필요)")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"텐배거 헌터 <{sender}>"
        msg["To"] = recipient
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, recipient, msg.as_string())

        print(f"[ADVISOR] 이메일 발송 완료 → {recipient}")
        return True
    except Exception as e:
        print(f"[ADVISOR] 이메일 발송 실패 → {recipient}: {e}")
        return False


def send_to_all_subscribers(html_content: str, subject: str) -> dict:
    """전체 구독자에게 발송, 성공/실패 집계 반환"""
    subscribers = get_subscribers()
    success, fail = 0, 0
    for email in subscribers:
        if send_email(html_content, email, subject):
            success += 1
        else:
            fail += 1
    print(f"[ADVISOR] 발송 결과: 성공 {success}명 / 실패 {fail}명 (총 {len(subscribers)}명)")
    return {"success": success, "fail": fail, "total": len(subscribers)}


def save_report(html_content: str):
    """리포트 파일로 저장"""
    os.makedirs("/app/reports", exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"[ADVISOR] 리포트 저장: {REPORT_PATH}")


async def run_etl_refresh():
    """ETL 실행 (skip-existing)"""
    print("[ADVISOR] ETL 전종목 갱신 시작...")
    import subprocess
    proc = subprocess.run(
        ["python", "-m", "app.workers.etl", "--market", "ALL", "--skip-existing"],
        capture_output=True, text=True, timeout=7200,
    )
    print(f"[ADVISOR] ETL 완료 (return code: {proc.returncode})")


async def main(dry_run: bool = False, skip_etl: bool = False):
    print(f"\n{'='*60}")
    print(f"  텐배거 헌터 Daily Advisor — {TODAY.isoformat()}")
    print(f"  dry_run={dry_run}, skip_etl={skip_etl}")
    print(f"{'='*60}\n")

    # 1. ETL 갱신
    if not skip_etl:
        await run_etl_refresh()
    else:
        print("[ADVISOR] ETL 스킵")

    # 2. DB 조회
    print("[ADVISOR] 스코어 데이터 로드 중...")
    all_scores = get_all_scores()
    if not all_scores:
        print("[ADVISOR] DB에 데이터 없음. 종료.")
        return

    top_candidates = get_top_candidates(all_scores, n=10)
    new_candidates = get_new_candidates(all_scores)
    stats = get_accuracy_stats(all_scores)

    print(f"[ADVISOR] 총 {stats['total_analyzed']}개 | TENBAGGER {stats['grades']['TENBAGGER']}개 | 신규 {len(new_candidates)}개")

    # 3. 백테스트 샘플 (상위 5개)
    top_tickers = [s["ticker"] for s in top_candidates[:5]]
    print(f"[ADVISOR] 백테스트 샘플 실행 중... ({top_tickers})")
    backtest_samples = await run_backtest_sample(top_tickers, base_year=2020)

    # 4. AI 투자 의견 생성
    print("[ADVISOR] GPT 투자 의견 생성 중...")
    ai_commentary = await generate_investment_report(
        top_candidates, new_candidates, stats, backtest_samples
    )

    # 5. HTML 리포트 생성
    html = build_html_report(top_candidates, new_candidates, stats, ai_commentary, backtest_samples)
    save_report(html)

    # 6. 이메일 + 카카오 Push 발송
    subject = f"[텐배거헌터] {TODAY.strftime('%m/%d')} 일일 리포트 — TENBAGGER {stats['grades']['TENBAGGER']}개 발굴"
    if new_candidates:
        subject = f"⭐ [텐배거헌터] {TODAY.strftime('%m/%d')} 신규 텐배거 {len(new_candidates)}개 발굴!"

    if not dry_run:
        # 6-1. 이메일 발송
        result = send_to_all_subscribers(html, subject)
        if result["success"] == 0:
            print(f"[ADVISOR] 전체 발송 실패 → 리포트 파일 확인: {REPORT_PATH}")

        # 6-2. 카카오 챗봇 Push 발송 (베타: 구독 등급 무관 전체)
        kakao_msg = _build_kakao_push_message(top_candidates, new_candidates, stats)
        try:
            from app.api.kakao import send_kakao_push_to_all
            push_result = await asyncio.to_thread(send_kakao_push_to_all, kakao_msg)
            print(f"[ADVISOR] 카카오 Push: 성공 {push_result['sent']}건, 실패 {push_result.get('failed',0)}건")
        except Exception as e:
            print(f"[ADVISOR] 카카오 Push 오류 (무시): {e}")
    else:
        subscribers = get_subscribers()
        print(f"[ADVISOR] dry-run 모드 → 발송 생략. 이메일 구독자 {len(subscribers)}명. 리포트: {REPORT_PATH}")

    print(f"\n{'='*60}")
    print(f"  ✅ 어드바이저 완료!")
    print(f"  리포트: {REPORT_PATH}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="텐배거 헌터 일일 어드바이저")
    parser.add_argument("--dry-run", action="store_true", help="이메일 발송 없이 리포트만 생성")
    parser.add_argument("--skip-etl", action="store_true", help="ETL 갱신 건너뜀")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run, skip_etl=args.skip_etl))
