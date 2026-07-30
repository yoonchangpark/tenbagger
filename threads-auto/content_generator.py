"""텐배거 주제로 Threads 게시물을 생성해 큐에 적재한다.

OpenAI로 한국어 Threads 포스트(후킹 첫 문장 → 핵심 → 가벼운 CTA)를 만들고
content_queue에 pending 상태로 넣는다. 발행은 스케줄러(main.py)가 담당한다.

이 모듈은 텐배거 DB에 직접 붙지 않는다. "주제(topic)"만 입력받아 글을 만든다.
주제는 사람이 주거나, 상위 시스템(스크리너/숏츠 피드 등)이 뽑아 넘겨줄 수 있다.

사용법:
  python content_generator.py "한화에어로스페이스 K-방산 성장" "AI 전력 인프라 텐배거"
  python content_generator.py "삼성전자 배당 성장" --count 2   # 주제당 2개 변형 생성
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
- 맨 끝에 '※ 투자 판단·손익은 본인 책임' 한 줄을 넣는다."""

USER_TEMPLATE = """아래 주제로 Threads 게시물 1개를 작성해줘.

주제: {topic}

형식:
1) 첫 문장: 시선을 끄는 한 줄
2) 본문: 핵심 포인트 2~3개 (짧은 문장, 줄바꿈으로 구분)
3) 마무리: 저장을 유도하는 가벼운 한 줄
4) 해시태그: 2~4개
5) 맨 끝 줄: ※ 투자 판단·손익은 본인 책임

전체 길이는 공백 포함 {max_chars}자 이내. 인사말/서론 없이 바로 본문부터.
결과는 게시물 본문 텍스트만 출력해."""


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


def generate_post(topic: str, *, model: Optional[str] = None, client=None) -> str:
    """주제 하나로 Threads 게시물 본문을 생성해 반환한다."""
    client = client or _client()
    model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_TEMPLATE.format(topic=topic, max_chars=MAX_CHARS)},
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
                text = generate_post(topic, model=model, client=client)
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
    count = 1
    if "--count" in args:
        i = args.index("--count")
        try:
            count = int(args[i + 1])
        except (IndexError, ValueError):
            print("사용법: --count 뒤에 숫자를 넣어주세요.")
            return 1
        args = args[:i] + args[i + 2 :]

    topics = [a for a in args if a.strip()]
    if not topics:
        print('사용법: python content_generator.py "주제1" "주제2" [--count N]')
        return 1

    try:
        added = generate_and_enqueue(topics, count=count)
    except RuntimeError as exc:
        # 설정 누락(키·패키지)은 예상 가능한 상황 — traceback 대신 안내만 출력
        print(f"실행할 수 없습니다: {exc}")
        print("  .env에 OPENAI_API_KEY=... 를 추가하고 다시 시도하세요.")
        return 1

    print(f"{added}개 게시물을 큐에 추가했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
