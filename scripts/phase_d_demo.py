"""
Phase D — 이벤트 드리븐 분석 데모
===================================
자진상폐·공개매수·유통주식·현금 이상 변동을 감지하는 독립 실행 스크립트.
FastAPI/Docker 불필요 — Python 3.11 + httpx + asyncio 만으로 실행.

실행:
  cd tenbagger/scripts
  pip install httpx
  python phase_d_demo.py 016590         # 신대양제지 (기본)
  python phase_d_demo.py 005930         # 삼성전자
  python phase_d_demo.py 016590 2023    # 특정 연도 기준
"""

import asyncio
import io
import math
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional
import httpx

# Windows 콘솔 UTF-8 출력
if sys.platform == "win32":
    import io as _io
    sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = _io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── 환경 변수에서 DART API 키 로드 ──────────────────────────────────────────
import os
from pathlib import Path

def _load_env():
    """스크립트 위치 기준으로 ../.env 탐색 후 로드"""
    env_path = Path(__file__).parent.parent / "backend" / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

_load_env()
DART_KEY = os.environ.get("DART_API_KEY", "")
DART_BASE = "https://opendart.fss.or.kr/api"

if not DART_KEY:
    print("❌  DART_API_KEY 미설정 — backend/.env 파일을 확인하세요.")
    sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# DART 저수준 HTTP 헬퍼
# ══════════════════════════════════════════════════════════════════════════════

async def _get(path: str, params: dict) -> dict:
    params = {"crtfc_key": DART_KEY, **params}
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.get(f"{DART_BASE}/{path}", params=params)
        r.raise_for_status()
        return r.json()


def _parse_num(s) -> Optional[float]:
    """DART 숫자 문자열 → float (쉼표·공백 제거)"""
    if s is None:
        return None
    s = str(s).replace(",", "").replace(" ", "").strip()
    if s in ("", "-", "N/A", "－"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _extract_account(items: list, names: list) -> Optional[float]:
    """재무제표 항목 리스트에서 계정명으로 당기 금액 추출"""
    for name in names:
        for it in items:
            if it.get("account_nm", "").strip() == name:
                val = _parse_num(it.get("thstrm_amount"))
                if val is not None:
                    return val
    return None


def _extract_account_prev(items: list, names: list) -> Optional[float]:
    """전기(frmtrm_amount) 추출"""
    for name in names:
        for it in items:
            if it.get("account_nm", "").strip() == name:
                val = _parse_num(it.get("frmtrm_amount"))
                if val is not None:
                    return val
    return None


# ══════════════════════════════════════════════════════════════════════════════
# DART 데이터 수집 함수들
# ══════════════════════════════════════════════════════════════════════════════

async def resolve_corp_code(ticker: str) -> tuple[str, str]:
    """종목코드 → (corp_code, corp_name)"""
    print(f"  🔍 종목 코드 조회 중: {ticker}")
    async with httpx.AsyncClient(timeout=60.0) as c:
        r = await c.get(f"{DART_BASE}/corpCode.xml", params={"crtfc_key": DART_KEY})
        r.raise_for_status()

    zf = zipfile.ZipFile(io.BytesIO(r.content))
    xml_data = zf.read("CORPCODE.xml")
    root = ET.fromstring(xml_data)

    for item in root.findall("list"):
        code = item.findtext("stock_code", "").strip()
        if code == ticker:
            return item.findtext("corp_code", "").strip(), item.findtext("corp_name", "").strip()

    raise ValueError(f"종목코드 {ticker}를 DART에서 찾을 수 없습니다.")


async def fetch_company_info(corp_code: str) -> dict:
    """기업 기본 정보 (대표이사, 설립일, 상장일 등)"""
    try:
        return await _get("company.json", {"corp_code": corp_code})
    except Exception as e:
        print(f"  ⚠️ company.json 실패: {e}")
        return {}


async def fetch_major_shareholders(corp_code: str, year: int) -> list:
    """최대주주 및 특수관계인 지분 현황 (majorstock.json)"""
    for reprt in ["11013", "11012", "11011"]:  # 1Q → 반기 → 사업보고서 순 시도
        try:
            r = await _get("majorstock.json", {
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": reprt,
            })
            items = r.get("list", [])
            if items:
                return items
        except Exception:
            continue
    return []


async def fetch_stock_totqy(corp_code: str, year: int) -> dict:
    """주식 총수 현황 (stockTotqySttus.json)
    반환 필드: istc_totqy(발행총수), tesstk_co(자기주식), distb_stock_co(유통주식)
    """
    try:
        r = await _get("stockTotqySttus.json", {
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": "11011",
        })
        items = r.get("list", [])
        # 보통주 행 우선
        for it in items:
            if "보통주" in str(it.get("se", "")):
                return it
        return items[0] if items else {}
    except Exception as e:
        print(f"  ⚠️ stockTotqySttus.json 실패: {e}")
        return {}


async def fetch_financials(corp_code: str, year: int) -> list:
    """재무제표 항목 전체 (연결 우선, 없으면 별도)"""
    for fs_div in ("CFS", "OFS"):
        try:
            r = await _get("fnlttSinglAcntAll.json", {
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": "11011",
                "fs_div": fs_div,
            })
            items = r.get("list", [])
            if items:
                return items
        except Exception:
            continue
    return []


async def fetch_recent_disclosures(corp_code: str, days: int = 180) -> list:
    """최근 공시 목록"""
    import datetime
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
    except Exception as e:
        print(f"  ⚠️ list.json 실패: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
# Phase D 분석 로직
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class FloatAnalysis:
    total_shares: Optional[float] = None         # 발행주식 총수
    major_holder_pct: Optional[float] = None     # 최대주주+특수관계인 %
    treasury_pct: Optional[float] = None         # 자사주 %
    float_pct: Optional[float] = None            # 유통주식 %
    float_shares: Optional[float] = None         # 유통주식 수 (주)
    major_holders: list = field(default_factory=list)
    signal: str = "NEUTRAL"                      # LOW_FLOAT / SQUEEZE_RISK / NEUTRAL
    detail: str = ""


@dataclass
class TenderOfferEstimate:
    bps: Optional[float] = None                  # 주당 순자산 (BPS)
    min_offer_price: Optional[float] = None      # 최소 공개매수가 (BPS × 1.2)
    premium_20pct: Optional[float] = None
    premium_30pct: Optional[float] = None
    total_equity: Optional[float] = None
    total_shares: Optional[float] = None
    signal: str = "NEUTRAL"


@dataclass
class CashAnomalyResult:
    cash_current: Optional[float] = None         # 당기 현금
    cash_prev: Optional[float] = None            # 전기 현금
    cash_change_pct: Optional[float] = None
    foreign_curr_current: Optional[float] = None # 당기 외화자산
    foreign_curr_prev: Optional[float] = None
    total_assets: Optional[float] = None
    cash_to_assets_pct: Optional[float] = None
    signal: str = "NEUTRAL"                      # CASH_SURGE / CASH_DROP / NORMAL
    detail: str = ""


@dataclass
class DelistingScore:
    score: int = 0                               # 0~100
    grade: str = "WATCH"                         # HOT / WARM / WATCH / COLD
    factors: list = field(default_factory=list)
    opportunity: str = ""


# ── 분석 함수들 ─────────────────────────────────────────────────────────────

def _analyze_float(major_items: list, totqy_item: dict, _unused: list) -> FloatAnalysis:
    """
    유통주식 분석.
    - totqy_item: stockTotqySttus.json 보통주 행 (istc_totqy, tesstk_co, distb_stock_co)
    - major_items: majorstock.json 대량보유보고서 리스트 (stkqy, stkrt 사용)
    """
    fa = FloatAnalysis()

    # 1. 발행주식 총수 & 자기주식 — stockTotqySttus 기준
    total = _parse_num(totqy_item.get("istc_totqy"))
    treasury = _parse_num(totqy_item.get("tesstk_co"))
    dart_distb = _parse_num(totqy_item.get("distb_stock_co"))  # DART 계산 유통주식
    fa.total_shares = total

    # 2. 최대주주+특수관계인 — 대량보유보고서 가장 최근 항목 사용
    # items는 rcept_dt 내림차순이므로 [0]이 가장 최근
    major_shares = None
    major_pct = None
    ctr_pct = None

    for it in major_items:
        sq = _parse_num(it.get("stkqy"))
        sr = _parse_num(it.get("stkrt"))
        if sq is not None and sr is not None:
            major_shares = sq
            major_pct = sr
            ctr_pct = _parse_num(it.get("ctr_stkrt"))
            reporter = it.get("repror", "")
            fa.major_holders = [f"{reporter}+특수관계인({major_pct:.1f}%)"]
            if ctr_pct:
                fa.major_holders.append(f"  └ 특수관계인 {ctr_pct:.1f}%")
            break

    # 3. 유통주식 계산
    # 진짜 float = 발행총수 - 자기주식 - 최대주주 보유
    if total and total > 0:
        treasury = treasury or 0.0
        fa.treasury_pct = round(treasury / total * 100, 1)

        if major_shares is not None:
            fa.major_holder_pct = major_pct  # 이미 % 값
            real_float_shares = total - treasury - major_shares
            fa.float_pct = round(real_float_shares / total * 100, 1)
            fa.float_shares = real_float_shares
        elif dart_distb is not None:
            # 대량보유보고서 없을 때: DART 기준 유통주식만 표시
            fa.float_pct = round(dart_distb / total * 100, 1)
            fa.float_shares = dart_distb
            fa.major_holder_pct = round((total - treasury - dart_distb) / total * 100, 1)

    # 4. 시그널 판단
    if fa.float_pct is not None:
        if fa.float_pct < 10:
            fa.signal = "LOW_FLOAT_CRITICAL"
            fa.detail = f"유통주식 {fa.float_pct:.1f}% — 자진상폐 실행 가능 구간. 공개매수 or 스퀴즈아웃 가능성 높음."
        elif fa.float_pct < 20:
            fa.signal = "LOW_FLOAT"
            fa.detail = f"유통주식 {fa.float_pct:.1f}% — 낮은 float. 대주주 추가 매집 시 자진상폐 조건 달성 근접."
        elif fa.float_pct < 35:
            fa.signal = "MODERATE_FLOAT"
            fa.detail = f"유통주식 {fa.float_pct:.1f}% — 보통 수준. 지속 모니터링 필요."
        else:
            fa.signal = "NORMAL"
            fa.detail = f"유통주식 {fa.float_pct:.1f}% — 정상 float 수준."
    else:
        fa.detail = "주주현황 데이터 부족 — DART API 응답 확인 필요."

    return fa


def _estimate_tender_offer(fin_items: list, float_analysis: FloatAnalysis) -> TenderOfferEstimate:
    te = TenderOfferEstimate()

    # BPS = 자본총계 / 발행주식수
    equity = _extract_account(fin_items, ["자본총계"])
    te.total_equity = equity

    total_sh = float_analysis.total_shares
    te.total_shares = total_sh

    if equity and total_sh and total_sh > 0:
        te.bps = round(equity / total_sh)
        te.min_offer_price = round(te.bps * 1.20)
        te.premium_20pct = round(te.bps * 1.20)
        te.premium_30pct = round(te.bps * 1.30)

        if te.bps > 0:
            te.signal = "CALCULABLE"
    else:
        te.signal = "INSUFFICIENT_DATA"

    return te


def _detect_cash_anomaly(fin_items: list) -> CashAnomalyResult:
    ca = CashAnomalyResult()

    ca.cash_current = _extract_account(fin_items, ["현금및현금성자산"])
    ca.cash_prev    = _extract_account_prev(fin_items, ["현금및현금성자산"])
    ca.total_assets = _extract_account(fin_items, ["자산총계"])

    # 단기금융상품·기타 유동성 자산도 추출
    short_term = _extract_account(fin_items, ["단기금융상품", "단기투자자산", "기타유동금융자산"])

    # 외화 자산 (현금으로 봐도 무방한 달러 예금 등)
    ca.foreign_curr_current = _extract_account(fin_items, ["외화현금및현금성자산", "외화예금"])

    if ca.cash_current and ca.total_assets and ca.total_assets > 0:
        ca.cash_to_assets_pct = round(ca.cash_current / ca.total_assets * 100, 1)

    # QoQ(전기 대비) 변동률
    if ca.cash_current and ca.cash_prev and ca.cash_prev != 0:
        ca.cash_change_pct = round((ca.cash_current - ca.cash_prev) / abs(ca.cash_prev) * 100, 1)

    # 시그널
    if ca.cash_change_pct is not None:
        if ca.cash_change_pct >= 30:
            ca.signal = "CASH_SURGE"
            ca.detail = f"현금 전기 대비 +{ca.cash_change_pct:.1f}% 급증 — 자산 매각·자사주 매입·배당 실탄 확보 가능."
        elif ca.cash_change_pct <= -30:
            ca.signal = "CASH_DROP"
            ca.detail = f"현금 전기 대비 {ca.cash_change_pct:.1f}% 급감 — CAPEX 집행 또는 부채 상환 여부 확인 필요."
        else:
            ca.signal = "NORMAL"
            ca.detail = f"현금 전기 대비 {ca.cash_change_pct:+.1f}% — 정상 범위."
    else:
        ca.signal = "UNKNOWN"
        ca.detail = "전기 현금 데이터 없음."

    return ca


def _compute_delisting_score(
    company_info: dict,
    fa: FloatAnalysis,
    te: TenderOfferEstimate,
    ca: CashAnomalyResult,
    disclosures: list,
) -> DelistingScore:
    ds = DelistingScore()
    factors = []
    score = 0

    # ① 유통주식 비율 (최대 40점)
    if fa.float_pct is not None:
        if fa.float_pct < 10:
            pts = 40
            factors.append(f"[+{pts}] 유통주식 {fa.float_pct:.1f}% — 임박 구간")
        elif fa.float_pct < 20:
            pts = 28
            factors.append(f"[+{pts}] 유통주식 {fa.float_pct:.1f}% — 경계 구간")
        elif fa.float_pct < 35:
            pts = 14
            factors.append(f"[+{pts}] 유통주식 {fa.float_pct:.1f}% — 보통")
        else:
            pts = 0
            factors.append(f"[+{pts}] 유통주식 {fa.float_pct:.1f}% — 일반")
        score += pts

    # ② 현금 자산 풍부도 (최대 20점)
    if ca.cash_to_assets_pct is not None:
        if ca.cash_to_assets_pct >= 30:
            pts = 20
            factors.append(f"[+{pts}] 현금/자산 {ca.cash_to_assets_pct:.1f}% — 공개매수 실탄 충분")
        elif ca.cash_to_assets_pct >= 15:
            pts = 12
            factors.append(f"[+{pts}] 현금/자산 {ca.cash_to_assets_pct:.1f}% — 중간 수준")
        else:
            pts = 4
            factors.append(f"[+{pts}] 현금/자산 {ca.cash_to_assets_pct:.1f}% — 낮음")
        score += pts

    # ③ 현금 급증 시그널 (최대 15점)
    if ca.signal == "CASH_SURGE":
        pts = 15
        factors.append(f"[+{pts}] 현금 급증 (전기 대비 +{ca.cash_change_pct:.1f}%) — 상폐 준비 가능성")
        score += pts

    # ④ 대표이사 나이 / 승계 리스크 (최대 15점)
    ceo = company_info.get("ceo_nm", "")
    est_year = company_info.get("est_dt", "")
    if ceo:
        factors.append(f"[ 0] 대표이사: {ceo} (설립: {est_year[:4] if est_year else '?'}년)")
    # 회사 설립 후 30년 이상 → 오너 고령화 가능성
    if est_year and len(est_year) >= 4:
        try:
            age_of_company = 2025 - int(est_year[:4])
            if age_of_company >= 40:
                pts = 15
                factors.append(f"[+{pts}] 창업 {age_of_company}년 경과 — 2세 승계 또는 자진상폐 검토 시점")
                score += pts
            elif age_of_company >= 25:
                pts = 8
                factors.append(f"[+{pts}] 창업 {age_of_company}년 경과 — 오너 세대 전환 가능성 존재")
                score += pts
        except ValueError:
            pass

    # ⑤ 최근 공시 이벤트 키워드 (최대 10점)
    hot_keywords = ["자기주식취득", "공개매수", "상장폐지", "주식매수청구권", "자진상폐"]
    warm_keywords = ["특수관계인", "대량보유", "주식양수도", "임원변경"]
    disc_signals = []
    for d in disclosures:
        title = d.get("report_nm", "")
        for kw in hot_keywords:
            if kw in title:
                disc_signals.append(f"🔥 {title} ({d.get('rcept_dt', '')})")
                score += 10
                break
        for kw in warm_keywords:
            if kw in title:
                disc_signals.append(f"🌡 {title} ({d.get('rcept_dt', '')})")
                score += 5
                break

    if disc_signals:
        factors.append(f"[이벤트 공시] " + " / ".join(disc_signals[:3]))

    # 등급 결정
    score = min(score, 100)
    ds.score = score
    ds.factors = factors

    if score >= 70:
        ds.grade = "HOT"
        ds.opportunity = "🔥 자진상폐 고위험/고기회 — 즉시 세부 분석 필요"
    elif score >= 50:
        ds.grade = "WARM"
        ds.opportunity = "🌡 자진상폐 가능성 있음 — 분기별 지분 변동 모니터링 권장"
    elif score >= 30:
        ds.grade = "WATCH"
        ds.opportunity = "👀 관심 후보 — 중장기 지분율 추이 확인"
    else:
        ds.grade = "COLD"
        ds.opportunity = "❄ 자진상폐 가능성 낮음"

    return ds


# ══════════════════════════════════════════════════════════════════════════════
# 메인 분석 파이프라인
# ══════════════════════════════════════════════════════════════════════════════

def _fmt_num(n: Optional[float], unit: str = "억원") -> str:
    if n is None:
        return "N/A"
    if unit == "억원":
        return f"{n/1e8:,.0f}억원"
    if unit == "주":
        return f"{n:,.0f}주"
    if unit == "원":
        return f"{n:,.0f}원"
    return f"{n:,.2f}"


async def analyze(ticker: str, year: int):
    print(f"\n{'='*60}")
    print(f"  Phase D — 이벤트 드리븐 분석 데모")
    print(f"  종목: {ticker}  |  기준연도: {year}")
    print(f"{'='*60}\n")

    # ── 1. 기본 정보 ─────────────────────────────────────────────────────────
    print("📡 [1/5] DART 기업 코드 조회...")
    corp_code, corp_name = await resolve_corp_code(ticker)
    print(f"  ✅ {corp_name} (corp_code: {corp_code})")

    print("\n📡 [2/5] 기업 기본 정보 및 재무 데이터 수집 (병렬)...")
    company_info, fin_items, major_items, totqy_item, disclosures = await asyncio.gather(
        fetch_company_info(corp_code),
        fetch_financials(corp_code, year),
        fetch_major_shareholders(corp_code, year),
        fetch_stock_totqy(corp_code, year),
        fetch_recent_disclosures(corp_code, 365),
    )
    print(f"  ✅ 재무항목 {len(fin_items)}개  |  대량보유보고 {len(major_items)}건  |  최근공시 {len(disclosures)}건")

    # ── 2. 유통주식 분석 ─────────────────────────────────────────────────────
    print("\n🔬 [3/5] 유통주식 분석...")
    fa = _analyze_float(major_items, totqy_item, [])

    # ── 3. 공개매수가 추정 ───────────────────────────────────────────────────
    print("🔬 [4/5] BPS 기반 공개매수가 추정...")
    te = _estimate_tender_offer(fin_items, fa)

    # ── 4. 현금 이상 변동 감지 ───────────────────────────────────────────────
    print("🔬 [5/5] 현금성 자산 이상 변동 분석...")
    ca = _detect_cash_anomaly(fin_items)

    # ── 5. 종합 자진상폐 스코어 ──────────────────────────────────────────────
    ds = _compute_delisting_score(company_info, fa, te, ca, disclosures)

    # ══════════════════════════════════════════════════════════════════════════
    # 출력 리포트
    # ══════════════════════════════════════════════════════════════════════════
    print(f"\n{'='*60}")
    print(f"  📊 Phase D 분석 리포트 — {corp_name} ({ticker})")
    print(f"{'='*60}")

    # ── 기업 기본 정보 ───────────────────────────────────────────────────────
    print("\n▶ 기업 개요")
    print(f"  대표이사   : {company_info.get('ceo_nm', 'N/A')}")
    print(f"  업종       : {company_info.get('induty_code', 'N/A')} — {company_info.get('corp_cls', 'N/A')}")
    print(f"  설립일     : {company_info.get('est_dt', 'N/A')}")
    print(f"  상장일     : {company_info.get('listing_dt', 'N/A')}")

    # ── 유통주식 분석 ────────────────────────────────────────────────────────
    print("\n▶ 유통주식 분석 (Float Analysis)")
    if fa.total_shares:
        print(f"  발행주식 총수    : {_fmt_num(fa.total_shares, '주')}")
    if fa.major_holder_pct is not None:
        print(f"  최대주주+특관계인 : {fa.major_holder_pct:.1f}%")
    if fa.treasury_pct is not None:
        print(f"  자기주식         : {fa.treasury_pct:.1f}%")
    if fa.float_pct is not None:
        marker = "🚨" if fa.float_pct < 15 else ("⚠️" if fa.float_pct < 30 else "✅")
        print(f"  유통주식(Float)  : {marker} {fa.float_pct:.1f}% ({_fmt_num(fa.float_shares, '주')})")
    if fa.major_holders:
        print(f"  주요주주         : {', '.join(fa.major_holders[:4])}")
    print(f"  시그널           : [{fa.signal}] {fa.detail}")

    # ── 공개매수가 추정 ──────────────────────────────────────────────────────
    print("\n▶ 공개매수가 추정 (BPS 기반)")
    if te.total_equity:
        print(f"  자본총계         : {_fmt_num(te.total_equity)}")
    if te.bps:
        print(f"  BPS              : {_fmt_num(te.bps, '원')}")
        print(f"  최소 공개매수가  : {_fmt_num(te.min_offer_price, '원')} (BPS × 1.20)")
        print(f"  30% 프리미엄     : {_fmt_num(te.premium_30pct, '원')} (BPS × 1.30)")
    else:
        print(f"  [데이터 부족] 발행주식 총수 또는 자본총계 미확인")

    # ── 현금 이상 변동 ───────────────────────────────────────────────────────
    print("\n▶ 현금성 자산 이상 변동 감지")
    if ca.cash_current:
        print(f"  당기 현금        : {_fmt_num(ca.cash_current)}")
    if ca.cash_prev:
        print(f"  전기 현금        : {_fmt_num(ca.cash_prev)}")
    if ca.cash_change_pct is not None:
        arrow = "▲" if ca.cash_change_pct > 0 else "▼"
        marker = "🔥" if abs(ca.cash_change_pct) >= 30 else "ℹ"
        print(f"  전기 대비 변동   : {marker} {arrow}{abs(ca.cash_change_pct):.1f}%")
    if ca.cash_to_assets_pct:
        print(f"  현금/총자산 비율 : {ca.cash_to_assets_pct:.1f}%")
    if ca.foreign_curr_current:
        print(f"  외화현금성자산   : {_fmt_num(ca.foreign_curr_current)}")
    print(f"  시그널           : [{ca.signal}] {ca.detail}")

    # ── 최근 주요 공시 ───────────────────────────────────────────────────────
    print("\n▶ 최근 주목 공시 (최대 8건)")
    hot_kws = {"자기주식", "공개매수", "상장폐지", "특수관계", "대량보유", "배당", "합병", "분할"}
    shown = 0
    for d in disclosures:
        title = d.get("report_nm", "")
        if any(kw in title for kw in hot_kws):
            print(f"  📄 {d.get('rcept_dt', '')} — {title}")
            shown += 1
            if shown >= 8:
                break
    if shown == 0:
        print("  (조회 기간 내 주목 공시 없음)")

    # ── 종합 자진상폐 스코어 ─────────────────────────────────────────────────
    bar_len = ds.score // 5
    bar = "█" * bar_len + "░" * (20 - bar_len)
    print(f"\n{'─'*60}")
    print(f"  🎯 자진상폐 기회 스코어: {ds.score}/100  [{ds.grade}]")
    print(f"  [{bar}] {ds.score}%")
    print(f"\n  판정: {ds.opportunity}")
    print(f"\n  스코어 구성:")
    for f in ds.factors:
        print(f"    {f}")

    # ── Phase D 한라산불곰식 코멘트 ─────────────────────────────────────────
    print(f"\n{'─'*60}")
    print("  💡 Phase D 인사이트 (한라산불곰 프레임워크 적용)")

    if fa.float_pct is not None and fa.float_pct < 25:
        print(f"""
  [유통주식 스퀴즈 시나리오]
  현재 유통주식 {fa.float_pct:.1f}%. 대주주가 시장에서 추가 {max(0, fa.float_pct - 5):.0f}%p만
  매집하면 자진상폐 신청 가능 구간({fa.float_pct - max(0, fa.float_pct - 5):.1f}%)에 진입.
  유통주식이 희박할수록 소수 매수로 주가가 급등 — 단기 스퀴즈 가능성 내재.
""")

    if te.bps and te.min_offer_price:
        print(f"""  [공개매수가 분석]
  BPS {_fmt_num(te.bps, '원')} 기준 최소 공개매수가 {_fmt_num(te.min_offer_price, '원')}.
  현재 주가가 이보다 낮다면 → 공개매수 디스카운트 → 매수 기회.
  현재 주가가 이보다 높다면 → 공개매수 프리미엄 소진 → 차익실현 고려.
  ※ 실시간 주가는 증권사 API로 별도 확인 필요.
""")

    if ca.signal in ("CASH_SURGE",):
        print(f"""  [현금 급증 이벤트]
  전기 대비 현금 {ca.cash_change_pct:+.1f}%. 단기간 현금 급증은 주로:
  ① 자산 매각 → 자사주 매입·소각·공개매수 실탄
  ② 장기채권 만기 → 재투자 대기
  ③ 해외 사업 청산 → 본사 현금 집중
  → 이사회 자사주 취득 결의 공시 모니터링 필수.
""")

    print(f"{'='*60}\n")


# ══════════════════════════════════════════════════════════════════════════════
# CLI 진입점
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import datetime

    ticker = sys.argv[1] if len(sys.argv) > 1 else "016590"  # 신대양제지 기본
    year   = int(sys.argv[2]) if len(sys.argv) > 2 else datetime.date.today().year - 1

    asyncio.run(analyze(ticker, year))
