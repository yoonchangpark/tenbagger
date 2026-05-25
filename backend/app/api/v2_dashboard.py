"""
v2 대시보드 API - DART 공시 목록 조회 + AI 공시 분석 + 분기 비교 분석
"""
from fastapi import APIRouter, Query, BackgroundTasks, Request
from pydantic import BaseModel
from typing import Optional
from app.infra.clients.dart_client import _get
from app.core.config import settings
from app.core.database import SessionLocal
from sqlalchemy import text
import datetime
import json

router = APIRouter(prefix="/api/v2", tags=["dashboard"])

# 노이즈 공시 키워드 (숨김)
_HIDE_KEYWORDS = ["기업설명회", "의결권대리", "파생결합", "펀드공시", "주주총회소집공고", "소액공모"]
# 반드시 표시할 공시 키워드 (Priority 3 대형주에도 적용)
_MUST_SHOW_KEYWORDS = [
    "유상증자", "무상증자", "공개매수", "합병", "분할", "전환사채", "신주인수권부사채",
    "자기주식취득", "자기주식소각", "단일판매", "타법인주식취득", "타법인주식처분",
    "영업양수도", "감사의견", "불성실공시", "횡령", "배임",
]


def _is_noise(report_nm: str) -> bool:
    nm = report_nm.replace(" ", "")
    return any(k in nm for k in _HIDE_KEYWORDS)


def _is_must_show(report_nm: str) -> bool:
    nm = report_nm.replace(" ", "")
    return any(k in nm for k in _MUST_SHOW_KEYWORDS)


def _get_user_tickers(user_id: int) -> dict:
    """로그인 유저의 보유종목·관심종목 ticker set 반환"""
    try:
        with SessionLocal() as db:
            holdings = db.execute(text(
                "SELECT ticker FROM user_holdings WHERE user_id = :uid"
            ), {"uid": user_id}).fetchall()
            watchlist = db.execute(text(
                "SELECT ticker FROM watchlist WHERE user_id = :uid"
            ), {"uid": user_id}).fetchall()
        return {
            "holdings": {r.ticker.upper() for r in holdings},
            "watchlist": {r.ticker.upper() for r in watchlist},
        }
    except Exception:
        return {"holdings": set(), "watchlist": set()}


def _get_high_grade_tickers() -> set:
    """TENBAGGER / COMPOUNDER 등급 ticker set"""
    try:
        with SessionLocal() as db:
            rows = db.execute(text(
                "SELECT ticker FROM score_cache WHERE grade IN ('TENBAGGER','COMPOUNDER')"
            )).fetchall()
        return {r.ticker.upper() for r in rows}
    except Exception:
        return set()


def _label_priority(stock_code: str, report_nm: str,
                    holdings: set, watchlist: set, high_grade: set) -> Optional[str]:
    """공시 우선순위 라벨 반환. None이면 표시 안 함."""
    if _is_noise(report_nm):
        return None
    ticker = (stock_code or "").upper()
    if ticker in holdings:
        return "내종목"
    if ticker in watchlist:
        return "관심종목"
    if ticker in high_grade:
        return "추천종목"
    if _is_must_show(report_nm):
        return "주요공시"
    return None  # 나머지는 숨김


@router.get("/disclosures")
async def get_disclosures(
    request: Request,
    corp_cls: str = Query("", description="Y=KOSPI, K=KOSDAQ, 빈값=전체"),
    bgn_de: str = Query(None, description="시작일 YYYYMMDD"),
    end_de: str = Query(None, description="종료일 YYYYMMDD"),
    page_count: int = Query(40, le=100),
    personalize: bool = Query(True, description="개인화 필터 적용 여부"),
):
    today = datetime.date.today()
    if end_de is None:
        end_de = today.strftime("%Y%m%d")
    if bgn_de is None:
        bgn_de = (today - datetime.timedelta(days=7)).strftime("%Y%m%d")

    params = {
        "bgn_de": bgn_de,
        "end_de": end_de,
        "page_count": "100",   # 필터링 여유분 확보
        "sort": "date",
        "sort_mth": "desc",
    }
    if corp_cls:
        params["corp_cls"] = corp_cls

    try:
        result = await _get("list.json", params)
    except Exception as e:
        return {"status": "error", "message": str(e), "list": []}

    items = result.get("list", [])

    if not personalize:
        return {**result, "list": items[:page_count]}

    # 로그인 유저 확인
    user_id = None
    try:
        from app.core.auth import decode_token
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            payload = decode_token(auth[7:])
            user_id = payload.get("uid") or payload.get("id")
    except Exception:
        pass

    user_tickers = _get_user_tickers(user_id) if user_id else {"holdings": set(), "watchlist": set()}
    high_grade = _get_high_grade_tickers()

    priority_order = {"내종목": 0, "관심종목": 1, "추천종목": 2, "주요공시": 3}
    labeled = []
    for item in items:
        stock_code = (item.get("stock_code") or "").upper()
        report_nm = item.get("report_nm", "")
        label = _label_priority(stock_code, report_nm,
                                user_tickers["holdings"], user_tickers["watchlist"], high_grade)
        if label:
            labeled.append({**item, "priority_label": label})

    labeled.sort(key=lambda x: priority_order.get(x["priority_label"], 9))
    return {**result, "list": labeled[:page_count], "personalized": True, "total_filtered": len(labeled)}


# ── 공시 AI 분석 ────────────────────────────────────────────────────────────

class DisclosureAnalyzeRequest(BaseModel):
    rcept_no: str
    corp_name: str
    report_nm: str
    ticker: Optional[str] = None
    corp_code: Optional[str] = None
    rcept_dt: Optional[str] = None


def _classify_disclosure(report_nm: str) -> str:
    """공시 제목 키워드 → 유형별 분석 힌트 반환"""
    nm = report_nm.replace(" ", "")
    if any(k in nm for k in ["유상증자", "주식발행"]):
        return "유상증자 공시입니다. 발행 규모(기존 주식 대비 희석률%), 발행 목적(운영자금/시설투자/부채상환), 발행가 할인율을 중점 분석하세요. '주주 가치 훼손인가, 성장 투자인가'가 핵심 판단 기준입니다."
    if any(k in nm for k in ["자기주식취득", "자사주매입", "자기주식소각"]):
        return "자사주 매입/소각 공시입니다. 매입 규모(발행주식 대비 %, 시가총액 대비 %), 재원(영업현금/차입), 소각 여부를 분석하세요. 주주환원 신호의 강도와 진정성이 핵심입니다."
    if any(k in nm for k in ["공개매수"]):
        return "공개매수 공시입니다. 매수 주체, 매수 가격(현재 시장가 대비 프리미엄%), 매수 수량(지분율 변화), 공개매수 목적(경영권 취득/지분 확대), 성사 가능성을 분석하세요. 차익 실현 기회와 경영권 변동 리스크가 핵심입니다."
    if any(k in nm for k in ["전환사채", "신주인수권부사채", "CB발행", "BW발행", "교환사채"]):
        return "CB/BW/EB 발행 공시입니다. 발행 규모, 전환가격(현재 주가 대비 할인율%), 리픽싱 조건, 만기, 발행 대상(기관/특수관계인)을 분석하세요. 잠재적 오버행(희석) 리스크가 핵심입니다."
    if any(k in nm for k in ["단일판매", "공급계약", "수주"]):
        return "대규모 계약/수주 공시입니다. 계약 금액(연간 매출 대비 비중%), 계약 상대방(국내/해외, 대기업 여부), 계약 기간, 계약 조건을 분석하세요. 실적 변곡점이 될 수 있는지가 핵심입니다."
    if any(k in nm for k in ["합병", "분할", "인수", "양수도", "영업양수"]):
        return "M&A/구조조정 공시입니다. 거래 금액, 합병 비율(기존 주주 지분 희석), 피인수 기업의 재무 상태(부채 포함 여부), 시너지 구체성을 분석하세요."
    if any(k in nm for k in ["투자판단관련주요경영사항", "주요경영사항"]):
        return "주요 경영사항 공시입니다. 원문에서 '무엇을 결정했는지(의사결정 내용)', '규모(금액·비율)', '일정(언제부터 적용)', '기대 효과'를 추출하세요. 재무비율이 아닌 경영 이벤트 자체가 분석 대상입니다."
    if any(k in nm for k in ["횡령", "배임", "사기"]):
        return "횡령·배임 공시입니다. 피해 금액(자기자본 대비 %), 당사자(임원/직원), 사건 발생 시점과 발견 경위, 형사 고발 여부, 회사의 내부통제 신뢰도 훼손 가능성을 중점 분석하세요."
    if any(k in nm for k in ["소송", "분쟁", "제소"]):
        return "소송·분쟁 공시입니다. 청구 금액(자기자본 대비 %), 소송 원인, 회사 측 입장, 최종 판결 시 재무 영향을 분석하세요."
    if any(k in nm for k in ["사업보고서", "분기보고서", "반기보고서"]):
        return "정기 보고서입니다. 전분기/전년 대비 매출·영업이익·순이익 변화율, 부채비율 추이, 배당 정책 변화, 경영진 코멘트 톤(긍정/부정)을 분석하세요."
    if any(k in nm for k in ["임원변경", "대표이사변경", "이사선임", "이사해임"]):
        return "임원 변경 공시입니다. 변경 임원의 경력과 전문성, 변경 시기(어닝 시즌 전후), 내부 승진 vs 외부 영입, 전임자 퇴임 사유를 분석하세요."
    if any(k in nm for k in ["배당", "현금배당", "주식배당"]):
        return "배당 공시입니다. 배당 금액(주당), 배당수익률(현재 주가 대비), 전년 대비 증감률, 배당성향(순이익 대비 %)을 분석하세요."
    if any(k in nm for k in ["대규모내부거래", "특수관계인"]):
        return "내부거래 공시입니다. 거래 상대방(계열사명), 거래 금액(자산 대비 %), 거래 조건의 공정성, 소액주주 이익 침해 가능성을 분석하세요."
    if any(k in nm for k in ["신규시설투자", "시설투자"]):
        return "신규 시설 투자 공시입니다. 투자 금액(자산 대비 %), 투자 목적(CAPA 확대/신사업), 예상 완공 시기, 투자 재원(자체/차입)을 분석하세요. 미래 성장 드라이버가 될 수 있는지가 핵심입니다."
    return "공시 원문 전체를 읽고, 이 공시가 장기투자자의 보유 결정에 어떤 영향을 주는지 구체적 수치와 함께 분석하세요."


@router.post("/disclosures/analyze")
async def analyze_disclosure(body: DisclosureAnalyzeRequest):
    """
    DART 공시 원문(document.json) + 회사 스코어 기반 GPT-4o 심층 분석.
    원문 접근 불가 시 bzSummary + 재무지표로 보완.
    """
    if not settings.openai_api_key:
        return {"error": "OpenAI API 키가 설정되지 않았습니다."}

    from app.infra.clients.dart_client import fetch_document_text, get_corp_code
    import asyncio as _asyncio

    # 1. 원문 텍스트 병렬 수집 시작 (30,000자 전문)
    doc_text_task = _asyncio.create_task(fetch_document_text(body.rcept_no, max_chars=30000))

    # 2. corp_code 확보
    corp_code = body.corp_code
    if not corp_code:
        query = body.ticker or body.corp_name
        corp_code, _ = await get_corp_code(query)

    # 3. DB 스코어 조회
    score_context = ""
    close_price = None
    if body.ticker:
        try:
            with SessionLocal() as session:
                row = session.execute(text("""
                    SELECT grade, total_score, growth_score, stability_score,
                           cashflow_score, revenue_cagr_5y, avg_roe_5y,
                           avg_fcf_margin, per, pbr
                    FROM scores WHERE ticker = :t LIMIT 1
                """), {"t": body.ticker}).fetchone()
            if row:
                score_context = (
                    f"\n[텐배거 시스템 평가]\n"
                    f"등급: {row.grade} | 총점: {row.total_score}/10\n"
                    f"성장성: {row.growth_score} | 안정성: {row.stability_score} | 현금흐름: {row.cashflow_score}\n"
                    f"매출CAGR(5y): {row.revenue_cagr_5y}% | ROE: {row.avg_roe_5y}% | FCF마진: {row.avg_fcf_margin}%\n"
                    f"PER: {row.per} | PBR: {row.pbr}"
                )
        except Exception:
            pass
        try:
            with SessionLocal() as session:
                cp_row = session.execute(text(
                    "SELECT close_price FROM score_cache WHERE ticker = :t LIMIT 1"
                ), {"t": body.ticker}).fetchone()
            if cp_row:
                close_price = float(cp_row.close_price or 0) or None
        except Exception:
            pass

    # 4. 원문 결과 대기
    doc_text = await doc_text_task

    # 5. 원문 없으면 bzSummary + 재무지표로 보완
    fallback_context = ""
    if not doc_text and corp_code:
        try:
            from app.agents.dart_report_parser import fetch_bz_summary, fetch_financial_indices
            import datetime as _dt
            year = str(_dt.date.today().year - 1)
            bz, indices = await _asyncio.gather(
                fetch_bz_summary(corp_code, year, "11011"),
                fetch_financial_indices(corp_code, year, "11011"),
                return_exceptions=True,
            )
            if isinstance(bz, str) and bz:
                fallback_context += f"\n[사업 개요]\n{bz[:2000]}"
            if isinstance(indices, dict) and indices:
                lines = [f"  {k}: {v}%" for k, v in list(indices.items())[:8]]
                fallback_context += f"\n[주요 재무지표]\n" + "\n".join(lines)
        except Exception:
            pass

    date_str = ""
    if body.rcept_dt and len(body.rcept_dt) == 8:
        date_str = f"{body.rcept_dt[:4]}년 {body.rcept_dt[4:6]}월 {body.rcept_dt[6:]}일"

    has_doc = bool(doc_text)
    nm = body.report_nm.replace(" ", "")
    content_section = (
        f"\n[공시 원문 전문 — {len(doc_text):,}자]\n{doc_text}" if has_doc
        else (fallback_context or "\n(원문 데이터 없음 — 제목 및 회사 프로필 기반 분석)")
    )

    # ── 공시 유형 분기 ──────────────────────────────────────────────────────
    is_tender = any(k in nm for k in ["공개매수"])
    is_rights = any(k in nm for k in ["유상증자", "주식발행"])
    is_quarterly = any(k in nm for k in ["분기보고서", "반기보고서", "사업보고서"])

    if is_tender:
        prompt = f"""다음 공개매수 공시를 한국 소액주주 투자자 관점에서 철저히 분석하세요.

[공시 기본 정보]
- 회사명: {body.corp_name}
- 공시 제목: {body.report_nm}
- 공시 날짜: {date_str or '미상'}
{score_context}
{content_section}

[추출·계산 지시]
원문에서 아래를 정확히 추출하고, 계산을 수행하세요.

1. 기본 추출:
   - 매수 가격(주당 원), 매수 예정 주식수, 공개매수 청약 기간(시작~종료), 결제일
   - 매수 목적(자진상폐/경영권 강화/지분 확대 등)
   - 매수인(공개매수자) 명칭 및 공개매수 후 예상 지분율

2. 안분 비례 계산:
   - 원문에서 유통주식수 또는 전체 발행주식수를 찾아 기재
   - 안분율 추정 = 매수 예정 주식수 / (전체 발행주식수 - 매수인 기보유 주식수)
   - 최악의 경우(전량 청약 시) 성공률% 계산

3. 엑시트 시나리오 2가지:
   시나리오 A — 장내 매도:
   - 마지막 장내 매도 가능일: 결제일 기준 2영업일 전 (주말/공휴일 제외 계산)
   - 세금: 상장주식 소액주주 장내 양도 → 양도소득세 비과세 (증권거래세 0.18%만)
   - 장단점 기재
   시나리오 B — 공개매수 청약:
   - 청약 후 예상 수령 금액(안분율 기준)
   - 세금: 양도소득세 22% 적용 (공개매수는 장외거래), 250만원 기본공제(1회/연)
   - 절세 팁: 장내 매도 후 동일 수량 재매수 → 취득가 상향 → 이후 청약 시 차익 감소 → 세금 절감
   - 장단점 기재

4. 지배구조 목적:
   - 상장폐지(자진상폐) 전략인지, 단순 지분 확대인지 판단 근거

아래 JSON 형식으로만 응답하세요:
{{
  "summary": "핵심 내용 3문장. 매수가·주식수·기간 수치 포함 필수.",
  "impact": "CAUTION",
  "impact_reason": "투자자 관점 임팩트 1~2문장. 수치 근거 필수.",
  "offer_price": 숫자 (주당 원, 없으면 null),
  "offer_shares": 숫자 (주식수, 없으면 null),
  "tender_period": {{"start": "M/D", "end": "M/D"}},
  "settlement_date": "M/D",
  "last_market_sell_date": "M/D (결제일 2영업일 전)",
  "purpose": "자진상폐|경영권강화|지분확대|기타",
  "proration_rate": "최악 기준 XX% (발행주식 YY만주 기준)" 또는 null,
  "exit_scenarios": [
    {{
      "type": "장내매도",
      "deadline": "M/D 장 마감까지",
      "tax": "비과세 (소액주주 상장주식 양도)",
      "pros": "확실한 매도, 양도세 없음",
      "cons": "공개매수가보다 낮을 수 있음"
    }},
    {{
      "type": "공개매수청약",
      "offer_price_str": "XX,XXX원",
      "proration": "최악 XX% 성공 예상",
      "tax": "양도소득세 22%, 250만원 공제",
      "tax_tip": "청약 전 장내 매도 후 재매수로 취득가 높이면 과세 차익 감소",
      "pros": "공개매수가로 매도 확정",
      "cons": "안분 비례로 일부만 매도, 양도세 부담"
    }}
  ],
  "key_points": [
    "수치 포함 포인트 1",
    "수치 포함 포인트 2",
    "수치 포함 포인트 3",
    "지배구조 또는 상폐 관련 판단"
  ],
  "action": "지금 보유자가 취해야 할 행동 1문장 (예: M/D까지 장내 매도 or 청약 선택)",
  "confidence": "HIGH"
}}"""

    elif is_rights:
        hint = _classify_disclosure(body.report_nm)
        prompt = f"""다음 유상증자 공시를 분석하세요.

[공시 기본 정보]
- 회사명: {body.corp_name}
- 공시 제목: {body.report_nm}
- 공시 날짜: {date_str or '미상'}
{score_context}
{content_section}

원문에서 아래를 추출하고 계산하세요:
1. 신주 발행 수량, 발행가, 기존 발행주식수
2. 희석률 = 신주 수량 / (기존 발행주식수 + 신주 수량) × 100
3. 발행 목적(운영자금/시설투자/부채상환), 납입일
4. 주주배정/제3자배정 여부

아래 JSON 형식으로 응답하세요:
{{
  "summary": "핵심 내용 3문장. 발행가·주식수·희석률 수치 포함 필수.",
  "impact": "POSITIVE 또는 NEGATIVE 중 하나 (목적이 시설투자→POSITIVE, 부채상환/운영자금→NEGATIVE)",
  "impact_reason": "판단 근거 1~2문장",
  "new_shares": 숫자,
  "issue_price": 숫자,
  "dilution_rate": "XX.X%",
  "purpose": "운영자금|시설투자|부채상환|기타",
  "payment_date": "M/D",
  "rights_type": "주주배정|제3자배정|일반공모",
  "key_points": ["포인트1","포인트2","포인트3","투자자 행동"],
  "action": "기존 주주 대응 방안 1문장",
  "confidence": "HIGH"
}}"""

    elif is_quarterly:
        price_ctx = f"\n현재 주가(참고): {int(close_price):,}원" if close_price else ""
        quarter_label = "Q1" if "1분기" in body.report_nm else ("Q2" if "2분기" in body.report_nm else ("Q3" if "3분기" in body.report_nm else "연간"))
        prompt = f"""다음 분기/사업보고서를 한라산불곰 스타일의 가치투자 관점에서 분석하세요.

[공시 기본 정보]
- 회사명: {body.corp_name}
- 공시 제목: {body.report_nm} ({quarter_label})
- 공시 날짜: {date_str or '미상'}
{score_context}{price_ctx}
{content_section}

[추출·계산 지시 — L1(현재가치) + L2(실적 시나리오)]

L1 — 현재 가치 앵커 (원문 또는 참고 주가로 계산):
1. EPS = 당기순이익 / 발행주식수 (원)
2. 이익수익률 = EPS / 현재주가 × 100 (%) — 채권 대비 매력도 척도
3. DPS = 최근 연간 주당배당금 (원)
4. 배당수익률 = DPS / 현재주가 × 100 (%)
5. PBR = 현재주가 / 주당순자산 (BPS)
6. 자사주 비율 = 자사주수 / 발행주식수 × 100 (%)

L2 — 실적 시나리오 추산 (이번 분기 실적 기반):
7. 이번 분기 매출, 영업이익, 순이익 수치와 전년 동기 대비 YoY% 계산
8. 연간 추산 EPS = 이번 분기 순이익 × 4 (분기보고서의 경우)
9. 배당 시나리오 3가지:
   - 비관: DPS 50% 삭감 → 배당수익률%
   - 기본: DPS 유지 → 배당수익률%
   - 낙관: DPS 30% 증가 → 배당수익률%
10. 최악 시나리오 판단: 비관 시나리오에서도 배당수익률이 3% 이상이면 "안전마진 있음"

아래 JSON 형식으로만 응답하세요 (모든 숫자 필드는 null 허용):
{{
  "summary": "이번 실적 핵심 3문장. 매출·영업이익·순이익 YoY 수치 반드시 포함.",
  "impact": "POSITIVE 또는 NEUTRAL 또는 NEGATIVE 중 하나",
  "impact_reason": "실적 방향 판단 근거 1~2문장",
  "revenue_qtr": 숫자(억원),
  "op_profit_qtr": 숫자(억원),
  "net_profit_qtr": 숫자(억원),
  "revenue_yoy": "YoY%문자열 예: +5.3%",
  "op_profit_yoy": "YoY%문자열 예: -12.1%",
  "net_profit_yoy": "YoY%문자열 예: +8.7%",
  "eps": 숫자(원) 또는 null,
  "eps_annual_estimate": 숫자(원, 분기×4) 또는 null,
  "earnings_yield": 숫자(%) 또는 null,
  "dps": 숫자(원) 또는 null,
  "dividend_yield": 숫자(%) 또는 null,
  "pbr": 숫자 또는 null,
  "treasury_ratio": 숫자(%) 또는 null,
  "scenarios": {{
    "bear": {{"dps": 숫자, "div_yield": 숫자, "label": "DPS -50%"}},
    "base": {{"dps": 숫자, "div_yield": 숫자, "label": "DPS 유지"}},
    "bull": {{"dps": 숫자, "div_yield": 숫자, "label": "DPS +30%"}}
  }},
  "safety_margin": "있음 또는 없음 또는 불확실",
  "safety_margin_reason": "비관 시나리오 배당수익률 X% → 판단 1문장",
  "key_points": ["수치포함포인트1","수치포함포인트2","수치포함포인트3","투자자행동"],
  "action": "장기투자자 보유/매수/매도/관망 판단 1문장 + 근거",
  "confidence": "HIGH 또는 MEDIUM 또는 LOW"
}}"""

    else:
        disclosure_hint = _classify_disclosure(body.report_nm)
        confidence_note = "HIGH" if has_doc else "제목만으로 판단 가능하면 MEDIUM, 내용 확인 필요하면 LOW"
        analysis_instruction = (
            f"위 공시 원문 전문을 직접 읽고 분석하세요. {disclosure_hint}\n"
            "원문에 등장하는 구체적 수치(금액·주식수·비율·날짜·가격 등)를 반드시 key_points에 인용하세요."
        ) if has_doc else f"공시 제목과 회사 프로필을 기반으로 분석하세요. {disclosure_hint}"

        prompt = f"""다음 DART 공시를 장기투자 관점에서 심층 분석해주세요.

[공시 기본 정보]
- 회사명: {body.corp_name}
- 공시 제목: {body.report_nm}
- 공시 날짜: {date_str or '미상'}
{score_context}
{content_section}

[분석 지시]
{analysis_instruction}

아래 JSON 형식으로만 응답하세요:
{{
  "summary": "공시의 핵심 내용 3~4문장. 구체적 수치 반드시 포함.",
  "impact": "POSITIVE 또는 NEUTRAL 또는 NEGATIVE 또는 CAUTION 중 하나",
  "impact_reason": "투자 임팩트 판단 근거 1~2문장. 원문 수치 근거 필수.",
  "key_points": ["포인트1","포인트2","포인트3","투자자 행동"],
  "action": "장기투자자 관점 행동 1문장 (보유/매수/매도/관망 + 이유)",
  "confidence": "{confidence_note}"
}}

impact: POSITIVE=실적호전·자사주·배당증가 / NEGATIVE=실적악화·횡령·희석증자 / CAUTION=합병·대규모투자·임원변경 / NEUTRAL=통상공시"""

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        resp = await client.chat.completions.create(
            model="gpt-4o",
            max_tokens=2000,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "당신은 한국 주식 DART 공시 전문 분석가입니다. 공시 원문을 직접 읽고 구체적 수치와 함께 소액주주에게 실질적 행동 가이드를 제공합니다. 반드시 JSON 형식으로만 응답하세요."},
                {"role": "user", "content": prompt},
            ],
        )
        result = json.loads(resp.choices[0].message.content)
        result["corp_name"] = body.corp_name
        result["report_nm"] = body.report_nm
        result["rcept_no"] = body.rcept_no
        result["has_doc"] = has_doc
        if is_tender:
            result["analysis_type"] = "tender_offer"
        elif is_rights:
            result["analysis_type"] = "rights_offering"
        elif is_quarterly:
            result["analysis_type"] = "quarterly_report"
        else:
            result["analysis_type"] = "general"

        impact_emoji = {"POSITIVE": "✅", "NEGATIVE": "🔴", "CAUTION": "⚠️", "NEUTRAL": "ℹ️"}
        result["impact_emoji"] = impact_emoji.get(result.get("impact", "NEUTRAL"), "ℹ️")

        return result
    except Exception as e:
        return {"error": f"분석 실패: {e}"}


# ── 분기 비교 분석 ──────────────────────────────────────────────────────────

def _save_compare_cache(result: dict):
    with SessionLocal() as session:
        session.execute(text("""
            INSERT INTO quarter_compare_cache
                (ticker, corp_name, prev_label, curr_label, prev_indices, curr_indices, analysis_md, updated_at)
            VALUES
                (:ticker, :corp_name, :prev_label, :curr_label,
                 :prev_indices::jsonb, :curr_indices::jsonb, :analysis_md, NOW())
            ON CONFLICT (ticker) DO UPDATE SET
                corp_name   = EXCLUDED.corp_name,
                prev_label  = EXCLUDED.prev_label,
                curr_label  = EXCLUDED.curr_label,
                prev_indices = EXCLUDED.prev_indices,
                curr_indices = EXCLUDED.curr_indices,
                analysis_md = EXCLUDED.analysis_md,
                updated_at  = NOW()
        """), {
            "ticker": result["ticker"],
            "corp_name": result["corp_name"],
            "prev_label": result["prev_label"],
            "curr_label": result["curr_label"],
            "prev_indices": json.dumps(result["prev_indices"], ensure_ascii=False),
            "curr_indices": json.dumps(result["curr_indices"], ensure_ascii=False),
            "analysis_md": result["analysis_md"],
        })
        session.commit()


def _load_compare_cache(ticker: str) -> Optional[dict]:
    with SessionLocal() as session:
        row = session.execute(text("""
            SELECT ticker, corp_name, prev_label, curr_label,
                   prev_indices, curr_indices, analysis_md, updated_at
            FROM quarter_compare_cache WHERE ticker = :t
        """), {"t": ticker}).fetchone()
    if not row:
        return None
    return {
        "ticker": row.ticker,
        "corp_name": row.corp_name,
        "prev_label": row.prev_label,
        "curr_label": row.curr_label,
        "prev_indices": row.prev_indices or {},
        "curr_indices": row.curr_indices or {},
        "analysis_md": row.analysis_md,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "cached": True,
    }


@router.get("/company/{ticker}/quarter-compare")
async def get_quarter_compare(
    ticker: str,
    refresh: bool = Query(False, description="True이면 캐시 무시 후 재분석"),
    background_tasks: BackgroundTasks = None,
):
    """
    분기 비교 분석 조회.
    - 캐시 있으면 즉시 반환
    - 캐시 없거나 refresh=True이면 DART + GPT 실시간 분석 (10~20초)
    """
    if not refresh:
        cached = _load_compare_cache(ticker)
        if cached:
            return cached

    try:
        from app.agents.dart_report_parser import fetch_quarter_compare
        result = await fetch_quarter_compare(ticker)
        _save_compare_cache(result)
        result["cached"] = False
        result["updated_at"] = datetime.datetime.utcnow().isoformat()
        return result
    except Exception as e:
        return {"error": str(e), "ticker": ticker}


async def _precache_one(ticker: str):
    try:
        from app.agents.dart_report_parser import fetch_quarter_compare
        result = await fetch_quarter_compare(ticker)
        _save_compare_cache(result)
        print(f"[QC] {ticker} 캐시 완료")
    except Exception as e:
        print(f"[QC] {ticker} 실패: {e}")


@router.post("/admin/quarter-compare/precache")
async def precache_quarter_compare(background_tasks: BackgroundTasks):
    """
    관심종목 + TENBAGGER 전종목 분기비교 사전 캐시 (백그라운드 실행).
    """
    tickers = []
    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT DISTINCT ticker FROM (
                SELECT ticker FROM watchlist
                UNION
                SELECT ticker FROM scores WHERE grade = 'TENBAGGER'
            ) t
        """)).fetchall()
        tickers = [r[0] for r in rows]

    async def run_all():
        import asyncio
        for ticker in tickers:
            await _precache_one(ticker)
            await asyncio.sleep(2)  # DART API 레이트 리밋 배려

    background_tasks.add_task(run_all)
    return {"message": f"{len(tickers)}개 종목 백그라운드 캐시 시작", "tickers": tickers}
