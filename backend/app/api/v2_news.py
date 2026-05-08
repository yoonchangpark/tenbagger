"""
뉴스 감성 분석 API (v2)
GET /api/v2/news/sentiment/{ticker}   종목 뉴스 감성 (최근 30일)
GET /api/v2/news/sector/{sector}      업종 뉴스 감성
GET /api/v2/news/signals              BUY/SELL 시그널 종목 목록
GET /api/v2/news/sector-overview      전체 업종 감성 현황
POST /api/v2/news/run                 (Admin) 뉴스 분석 즉시 실행
"""
import asyncio
import datetime
from fastapi import APIRouter, Query, HTTPException
from sqlalchemy import text
from app.core.database import SessionLocal
from app.core.config import settings

router = APIRouter(prefix="/api/v2/news", tags=["news-sentiment"])

# Lazy 분석 시 동시 실행 방지용 (같은 ticker 중복 트리거 방지)
_lazy_in_progress: set[str] = set()


async def _lazy_analyze_ticker(ticker: str, name: str):
    """단일 종목 백그라운드 분석 (lazy 모드 전용)"""
    if ticker in _lazy_in_progress:
        return  # 이미 분석 중
    _lazy_in_progress.add(ticker)
    try:
        from openai import AsyncOpenAI
        from app.workers.news_worker import analyze_ticker
        client = AsyncOpenAI()
        today = datetime.date.today()
        await analyze_ticker(ticker, name, client, today)
        print(f"[LAZY] {name}({ticker}) 뉴스 분석 완료")
    except Exception as e:
        print(f"[LAZY] {ticker} 분석 오류: {e}")
    finally:
        _lazy_in_progress.discard(ticker)


def _needs_refresh(latest_date) -> bool:
    """24시간 이상 오래된 데이터면 갱신 필요"""
    if not latest_date:
        return True
    today = datetime.date.today()
    delta = (today - latest_date).days
    return delta >= 1


# ── 시그널 → 색상/이모지 매핑 ────────────────────────────────────────────────
SIGNAL_META = {
    "BUY_SIGNAL":  {"emoji": "🟢", "color": "#22c55e", "label": "매수 신호"},
    "POSITIVE":    {"emoji": "🟡", "color": "#84cc16", "label": "긍정적"},
    "NEUTRAL":     {"emoji": "⚪", "color": "#6b7280", "label": "중립"},
    "CAUTION":     {"emoji": "🟠", "color": "#f97316", "label": "주의"},
    "SELL_SIGNAL": {"emoji": "🔴", "color": "#ef4444", "label": "매도 신호"},
}


@router.get("/sentiment/{ticker}")
async def get_ticker_sentiment(
    ticker: str,
    days: int = Query(30, ge=1, le=90),
):
    """
    종목 뉴스 감성 분석 결과 (최근 N일)
    """
    since = datetime.date.today() - datetime.timedelta(days=days)

    with SessionLocal() as session:
        # 일별 감성 집계 이력
        rows = session.execute(text("""
            SELECT sentiment_date, avg_score, signal,
                   article_count, positive_count, negative_count, neutral_count,
                   key_topics
            FROM news_sentiment
            WHERE ticker = :ticker AND sentiment_date >= :since
            ORDER BY sentiment_date DESC
        """), {"ticker": ticker, "since": since}).fetchall()

        if not rows:
            # 데이터 없음 → Lazy 분석 백그라운드 트리거
            name_row = session.execute(text(
                "SELECT name FROM scores WHERE ticker = :t LIMIT 1"
            ), {"t": ticker}).fetchone()
            stock_name = name_row.name if name_row else ticker
            # 백그라운드 분석 시작 (응답은 즉시)
            asyncio.create_task(_lazy_analyze_ticker(ticker, stock_name))
            return {
                "ticker": ticker,
                "name": stock_name,
                "has_data": False,
                "analyzing": True,
                "message": "뉴스 분석을 시작했습니다. 30~60초 후 다시 조회하세요.",
            }

        # 최신 감성 정보
        latest = rows[0]
        signal = latest.signal or "NEUTRAL"
        meta = SIGNAL_META.get(signal, SIGNAL_META["NEUTRAL"])

        # 24시간 이상 오래된 데이터면 백그라운드 갱신 트리거
        if _needs_refresh(latest.sentiment_date):
            name_row = session.execute(text(
                "SELECT name FROM scores WHERE ticker = :t LIMIT 1"
            ), {"t": ticker}).fetchone()
            stock_name = name_row.name if name_row else ticker
            asyncio.create_task(_lazy_analyze_ticker(ticker, stock_name))

        # 최근 10개 기사 (ticker 기준)
        recent_articles = session.execute(text("""
            SELECT title, url, published_at, sentiment_score, sentiment_label, key_topics
            FROM news_articles
            WHERE ticker = :ticker
            ORDER BY published_at DESC NULLS LAST
            LIMIT 10
        """), {"ticker": ticker}).fetchall()

    history = [
        {
            "date": str(r.sentiment_date),
            "avg_score": round(r.avg_score or 0, 3),
            "signal": r.signal,
            "article_count": r.article_count,
            "positive_count": r.positive_count,
            "negative_count": r.negative_count,
            "key_topics": r.key_topics or [],
        }
        for r in rows
    ]

    articles_list = [
        {
            "title": a.title,
            "url": a.url,
            "published_at": str(a.published_at) if a.published_at else None,
            "score": round(a.sentiment_score or 0, 3),
            "label": a.sentiment_label,
            "topics": a.key_topics or [],
        }
        for a in recent_articles
    ]

    return {
        "ticker": ticker,
        "has_data": True,
        "latest": {
            "date": str(latest.sentiment_date),
            "avg_score": round(latest.avg_score or 0, 3),
            "signal": signal,
            "signal_emoji": meta["emoji"],
            "signal_label": meta["label"],
            "signal_color": meta["color"],
            "article_count": latest.article_count,
            "key_topics": latest.key_topics or [],
        },
        "history": history,
        "recent_articles": articles_list,
    }


@router.get("/sector/{sector}")
async def get_sector_sentiment(sector: str, days: int = Query(30, ge=1, le=90)):
    """
    업종 뉴스 감성 분석 결과 (최근 N일)
    """
    from app.infra.clients.news_client import SECTOR_KEYWORDS
    if sector not in SECTOR_KEYWORDS:
        raise HTTPException(
            status_code=404,
            detail=f"알 수 없는 업종: {sector}. 가능한 업종: {list(SECTOR_KEYWORDS.keys())}",
        )

    since = datetime.date.today() - datetime.timedelta(days=days)
    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT sentiment_date, avg_score, signal,
                   article_count, positive_count, negative_count, key_topics, summary
            FROM sector_sentiment
            WHERE sector = :sector AND sentiment_date >= :since
            ORDER BY sentiment_date DESC
        """), {"sector": sector, "since": since}).fetchall()

        recent_articles = session.execute(text("""
            SELECT title, url, published_at, sentiment_score, sentiment_label
            FROM news_articles
            WHERE sector = :sector
            ORDER BY published_at DESC NULLS LAST
            LIMIT 10
        """), {"sector": sector}).fetchall()

    if not rows:
        return {
            "sector": sector,
            "has_data": False,
            "message": "업종 감성 데이터가 없습니다.",
        }

    latest = rows[0]
    signal = latest.signal or "NEUTRAL"
    meta = SIGNAL_META.get(signal, SIGNAL_META["NEUTRAL"])

    return {
        "sector": sector,
        "has_data": True,
        "latest": {
            "date": str(latest.sentiment_date),
            "avg_score": round(latest.avg_score or 0, 3),
            "signal": signal,
            "signal_emoji": meta["emoji"],
            "signal_label": meta["label"],
            "signal_color": meta["color"],
            "article_count": latest.article_count,
            "key_topics": latest.key_topics or [],
            "summary": latest.summary,
        },
        "history": [
            {
                "date": str(r.sentiment_date),
                "avg_score": round(r.avg_score or 0, 3),
                "signal": r.signal,
                "article_count": r.article_count,
            }
            for r in rows
        ],
        "recent_articles": [
            {
                "title": a.title,
                "url": a.url,
                "published_at": str(a.published_at) if a.published_at else None,
                "score": round(a.sentiment_score or 0, 3),
                "label": a.sentiment_label,
            }
            for a in recent_articles
        ],
    }


@router.get("/signals")
async def get_signals(
    signal: str = Query("BUY_SIGNAL", description="BUY_SIGNAL|POSITIVE|CAUTION|SELL_SIGNAL"),
    days: int = Query(1, ge=1, le=7),
    limit: int = Query(20, ge=1, le=100),
):
    """
    특정 시그널을 가진 종목 목록 (최근 N일 내)
    """
    since = datetime.date.today() - datetime.timedelta(days=days)
    allowed = {"BUY_SIGNAL", "POSITIVE", "NEUTRAL", "CAUTION", "SELL_SIGNAL"}
    if signal not in allowed:
        raise HTTPException(status_code=400, detail=f"signal은 {allowed} 중 하나여야 합니다.")

    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT ns.ticker, ns.name, ns.avg_score, ns.signal,
                   ns.article_count, ns.key_topics, ns.sentiment_date,
                   s.grade, s.total_score, s.close
            FROM news_sentiment ns
            LEFT JOIN scores s ON s.ticker = ns.ticker
            WHERE ns.signal = :signal AND ns.sentiment_date >= :since
            ORDER BY ns.avg_score DESC
            LIMIT :limit
        """), {"signal": signal, "since": since, "limit": limit}).fetchall()

    meta = SIGNAL_META.get(signal, SIGNAL_META["NEUTRAL"])
    return {
        "signal": signal,
        "signal_emoji": meta["emoji"],
        "signal_label": meta["label"],
        "since": str(since),
        "count": len(rows),
        "stocks": [
            {
                "ticker": r.ticker,
                "name": r.name,
                "avg_score": round(r.avg_score or 0, 3),
                "signal": r.signal,
                "article_count": r.article_count,
                "key_topics": r.key_topics or [],
                "sentiment_date": str(r.sentiment_date),
                "grade": r.grade,
                "total_score": r.total_score,
                "close": r.close,
            }
            for r in rows
        ],
    }


@router.get("/sector-overview")
async def get_sector_overview():
    """
    전체 업종 감성 현황 (오늘 or 최근 1일)
    — 히트맵 데이터용
    """
    from app.infra.clients.news_client import SECTOR_KEYWORDS
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)

    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT sector, avg_score, signal, article_count, key_topics, sentiment_date
            FROM sector_sentiment
            WHERE sentiment_date >= :since
            ORDER BY avg_score DESC
        """), {"since": yesterday}).fetchall()

    # DB에 없는 업종은 "데이터 없음"으로 채움
    db_map = {r.sector: r for r in rows}
    result = []
    for sector in SECTOR_KEYWORDS:
        if sector in db_map:
            r = db_map[sector]
            signal = r.signal or "NEUTRAL"
            meta = SIGNAL_META.get(signal, SIGNAL_META["NEUTRAL"])
            result.append({
                "sector": sector,
                "avg_score": round(r.avg_score or 0, 3),
                "signal": signal,
                "signal_emoji": meta["emoji"],
                "signal_color": meta["color"],
                "signal_label": meta["label"],
                "article_count": r.article_count,
                "key_topics": r.key_topics or [],
                "date": str(r.sentiment_date),
            })
        else:
            result.append({
                "sector": sector,
                "avg_score": None,
                "signal": "NO_DATA",
                "signal_emoji": "⬜",
                "signal_color": "#d1d5db",
                "signal_label": "데이터 없음",
                "article_count": 0,
                "key_topics": [],
                "date": None,
            })

    return {"sectors": result, "updated_at": str(today)}


@router.post("/run")
async def run_news_analysis(
    secret: str = Query(..., description="관리자 시크릿 키"),
    grade: str = Query(None, description="등급 필터 (TENBAGGER|COMPOUNDER|WATCHLIST)"),
    limit: int = Query(None, description="종목 수 제한 (테스트용)"),
    sector_only: bool = Query(False, description="업종 분석만 실행"),
):
    """
    뉴스 감성 분석 즉시 실행 (관리자 전용)
    백그라운드에서 비동기 실행 → 즉시 응답 반환
    """
    if secret != settings.admin_secret:
        raise HTTPException(status_code=403, detail="Invalid secret")

    async def _bg_run():
        from app.workers.news_worker import run_news_analysis as _worker
        try:
            await _worker(grade_filter=grade, limit=limit, sector_only=sector_only)
        except Exception as e:
            print(f"[NEWS API] 백그라운드 실행 오류: {e}")

    asyncio.create_task(_bg_run())

    return {
        "status": "started",
        "grade_filter": grade,
        "limit": limit,
        "sector_only": sector_only,
        "message": "뉴스 감성 분석이 백그라운드에서 시작되었습니다. 수 분 후 /api/v2/news/signals 에서 결과를 확인하세요.",
    }
