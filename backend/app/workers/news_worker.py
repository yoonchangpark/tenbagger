"""
뉴스 감성 분석 워커 (Pre-compute + Delta 패턴)

실행 방법:
  python -m app.workers.news_worker                      # 전체 종목
  python -m app.workers.news_worker --limit 50           # 상위 50종목만 (테스트)
  python -m app.workers.news_worker --grade TENBAGGER    # 특정 등급만
  python -m app.workers.news_worker --sector-only        # 업종 분석만

비용:
  GPT-4o-mini 기준 신규 기사 1건 = ~$0.0003
  2,000종목 × 평균 3개 신규 기사 = 약 $1.8/일
"""
import asyncio
import datetime
import argparse
import json

from openai import AsyncOpenAI
from sqlalchemy import text

from app.infra.clients.news_client import (
    fetch_stock_news, fetch_sector_news,
    make_article_hash, SECTOR_KEYWORDS,
)
from app.core.database import SessionLocal


# ── 감성 분석 함수들 ─────────────────────────────────────────────────────────

async def _analyze_articles_batch(
    articles: list[dict],
    context: str,
    client: AsyncOpenAI,
) -> list[dict]:
    """
    GPT-4o-mini로 기사 제목 배치 감성 분석
    Returns: [{"score": float, "label": str, "topics": list[str]}, ...]
    """
    if not articles:
        return []

    titles_text = "\n".join(
        f"{i+1}. {a['title']}" for i, a in enumerate(articles)
    )
    prompt = f"""다음 {context} 관련 한국 주식 뉴스 제목들을 투자자 관점에서 감성 분석하세요.

각 기사에 대해 정확히 아래 형식의 JSON 배열을 반환하세요:
[
  {{"score": 0.8, "label": "positive", "topics": ["실적개선", "영업이익"]}},
  ...
]

규칙:
- score: -1.0(매우 부정) ~ 0(중립) ~ +1.0(매우 긍정)
- label: "positive"(score>0.1), "negative"(score<-0.1), "neutral"(그 외)
- topics: 핵심 투자 키워드 1~3개 (한국어)
- 기사 수와 동일한 개수의 객체를 반환

뉴스 목록:
{titles_text}"""

    try:
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=600,
        )
        raw = resp.choices[0].message.content.strip()
        # JSON 배열 추출 (```json ... ``` 제거)
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        results = json.loads(raw)
        if isinstance(results, list):
            return results
        # 일부 모델이 {"results": [...]} 형태로 반환하는 경우
        if isinstance(results, dict):
            for key in ("results", "data", "items", "articles"):
                if key in results and isinstance(results[key], list):
                    return results[key]
        return []
    except Exception as e:
        print(f"    [NEWS] GPT 분석 실패 ({context[:20]}): {e}")
        return [{"score": 0.0, "label": "neutral", "topics": []}] * len(articles)


def _score_to_signal(avg_score: float) -> str:
    """평균 감성 점수 → 투자 시그널"""
    if avg_score >= 0.4:
        return "BUY_SIGNAL"
    elif avg_score >= 0.15:
        return "POSITIVE"
    elif avg_score <= -0.4:
        return "SELL_SIGNAL"
    elif avg_score <= -0.15:
        return "CAUTION"
    else:
        return "NEUTRAL"


def _flatten_topics(sentiments: list[dict], top_n: int = 5) -> list[str]:
    """여러 기사의 topics를 합쳐 빈도 기준 상위 N개 추출"""
    from collections import Counter
    all_topics: list[str] = []
    for s in sentiments:
        all_topics.extend(s.get("topics", []))
    counter = Counter(all_topics)
    return [t for t, _ in counter.most_common(top_n)]


# ── 종목 뉴스 분석 ────────────────────────────────────────────────────────────

# 투자 관련 키워드 (이게 제목에 없으면 GPT 호출 안 함 → 추가 30~50% 절약)
INVESTMENT_KEYWORDS = [
    "주가", "실적", "영업이익", "매출", "순이익", "적자", "흑자",
    "상승", "하락", "급등", "급락", "신고가", "신저가",
    "수주", "계약", "공급", "투자", "인수", "합병", "M&A",
    "신제품", "출시", "특허", "임상", "승인", "허가",
    "배당", "자사주", "유상증자", "무상증자", "분할",
    "목표가", "투자의견", "리포트", "전망", "기대",
    "리콜", "소송", "제재", "경고", "위반", "사고",
]


def _has_investment_keyword(title: str) -> bool:
    """제목에 투자 관련 키워드가 있는지 체크"""
    return any(kw in title for kw in INVESTMENT_KEYWORDS)


async def analyze_ticker(
    ticker: str,
    name: str,
    client: AsyncOpenAI,
    today: datetime.date,
) -> str:
    """단일 종목 뉴스 감성 분석 → DB 저장. 반환값: "ok"|"skip"|"error" """
    try:
        # 뉴스 수집
        articles = await fetch_stock_news(ticker, name, count=10)
        if not articles:
            return "no_news"

        # Delta: 신규 기사만 + 투자 키워드 필터링 (비용 절약)
        new_articles = []
        with SessionLocal() as session:
            for art in articles:
                # 투자 관련 키워드 없으면 스킵 (GPT 호출 없음)
                if not _has_investment_keyword(art["title"]):
                    continue
                h = make_article_hash(art.get("url"), art["title"])
                exists = session.execute(
                    text("SELECT 1 FROM news_articles WHERE article_hash = :h"),
                    {"h": h},
                ).fetchone()
                if not exists:
                    new_articles.append({**art, "hash": h})

        if not new_articles:
            return "skip"  # 모두 기분석 or 키워드 미스 → GPT 호출 없음

        # GPT 배치 분석
        sentiments = await _analyze_articles_batch(new_articles, f"{name}({ticker})", client)

        # DB 저장
        scores: list[float] = []
        with SessionLocal() as session:
            for art, sent in zip(new_articles, sentiments):
                score = float(sent.get("score", 0.0))
                label = sent.get("label", "neutral")
                topics = sent.get("topics", [])
                scores.append(score)

                session.execute(text("""
                    INSERT INTO news_articles
                        (ticker, article_hash, title, url, published_at,
                         sentiment_score, sentiment_label, key_topics)
                    VALUES (:ticker, :hash, :title, :url, :pub,
                            :score, :label, :topics)
                    ON CONFLICT (article_hash) DO NOTHING
                """), {
                    "ticker": ticker,
                    "hash": art["hash"],
                    "title": art["title"],
                    "url": art.get("url"),
                    "pub": art.get("published_at"),
                    "score": score,
                    "label": label,
                    "topics": topics,
                })

            if scores:
                avg = sum(scores) / len(scores)
                signal = _score_to_signal(avg)
                top_topics = _flatten_topics(sentiments)
                pos = sum(1 for s in scores if s > 0.1)
                neg = sum(1 for s in scores if s < -0.1)
                neu = len(scores) - pos - neg

                session.execute(text("""
                    INSERT INTO news_sentiment
                        (ticker, name, sentiment_date, avg_score, signal,
                         article_count, positive_count, negative_count, neutral_count,
                         key_topics)
                    VALUES (:ticker, :name, :date, :avg, :signal,
                            :total, :pos, :neg, :neu, :topics)
                    ON CONFLICT (ticker, sentiment_date) DO UPDATE
                        SET avg_score      = EXCLUDED.avg_score,
                            signal         = EXCLUDED.signal,
                            article_count  = EXCLUDED.article_count,
                            positive_count = EXCLUDED.positive_count,
                            negative_count = EXCLUDED.negative_count,
                            neutral_count  = EXCLUDED.neutral_count,
                            key_topics     = EXCLUDED.key_topics
                """), {
                    "ticker": ticker, "name": name, "date": today,
                    "avg": avg, "signal": signal, "total": len(scores),
                    "pos": pos, "neg": neg, "neu": neu, "topics": top_topics,
                })
                session.commit()

        return "ok"
    except Exception as e:
        print(f"    [NEWS] {ticker} 오류: {e}")
        return "error"


# ── 업종 뉴스 분석 ────────────────────────────────────────────────────────────

async def analyze_sector(
    sector: str,
    client: AsyncOpenAI,
    today: datetime.date,
) -> str:
    """단일 업종 뉴스 감성 분석 → DB 저장"""
    try:
        articles = await fetch_sector_news(sector, count=15)
        if not articles:
            return "no_news"

        # Delta: 신규 기사만
        new_articles = []
        with SessionLocal() as session:
            for art in articles:
                h = make_article_hash(art.get("url"), art["title"])
                exists = session.execute(
                    text("SELECT 1 FROM news_articles WHERE article_hash = :h"),
                    {"h": h},
                ).fetchone()
                if not exists:
                    new_articles.append({**art, "hash": h})

        if not new_articles:
            return "skip"

        sentiments = await _analyze_articles_batch(new_articles, f"{sector} 업종", client)

        scores: list[float] = []
        with SessionLocal() as session:
            for art, sent in zip(new_articles, sentiments):
                score = float(sent.get("score", 0.0))
                scores.append(score)
                session.execute(text("""
                    INSERT INTO news_articles
                        (sector, article_hash, title, url, published_at,
                         sentiment_score, sentiment_label, key_topics)
                    VALUES (:sector, :hash, :title, :url, :pub,
                            :score, :label, :topics)
                    ON CONFLICT (article_hash) DO NOTHING
                """), {
                    "sector": sector,
                    "hash": art["hash"],
                    "title": art["title"],
                    "url": art.get("url"),
                    "pub": art.get("published_at"),
                    "score": score,
                    "label": sent.get("label", "neutral"),
                    "topics": sent.get("topics", []),
                })

            if scores:
                avg = sum(scores) / len(scores)
                signal = _score_to_signal(avg)
                top_topics = _flatten_topics(sentiments)
                pos = sum(1 for s in scores if s > 0.1)
                neg = sum(1 for s in scores if s < -0.1)

                session.execute(text("""
                    INSERT INTO sector_sentiment
                        (sector, sentiment_date, avg_score, signal,
                         article_count, positive_count, negative_count, key_topics)
                    VALUES (:sector, :date, :avg, :signal,
                            :total, :pos, :neg, :topics)
                    ON CONFLICT (sector, sentiment_date) DO UPDATE
                        SET avg_score      = EXCLUDED.avg_score,
                            signal         = EXCLUDED.signal,
                            article_count  = EXCLUDED.article_count,
                            positive_count = EXCLUDED.positive_count,
                            negative_count = EXCLUDED.negative_count,
                            key_topics     = EXCLUDED.key_topics
                """), {
                    "sector": sector, "date": today,
                    "avg": avg, "signal": signal, "total": len(scores),
                    "pos": pos, "neg": neg, "topics": top_topics,
                })
                session.commit()

        return "ok"
    except Exception as e:
        print(f"    [NEWS] 업종 {sector} 오류: {e}")
        return "error"


# ── 메인 실행 함수 ────────────────────────────────────────────────────────────

async def run_news_analysis(
    grade_filter: str | None = None,
    limit: int | None = None,
    sector_only: bool = False,
):
    """뉴스 감성 분석 전체 실행"""
    today = datetime.date.today()
    client = AsyncOpenAI()

    print(f"\n{'='*60}")
    print(f"  뉴스 감성 분석 워커 시작 ({today})")
    print(f"  모드: {'업종만' if sector_only else '종목+업종'}")
    if grade_filter:
        print(f"  등급 필터: {grade_filter}")
    print(f"{'='*60}\n")

    # ── 1. 업종 뉴스 분석 ────────────────────────────────────────────────────
    print("[NEWS] 업종 뉴스 분석 시작...")
    sector_ok = sector_skip = sector_err = 0
    for sector in SECTOR_KEYWORDS:
        status = await analyze_sector(sector, client, today)
        if status == "ok":
            sector_ok += 1
            print(f"  ✅ {sector} 업종 분석 완료")
        elif status == "skip":
            sector_skip += 1
            print(f"  ⏭️  {sector} 업종 — 신규 기사 없음 (스킵)")
        elif status == "no_news":
            print(f"  ⚠️  {sector} 업종 — 뉴스 없음")
        else:
            sector_err += 1
            print(f"  ❌ {sector} 업종 오류")
        await asyncio.sleep(0.3)

    print(f"\n  업종 결과: 성공 {sector_ok} | 스킵 {sector_skip} | 실패 {sector_err}\n")

    if sector_only:
        print("업종 전용 모드 완료.")
        return

    # ── 2. 종목 뉴스 분석 ────────────────────────────────────────────────────
    # 비용 최적화: 등급 필터 종목 + 워치리스트 등록 종목 (UNION)
    with SessionLocal() as session:
        if grade_filter:
            # 등급 필터 + 워치리스트 종목 합집합 (워치리스트는 등급 무관 분석)
            rows = session.execute(text("""
                SELECT DISTINCT ticker, name FROM (
                    SELECT s.ticker, s.name, s.total_score
                    FROM scores s
                    WHERE s.grade = :grade
                    UNION
                    SELECT w.ticker, w.name, COALESCE(s.total_score, 0) AS total_score
                    FROM watchlist w
                    LEFT JOIN scores s ON s.ticker = w.ticker
                ) t
                ORDER BY total_score DESC NULLS LAST
            """), {"grade": grade_filter}).fetchall()
        else:
            query = "SELECT ticker, name FROM scores ORDER BY total_score DESC"
            if limit:
                query += f" LIMIT {limit}"
            rows = session.execute(text(query)).fetchall()

    tickers = [(r.ticker, r.name) for r in rows]
    if limit:
        tickers = tickers[:limit]
    total = len(tickers)
    print(f"[NEWS] 종목 {total}개 뉴스 분석 시작...\n")

    ok = skip = err = no_news = 0
    for i, (ticker, name) in enumerate(tickers, 1):
        status = await analyze_ticker(ticker, name, client, today)
        mark = {"ok": "✅", "skip": "⏭️ ", "no_news": "⚪", "error": "❌"}.get(status, "?")
        print(f"  [{i:4d}/{total}] {mark} {name:<15} {ticker}")
        if status == "ok":
            ok += 1
        elif status == "skip":
            skip += 1
        elif status == "no_news":
            no_news += 1
        else:
            err += 1

        await asyncio.sleep(0.3)  # API 레이트 리밋

        if i % 50 == 0:
            print(f"\n  ── 진행 {i}/{total} | 신규:{ok} 스킵:{skip} 뉴스없음:{no_news} 오류:{err} ──\n")

    print(f"\n{'='*60}")
    print(f"  뉴스 분석 완료!")
    print(f"  신규분석: {ok} | 스킵: {skip} | 뉴스없음: {no_news} | 오류: {err}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="뉴스 감성 분석 워커")
    parser.add_argument("--grade", default=None,
                        help="분석 대상 등급 (TENBAGGER|COMPOUNDER|WATCHLIST)")
    parser.add_argument("--limit", type=int, default=None,
                        help="종목 수 제한 (테스트용)")
    parser.add_argument("--sector-only", action="store_true",
                        help="업종 분석만 실행")
    args = parser.parse_args()

    asyncio.run(run_news_analysis(
        grade_filter=args.grade,
        limit=args.limit,
        sector_only=args.sector_only,
    ))
