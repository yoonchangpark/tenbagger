"""
텐배거 헌터 연동 모듈
/api/screener에서 TENBAGGER/COMPOUNDER 등급 종목을 가져와
쇼츠 파이프라인의 (주제, 문맥 데이터) 형식으로 변환한다.

환경변수:
  TENBAGGER_API_BASE — 텐배거 API 주소 (기본: http://localhost:8000)
"""
import os
import logging
import httpx

logger = logging.getLogger(__name__)

DISCLAIMER = "이 영상은 재무 데이터 기반 분석이며 투자 권유가 아닙니다. 투자 판단의 책임은 본인에게 있습니다."


def _fmt(v, suffix="", digits=1):
    if v is None:
        return "N/A"
    try:
        return f"{float(v):,.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return str(v)


def _build_context(c: dict) -> str:
    """스코어 row → GPT 대본용 팩트 문자열"""
    lines = [
        f"종목명: {c['name']} ({c['ticker']}, {c.get('market', '')})",
        f"텐배거 등급: {c.get('grade')} | 종합점수 {_fmt(c.get('total_score'))}/10 | 성장성 {_fmt(c.get('growth_score'))}/10",
        f"5년 매출 CAGR: {_fmt(c.get('revenue_cagr_5y'), '%')}",
        f"5년 EPS CAGR: {_fmt(c.get('eps_cagr_5y'), '%')}",
        f"5년 평균 ROE: {_fmt(c.get('avg_roe_5y'), '%')}",
        f"평균 FCF 마진: {_fmt(c.get('avg_fcf_margin'), '%')}",
        f"부채비율: {_fmt(c.get('debt_ratio'), '%')}",
        f"배당수익률: {_fmt(c.get('dividend_yield'), '%')}",
        f"PER: {_fmt(c.get('per'))} | PBR: {_fmt(c.get('pbr'))}",
        f"업종: {c.get('sector', '')} {c.get('growth_tag', '')}".strip(),
    ]
    return "\n".join(lines)


async def _fetch_qualitative(base: str, ticker: str) -> str:
    """AI 정성 분석 (사업모델·해자) — 실패해도 파이프라인은 계속 (빈 문자열 반환)"""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{base}/api/v2/company/{ticker}/qualitative", timeout=60.0
            )
            resp.raise_for_status()
            q = resp.json()
        moat = q.get("moat_detail") or {}
        parts = []
        if q.get("business_model"):
            parts.append(f"사업모델: {q['business_model']}")
        if q.get("moat_score") is not None:
            parts.append(f"경쟁우위(해자) 점수: {q['moat_score']}/10 — {moat.get('summary', '')}")
        return "\n".join(parts)
    except Exception as e:
        logger.warning(f"정성 분석 조회 실패 (수치만으로 진행): {e}")
        return ""


async def pick_tenbagger_topic(exclude_topics: list[str] | None = None) -> tuple[str, str]:
    """
    상위 등급 종목 중 히스토리에 없는 첫 종목을 골라 (주제, 문맥) 반환.
    실패 시 ValueError — 호출 측에서 기존 트렌드 모드로 폴백할 것.
    """
    exclude = exclude_topics or []
    base = os.getenv("TENBAGGER_API_BASE", "http://localhost:8000").rstrip("/")
    url = f"{base}/api/screener"
    params = {"grade": "TENBAGGER,COMPOUNDER", "sort": "total_score", "limit": 20}

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params, timeout=20.0)
        resp.raise_for_status()
        companies = resp.json().get("companies", [])

    if not companies:
        raise ValueError("텐배거 추천 종목 없음 (ETL 미실행?)")

    for c in companies:
        topic = f"{c['name']} 주가 10배 가능성 분석"
        if any(c["name"] in t for t in exclude):
            continue
        qualitative = await _fetch_qualitative(base, c["ticker"])
        narrative_block = f"\n[왜 이 종목인가 — AI 정성 분석]\n{qualitative}\n" if qualitative else ""
        context = (
            f"{_build_context(c)}\n{narrative_block}\n"
            f"[대본 구조 강제 규칙]\n"
            f"1. 첫 Scene의 narration은 반드시 종목명과 핵심 결론으로 시작할 것 (예: \"{c['name']}, 시스템 점수 {_fmt(c.get('total_score'))}점.\"). 배경 설명·분위기 조성 멘트로 시작 금지.\n"
            f"2. 모든 Scene의 narration에 위 재무 수치 중 최소 1개를 포함할 것 (사업모델·해자 설명 Scene 1~2개만 예외 허용). '전문가들', '월가', '시장이 주목' 같은 수치 없는 추상 멘트 절대 금지.\n"
            f"3. 전체 분량은 40~50초, 위 수치 중 최소 5개를 시청자에게 전달할 것.\n"
            f"4. 마지막 Scene의 narration은 반드시 다음 문구로 끝낼 것: \"{DISCLAIMER}\""
        )
        logger.info(f"텐배거 주제 선정: {topic} (점수 {c.get('total_score')})")
        return (topic, context)

    raise ValueError("미사용 텐배거 종목 없음 — 전부 히스토리에 존재")
