"""
DART 사업보고서 / 분기보고서 원문 텍스트 파싱 + 분기 비교 분석

사용법:
  # 최신 사업보고서 분기 비교 (삼성전자)
  docker exec -it tenbagger_api python -m app.agents.dart_report_parser --ticker 005930

  # 특정 rcept_no 두 개 직접 비교
  docker exec -it tenbagger_api python -m app.agents.dart_report_parser \
    --prev 20241114002642 --curr 20250311001085
"""

import argparse
import io
import re
import zipfile
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from app.core.config import settings


DART_BASE = "https://opendart.fss.or.kr/api"
DART_KEY  = None  # settings에서 런타임에 로드

# 추출할 섹션 키워드 (우선순위 순)
TARGET_SECTIONS = [
    "향후 전망", "향후전망",
    "영업의 현황", "사업의 현황",
    "사업의 개요", "주요 경영현황",
    "시장 현황", "경쟁 현황",
    "연구개발",
]

# 최대 섹션 텍스트 길이 (토큰 절감)
MAX_SECTION_CHARS = 3000


# ── DART API ────────────────────────────────────────────────────────────────

async def search_annual_reports(corp_code: str, count: int = 4) -> list[dict]:
    """최근 사업보고서 + 분기보고서 rcept_no 목록 반환"""
    from app.infra.clients.dart_client import _get
    import datetime
    today = datetime.date.today().strftime("%Y%m%d")
    two_years_ago = (datetime.date.today() - datetime.timedelta(days=730)).strftime("%Y%m%d")

    data = await _get("list.json", {
        "corp_code": corp_code,
        "bgn_de": two_years_ago,
        "end_de": today,
        "page_count": "100",
        "sort": "date",
        "sort_mth": "desc",
    })
    results = []
    for item in data.get("list", []):
        nm = item.get("report_nm", "")
        if any(k in nm for k in ["사업보고서", "분기보고서", "반기보고서"]):
            results.append({
                "rcept_no": item["rcept_no"],
                "report_nm": nm.strip(),
                "rcept_dt": item["rcept_dt"],
            })
        if len(results) >= count:
            break
    return results


async def get_corp_code(ticker: str) -> Optional[str]:
    """ticker → corp_code (기존 dart_client 활용)"""
    from app.infra.clients.dart_client import get_corp_code as _get_corp_code
    corp_code, _ = await _get_corp_code(ticker)
    return corp_code


# ── 문서 다운로드 + 파싱 ─────────────────────────────────────────────────────

async def download_document(rcept_no: str) -> dict[str, bytes]:
    """DART 공시 ZIP 다운로드 → {파일명: bytes} 반환"""
    params = {"crtfc_key": settings.dart_api_key, "rcept_no": rcept_no}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(f"{DART_BASE}/document.json", params=params)
    r.raise_for_status()
    # 에러 응답 확인 (DART는 오류 시 JSON 반환)
    if r.headers.get("content-type", "").startswith("application/json"):
        raise RuntimeError(f"DART API 오류: {r.text[:200]}")
    try:
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        return {name: zf.read(name) for name in zf.namelist()}
    except zipfile.BadZipFile:
        raise RuntimeError(f"ZIP 파싱 실패 (응답 앞부분): {r.content[:200]}")


def html_to_text(html_bytes: bytes) -> str:
    """HTML → 순수 텍스트 (표 포함)"""
    soup = BeautifulSoup(html_bytes, "html.parser")
    # 스크립트·스타일 제거
    for tag in soup(["script", "style", "head"]):
        tag.decompose()
    # 표: 셀 사이 탭, 행 사이 줄바꿈
    for tr in soup.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        tr.replace_with("\t".join(cells) + "\n")
    return re.sub(r"\n{3,}", "\n\n", soup.get_text())


def extract_sections(text: str) -> dict[str, str]:
    """텍스트에서 투자 관련 섹션 추출"""
    sections = {}
    lines = text.split("\n")

    current_key = None
    current_buf = []

    for line in lines:
        stripped = line.strip()
        # 섹션 헤더 감지
        matched = next((k for k in TARGET_SECTIONS if k in stripped and len(stripped) < 60), None)
        if matched:
            if current_key and current_buf:
                sections[current_key] = "\n".join(current_buf)[:MAX_SECTION_CHARS]
            current_key = matched
            current_buf = [stripped]
        elif current_key:
            current_buf.append(line)
            # 다음 대분류 헤더가 나오면 종료
            if stripped and stripped[0] in "ⅠⅡⅢⅣⅤIVXIV" and len(stripped) < 40:
                sections[current_key] = "\n".join(current_buf)[:MAX_SECTION_CHARS]
                current_key = None
                current_buf = []

    if current_key and current_buf:
        sections[current_key] = "\n".join(current_buf)[:MAX_SECTION_CHARS]

    return sections


async def parse_report(rcept_no: str) -> dict[str, str]:
    """rcept_no → 섹션별 텍스트 추출"""
    print(f"  다운로드 중: {rcept_no} ...", flush=True)
    files = await download_document(rcept_no)

    all_text = ""
    # HTML 파일만, 크기 순 정렬 (본문이 큰 파일)
    html_files = sorted(
        [(n, b) for n, b in files.items() if n.lower().endswith((".html", ".htm"))],
        key=lambda x: len(x[1]), reverse=True
    )
    for name, content in html_files[:5]:
        all_text += html_to_text(content) + "\n\n"

    sections = extract_sections(all_text)
    print(f"  추출된 섹션: {list(sections.keys())}", flush=True)
    return sections


# ── GPT 비교 분석 ────────────────────────────────────────────────────────────

async def compare_with_gpt(
    prev_meta: dict, prev_sections: dict[str, str],
    curr_meta: dict, curr_sections: dict[str, str],
) -> str:
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    def fmt(sections: dict) -> str:
        if not sections:
            return "섹션 추출 실패"
        return "\n\n".join(f"[{k}]\n{v}" for k, v in sections.items())

    prompt = f"""두 보고서의 경영진 발언과 향후 전망을 비교 분석해주세요.

=== 직전 보고서: {prev_meta['report_nm']} ({prev_meta['rcept_dt']}) ===
{fmt(prev_sections)}

=== 최신 보고서: {curr_meta['report_nm']} ({curr_meta['rcept_dt']}) ===
{fmt(curr_sections)}

다음 형식으로 분석해주세요:

## 핵심 변화 요약
(직전 → 최신에서 달라진 핵심 포인트 3가지)

## 항목별 비교
| 카테고리 | 직전 발언 요약 | 최신 발언 요약 | 변화 분류 |
(변화 분류: 🔴상향 / 🟢유지 / 🔵하향 / 🟡신규언급 / ⚫삭제)

## 투자 시사점
장기투자자 관점에서 이 변화가 의미하는 것 (2~3문장)

## 모니터링 포인트
다음 분기에 확인해야 할 항목 (불릿 2~3개)"""

    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=1200,
        temperature=0.2,
        messages=[
            {"role": "system", "content": "한국 주식 DART 보고서 분석 전문가입니다. 경영진 발언의 변화를 정확하게 포착하고 투자 시사점을 제공합니다."},
            {"role": "user", "content": prompt},
        ],
    )
    return resp.choices[0].message.content


# ── 메인 ─────────────────────────────────────────────────────────────────────

async def run(ticker: Optional[str], prev_rcept: Optional[str], curr_rcept: Optional[str]):
    import asyncio

    if ticker:
        print(f"\n{ticker} corp_code 조회 중...")
        corp_code = await get_corp_code(ticker)
        if not corp_code:
            print("corp_code를 찾을 수 없습니다.")
            return

        print(f"최근 보고서 목록 조회 중 (corp_code: {corp_code})...")
        reports = await search_annual_reports(corp_code, count=4)
        if len(reports) < 2:
            print("비교할 보고서가 2개 미만입니다.")
            return

        print("\n비교 대상:")
        for i, r in enumerate(reports[:4]):
            print(f"  [{i}] {r['report_nm']} ({r['rcept_dt']}) - {r['rcept_no']}")

        curr_meta = reports[0]
        prev_meta = reports[1]
    else:
        curr_meta = {"rcept_no": curr_rcept, "report_nm": "최신 보고서", "rcept_dt": ""}
        prev_meta = {"rcept_no": prev_rcept, "report_nm": "직전 보고서", "rcept_dt": ""}

    print(f"\n직전: {prev_meta['report_nm']} ({prev_meta['rcept_no']})")
    print(f"최신: {curr_meta['report_nm']} ({curr_meta['rcept_no']})")
    print()

    prev_sections = await parse_report(prev_meta["rcept_no"])
    curr_sections = await parse_report(curr_meta["rcept_no"])

    if not prev_sections and not curr_sections:
        print("텍스트 섹션 추출 실패 — 보고서 구조를 확인하세요.")
        return

    print("\nGPT 분석 중...")
    result = await compare_with_gpt(prev_meta, prev_sections, curr_meta, curr_sections)

    print("\n" + "=" * 70)
    print(result)
    print("=" * 70)


if __name__ == "__main__":
    import asyncio

    parser = argparse.ArgumentParser(description="DART 보고서 원문 분기 비교 분석")
    parser.add_argument("--ticker", help="종목코드 (예: 005930)")
    parser.add_argument("--prev", help="직전 보고서 rcept_no")
    parser.add_argument("--curr", help="최신 보고서 rcept_no")
    args = parser.parse_args()

    if not args.ticker and not (args.prev and args.curr):
        parser.error("--ticker 또는 --prev + --curr 를 지정하세요")

    asyncio.run(run(args.ticker, args.prev, args.curr))
