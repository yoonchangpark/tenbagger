"""
Phase D — 이벤트 드리븐 분석 API
GET /api/v2/company/{ticker}/phase_d

유통주식 · 현금 이상 · 자진상폐 스코어 · 투자 의견 반환
"""
import asyncio
import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException

from app.infra.clients.dart_client import _load_corp_cache, _get, get_company_info

router = APIRouter()

# 인메모리 캐시 (6시간 TTL — DART 호출 최소화)
_pd_cache: dict = {}
_PD_TTL = 3600 * 6


# ── 유틸 ──────────────────────────────────────────────────────────────────

def _n(s) -> Optional[float]:
    if s is None:
        return None
    s = str(s).replace(",", "").replace(" ", "").strip()
    if s in ("", "-", "N/A", "－"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _acct(items: list, names: list, field: str = "thstrm_amount") -> Optional[float]:
    for name in names:
        for it in items:
            if it.get("account_nm", "").strip() == name:
                v = _n(it.get(field))
                if v is not None:
                    return v
    return None


# ── DART 데이터 수집 ──────────────────────────────────────────────────────

async def _fetch_financials(corp_code: str, year: int) -> list:
    for fs in ("CFS", "OFS"):
        try:
            r = await _get("fnlttSinglAcntAll.json", {
                "corp_code": corp_code, "bsns_year": str(year),
                "reprt_code": "11011", "fs_div": fs,
            })
            items = r.get("list", [])
            if items:
                return items
        except Exception:
            continue
    return []


async def _fetch_major_shareholders(corp_code: str) -> list:
    """대량보유상황보고서 — 최근 지분 변동 이력"""
    year = datetime.date.today().year
    for y in (year, year - 1):
        for reprt in ("11013", "11012", "11011"):
            try:
                r = await _get("majorstock.json", {
                    "corp_code": corp_code, "bsns_year": str(y),
                    "reprt_code": reprt,
                })
                items = r.get("list", [])
                if items:
                    return items
            except Exception:
                continue
    return []


async def _fetch_stock_totqy(corp_code: str, year: int) -> dict:
    """주식 총수 현황 — 발행주식·자기주식·유통주식"""
    try:
        r = await _get("stockTotqySttus.json", {
            "corp_code": corp_code, "bsns_year": str(year),
            "reprt_code": "11011",
        })
        items = r.get("list", [])
        for it in items:
            if "보통주" in str(it.get("se", "")):
                return it
        return items[0] if items else {}
    except Exception:
        return {}


async def _fetch_recent_disclosures(corp_code: str, days: int = 365) -> list:
    end = datetime.date.today()
    bgn = end - datetime.timedelta(days=days)
    try:
        r = await _get("list.json", {
            "corp_code": corp_code,
            "bgn_de": bgn.strftime("%Y%m%d"),
            "end_de": end.strftime("%Y%m%d"),
            "page_count": "40",
        })
        return r.get("list", [])
    except Exception:
        return []


# ── 분석 로직 ─────────────────────────────────────────────────────────────

def _analyze(ticker, corp_name, company_info, fin_items, major_items, totqy, disclosures):
    year = datetime.date.today().year - 1

    # ── 1. 유통주식 ───────────────────────────────────────────────────────
    total_sh  = _n(totqy.get("istc_totqy"))
    treasury  = _n(totqy.get("tesstk_co")) or 0.0
    dart_dist = _n(totqy.get("distb_stock_co"))

    major_sh = None
    major_pct = None
    ctr_pct = None
    reporter = ""
    for it in major_items:
        sq = _n(it.get("stkqy"))
        sr = _n(it.get("stkrt"))
        if sq is not None and sr is not None:
            major_sh = sq
            major_pct = sr
            ctr_pct = _n(it.get("ctr_stkrt"))
            reporter = it.get("repror", "")
            break

    float_pct = None
    float_sh  = None
    trs_pct   = None
    if total_sh and total_sh > 0:
        trs_pct = round(treasury / total_sh * 100, 1)
        if major_sh is not None:
            float_sh  = total_sh - treasury - major_sh
            float_pct = round(float_sh / total_sh * 100, 1)
        elif dart_dist is not None:
            float_sh  = dart_dist
            float_pct = round(dart_dist / total_sh * 100, 1)

    # ── 2. 재무 데이터 ────────────────────────────────────────────────────
    equity   = _acct(fin_items, ["자본총계"])
    cash_cur = _acct(fin_items, ["현금및현금성자산"])
    cash_pre = _acct(fin_items, ["현금및현금성자산"], "frmtrm_amount")
    assets   = _acct(fin_items, ["자산총계"])

    # BPS 계산 — DART 재무제표 단위(원/천원) 자동 보정
    # 천원 단위 보고서는 BPS 원시값이 비정상적으로 낮게 나옴 (< 100원)
    _bps_raw = (equity / total_sh) if equity and total_sh else None
    if _bps_raw is not None and _bps_raw < 100:
        _bps_raw *= 1000  # 천원 단위 → 원 변환
    bps = round(_bps_raw) if _bps_raw else None
    cash_chg = None
    if cash_cur and cash_pre and cash_pre != 0:
        cash_chg = round((cash_cur - cash_pre) / abs(cash_pre) * 100, 1)
    cash_to_assets = round(cash_cur / assets * 100, 1) if cash_cur and assets else None

    # ── 3. 공개매수가 추정 ────────────────────────────────────────────────
    offer_min = round(bps * 1.20) if bps else None
    offer_30  = round(bps * 1.30) if bps else None

    # ── 4. 기업 개요 ──────────────────────────────────────────────────────
    ceo      = company_info.get("ceo_nm", "")
    est_dt   = company_info.get("est_dt", "")
    corp_age = None
    if est_dt and len(est_dt) >= 4:
        try:
            corp_age = 2025 - int(est_dt[:4])
        except ValueError:
            pass

    # ── 5. 이벤트 공시 감지 ───────────────────────────────────────────────
    HOT_KW  = ["자기주식취득", "자기주식소각", "공개매수", "상장폐지", "주식매수청구"]
    WARM_KW = ["대량보유", "특수관계인", "배당결정", "현금배당"]
    event_discs = []
    for d in disclosures:
        title = d.get("report_nm", "")
        dt    = d.get("rcept_dt", "")
        for kw in HOT_KW:
            if kw in title:
                event_discs.append({"title": title, "date": dt, "level": "hot"})
                break
        else:
            for kw in WARM_KW:
                if kw in title:
                    event_discs.append({"title": title, "date": dt, "level": "warm"})
                    break

    # ── 6. 자진상폐 스코어 ────────────────────────────────────────────────
    score = 0
    score_factors = []

    # 유통주식 비율 (40점 만점)
    if float_pct is not None:
        if float_pct < 10:
            pts = 40; lbl = "임박 구간 (10% 미만)"
        elif float_pct < 20:
            pts = 28; lbl = "경계 구간 (20% 미만)"
        elif float_pct < 35:
            pts = 14; lbl = "보통 수준"
        else:
            pts = 0; lbl = "일반 수준"
        score += pts
        score_factors.append({"factor": f"유통주식 {float_pct:.1f}%", "pts": pts, "detail": lbl})

    # 현금 급증 (15점)
    if cash_chg is not None and cash_chg >= 30:
        score += 15
        score_factors.append({"factor": f"현금 전기 대비 +{cash_chg:.0f}%", "pts": 15, "detail": "공개매수 실탄 확보 가능성"})

    # 현금/자산 비율 (20점)
    if cash_to_assets is not None:
        if cash_to_assets >= 30:
            pts = 20; lbl = "풍부한 현금 — 공개매수 여력 충분"
        elif cash_to_assets >= 15:
            pts = 12; lbl = "중간 수준"
        else:
            pts = 4; lbl = "현금 비중 낮음"
        score += pts
        score_factors.append({"factor": f"현금/총자산 {cash_to_assets:.1f}%", "pts": pts, "detail": lbl})

    # 창업 연수 (15점)
    if corp_age:
        if corp_age >= 40:
            score += 15
            score_factors.append({"factor": f"창업 {corp_age}년 경과", "pts": 15, "detail": "2세 승계 또는 자진상폐 검토 시점"})
        elif corp_age >= 25:
            score += 8
            score_factors.append({"factor": f"창업 {corp_age}년 경과", "pts": 8, "detail": "오너 세대 전환 가능성"})

    # 이벤트 공시 (최대 10점)
    hot_cnt = sum(1 for d in event_discs if d["level"] == "hot")
    warm_cnt = sum(1 for d in event_discs if d["level"] == "warm")
    disc_pts = min(hot_cnt * 10 + warm_cnt * 5, 10)
    if disc_pts > 0:
        score += disc_pts
        score_factors.append({"factor": f"이벤트 공시 {len(event_discs)}건", "pts": disc_pts, "detail": "관련 공시 감지"})

    score = min(score, 100)
    if score >= 70:
        grade = "HOT"; grade_color = "#ef4444"
    elif score >= 50:
        grade = "WARM"; grade_color = "#f59e0b"
    elif score >= 30:
        grade = "WATCH"; grade_color = "#3b82f6"
    else:
        grade = "COLD"; grade_color = "#6b7280"

    # ── 7. 시그널 목록 ────────────────────────────────────────────────────
    signals = []

    if float_pct is not None:
        if float_pct < 10:
            signals.append({
                "icon": "🚨", "label": "CRITICAL LOW FLOAT",
                "value": f"{float_pct:.1f}%",
                "color": "#ef4444",
                "meaning": f"유통주식이 {float_pct:.1f}%로 자진상폐 실행 가능 구간입니다. 대주주가 공개매수 또는 스퀴즈아웃을 즉시 실행할 수 있는 수준입니다.",
            })
        elif float_pct < 20:
            signals.append({
                "icon": "⚠️", "label": "LOW FLOAT",
                "value": f"{float_pct:.1f}%",
                "color": "#f59e0b",
                "meaning": f"유통주식 {float_pct:.1f}%. 대주주가 소량 추가 매집만으로 자진상폐 요건에 근접할 수 있습니다.",
            })

    if cash_chg is not None and cash_chg >= 30:
        signals.append({
            "icon": "🔥", "label": "CASH SURGE",
            "value": f"+{cash_chg:.0f}%",
            "color": "#f59e0b",
            "meaning": f"현금이 전기 대비 {cash_chg:.0f}% 급증했습니다. 자산 매각 또는 채권 만기로 공개매수 실탄이 쌓인 가능성이 있습니다.",
        })
    elif cash_chg is not None and cash_chg <= -30:
        signals.append({
            "icon": "📉", "label": "CASH DROP",
            "value": f"{cash_chg:.0f}%",
            "color": "#6b7280",
            "meaning": f"현금이 {cash_chg:.0f}% 급감했습니다. CAPEX 집행 또는 부채 상환 가능성이 있습니다.",
        })

    if hot_cnt > 0:
        signals.append({
            "icon": "📢", "label": "이벤트 공시",
            "value": f"{hot_cnt}건",
            "color": "#ef4444",
            "meaning": "자기주식취득·공개매수 등 직접 행동 공시가 감지됐습니다.",
        })
    elif warm_cnt > 0:
        signals.append({
            "icon": "📋", "label": "관련 공시",
            "value": f"{warm_cnt}건",
            "color": "#a78bfa",
            "meaning": "대량보유·배당결정 등 관련 공시가 감지됐습니다.",
        })

    # ── 8. 투자 의견 생성 ─────────────────────────────────────────────────
    opinion_lines = []

    if bps and offer_min:
        opinion_lines.append(
            f"BPS(주당순자산) {bps:,}원 기준 최소 공개매수가는 {offer_min:,}원(BPS×1.2), "
            f"30% 프리미엄 기준 {offer_30:,}원입니다. "
            "현재 주가가 이 수준보다 낮다면 공개매수 디스카운트 구간으로 안전마진이 존재합니다."
        )

    if float_pct and float_pct < 20 and major_pct:
        remain = round(float_pct - 5, 1)
        opinion_lines.append(
            f"현재 유통주식 {float_pct:.1f}%. 대주주가 시장에서 {remain:.0f}%p만 추가 매집하면 "
            "자진상폐 신청 가능 구간에 진입합니다. "
            "유통 주식이 희박할수록 소량 매수만으로도 주가가 급등할 수 있어 단기 스퀴즈 리스크도 내재합니다."
        )

    warm_discs = [d for d in event_discs if d["level"] == "warm" and "대량보유" in d["title"]]
    if len(warm_discs) >= 2:
        opinion_lines.append(
            f"대량보유상황보고서가 최근 {len(warm_discs)}회 연속 제출됐습니다. "
            "지분 변동이 현재 진행 중임을 의미합니다. 분기별 공시를 집중 모니터링하세요."
        )

    if corp_age and corp_age >= 40:
        opinion_lines.append(
            f"창업 {corp_age}년차 오너 기업으로 세대 전환 또는 경영권 정리 시점에 있습니다. "
            "자진상폐를 통한 비상장화 선택지가 오너 입장에서 유리할 수 있습니다."
        )

    if not opinion_lines:
        opinion_lines.append("현재 확보된 데이터만으로는 뚜렷한 이벤트 시그널이 없습니다. 지속 모니터링을 권장합니다.")

    return {
        "ticker": ticker,
        "corp_name": corp_name,
        "score": score,
        "grade": grade,
        "grade_color": grade_color,
        "signals": signals,
        "opinion": " ".join(opinion_lines),
        "score_factors": score_factors,
        "float": {
            "total_shares": total_sh,
            "major_pct": major_pct,
            "treasury_pct": trs_pct,
            "float_pct": float_pct,
            "float_shares": float_sh,
            "reporter": reporter,
            "ctr_pct": ctr_pct,
        },
        "cash": {
            "current": cash_cur,
            "prev": cash_pre,
            "change_pct": cash_chg,
            "to_assets_pct": cash_to_assets,
        },
        "offer": {
            "bps": bps,
            "min_price": offer_min,
            "price_30pct": offer_30,
        },
        "company": {
            "ceo": ceo,
            "corp_age": corp_age,
            "est_dt": est_dt,
        },
        "event_disclosures": event_discs[:5],
    }


# ── API 엔드포인트 ────────────────────────────────────────────────────────

@router.get("/api/v2/company/{ticker}/phase_d")
async def get_phase_d(ticker: str):
    """
    Phase D 이벤트 드리븐 분석.
    유통주식·현금 이상·자진상폐 스코어·투자 의견 반환.
    결과는 6시간 캐시.
    """
    now = datetime.datetime.utcnow().timestamp()
    if ticker in _pd_cache:
        result, ts = _pd_cache[ticker]
        if now - ts < _PD_TTL:
            return result

    # corp_code 조회
    cache = await _load_corp_cache()
    corp_code, corp_name = None, ticker
    for item in cache:
        if item["stock_code"] == ticker:
            corp_code = item["corp_code"]
            corp_name = item["corp_name"]
            break

    if not corp_code:
        raise HTTPException(404, f"종목코드 {ticker} 미발견")

    year = datetime.date.today().year - 1

    # 병렬 DART 호출
    company_info, fin_items, major_items, totqy, disclosures = await asyncio.gather(
        get_company_info(corp_code),
        _fetch_financials(corp_code, year),
        _fetch_major_shareholders(corp_code),
        _fetch_stock_totqy(corp_code, year),
        _fetch_recent_disclosures(corp_code, 365),
    )

    result = _analyze(ticker, corp_name, company_info, fin_items, major_items, totqy, disclosures)
    _pd_cache[ticker] = (result, now)
    return result
