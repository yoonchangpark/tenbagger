"""텐배거 주제로 Threads 게시물을 생성해 큐에 적재한다.

OpenAI로 한국어 Threads 포스트(후킹 첫 문장 → 핵심 → 가벼운 CTA)를 만들고
content_queue에 pending 상태로 넣는다. 발행은 스케줄러(main.py)가 담당한다.

이 모듈은 텐배거 DB에 직접 붙지 않는다. "주제(topic)"만 입력받아 글을 만든다.
주제는 사람이 주거나, 상위 시스템(스크리너/숏츠 피드 등)이 뽑아 넘겨줄 수 있다.

사용법:
  # 실제 재무 수치를 주면 그 숫자를 중심으로 쓴다 (권장)
  python content_generator.py "제룡전기" --facts "매출 1,806억→3,067억(2022→2024), 영업이익률 28%, 부채비율 21%"

  # 수치 없이도 생성되지만, 숫자를 지어내지 않는 대신 일반론이 되기 쉽다
  python content_generator.py "AI 전력 인프라 텐배거"
  python content_generator.py "삼성전자 배당 성장" --count 2   # 2개 변형 생성

  # tenbagger shorts-feed에서 종목코드로 자동으로 topic+facts를 채운다 (수동 --facts 불필요)
  # candidate → retrospective 순서로 찾는다. TENBAGGER_API_BASE 환경변수로 API 주소 지정.
  python content_generator.py --ticker 033100

재무 수치는 DART에서 확인한 값을 넣는다. 모델은 주어진 숫자만 쓰고
없는 수치는 지어내지 않도록 지시받는다.
"""
from __future__ import annotations

import os
import sys
import logging
from typing import List, Optional

from content_queue import build_queue, ContentQueue

logger = logging.getLogger("threads-auto.generator")

# Threads 본문 상한(약 500자). 여유를 두고 목표치를 잡는다.
MAX_CHARS = 480

# 텐배거 계정 페르소나 (Notion #0 계정 설정 프롬프트를 텐배거용으로 고정)
SYSTEM_PROMPT = """너는 한국 장기투자 콘텐츠 계정의 Threads 카피라이터다.

계정 정보:
- 주제: 한국 주식 장기투자, 재무데이터 기반 '텐배거(10배 성장주)' 발굴
- 타겟: 개별 종목을 스스로 공부하려는 개인 장기투자자
- 톤: 솔직하고 데이터에 근거함. 과장·훅베이트 금지, 쉬운 표현 사용
- 목표: 저장·팔로우 (조회수만 노린 자극적 주제 금지)

반드시 지킬 규칙:
- 특정 종목의 매수/매도를 단정적으로 권하지 않는다. '투자 판단은 본인 몫'이라는 태도.
- 근거 없는 목표주가·수익률 단언 금지. 재무 지표/사업 구조 중심으로 설명.
- 첫 문장은 2초 안에 시선을 끌되 사실인 내용만.
- 마지막에 가벼운 한 줄 안내(저장 유도 등)를 붙인다.
- 맨 끝에 '※ 투자 판단·손익은 본인 책임' 한 줄을 넣는다.

절대 쓰지 말 것 (내용 없는 일반론):
- "미래의 핵심입니다", "성장이 기대됩니다", "주목받고 있습니다" 같은 빈 문장
- "수요가 급증하고 있습니다" 처럼 숫자 없는 추세 주장
- 어느 종목·어느 산업에 갖다 붙여도 말이 되는 문장
독자가 이 글에서 새로 알게 되는 사실이 없다면 실패한 글이다."""

USER_TEMPLATE = """아래 주제로 Threads 게시물 1개를 작성해줘.

주제: {topic}
{facts_block}
형식:
1) 첫 문장: 시선을 끄는 한 줄
2) 본문: 핵심 포인트 2~3개 (짧은 문장, 줄바꿈으로 구분)
3) 마무리: 저장을 유도하는 가벼운 한 줄
4) 해시태그: 2~4개
5) 맨 끝 줄: ※ 투자 판단·손익은 본인 책임

전체 길이는 공백 포함 {max_chars}자 이내. 인사말/서론 없이 바로 본문부터.
결과는 게시물 본문 텍스트만 출력해."""

# --facts 로 실제 재무 수치를 받았을 때 본문에 끼워 넣는 블록
FACTS_BLOCK = """
검증된 재무 데이터 (DART 기준):
{facts}

이 데이터 사용 규칙:
- 본문에 위 숫자를 반드시 인용한다. 숫자가 글의 중심이어야 한다.
- 위에 없는 수치는 절대 지어내지 않는다. 주가·목표가·수익률도 만들지 않는다.
- 숫자를 나열만 하지 말고, 그 숫자가 무엇을 뜻하는지 한 줄로 해석해준다.
"""

# 재무 데이터 없이 생성할 때 — 숫자를 지어내지 못하게 막는다
NO_FACTS_BLOCK = """
※ 확인된 재무 수치가 제공되지 않았다. 따라서:
- 매출·이익·비율 등 구체적 수치를 절대 지어내지 마라.
- 대신 '지표를 어떻게 읽는가', '흔한 실수', '판단 기준' 처럼
  숫자 없이도 실질적인 정보가 되는 내용으로 써라.
"""


def fetch_shorts_feed_item(ticker: str, base: Optional[str] = None) -> Optional[dict]:
    """tenbagger의 shorts-feed에서 종목코드에 해당하는 항목을 찾아 반환한다.

    candidate(현재 탑다운 후보) → retrospective(과거 실측 승자) 순서로 찾는다.
    두 모드 다 없으면 None. tenbagger 백엔드가 안 떠 있으면 경고만 남기고 None.
    """
    import requests

    base = (base or os.getenv("TENBAGGER_API_BASE", "http://localhost:8000")).rstrip("/")
    for mode in ("candidate", "retrospective"):
        try:
            resp = requests.get(
                f"{base}/api/v2/shorts-feed",
                params={"mode": mode, "limit": 50},
                timeout=15.0,
            )
            resp.raise_for_status()
            for item in resp.json().get("items", []):
                if item.get("ticker") == ticker:
                    item["_mode"] = mode
                    return item
        except Exception as exc:  # noqa: BLE001 — 조회 실패는 다음 모드로 계속
            logger.warning("shorts-feed 조회 실패(mode=%s): %s", mode, exc)
    return None


def facts_from_shorts_feed_item(item: dict) -> str:
    """shorts-feed 항목(facts[]·narrative·target_scenario 등) → 게시물 생성용 facts 문자열."""
    parts = []
    for f in item.get("facts", []) or []:
        label, value, period = f.get("label"), f.get("value"), f.get("period", "")
        if label and value:
            parts.append(f"{label} {value}" + (f" ({period})" if period else ""))
    if item.get("narrative"):
        parts.append(f"발굴 논리: {item['narrative']}")
    if item.get("_mode") == "candidate":
        if item.get("target_scenario"):
            parts.append(f"향후 시나리오(미확정): {item['target_scenario']}")
        parts.append("※ 아직 결과가 나오지 않은 미래 시나리오다 — 단정적 표현 금지.")
    return " / ".join(parts)


def _client():
    """OpenAI 클라이언트를 만든다. 패키지·키가 없으면 명확히 실패한다."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 환경변수가 필요합니다.")
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("`pip install openai` 가 필요합니다.") from exc
    return OpenAI(api_key=api_key)


def generate_post(
    topic: str,
    *,
    facts: str = "",
    model: Optional[str] = None,
    client=None,
) -> str:
    """주제 하나로 Threads 게시물 본문을 생성해 반환한다.

    Args:
        facts: DART 등에서 확인한 실제 재무 수치. 주면 그 숫자를 중심으로 쓰고,
               주지 않으면 수치를 지어내지 않는 방향으로 작성한다.
    """
    client = client or _client()
    model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    facts_block = FACTS_BLOCK.format(facts=facts.strip()) if facts.strip() else NO_FACTS_BLOCK
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": USER_TEMPLATE.format(
                    topic=topic, facts_block=facts_block, max_chars=MAX_CHARS
                ),
            },
        ],
        temperature=0.8,
    )
    text = (resp.choices[0].message.content or "").strip()
    if len(text) > 500:
        logger.warning("생성 결과가 500자 초과(%d) — 앞부분만 사용", len(text))
        text = text[:500].rstrip()
    return text


def generate_and_enqueue(
    topics: List[str],
    *,
    facts: str = "",
    count: int = 1,
    queue: Optional[ContentQueue] = None,
    model: Optional[str] = None,
) -> int:
    """여러 주제로 게시물을 생성해 큐에 넣고, 넣은 개수를 반환한다."""
    queue = queue or build_queue()
    client = _client()
    added = 0
    for topic in topics:
        for _ in range(count):
            try:
                text = generate_post(topic, facts=facts, model=model, client=client)
            except Exception as exc:  # noqa: BLE001 — 한 주제 실패가 전체를 막지 않도록
                logger.error("생성 실패 (%s): %s", topic, exc)
                continue
            queue.add(text)
            added += 1
            logger.info("큐 적재: %s", topic)
    return added


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    args = sys.argv[1:]

    def take_value(flag: str) -> Optional[str]:
        """--flag 값 형태를 args에서 떼어내 반환한다. 없으면 None."""
        nonlocal args
        if flag not in args:
            return None
        i = args.index(flag)
        value = args[i + 1] if i + 1 < len(args) else None
        args = args[:i] + args[i + 2 :]
        return value

    raw_count = take_value("--count")
    count = 1
    if raw_count is not None:
        try:
            count = int(raw_count)
        except ValueError:
            print("사용법: --count 뒤에 숫자를 넣어주세요.")
            return 1

    facts = take_value("--facts") or ""
    ticker = take_value("--ticker")

    topics = [a for a in args if a.strip()]

    if ticker:
        if topics or facts:
            print("--ticker 는 주제/--facts와 함께 쓸 수 없습니다 (shorts-feed에서 둘 다 자동으로 채웁니다).")
            return 1
        item = fetch_shorts_feed_item(ticker)
        if not item:
            print(f"shorts-feed(candidate/retrospective)에서 종목코드 {ticker}를 찾을 수 없습니다.")
            print("  TENBAGGER_API_BASE 환경변수(기본 http://localhost:8000)가 맞는지 확인하세요.")
            return 1
        topics = [item.get("name", ticker)]
        facts = facts_from_shorts_feed_item(item)
        print(f"shorts-feed에서 자동 로드 (mode={item.get('_mode')}): {item.get('name')} — {facts[:100]}")

    if not topics:
        print('사용법: python content_generator.py "주제" [--facts "매출 1,806억→3,067억, ROE 22%"] [--count N]')
        print('       또는: python content_generator.py --ticker 033100')
        return 1

    # 재무 수치는 주제 하나에 대응한다. 여러 주제에 같은 숫자를 붙이면 틀린 글이 나온다.
    if facts and len(topics) > 1:
        print("--facts 는 주제 하나에만 쓸 수 있습니다. 주제를 하나만 지정하세요.")
        return 1

    if not facts:
        print("참고: --facts 없이 생성합니다. 구체적 수치가 없는 글이 나올 수 있습니다.")

    try:
        added = generate_and_enqueue(topics, facts=facts, count=count)
    except RuntimeError as exc:
        # 설정 누락(키·패키지)은 예상 가능한 상황 — traceback 대신 안내만 출력
        print(f"실행할 수 없습니다: {exc}")
        print("  .env에 OPENAI_API_KEY=... 를 추가하고 다시 시도하세요.")
        return 1

    print(f"{added}개 게시물을 큐에 추가했습니다.")
    print("발행 전 확인: python preview.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
