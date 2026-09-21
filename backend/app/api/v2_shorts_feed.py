"""
숏츠 피드 API — AIVA(영상 생성 서버)가 소비하는 콘텐츠 데이터 계약
GET /api/v2/shorts-feed?mode=retrospective|candidate&limit=N

두 프로젝트 연결의 데이터 계약(Data Contract):
  tenbagger(발굴·검증) → JSON → aiva_server(tenbagger_topic.py → 대본 → 영상)

Phase 1 (mode=retrospective): 과거 실제 텐배거 — "왜 텐배거가 됐나" 회고편.
  ⚠️ 우리 과거 등급이 아니라 **실측 수익률 상위**에서 뽑는다
  (구 스코어링은 진짜 승자를 AVOID로 찍었다 — scoring_postmortem.md).
Phase 2 (mode=candidate): 현재 탑다운 후보 — "왜 텐배거가 될 것인가" 전망편.

정직성 원칙 (숏츠 #001·#002와 동일):
- 화면 자막 숫자는 facts[](DART 실공시 검증분)만 사용한다.
- approx_return_pct는 백테스트 근사치라 방향성 참고용 — 자막 인용 금지.
- fact_check_required=true 종목은 영상 제작 전 DART로 배수를 확인해 facts를 채운다.
"""
from fastapi import APIRouter, Query
from sqlalchemy import text

from app.core.database import SessionLocal

router = APIRouter(prefix="/api/v2/shorts-feed", tags=["ShortsFeed"])

# ── 회고편 큐레이션 — 실측 승자에 탑다운 내러티브를 입힌다 ──────────────────
# facts는 DART 사업보고서 실공시 확인분만 기재 (미확인 종목은 fact_check_required).
# broll_keywords는 aiva B롤 검색용.
RETRO_NARRATIVES: dict[str, dict] = {
    "042700": {  # 한미반도체
        "sector": "AI 반도체 · HBM 장비",
        "narrative": "AI 데이터 폭증 → HBM 수요 → HBM 본딩 장비(TC본더) 사실상 독점",
        "thesis_type": "수요폭증+공급독점",
        "facts": [
            {"label": "매출", "value": "1,590억 → 5,589억 (3.5배)", "period": "2023→2024"},
            {"label": "영업이익", "value": "346억 → 2,554억 (7.4배)", "period": "2023→2024"},
            {"label": "영업이익률", "value": "46%", "period": "2024"},
        ],
        "dart_verified": True,
        "broll_keywords": ["HBM", "반도체 장비", "AI 데이터센터", "TC본더"],
    },
    "012450": {  # 한화에어로스페이스
        "sector": "방산 · 지정학",
        "narrative": "지정학 긴장 → 유럽 재무장 → K-방산(자주포·장갑차) 수출 과점",
        "thesis_type": "지정학+공급과점",
        "facts": [
            {"label": "매출", "value": "7.1조 → 11.2조 (1.6배)", "period": "2022→2024"},
            {"label": "영업이익", "value": "4,003억 → 1조7,319억 (4.3배)", "period": "2022→2024"},
            {"label": "영업이익률", "value": "15.4%", "period": "2024"},
        ],
        "dart_verified": True,
        "broll_keywords": ["K9 자주포", "유럽 재무장", "방산 수출", "폴란드 계약"],
    },
    "196170": {  # 알테오젠
        "sector": "바이오 · 플랫폼 기술",
        "narrative": "블록버스터 바이오의약품 특허절벽 → SC 제형변경 플랫폼 독점 라이선스",
        "thesis_type": "기술독점+라이선스",
        "facts": [
            {"label": "매출", "value": "288억 → 1,029억 (3.6배)", "period": "2022→2024"},
            {"label": "영업이익", "value": "-294억(적자) → 254억(흑자전환)", "period": "2022→2024"},
            {"label": "영업이익률", "value": "24.7%", "period": "2024"},
        ],
        "dart_verified": True,
        "broll_keywords": ["바이오 연구실", "주사제", "신약 개발"],
    },
    "000660": {  # SK하이닉스
        "sector": "메모리 반도체",
        "narrative": "2016 메모리 다운사이클 바닥 → 슈퍼사이클 + AI HBM 선점",
        "thesis_type": "사이클바닥+기술선점",
        "facts": [
            {"label": "매출", "value": "32.8조 → 66.2조 (2.0배)", "period": "2023→2024"},
            {"label": "영업이익", "value": "-7.7조(적자) → 23.5조(흑자전환)", "period": "2023→2024"},
            {"label": "영업이익률", "value": "35.5%", "period": "2024"},
        ],
        "dart_verified": True,
        "broll_keywords": ["반도체 팹", "HBM", "메모리 칩"],
    },
    "003230": {  # 삼양식품
        "sector": "식품 · 수출",
        "narrative": "불닭볶음면 글로벌 밈 → 수출 폭증 → 해외 매출이 본체가 된 K-푸드",
        "thesis_type": "브랜드+수출폭증",
        "facts": [
            {"label": "매출", "value": "9,090억 → 1조7,280억 (1.9배)", "period": "2022→2024"},
            {"label": "영업이익", "value": "904억 → 3,446억 (3.8배)", "period": "2022→2024"},
            {"label": "영업이익률", "value": "19.9%", "period": "2024"},
        ],
        "dart_verified": True,
        "broll_keywords": ["불닭볶음면", "K푸드", "수출 컨테이너"],
    },
    # ⚠️ 포스코퓨처엠(003670)은 의도적으로 제외했다 — 2022→2024 DART 실공시 확인 결과
    # 영업이익이 1,659억→359억→7.2억(사실상 소멸), 2024년 순손실 전환(-2,313억)로
    # "산업전환→소재 과점→실적 폭발" 서사와 정반대(2차전지 캐즘). 실제 주가 급등은
    # 2022~2023년 광풍 랠리였고 에코프로·에코프로비엠과 같은 "frenzy" 패턴이라
    # 이 형태의 회고 사례(성공 서사)로는 정직하지 않다. 재검토 없이 추가 금지.
}

# ── 전망편 큐레이션 — 현재 탑다운 후보 (거시→산업→독점, 모멘텀·밸류로 검증) ──
# 편입 기준(2026-09 정립): 가장 최근 연도(2023→2024)에 매출·영업이익이 **둘 다 증가**한
# 종목만 넣는다. "앞으로 좋아질 것"이 아니라 "이미 숫자가 가속 중"인 것만 다룬다.
# 이 기준으로 에스티팜(237690)·두산에너빌리티(034020)는 2024 실적이 2023 대비 감소해
# 제외했다 — DART 사업보고서 확인분. 재검토 없이 추가 금지.
CANDIDATES: list[dict] = [
    {
        "ticker": "033100", "name": "제룡전기",
        "sector": "AI 전력 · 변압기",
        "narrative": "AI 데이터센터 전력폭증 + 美 송전망 교체 → 변압기 공급부족 → 가격결정력",
        "thesis_type": "수요폭증+공급부족",
        "facts": [
            {"label": "매출", "value": "861억 → 2,627억 (3.0배)", "period": "2022→2024"},
            {"label": "순이익", "value": "125억 → 799억 (6.4배)", "period": "2022→2024"},
            {"label": "영업이익률", "value": "37%", "period": "2024"},
        ],
        "dart_verified": True,
        "target_scenario": "북미 전력망 투자 사이클 지속 → 수주잔고 기반 실적 가시성",
        "risk_note": "이미 크게 오른 종목. 변압기 증설 경쟁·관세 리스크.",
        "broll_keywords": ["변압기", "송전탑", "데이터센터 전력"],
    },
    {
        "ticker": "298040", "name": "효성중공업",
        "sector": "AI 전력 · 초고압 변압기",
        "narrative": "북미 노후 송전망 교체 + 데이터센터 수전설비 → 초고압 변압기 3~4년치 수주잔고",
        "thesis_type": "수요폭증+진입장벽",
        "facts": [
            {"label": "매출", "value": "3조5,101억 → 4조8,950억 (1.4배)", "period": "2022→2024"},
            {"label": "영업이익", "value": "1,432억 → 3,625억 (2.5배)", "period": "2022→2024"},
            {"label": "영업이익률", "value": "7.4%", "period": "2024"},
        ],
        "dart_verified": True,
        "target_scenario": "중공업 부문 증설 완료 → 고마진 초고압 비중 확대로 이익률 개선",
        "risk_note": "연결에 건설부문이 섞여 영업이익률이 희석된다. 건설 업황이 전체 실적을 끌어내릴 수 있음.",
        "broll_keywords": ["초고압 변압기", "송전망", "중공업 공장"],
    },
    {
        "ticker": "010120", "name": "LS ELECTRIC",
        "sector": "AI 전력 · 배전/전력기기",
        "narrative": "데이터센터·전기차 충전 인프라 확산 → 배전반·전력기기 수요 → 북미 매출 비중 상승",
        "thesis_type": "인프라투자+해외확장",
        "facts": [
            {"label": "매출", "value": "3조3,771억 → 4조5,518억 (1.3배)", "period": "2022→2024"},
            {"label": "영업이익", "value": "1,875억 → 3,897억 (2.1배)", "period": "2022→2024"},
            {"label": "영업이익률", "value": "5.6% → 8.6%", "period": "2022→2024"},
        ],
        "dart_verified": True,
        "target_scenario": "북미 배전 수요 지속 → 매출 성장보다 이익률 개선이 더 큰 레버",
        "risk_note": "이익률 8%대로 절대 수준이 낮다. 국내 건설·플랜트 경기에 물량이 연동됨.",
        "broll_keywords": ["배전반", "전력기기 공장", "전기차 충전소"],
    },
    {
        "ticker": "083650", "name": "비에이치아이",
        "sector": "발전 기자재 · HRSG",
        "narrative": "데이터센터 전력원으로 가스복합 재부상 → 배열회수보일러(HRSG) 수주 확대",
        "thesis_type": "사이클전환+틈새과점",
        "facts": [
            {"label": "매출", "value": "3,302억 → 4,047억 (1.2배)", "period": "2022→2024"},
            {"label": "영업이익", "value": "81억 → 219억 (2.7배)", "period": "2022→2024"},
            {"label": "순이익", "value": "-191억(적자) → 196억(흑자전환)", "period": "2022→2024"},
        ],
        "dart_verified": True,
        "target_scenario": "가스복합·원전 기자재 수주잔고가 매출로 인식되며 이익률 정상화",
        "risk_note": "2024년말 부채비율 351%(부채 4,083억·자본 1,164억). 수주 지연 시 재무 부담이 즉시 커지는 구조.",
        "broll_keywords": ["발전소 보일러", "가스복합발전", "플랜트 기자재"],
    },
    {
        "ticker": "214450", "name": "파마리서치",
        "sector": "미용의료 · 리쥬란",
        "narrative": "K-뷰티 시술 글로벌 확산 → 리쥬란(PN) 브랜드 해자 → 30%대 이익률 유지 성장",
        "thesis_type": "브랜드해자+수출확장",
        "facts": [
            {"label": "매출", "value": "1,948억 → 3,501억 (1.8배)", "period": "2022→2024"},
            {"label": "영업이익", "value": "659억 → 1,261억 (1.9배)", "period": "2022→2024"},
            {"label": "영업이익률", "value": "36.0%", "period": "2024"},
        ],
        "dart_verified": True,
        "target_scenario": "해외 인허가 확대 → 수출 비중 상승이 이익률을 유지한 채 외형을 키움",
        "risk_note": "미용의료는 경쟁 진입이 빠르고 규제·광고 이슈에 민감. 내수 시술 경기 둔화 리스크.",
        "broll_keywords": ["피부과 시술", "K뷰티", "제약 생산라인"],
    },
    {
        "ticker": "009540", "name": "HD한국조선해양",
        "sector": "조선 · 슈퍼사이클",
        "narrative": "환경규제 교체수요 + 도크 부족 → 선가 상승분이 고선가 물량으로 인식되는 구간",
        "thesis_type": "사이클전환+공급제약",
        "facts": [
            {"label": "매출", "value": "17조3,020억 → 25조5,386억 (1.5배)", "period": "2022→2024"},
            {"label": "영업이익", "value": "-3,556억(적자) → 1조4,341억(흑자전환)", "period": "2022→2024"},
            {"label": "영업이익률", "value": "5.6%", "period": "2024"},
        ],
        "dart_verified": True,
        "target_scenario": "저선가 물량 소진 완료 → 고선가 수주분 인식으로 이익률 추가 개선",
        "risk_note": "환율·후판가·인건비에 이익이 바로 흔들린다. 조선은 사이클 산업이라 고점 판단이 어렵다.",
        "broll_keywords": ["조선소 도크", "LNG 운반선", "용접 작업"],
    },
    {
        "ticker": "007660", "name": "이수페타시스",
        "sector": "AI 인프라 · MLB 기판",
        "narrative": "AI 가속기용 고다층 기판(MLB) 수요 → 소수 업체 과점 → 가동률이 곧 이익률",
        "thesis_type": "수요폭증+공급과점",
        "facts": [
            {"label": "매출", "value": "6,429억 → 8,369억 (1.3배)", "period": "2022→2024"},
            {"label": "영업이익", "value": "1,166억 → 622억 → 1,019억", "period": "2022→2023→2024"},
            {"label": "영업이익률", "value": "18.1% → 9.2% → 12.2%", "period": "2022→2023→2024"},
        ],
        "dart_verified": True,
        "target_scenario": "증설 라인 가동 + AI 기판 비중 확대 → 2022년 이익률 수준 회복 여부가 관전 포인트",
        "risk_note": "★2024 영업이익(1,019억)이 2022년(1,166억)보다 아직 낮다. 매출은 최고치지만 이익은 회복 중인 종목이라 '이미 증명된 텐배거'로 다루면 안 된다.",
        "broll_keywords": ["PCB 기판", "AI 가속기", "반도체 기판 생산라인"],
    },
]

_RISK_DEFAULT = "매수 추천이 아닌 방법론 사례입니다. 투자 판단·책임은 본인에게 있습니다."


@router.get("")
def get_shorts_feed(
    mode: str = Query("retrospective", description="retrospective(과거 실측 승자) | candidate(현재 탑다운 후보)"),
    limit: int = Query(20, ge=1, le=50),
):
    """AIVA 숏츠 파이프라인용 종목 피드. 스키마는 mode 공통, 채워지는 칸만 다르다."""
    if mode == "candidate":
        items = [{**c, "mode": "candidate"} for c in CANDIDATES[:limit]]
        return _envelope(mode, items,
                         note="현재 시점 탑다운 후보 — 미래 시나리오이므로 단정 표현 금지, risk_note 필수 노출.")

    if mode != "retrospective":
        return {"error": "mode는 retrospective | candidate 중 하나"}

    # 실측 백테스트에서 수익률 상위 종목을 뽑는다 (등급이 아니라 결과 기준 — 정직성).
    with SessionLocal() as session:
        exists = session.execute(text("""
            SELECT EXISTS (SELECT 1 FROM information_schema.tables
                           WHERE table_name = 'backfill_results')
        """)).scalar()
        rows = []
        if exists:
            rows = session.execute(text("""
                SELECT DISTINCT ON (b.ticker)
                       b.ticker, s.name, b.base_year, b.hold_years,
                       b.return_pct, b.grade, b.total_score, b.momentum_score
                FROM backfill_results b
                LEFT JOIN scores s ON s.ticker = b.ticker
                WHERE b.return_pct IS NOT NULL
                ORDER BY b.ticker, b.return_pct DESC
            """)).fetchall()

    ranked = sorted(rows, key=lambda r: float(r[4]), reverse=True)[:limit]
    items = []
    for r in ranked:
        ticker = r[0]
        cur = RETRO_NARRATIVES.get(ticker, {})
        items.append({
            "mode": "retrospective",
            "ticker": ticker,
            "name": r[1] or ticker,
            "sector": cur.get("sector"),
            "narrative": cur.get("narrative"),
            "thesis_type": cur.get("thesis_type"),
            "base_year": int(r[2]),
            "hold_years": int(r[3]),
            "approx_return_pct": float(r[4]),   # ⚠️ 백테스트 근사치 — 자막 인용 금지
            "grade_then": r[5],                  # 당시 우리 등급 (대부분 AVOID — 그 자체가 스토리)
            "score_then": float(r[6]) if r[6] is not None else None,
            "momentum_then": float(r[7]) if r[7] is not None else None,
            "facts": cur.get("facts", []),
            "dart_verified": cur.get("dart_verified", False),
            "fact_check_required": not cur.get("dart_verified", False),
            "broll_keywords": cur.get("broll_keywords", []),
            "risk_note": _RISK_DEFAULT,
        })
    return _envelope(mode, items,
                     note="실측 수익률 상위 순. approx_return_pct는 자막 인용 금지 — "
                          "화면 숫자는 facts[](DART 검증분)만. fact_check_required=true는 제작 전 DART 확인.")


def _envelope(mode: str, items: list[dict], note: str) -> dict:
    return {
        "mode": mode,
        "count": len(items),
        "items": items,
        "meta": {
            "consumer": "aiva_server/tenbagger_topic.py",
            "structure": "거시 → 산업 독점 → 해자=숫자 (숏츠 #001·#002 구조)",
            "note": note,
            "disclaimer": _RISK_DEFAULT,
        },
    }
