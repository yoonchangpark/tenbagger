"""
텐배거 사례 API — 탑다운 발굴 방식의 실제 사례 공개
GET /api/v2/track-record

⚠️ 정직성 원칙:
이 목록은 "우리 과거 점수가 예측했다"는 주장이 아니다. (백테스트 결과, 구
스코어링은 이런 종목을 오히려 AVOID로 분류했다 — scoring_postmortem.md 참조.)
대신 **"텐배거는 어떻게 나오는가"의 방법론 사례**다:
  거시 흐름 → 필연 성장 산업 → 공급 독점 기업 → 실적 폭발(DART 실공시로 검증).
모든 재무 배수는 DART 사업보고서 실공시(2026-07 조회) 기반의 사실이다.
"""
from fastapi import APIRouter

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
