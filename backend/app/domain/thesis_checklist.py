"""
텐배거 내러티브 체크리스트 — 숫자 확인(DART) 부분

정의: agent_prompts/narrative_thesis_structure.md
  ① 수요 폭발 → ② 독자성 → ③ 진입장벽 → ④ 수급 불균형 → ⑤ 단가·마진 상승

고리마다 세부 항목을 '숫자 확인'(여기서 계산)과 '서술 확인'(리서치 근거 필요, LLM)으로 나눈다.
서술 확인은 근거 수집 파이프라인이 준비되기 전까지 전부 '미확인'으로 둔다 — LLM이
근거 없이 아는 척한 답을 '확인'으로 만들지 않기 위함.

DART 사업보고서 1건에 당기·전기·전전기 3개년이 들어 있으므로 종목당 1~2회 호출로 끝난다.
v1 fetch_yearly_financials()와 분리한 이유: 그 결과는 v1 /api/company 응답에 그대로 실리므로
계정을 더하면 v1 응답이 바뀐다.
"""
import datetime
from typing import Optional

from app.domain.scoring import calculate_momentum_score

CONFIRMED, UNCONFIRMED, CONTRADICTED = "확인", "미확인", "반증"

# (필드, 허용 재무제표, account_id 후보, account_nm 후보) — account_id 우선, 이름은 폴백
_ACCOUNTS = [
    ("revenue", ("IS", "CIS"), ("ifrs-full_Revenue",), ("매출액", "수익(매출액)", "영업수익", "매출")),
    ("cost_of_sales", ("IS", "CIS"), ("ifrs-full_CostOfSales",), ("매출원가",)),
    ("gross_profit", ("IS", "CIS"), ("ifrs-full_GrossProfit",), ("매출총이익", "매출총이익(손실)")),
    ("operating_profit", ("IS", "CIS"), ("dart_OperatingIncomeLoss",), ("영업이익", "영업이익(손실)")),
    ("net_income", ("IS", "CIS"), ("ifrs-full_ProfitLoss",), ("당기순이익", "당기순이익(손실)")),
    ("total_equity", ("BS",), ("ifrs-full_Equity",), ("자본총계",)),
    ("inventory", ("BS",), ("ifrs-full_Inventories",), ("재고자산", "유동재고자산")),
    ("receivables", ("BS",), ("ifrs-full_TradeAndOtherCurrentReceivables", "ifrs-full_CurrentTradeReceivables"),
     ("매출채권 및 기타유동채권", "매출채권및기타채권", "매출채권")),
    ("contract_liabilities", ("BS",), ("ifrs-full_CurrentContractLiabilities", "ifrs-full_ContractLiabilities"),
     ("계약부채", "유동계약부채", "선수금")),
    ("ppe", ("BS",), ("ifrs-full_PropertyPlantAndEquipment",), ("유형자산",)),
]
_PERIOD_KEYS = ("bfefrmtrm_amount", "frmtrm_amount", "thstrm_amount")  # 전전기, 전기, 당기


def _amount(raw) -> Optional[float]:
    if raw in (None, "", "-"):
        return None
    try:
        return float(str(raw).replace(",", ""))
    except ValueError:
        return None


def parse_three_years(items: list, bsns_year: int) -> list[dict]:
    """사업보고서 1건의 전체 계정 목록 → 3개년 행 [{year, revenue, ...}] (연도 오름차순)."""
    rows = [{"year": bsns_year - 2}, {"year": bsns_year - 1}, {"year": bsns_year}]
    for field, divs, ids, names in _ACCOUNTS:
        pool = [it for it in items if it.get("sj_div") in divs]
        hit = next((it for aid in ids for it in pool if it.get("account_id") == aid), None)
        if hit is None:
            hit = next((it for nm in names for it in pool
                        if (it.get("account_nm") or "").strip() == nm), None)
        for row, key in zip(rows, _PERIOD_KEYS):
            row[field] = _amount(hit.get(key)) if hit else None
    for row in rows:
        if row["gross_profit"] is None and row["revenue"] is not None and row["cost_of_sales"] is not None:
            row["gross_profit"] = row["revenue"] - row["cost_of_sales"]
    return [r for r in rows if r.get("revenue")]


async def fetch_thesis_financials(corp_code: str) -> tuple[list[dict], Optional[int]]:
    """최신 사업보고서 1건에서 3개년을 가져온다. 올해 보고서가 아직 없으면 한 해 전으로."""
    from app.infra.clients.dart_client import get_financial_statements
    this_year = datetime.date.today().year
    for year in (this_year - 1, this_year - 2):
        data = await get_financial_statements(corp_code, year)
        items = data.get("list") or []
        if items:
            rows = parse_three_years(items, year)
            if len(rows) >= 3:
                return rows, year
    return [], None


# ── 숫자 확인 ─────────────────────────────────────────────────────────────

def _ratio(a, b) -> Optional[float]:
    return a / b * 100 if a is not None and b else None


def _growth(first, last) -> Optional[float]:
    return (last / first - 1) * 100 if first and first > 0 and last is not None else None


def _cagr2(first, last) -> Optional[float]:
    return ((last / first) ** 0.5 - 1) * 100 if first and first > 0 and last and last > 0 else None


def _check(key: str, label: str, passed: Optional[bool], value=None, rule: str = "") -> dict:
    return {"key": key, "label": label, "kind": "number",
            "result": None if passed is None else bool(passed),
            "value": value, "rule": rule}


def _narrative(key: str, label: str, evidence_needed: str) -> dict:
    return {"key": key, "label": label, "kind": "narrative", "result": None,
            "status": UNCONFIRMED, "evidence": [], "evidence_needed": evidence_needed}


def _r(v, n=1):
    return round(v, n) if v is not None else None


def _any_true(*vals) -> Optional[bool]:
    known = [v for v in vals if v is not None]
    return any(known) if known else None


def _link_status(checks: list[dict]) -> str:
    """숫자 항목만으로 판정: 전부 통과=확인, 전부 실패=반증, 그 외(부분·데이터 없음)=미확인."""
    results = [c["result"] for c in checks if c["kind"] == "number" and c["result"] is not None]
    if not results:
        return UNCONFIRMED
    if all(results):
        return CONFIRMED
    if not any(results):
        return CONTRADICTED
    return UNCONFIRMED


def evaluate(rows: list[dict]) -> dict:
    """3개년 행 → 다섯 고리 체크리스트와 stage."""
    if len(rows) < 3:
        return {"available": False, "reason": "3개년 재무 부족"}
    y0, y1, y2 = rows[-3], rows[-2], rows[-1]
    gm = [_ratio(r.get("gross_profit"), r.get("revenue")) for r in (y0, y1, y2)]
    om = [_ratio(r.get("operating_profit"), r.get("revenue")) for r in (y0, y1, y2)]
    inv = [_ratio(r.get("inventory"), r.get("revenue")) for r in (y0, y1, y2)]
    rec = [_ratio(r.get("receivables"), r.get("revenue")) for r in (y0, y1, y2)]
    rev_cagr = _cagr2(y0.get("revenue"), y2.get("revenue"))
    rev_g = _growth(y0.get("revenue"), y2.get("revenue"))
    op_g = _growth(y0.get("operating_profit"), y2.get("operating_profit"))
    ppe_g = _growth(y0.get("ppe"), y2.get("ppe"))
    cl0, cl2 = y0.get("contract_liabilities"), y2.get("contract_liabilities")
    mom = calculate_momentum_score(rows).get("momentum_score")

    links = {
        "demand": {
            "title": "① 산업 수요 폭발",
            "checks": [
                _narrative("demand_driver", "이름 있는 수요 동인(계약·정책·고객 발표)이 있는가",
                           "공시·기사 등 출처와 날짜가 있는 사건 1건 이상"),
                _narrative("demand_durable", "수요가 여러 해 이어질 구조적 이유가 있는가",
                           "다년 투자계획·규제 일정 등 기간이 명시된 근거"),
                _check("revenue_cagr", "매출 2년 CAGR 20% 이상",
                       None if rev_cagr is None else rev_cagr >= 20, _r(rev_cagr), ">= 20%"),
                _check("receivables_ok", "매출채권/매출이 5%p 넘게 늘지 않음(밀어내기 매출 아님)",
                       None if None in (rec[0], rec[2]) else rec[2] - rec[0] <= 5,
                       [_r(v) for v in rec], "당기 - 전전기 <= 5%p"),
            ],
        },
        "unique": {
            "title": "② 기업의 독자성",
            "checks": [
                _narrative("unique_what", "경쟁사에 없는 것(제품·고객·인증·지역)이 구체적으로 있는가",
                           "주요 고객사명, 인증, 공시된 계약"),
                _check("gross_margin_level", "매출총이익률 25% 이상",
                       None if gm[2] is None else gm[2] >= 25, _r(gm[2]), ">= 25%"),
                _check("gross_margin_not_falling", "매출이 느는 동안 매출총이익률이 떨어지지 않음",
                       None if None in (gm[0], gm[2]) else gm[2] >= gm[0],
                       [_r(v) for v in gm], "당기 >= 전전기"),
                # 유통업처럼 매출총이익률은 높아도 판관비에 다 먹히는 업종을 거른다
                _check("operating_margin_level", "영업이익률 5% 이상 (판관비를 치르고도 남는 몫)",
                       None if om[2] is None else om[2] >= 5, _r(om[2]), ">= 5%"),
            ],
        },
        "barrier": {
            "title": "③ 쉽게 못 따라오는 진입장벽",
            "checks": [
                _narrative("barrier_type", "장벽의 종류가 명시되는가(특허·공정·인증·증설 리드타임·인허가)",
                           "특허 번호, 인증 명칭, 증설 소요 기간 등"),
                _narrative("barrier_duration", "경쟁사가 따라오는 데 걸리는 기간이 근거와 함께 있는가",
                           "경쟁사 증설 계획·인증 취득 기간 자료"),
                _narrative("barrier_no_entrant", "경쟁 진입·증설 공시가 없는가(반증 확인)",
                           "경쟁사 공시 검색 결과"),
                _check("gross_margin_durable", "매출총이익률이 어느 해에도 2%p 넘게 꺾이지 않음",
                       None if None in gm else (gm[1] - gm[0] >= -2 and gm[2] - gm[1] >= -2),
                       [_r(v) for v in gm], "연간 하락폭 <= 2%p"),
                _check("operating_profit_positive", "3년 모두 영업흑자",
                       None if None in om else all(v > 0 for v in om), [_r(v) for v in om], "> 0"),
            ],
        },
        "imbalance": {
            "title": "④ 수급 불균형",
            "checks": [
                _narrative("backlog", "수주잔고 증가·납기 연장 근거가 있는가",
                           "사업보고서 본문 수주현황, 공시"),
                _narrative("capacity_plan", "공급이 언제 따라잡을지(증설 계획·완료 시점)가 파악되는가",
                           "시설투자 공시, 증설 완료 예정일"),
                _check("demand_outruns_capacity", "매출 증가율이 유형자산 증가율보다 큼",
                       None if None in (rev_g, ppe_g) else rev_g > ppe_g,
                       {"revenue_growth": _r(rev_g), "ppe_growth": _r(ppe_g)}, "매출 > 유형자산"),
                # 수주생산 업체는 수주잔고가 쌓일수록 재공품 재고도 늘어 재고 신호가 거꾸로 나온다.
                # 그런 회사는 선수금(계약부채)이 수급을 더 잘 보여주므로 둘 중 하나면 통과.
                _check("goods_or_prepayments", "재고/매출이 2년 전보다 낮거나, 계약부채(선수금)가 1.5배 이상 늘었음",
                       _any_true(None if None in (inv[0], inv[2]) else inv[2] < inv[0],
                                 None if not (cl0 and cl2) else cl2 >= cl0 * 1.5),
                       {"inventory_to_revenue": [_r(v) for v in inv],
                        "contract_liabilities": {"from": cl0, "to": cl2}},
                       "재고/매출 당기 < 전전기 또는 계약부채 >= 1.5배"),
            ],
        },
        "margin": {
            "title": "⑤ 단가·마진 상승",
            "checks": [
                _narrative("price_increase", "판가 인상·가격 협상력 언급이 있는가",
                           "IR 자료·사업보고서의 판가 관련 서술"),
                _check("gross_margin_expansion", "매출총이익률 2년간 3%p 이상 확장",
                       None if None in (gm[0], gm[2]) else gm[2] - gm[0] >= 3,
                       _r(None if None in (gm[0], gm[2]) else gm[2] - gm[0]), ">= +3%p"),
                _check("operating_leverage", "영업이익 증가율이 매출 증가율보다 큼",
                       None if None in (op_g, rev_g) else op_g > rev_g,
                       {"op_growth": _r(op_g), "revenue_growth": _r(rev_g)}, "영업이익 > 매출"),
                _check("momentum_score", "momentum_score 5 이상 (백테스트에서 단조성이 확인된 확인 필터)",
                       None if mom is None else mom >= 5, mom, ">= 5"),
            ],
        },
    }
    order = ["demand", "unique", "barrier", "imbalance", "margin"]
    for k in order:
        links[k]["number_status"] = _link_status(links[k]["checks"])

    # ① 수요가 숫자로 확인되지 않으면 뒤 고리가 좋아 보여도 이 구조의 이야기가 아니다
    # (매출이 안 느는 회사의 마진 개선은 비용 절감이지 수급 불균형이 아니다).
    confirmed = [i for i, k in enumerate(order, 1) if links[k]["number_status"] == CONFIRMED]
    stage = max(confirmed) if confirmed and links["demand"]["number_status"] == CONFIRMED else 0
    # 뒤 고리는 확인됐는데 앞 고리가 반증이면 일회성 가능성
    early_contradicted = [i for i, k in enumerate(order, 1)
                          if i < stage and links[k]["number_status"] == CONTRADICTED]
    stage_label = {
        0: "① 수요가 숫자로 안 보임 — 이야기가 있다면 초반부, 서술 근거가 전부",
        1: "① 수요만 숫자로 보임 — 초반부",
        2: "② 독자성까지 — 초반부",
        3: "③ 장벽까지 — 중반부",
        4: "④ 수급 불균형까지 — 중후반부",
        5: "⑤ 마진 반영 완료 — 후반부 (증거는 확실, 진입 시점은 늦었을 수 있음)",
    }[stage]
    return {
        "available": True,
        "years": [y0["year"], y1["year"], y2["year"]],
        "links": links,
        "stage": stage,
        "stage_label": stage_label,
        "warning": ("앞 고리가 숫자로 반증됨 — 뒤 고리 개선이 일회성일 수 있음"
                    if early_contradicted else None),
        "narrative_note": "서술 항목은 리서치 근거 수집 전이라 모두 '미확인'이다. 숫자 항목만 판정됐다.",
    }
