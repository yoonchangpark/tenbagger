"""
DART 보고서 분기 비교 분석 — bzSummary + 재무지표 기반

document.json은 기본 API 키로 접근 불가 → 공개 구조화 API 사용

사용법:
  # 삼성전자: 2024 사업보고서 vs 2025 Q1 비교
  docker exec -it tenbagger_api python -m app.agents.dart_report_parser --ticker 005930

  # 종목 + 연도 직접 지정
  docker exec -it tenbagger_api python -m app.agents.dart_report_parser --ticker 005930 --year 2024
"""

import argparse
import asyncio
from typing import Optional

from app.core.config import settings
from app.infra.clients.dart_client import _get, get_corp_code as _get_corp_code


# reprt_code 라벨
REPRT_LABELS = {
    "11011": "사업보고서(연간)",
    "11012": "반기보고서",
    "11013": "1분기보고서",
    "11014": "3분기보고서",
}


# ── DART 텍스트 + 지표 수집 ──────────────────────────────────────────────────

async def fetch_bz_summary(corp_code: str, bsns_year: str, reprt_code: str) -> str:
    """사업의 개요 텍스트 (bzSummary.json)"""
    try:
        data = await _get("bzSummary.json", {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
        })
        items = data.get("list", [])
        if not items:
            return ""
        # 텍스트 필드 합산
        fields = ["induty_cd_nm", "bsns_nm", "bsns_pp", "bsns_cmp"]
        parts = []
        for item in items:
            for f in fields:
                v = item.get(f, "").strip()
                if v and v != "-":
                    parts.append(v)
        return "\n".join(parts)[:4000]
    except Exception as e:
        return f"(bzSummary 조회 실패: {e})"


async def fetch_financial_indices(corp_code: str, bsns_year: str, reprt_code: str) -> dict:
    """수익성 + 성장성 지표"""
    result = {}
    for cl_code, cl_nm in [("M210000", "수익성"), ("M230000", "성장성")]:
        try:
            data = await _get("fnlttCmpnyIndx.json", {
                "corp_code": corp_code,
                "bsns_year": bsns_year,
                "reprt_code": reprt_code,
                "idx_cl_code": cl_code,
            })
            for item in data.get("list", []):
                nm = item.get("idx_nm", "")
                val = item.get("idx_val", "")
                if val:
                    result[nm] = val
        except Exception:
            pass
    return result


async def fetch_period_data(corp_code: str, bsns_year: str, reprt_code: str) -> dict:
    """한 분기의 텍스트 + 지표 통합 수집"""
    label = REPRT_LABELS.get(reprt_code, reprt_code)
    print(f"  [{bsns_year} {label}] 수집 중...", flush=True)

    bz_text, indices = await asyncio.gather(
        fetch_bz_summary(corp_code, bsns_year, reprt_code),
        fetch_financial_indices(corp_code, bsns_year, reprt_code),
    )
    return {
        "label": f"{bsns_year} {label}",
        "bz_summary": bz_text,
        "indices": indices,
    }


# ── GPT 비교 분석 ────────────────────────────────────────────────────────────

def fmt_indices(indices: dict) -> str:
    if not indices:
        return "(데이터 없음)"
    key_names = ["매출액증가율", "영업이익증가율", "순이익증가율", "순이익률", "매출총이익률", "ROE"]
    lines = []
    for nm in key_names:
        if nm in indices:
            lines.append(f"  - {nm}: {indices[nm]}%")
    return "\n".join(lines) if lines else "(주요 지표 없음)"


async def compare_with_gpt(prev: dict, curr: dict, company_name: str) -> str:
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    prompt = f"""
{company_name} — 분기 비교 분석 ({prev['label']} → {curr['label']})

=== 직전: {prev['label']} ===
[사업 개요]
{prev['bz_summary'] or '(데이터 없음)'}

[주요 재무지표]
{fmt_indices(prev['indices'])}

=== 최신: {curr['label']} ===
[사업 개요]
{curr['bz_summary'] or '(데이터 없음)'}

[주요 재무지표]
{fmt_indices(curr['indices'])}

위 두 기간을 비교하여 다음 형식으로 분석해주세요:

## 핵심 변화 요약
(가장 중요한 변화 3가지, 한 줄씩)

## 항목별 비교표
| 카테고리 | 직전 | 최신 | 변화 |
|---------|------|------|------|
(변화: 🔴상향/🟢유지/🔵하향/🟡신규/⚫삭제)

## 투자 시사점
장기투자자 관점에서 이 변화가 의미하는 것 (2~3문장)

## 다음 분기 모니터링 포인트
- (2~3가지)
"""

    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=1200,
        temperature=0.2,
        messages=[
            {"role": "system", "content": "한국 주식 DART 공시 분석 전문가. 분기별 변화를 포착하고 장기투자 관점의 인사이트를 제공합니다."},
            {"role": "user", "content": prompt},
        ],
    )
    return resp.choices[0].message.content


# ── 메인 ─────────────────────────────────────────────────────────────────────

async def run(ticker: str, base_year: Optional[int]):
    print(f"\n{ticker} corp_code 조회 중...")
    corp_code, _ = await _get_corp_code(ticker)
    if not corp_code:
        print("corp_code를 찾을 수 없습니다.")
        return

    # 회사명 조회
    try:
        info = await _get("company.json", {"corp_code": corp_code})
        company_name = info.get("corp_name", ticker)
    except Exception:
        company_name = ticker

    year = str(base_year or 2024)
    next_year = str(int(year) + 1)

    print(f"\n{company_name} ({corp_code})")
    print(f"비교: {year} 사업보고서 → {next_year} Q1\n")

    prev_data, curr_data = await asyncio.gather(
        fetch_period_data(corp_code, year, "11011"),       # 사업보고서
        fetch_period_data(corp_code, next_year, "11013"),  # Q1
    )

    print("\nGPT 비교 분석 중...")
    result = await compare_with_gpt(prev_data, curr_data, company_name)

    print("\n" + "=" * 70)
    print(result)
    print("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DART 보고서 분기 비교 분석")
    parser.add_argument("--ticker", required=True, help="종목코드 (예: 005930)")
    parser.add_argument("--year", type=int, default=2024, help="기준 연도 (기본: 2024)")
    args = parser.parse_args()

    asyncio.run(run(args.ticker, args.year))
