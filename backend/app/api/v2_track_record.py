"""
텐배거 사례 API — 탑다운 발굴 방식의 실제 사례 공개
GET /api/v2/track-record          — 교육용 과거 사례 (하드코딩)
GET /api/v2/track-record/live     — 실시간 예측 추적 (score_history → 현재가 비교)

⚠️ 정직성 원칙:
이 목록은 "우리 과거 점수가 예측했다"는 주장이 아니다. (백테스트 결과, 구
스코어링은 이런 종목을 오히려 AVOID로 분류했다 — scoring_postmortem.md 참조.)
대신 **"텐배거는 어떻게 나오는가"의 방법론 사례**다:
  거시 흐름 → 필연 성장 산업 → 공급 독점 기업 → 실적 폭발(DART 실공시로 검증).
모든 재무 배수는 DART 사업보고서 실공시(2026-07 조회) 기반의 사실이다.
"""
import datetime
from fastapi import APIRouter, Query
from sqlalchemy import text
from app.core.database import SessionLocal

router = APIRouter(prefix="/api/v2/track-record", tags=["TrackRecord"])

# 탑다운 사례 — 거시→산업→독점→실적. 재무 배수는 DART 사업보고서 실값.
CASES = [
    {
        "name": "한미반도체", "ticker": "042700", "sector": "AI 반도체 · HBM",
        "thesis": "AI 데이터 폭증 → HBM 수요 → HBM 본딩 장비(TC본더) 사실상 독점",
        "period": "2023 → 2024",
        "metrics": [
            {"label": "매출", "value": "3.5배"},
            {"label": "영업이익", "value": "7.4배"},
            {"label": "영업이익률", "value": "46%"},
        ],
        "detail": "매출 1,590억→5,589억, 영업이익 346억→2,554억",
    },
    {
        "name": "한화에어로스페이스", "ticker": "012450", "sector": "방산 · 지정학",
        "thesis": "지정학 긴장 → 유럽 재무장 → K-방산(자주포·장갑차) 수출 과점",
        "period": "2022 → 2024",
        "metrics": [
            {"label": "매출", "value": "1.6배"},
            {"label": "영업이익", "value": "4.3배"},
            {"label": "영업이익률", "value": "15%"},
        ],
        "detail": "매출 7.1조→11.2조, 영업이익 4,003억→1조7,319억",
    },
    {
        "name": "제룡전기", "ticker": "033100", "sector": "AI 전력 · 변압기",
        "thesis": "AI 데이터센터 전력폭증 + 美 송전망 교체 → 변압기 공급부족 → 가격결정력",
        "period": "2022 → 2024",
        "metrics": [
            {"label": "매출", "value": "3.0배"},
            {"label": "순이익", "value": "6.4배"},
            {"label": "영업이익률", "value": "37%"},
        ],
        "detail": "매출 861억→2,627억, 순이익 125억→799억",
    },
]


@router.get("")
async def get_track_record():
    """탑다운 발굴 방식의 실제 사례 — DART 실공시 재무로 '해자'를 검증한 케이스."""
    return {
        "cases": CASES,
        "meta": {
            "is_method_showcase": True,
            "title": "텐배거는 이렇게 나온다",
            "subtitle": "거시 흐름 → 산업 독점 → 실적 폭발 (DART 실공시로 검증)",
            "note": ("과거 예측 적중을 주장하는 목록이 아닙니다. 탑다운 발굴 방식"
                     "(거시→산업→독점→실적)을 실제 사례로 보여주는 것이며, "
                     "모든 재무 수치는 DART 사업보고서 실값입니다."),
        },
    }


@router.get("/live")
async def get_live_track_record(
    grade: str = Query("TENBAGGER,COMPOUNDER", description="등급 필터 (쉼표 구분)"),
    days: int = Query(90, ge=7, le=365, description="최근 N일 스냅샷 조회"),
    limit: int = Query(30, ge=1, le=100),
):
    """
    실시간 예측 추적 — score_history 첫 등재 이후 현재가 수익률 계산.

    ETL이 매일 score_history에 스냅샷을 쌓음.
    이 API는 grade 등재 첫날 주가 vs scores 테이블의 최신 종가를 비교해
    "며칠 전에 예측했는데 지금 얼마나 올랐나"를 반환한다.

    ⚠️ 투자 권유가 아닙니다. 시스템 검증용 데이터입니다.
    """
    grade_list = [g.strip().upper() for g in grade.split(",") if g.strip()]
    if not grade_list:
        return {"items": [], "summary": {}, "as_of": datetime.date.today().isoformat()}

    grade_in = ",".join(f"'{g}'" for g in grade_list)
    cutoff = datetime.date.today() - datetime.timedelta(days=days)

    with SessionLocal() as session:
        # score_history가 있는지 먼저 확인
        table_exists = session.execute(text("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'score_history'
            )
        """)).scalar()

        if not table_exists:
            return {
                "items": [],
                "summary": {},
                "as_of": datetime.date.today().isoformat(),
                "message": "score_history 테이블 없음. ETL 실행 후 데이터가 쌓입니다.",
            }

        # 각 종목의 grade 첫 등재일 + 첫 주가 조회
        rows = session.execute(text(f"""
            SELECT
                sh.ticker,
                sh.name,
                sh.grade,
                sh.total_score,
                MIN(sh.snapshot_date)                               AS first_date,
                (ARRAY_AGG(sh.close_price ORDER BY sh.snapshot_date ASC))[1]  AS entry_price,
                COUNT(sh.snapshot_date)                             AS days_tracked
            FROM score_history sh
            WHERE sh.grade IN ({grade_in})
              AND sh.snapshot_date >= :cutoff
              AND sh.close_price IS NOT NULL AND sh.close_price > 0
            GROUP BY sh.ticker, sh.name, sh.grade, sh.total_score
            ORDER BY sh.total_score DESC NULLS LAST
            LIMIT :limit
        """), {"cutoff": cutoff, "limit": limit}).fetchall()

        if not rows:
            return {
                "items": [],
                "summary": {},
                "as_of": datetime.date.today().isoformat(),
                "message": f"최근 {days}일 내 {grade} 등급 데이터가 없습니다. ETL이 실행 중인지 확인하세요.",
            }

        tickers = [r[0] for r in rows]

        # 최신 종가 — scores 테이블에서 가져옴 (ETL이 매일 갱신)
        ticker_in = ",".join(f"'{t}'" for t in tickers)
        price_rows = session.execute(text(f"""
            SELECT ticker, close, analyzed_at
            FROM scores
            WHERE ticker IN ({ticker_in})
              AND close IS NOT NULL AND close > 0
        """)).fetchall()

    current_prices = {r[0]: {"price": r[1], "as_of": r[2]} for r in price_rows}

    items = []
    returns = []
    for r in rows:
        ticker, name, grade_val, score, first_date, entry_price, days_tracked = r
        curr = current_prices.get(ticker, {})
        current_price = curr.get("price")
        price_as_of = curr.get("as_of")

        return_pct = None
        if entry_price and current_price:
            return_pct = round((current_price - entry_price) / entry_price * 100, 2)
            returns.append(return_pct)

        hold_days = (datetime.date.today() - first_date).days if first_date else None

        items.append({
            "ticker": ticker,
            "name": name,
            "grade": grade_val,
            "score": score,
            "first_date": first_date.isoformat() if first_date else None,
            "entry_price": entry_price,
            "current_price": current_price,
            "price_as_of": price_as_of.isoformat() if price_as_of else None,
            "return_pct": return_pct,
            "hold_days": hold_days,
            "days_tracked": days_tracked,
        })

    # 요약 통계
    summary = {}
    if returns:
        positive = [r for r in returns if r > 0]
        summary = {
            "total_tracked": len(items),
            "mean_return_pct": round(sum(returns) / len(returns), 2),
            "win_rate_pct": round(len(positive) / len(returns) * 100, 1),
            "best_pct": round(max(returns), 2),
            "worst_pct": round(min(returns), 2),
        }

    return {
        "items": items,
        "summary": summary,
        "as_of": datetime.date.today().isoformat(),
        "note": "투자 권유가 아닙니다. 시스템 검증용 데이터입니다.",
    }
