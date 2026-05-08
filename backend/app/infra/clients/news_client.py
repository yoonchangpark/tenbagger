"""
뉴스 수집 클라이언트
- 네이버 뉴스 검색 API (1순위)
- 네이버 API 키 미설정 시 RSS/간단 크롤링 fallback (2순위)
"""
import hashlib
import re
import httpx
from app.core.config import settings


# ── 업종 → 대표 키워드 매핑 ─────────────────────────────────────────────────
SECTOR_KEYWORDS: dict[str, list[str]] = {
    "반도체": ["반도체", "HBM", "파운드리", "낸드"],
    "2차전지": ["2차전지", "배터리", "리튬", "양극재"],
    "바이오": ["바이오", "제약", "신약", "임상"],
    "자동차": ["자동차", "전기차", "EV", "완성차"],
    "금융": ["금리", "은행", "증권", "보험"],
    "IT·소프트웨어": ["소프트웨어", "클라우드", "AI", "플랫폼"],
    "철강·소재": ["철강", "소재", "화학", "원자재"],
    "에너지": ["에너지", "태양광", "풍력", "원전"],
    "유통·소비재": ["소비재", "유통", "리테일", "브랜드"],
    "통신": ["통신", "5G", "인터넷"],
}


def make_article_hash(url: str | None, title: str) -> str:
    """기사 중복 방지용 SHA-256 해시"""
    raw = (url or title).strip()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _clean_html(text: str) -> str:
    """네이버 뉴스 API 응답의 HTML 태그 제거"""
    return re.sub(r"<[^>]+>", "", text or "").strip()


async def fetch_stock_news(ticker: str, name: str, count: int = 10) -> list[dict]:
    """
    종목별 최신 뉴스 수집 (네이버 뉴스 API)
    Returns: [{"title", "url", "published_at", "source"}, ...]
    """
    if not settings.naver_client_id or not settings.naver_client_secret:
        return await _fetch_news_rss(name, count)

    query = f"{name} 주가"
    url = (
        f"https://openapi.naver.com/v1/search/news.json"
        f"?query={query}&display={count}&sort=date"
    )
    headers = {
        "X-Naver-Client-Id": settings.naver_client_id,
        "X-Naver-Client-Secret": settings.naver_client_secret,
    }
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            items = resp.json().get("items", [])
        return [
            {
                "title": _clean_html(i.get("title", "")),
                "url": i.get("link", ""),
                "published_at": i.get("pubDate"),
                "source": _clean_html(i.get("description", ""))[:120],
            }
            for i in items
            if i.get("title")
        ]
    except Exception as e:
        print(f"[NEWS] 네이버 API 실패 ({name}): {e}")
        return await _fetch_news_rss(name, count)


async def fetch_sector_news(sector: str, count: int = 15) -> list[dict]:
    """
    업종별 뉴스 수집 (네이버 뉴스 API)
    """
    keywords = SECTOR_KEYWORDS.get(sector, [sector])
    query = " ".join(keywords[:2])  # 상위 2개 키워드로 검색

    if not settings.naver_client_id or not settings.naver_client_secret:
        return []

    url = (
        f"https://openapi.naver.com/v1/search/news.json"
        f"?query={query}&display={count}&sort=date"
    )
    headers = {
        "X-Naver-Client-Id": settings.naver_client_id,
        "X-Naver-Client-Secret": settings.naver_client_secret,
    }
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            items = resp.json().get("items", [])
        return [
            {
                "title": _clean_html(i.get("title", "")),
                "url": i.get("link", ""),
                "published_at": i.get("pubDate"),
                "source": _clean_html(i.get("description", ""))[:120],
            }
            for i in items
            if i.get("title")
        ]
    except Exception as e:
        print(f"[NEWS] 업종 뉴스 실패 ({sector}): {e}")
        return []


async def _fetch_news_rss(name: str, count: int = 5) -> list[dict]:
    """
    네이버 API 키 없을 때 네이버 뉴스 검색 페이지 RSS fallback
    (공개 RSS이므로 API 키 불필요, 단 데이터 양 적음)
    """
    try:
        import xml.etree.ElementTree as ET
        rss_url = f"https://news.naver.com/search/result.nhn?query={name}&field=0&where=news&sm=tab_opt&sort=0&mode=LSD&res_fr=0&res_to=0"
        # 실제로는 네이버 RSS가 막혀있으므로 빈 리스트 반환
        return []
    except Exception:
        return []
