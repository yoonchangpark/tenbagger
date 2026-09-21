"""개발 중인 것을 AS-IS → TO-BE 형태로 공개하는 Threads 빌드로그 생성기.

투자 콘텐츠(`content_generator.py`)와 목적이 다르다. 여기서는 종목이 아니라
"이 시스템을 이렇게 고치고 있다"를 쓴다. 독자가 얻는 건 투자 판단이 아니라
만드는 과정이고, 목표는 반응(댓글·저장)을 보는 것이다.

소스는 `CHANGELOG.md`다. 항목이 이미 AS-IS(`### 배경`)와
TO-BE(`### 추가` / `### 변경` / `### 수정`)로 나뉘어 있어 그대로 쓴다.

사용법:
  # CHANGELOG 최신 항목으로 1건 생성 → 큐 적재
  python devlog_generator.py --from-changelog

  # 아직 글로 안 만든 항목을 최대 3건까지
  python devlog_generator.py --from-changelog --count 3

  # 직접 써서 넣기 (CHANGELOG에 없는 것)
  python devlog_generator.py --asis "지금은 이렇다" --tobe "이렇게 바꿨다" --title "제목"

  # 큐가 얕을 때만 목표치까지 채운다 (스케줄러가 부르는 경로)
  python devlog_generator.py --refill
"""
from __future__ import annotations

import os
import re
import sys
import logging
from pathlib import Path
from typing import List, Optional, Dict

from content_queue import build_queue, ContentQueue
from content_generator import _client

logger = logging.getLogger("threads-auto.devlog")

MAX_CHARS = 480

# 큐를 이 개수까지 채운다. 주 3회 발행이므로 3이면 약 일주일치.
DEFAULT_TARGET_PENDING = 3

# 글감으로 볼 CHANGELOG 항목 수(최신순). 오래된 변경을 이제 와서 "방금 고쳤다"로
# 내보내지 않기 위한 상한이다. 최근 것을 다 썼으면 새로 만든 게 나올 때까지 쉰다.
MAX_BACKLOG = 5

SYSTEM_PROMPT = """너는 개인 개발자가 혼자 만드는 한국 주식 분석 서비스
'텐배거 헌터'의 빌드로그를 쓰는 Threads 카피라이터다.

읽는 사람:
- 이 서비스를 쓸 수도 있는 개인 투자자
- 비슷하게 뭔가 만들고 있는 개발자·1인 창업자

톤:
- 만드는 사람 1인칭. 잘된 것만이 아니라 뭐가 문제였는지도 그대로 쓴다.
- 홍보 문구가 아니라 작업 기록에 가깝다. 과장·감탄사 금지.
- 전문용어는 독자가 화면에서 겪는 일로 바꿔 쓴다.

반드시 지킬 것:
- 구조는 'AS-IS(전에는 이랬다) → TO-BE(이렇게 바꿨다)' 두 덩어리로 명확히 나눈다.
- 주어진 내용에 없는 성과·수치·일정을 지어내지 않는다.
  (사용자 수, 수익, 정확도 같은 숫자는 주어지지 않았으면 쓰지 않는다)
- 파일명·함수명·환경변수명은 쓰지 않는다. 독자가 알 수 없는 이름이다.
  대신 그게 사용자에게 무엇을 바꾸는지로 옮겨 쓴다.
- 마지막은 반응을 유도하는 질문 한 줄로 끝낸다. 이 글의 목적이 반응 확인이다.

이 글은 종목 추천이 아니므로 투자 책임 고지는 넣지 않는다."""

USER_TEMPLATE = """아래 개발 내용으로 Threads 게시물 1개를 써줘.

작업 제목: {title}

[AS-IS — 전에는 이랬다]
{asis}

[TO-BE — 이렇게 바꿨다]
{tobe}

형식:
1) 첫 줄: 무엇을 고쳤는지 한 문장 (과장 없이)
2) AS-IS: 전에 뭐가 문제였는지 1~2줄
3) TO-BE: 뭘 어떻게 바꿨는지 1~2줄
4) 마지막 줄: 읽는 사람에게 던지는 질문 한 줄
5) 해시태그 2~3개

전체 길이는 공백 포함 {max_chars}자 이내. 서론 없이 바로 본문부터.
결과는 게시물 본문 텍스트만 출력해."""

NO_ASIS_NOTE = """(이 작업에는 '배경'이 따로 적혀 있지 않다. TO-BE 내용에서
분명히 드러나는 범위 안에서만 AS-IS를 쓰고, 없는 문제를 지어내지 마라.)"""


def parse_changelog(path: Path) -> List[Dict[str, str]]:
    """CHANGELOG.md를 버전 항목 리스트로 파싱한다. 최신이 앞에 온다.

    각 항목: {version, title, asis, tobe}
    `### 배경`을 AS-IS로, `### 추가`/`### 변경`/`### 수정`을 TO-BE로 본다.
    """
    if not path.exists():
        logger.warning("CHANGELOG 없음: %s", path)
        return []

    text = path.read_text(encoding="utf-8")
    entries: List[Dict[str, str]] = []
    # "## v6.7 — 제목 (2026-09)" 로 시작하는 블록들
    blocks = re.split(r"^## (?=v\d)", text, flags=re.MULTILINE)[1:]

    for block in blocks:
        head, _, body = block.partition("\n")
        version = head.split()[0].strip()
        title = re.sub(r"^v\S+\s*[—-]\s*", "", head).strip()

        sections: Dict[str, str] = {}
        current = None
        buf: List[str] = []
        for line in body.splitlines():
            if line.startswith("### "):
                if current:
                    sections[current] = "\n".join(buf).strip()
                current = line[4:].strip()
                buf = []
            elif line.strip() == "---":
                break
            elif current:
                buf.append(line)
        if current:
            sections[current] = "\n".join(buf).strip()

        tobe = "\n".join(
            sections[name] for name in ("추가", "변경", "수정", "핵심 변경") if sections.get(name)
        ).strip()
        if not tobe:
            continue  # 바뀐 내용이 없으면 쓸 글도 없다
        entries.append(
            {
                "version": version,
                "title": title,
                "asis": sections.get("배경", ""),
                "tobe": tobe,
            }
        )
    return entries


def generate_devlog_post(
    title: str,
    asis: str,
    tobe: str,
    *,
    model: Optional[str] = None,
    client=None,
) -> str:
    """AS-IS/TO-BE 한 건으로 게시물 본문을 만든다."""
    client = client or _client()
    model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": USER_TEMPLATE.format(
                    title=title,
                    asis=asis.strip() or NO_ASIS_NOTE,
                    tobe=tobe.strip(),
                    max_chars=MAX_CHARS,
                ),
            },
        ],
        temperature=0.7,
    )
    text = (resp.choices[0].message.content or "").strip()
    if len(text) > 500:
        logger.warning("생성 결과가 500자 초과(%d) — 앞부분만 사용", len(text))
        text = text[:500].rstrip()
    return text


def changelog_path() -> Path:
    """레포 루트의 CHANGELOG.md 경로."""
    return Path(__file__).resolve().parent.parent / "CHANGELOG.md"


def enqueue_from_changelog(
    limit: int = 1,
    *,
    queue: Optional[ContentQueue] = None,
    model: Optional[str] = None,
) -> int:
    """아직 글로 만들지 않은 CHANGELOG 항목을 최신순으로 limit건 적재한다."""
    queue = queue or build_queue()
    entries = parse_changelog(changelog_path())
    if not entries:
        logger.warning("CHANGELOG에서 쓸 항목을 찾지 못했습니다.")
        return 0

    client = _client()
    added = 0
    for entry in entries[:MAX_BACKLOG]:
        if added >= limit:
            break
        source_key = f"changelog:{entry['version']}"
        if queue.has_source(source_key):
            continue  # 이미 이 버전으로 글을 냈다
        try:
            text = generate_devlog_post(
                entry["title"], entry["asis"], entry["tobe"], model=model, client=client
            )
        except Exception as exc:  # noqa: BLE001 — 한 건 실패가 전체를 막지 않도록
            logger.error("빌드로그 생성 실패 (%s): %s", entry["version"], exc)
            continue
        queue.add(text, source_key=source_key)
        added += 1
        logger.info("큐 적재: %s — %s", entry["version"], entry["title"])

    if added == 0:
        logger.info(
            "새로 적재할 항목이 없습니다 — 최근 %d개 변경은 이미 글로 만들었습니다.",
            MAX_BACKLOG,
        )
    return added


def refill(
    target: Optional[int] = None,
    *,
    queue: Optional[ContentQueue] = None,
    model: Optional[str] = None,
) -> int:
    """큐의 pending이 목표치보다 적으면 그 차이만큼 채운다.

    목표치는 QUEUE_TARGET_PENDING 환경변수로 조정한다(기본 3 = 약 한 주치).
    이미 충분하면 아무것도 하지 않고 0을 반환한다.
    """
    queue = queue or build_queue()
    if target is None:
        try:
            target = int(os.getenv("QUEUE_TARGET_PENDING", DEFAULT_TARGET_PENDING))
        except ValueError:
            logger.warning("QUEUE_TARGET_PENDING 형식 오류 — 기본값 %d 사용", DEFAULT_TARGET_PENDING)
            target = DEFAULT_TARGET_PENDING

    pending = queue.pending_count()
    shortfall = target - pending
    if shortfall <= 0:
        logger.info("대기 %d건 — 목표 %d건을 채우고 있어 보충하지 않습니다.", pending, target)
        return 0

    logger.info("대기 %d건 / 목표 %d건 — %d건 보충합니다.", pending, target, shortfall)
    return enqueue_from_changelog(shortfall, queue=queue, model=model)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    args = sys.argv[1:]

    def take_value(flag: str) -> Optional[str]:
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

    asis = take_value("--asis")
    tobe = take_value("--tobe")
    title = take_value("--title")

    try:
        if "--refill" in args:
            added = refill()
            print(f"{added}개 게시물을 큐에 추가했습니다.")
        elif "--from-changelog" in args:
            added = enqueue_from_changelog(count)
            print(f"{added}개 게시물을 큐에 추가했습니다.")
        elif tobe:
            text = generate_devlog_post(title or "개발 기록", asis or "", tobe)
            build_queue().add(text)
            print("1개 게시물을 큐에 추가했습니다.")
        else:
            print("사용법: python devlog_generator.py --from-changelog [--count N]")
            print('       python devlog_generator.py --asis "..." --tobe "..." [--title "..."]')
            print("       python devlog_generator.py --refill")
            return 1
    except RuntimeError as exc:
        print(f"실행할 수 없습니다: {exc}")
        print("  .env에 OPENAI_API_KEY=... 를 추가하고 다시 시도하세요.")
        return 1

    print("발행 전 확인: python preview.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
