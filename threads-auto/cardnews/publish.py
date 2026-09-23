"""CHANGELOG 한 항목 → 카드뉴스 5장 + 본문 → 발행 큐(캐러셀).

`devlog_generator.py`가 쓰는 본문에 카드 이미지를 붙여 큐에 넣는다.
본문 생성과 발행 자체는 건드리지 않는다 — 여기서 하는 일은 카드 스펙을 만들고
PNG로 렌더한 뒤, 그 공개 URL을 `image_urls`로 함께 적재하는 것뿐이다.

⚠️ 실행 위치: 렌더에 Chromium이 필요하므로 **로컬에서 돌린다.**
   PNG는 `frontend/media/threads-posts/`에 떨어지는데, 배포 컨테이너의
   파일시스템은 재배포마다 초기화된다. Meta 서버가 발행 시점에 image_url을
   직접 가져가므로 **PNG를 커밋·푸시해 배포된 뒤에 발행되어야 한다.**
   순서: 이 명령 실행 → PNG 커밋·푸시 → 배포 확인 → 예정된 발행일에 나감.

사용법:
  python cardnews/publish.py                 # 최신 미발행 CHANGELOG 항목 1건
  python cardnews/publish.py --render-only   # 렌더만 하고 큐에는 넣지 않는다
  python cardnews/publish.py --spec my.json  # 직접 쓴 스펙으로 (본문도 직접)
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from content_queue import build_queue                              # noqa: E402
from content_generator import _client                              # noqa: E402
from devlog_generator import (                                     # noqa: E402
    changelog_path,
    generate_devlog_post,
    parse_changelog,
    MAX_BACKLOG,
)
from cardnews.render import DEFAULT_OUT, public_urls, render_spec  # noqa: E402

logger = logging.getLogger("threads-auto.cardnews")

CARD_SYSTEM_PROMPT = """너는 개인 개발자가 혼자 만드는 한국 주식 분석 서비스
'텐배거 헌터'의 빌드로그 카드뉴스를 만드는 편집자다.

읽는 사람은 스레드에서 카드를 옆으로 넘긴다. 한 장에 한 가지만 담아야 한다.

톤:
- 만드는 사람 1인칭. 잘된 것만이 아니라 뭐가 문제였는지도 그대로 쓴다.
- 홍보가 아니라 작업 기록. 과장·감탄사 금지.

반드시 지킬 것:
- 주어진 내용에 없는 성과·수치·일정을 지어내지 않는다.
- 파일명·함수명·환경변수명은 쓰지 않는다. 독자가 화면에서 겪는 일로 옮겨 쓴다.
- 종목 추천이 아니므로 투자 책임 고지는 넣지 않는다.
- 한 장의 글자 수를 넘기지 않는다. 카드는 읽는 게 아니라 보는 것이다."""

CARD_USER_TEMPLATE = """아래 개발 내용으로 카드뉴스 5장을 만들어라.

작업 제목: {title}

[AS-IS — 전에는 이랬다]
{asis}

[TO-BE — 이렇게 바꿨다]
{tobe}

JSON 하나만 출력해라. 설명·코드펜스 없이 JSON만.

{{
  "label": "2026.09.22 — 개발 로그",
  "cards": [
    {{"kind": "hook", "headline": "40자 이내. 줄바꿈(\\n)으로 3~4줄로 끊어라",
      "sub": "60자 이내 한두 문장"}},
    {{"kind": "compare", "label": "문제", "asis": "무엇이 문제였는지 두 줄 이내",
      "tobe": "어떻게 바꿨는지 두 줄 이내"}},
    {{"kind": "body", "label": "과정", "title": "30자 이내, 줄바꿈으로 두 줄",
      "body": "세 문장 이내. 줄바꿈이 문단을 나눈다"}},
    {{"kind": "compare", "label": "결과", "asis_tag": "전", "tobe_tag": "후",
      "asis": "...", "tobe": "..."}},
    {{"kind": "outro", "label": "정리", "body": "두 문장 이내",
      "question": "읽는 사람에게 던지는 질문 한 줄. 줄바꿈으로 두 줄"}}
  ]
}}

강조는 `**굵게**`만 쓴다. 한 장에 한 번을 넘기지 마라.
hook의 headline은 이 작업에서 가장 사람 눈길을 끄는 한 가지여야 한다."""


def _slugify(title: str, fallback: str = "devlog") -> str:
    """제목 → 파일명에 쓸 영숫자 슬러그.

    제목이 한글뿐이면 남는 글자가 없으므로 fallback(버전)을 쓴다. 같은 날
    두 건을 렌더해도 파일명이 겹치지 않게 하려는 것이다.
    """
    s = re.sub(r"[^a-zA-Z0-9]+", "-", title).strip("-").lower()
    return s[:40] or fallback


def build_card_spec(title: str, asis: str, tobe: str, *, date: str, version: str = "",
                    model: Optional[str] = None, client=None) -> dict:
    """AS-IS/TO-BE 한 건 → render.py가 받는 스펙 dict."""
    client = client or _client()
    model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": CARD_SYSTEM_PROMPT},
            {"role": "user", "content": CARD_USER_TEMPLATE.format(
                title=title, asis=asis.strip() or "(따로 적힌 배경 없음 — 지어내지 마라)",
                tobe=tobe.strip())},
        ],
        response_format={"type": "json_object"},
        temperature=0.7,
    )
    spec = json.loads(resp.choices[0].message.content or "{}")
    if not spec.get("cards"):
        raise ValueError("카드 스펙 생성 실패 — cards가 비어 있다")
    spec["date"] = date
    spec["slug"] = _slugify(title, fallback=version.replace(".", "-") or "devlog")
    return spec


def enqueue_with_cards(*, render_only: bool = False, out_dir: Path = DEFAULT_OUT,
                       model: Optional[str] = None) -> int:
    """아직 글로 안 만든 최신 CHANGELOG 항목 1건을 카드 + 본문으로 적재한다."""
    from datetime import date as _date

    entries = parse_changelog(changelog_path())
    if not entries:
        logger.warning("CHANGELOG에서 쓸 항목을 찾지 못했습니다.")
        return 0

    queue = build_queue()
    client = _client()
    today = _date.today().isoformat()

    for entry in entries[:MAX_BACKLOG]:
        source_key = f"changelog-cards:{entry['version']}"
        if queue.has_source(source_key) or queue.has_source(f"changelog:{entry['version']}"):
            continue  # 본문만이든 카드까지든, 이 버전으로는 이미 냈다

        spec = build_card_spec(entry["title"], entry["asis"], entry["tobe"],
                               date=today, version=entry["version"],
                               model=model, client=client)
        paths = render_spec(spec, out_dir)
        if render_only:
            print("\n렌더만 했습니다. 큐에는 넣지 않았습니다.")
            return 0

        text = generate_devlog_post(entry["title"], entry["asis"], entry["tobe"],
                                    model=model, client=client)
        queue.add(text, image_urls=public_urls(paths), source_key=source_key)
        logger.info("큐 적재(카드 %d장): %s — %s", len(paths), entry["version"], entry["title"])
        return 1

    logger.info("새로 적재할 항목이 없습니다 — 최근 %d개 변경은 이미 글로 만들었습니다.", MAX_BACKLOG)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    args = sys.argv[1:]
    if "--spec" in args:
        i = args.index("--spec")
        if i + 1 >= len(args):
            print("--spec 뒤에 스펙 파일 경로를 넣어주세요.")
            return 1
        spec = json.loads(Path(args[i + 1]).read_text(encoding="utf-8"))
        paths = render_spec(spec, DEFAULT_OUT)
        print(f"\n{len(paths)}장 렌더했습니다. 본문은 직접 넣어주세요:")
        print(f"  python main.py --add \"본문\" --images {','.join(public_urls(paths))}")
        return 0

    try:
        added = enqueue_with_cards(render_only="--render-only" in args)
    except RuntimeError as exc:
        print(f"실행할 수 없습니다: {exc}")
        print("  .env에 OPENAI_API_KEY=... 를 추가하고 다시 시도하세요.")
        return 1

    if added:
        print("\n1건을 카드와 함께 큐에 넣었습니다.")
        print("⚠️ 발행 전에 PNG를 커밋·푸시해 배포해야 합니다 "
              "(Meta가 발행 시점에 이미지를 직접 가져갑니다).")
        print("확인: python preview.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
