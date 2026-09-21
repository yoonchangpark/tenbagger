"""빌드로그 카드뉴스 PNG 생성기.

`template.html`을 Chromium(Playwright)으로 열어 카드 한 장씩 캡처한다.
지금까지 손으로 만들던 카드뉴스(`frontend/media/threads-posts/`)를
JSON 하나로 다시 만들 수 있게 하는 게 목적이다.

소재는 종목 분석이 아니라 **만드는 과정**이다 — 텐배거를 어떻게 고치고 있는지,
숏츠를 만들며 뭐가 터졌는지. 글 본문은 `devlog_generator.py`가 쓰고,
여기서는 그 내용을 이미지로 만든다.

사용법:
  python cardnews/render.py spec.json
  python cardnews/render.py spec.json --out ../frontend/media/threads-posts

스펙(JSON):
  {
    "date": "2026-09-21",          # 파일명 접두사
    "slug": "shorts-inventory",    # 파일명 구분자
    "label": "숏츠 제작 로그",       # 좌상단 라벨 기본값 (카드별 재정의 가능)
    "cards": [
      {"kind": "hook",    "headline": "...", "sub": "..."},
      {"kind": "compare", "label": "issue #1 — ...", "asis": "...", "tobe": "..."},
      {"kind": "body",    "title": "...", "body": "한 줄이 한 문단"},
      {"kind": "outro",   "body": "...", "question": "..."}
    ]
  }

본문에서 쓸 수 있는 표기는 `**굵게**`와 백틱 코드 두 가지뿐이다.
줄바꿈은 문단 분리로 쓴다.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

CARD_W, CARD_H = 1080, 1350
SCALE = 2  # 2160x2700 — 스레드에서 확대해도 글자가 깨지지 않는 선

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "template.html"
DEFAULT_OUT = HERE.parent.parent / "frontend" / "media" / "threads-posts"


# 마지막 장 하단 문구. 도메인은 아직 없으므로 주소를 쓰지 않는다.
# 스펙의 카드마다 "footer"로 덮어쓸 수 있다.
LAST_FOOTER = "텐배거 헌터 — 만드는 중"


def _footer_for(index: int, total: int) -> str:
    return "→ SWIPE TO CONTINUE" if index < total - 1 else LAST_FOOTER


def render_spec(spec: dict, out_dir: Path) -> list[Path]:
    """스펙 하나를 PNG 여러 장으로 만든다. 만들어진 경로 리스트를 돌려준다."""
    from playwright.sync_api import sync_playwright

    cards = spec.get("cards") or []
    if not cards:
        raise ValueError("cards가 비어 있습니다.")
    date = spec.get("date") or ""
    slug = spec.get("slug") or "card"
    default_label = spec.get("label", "")

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    with sync_playwright() as p:
        # 설치된 Playwright와 브라우저 버전이 어긋난 환경에서는 CHROMIUM_PATH로 지정한다.
        launch_kwargs = {}
        chromium_path = os.getenv("CHROMIUM_PATH")
        if chromium_path:
            launch_kwargs["executable_path"] = chromium_path
        browser = p.chromium.launch(**launch_kwargs)
        page = browser.new_page(
            viewport={"width": CARD_W, "height": CARD_H},
            device_scale_factor=SCALE,
        )
        page.goto(TEMPLATE.as_uri())
        # 웹폰트(Noto Sans KR)가 오기 전에 찍으면 한글이 대체 글꼴로 나온다.
        page.wait_for_function("document.fonts.ready.then(() => true)")

        for i, card in enumerate(cards):
            payload = dict(card)
            payload.setdefault("label", default_label)
            payload["no"] = f"{i + 1:02d}"
            payload.setdefault("footer", _footer_for(i, len(cards)))
            page.evaluate("spec => window.render(spec)", payload)
            page.wait_for_timeout(120)  # 레이아웃·폰트 반영

            name = f"{date}-{slug}-card-{i + 1:02d}.png" if date else f"{slug}-card-{i + 1:02d}.png"
            path = out_dir / name
            page.locator("#card").screenshot(path=str(path))
            written.append(path)
            print(f"  {name}")

        browser.close()
    return written


def public_urls(paths: list[Path], base: Optional[str] = None) -> list[str]:
    """캐러셀 발행(`queue.add(..., image_urls=[...])`)에 넣을 공개 URL.

    Meta 서버가 image_url을 직접 가져가므로 배포된 주소여야 한다.
    """
    base = (base or "https://tenbagger-production.up.railway.app").rstrip("/")
    return [f"{base}/media/threads-posts/{p.name}" for p in paths]


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 1

    spec_path = Path(args[0])
    if not spec_path.exists():
        print(f"스펙 파일이 없습니다: {spec_path}")
        return 1

    out_dir = DEFAULT_OUT
    if "--out" in args:
        i = args.index("--out")
        if i + 1 >= len(args):
            print("--out 뒤에 경로를 넣어주세요.")
            return 1
        out_dir = Path(args[i + 1])

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    print(f"{spec_path.name} → {out_dir}")
    paths = render_spec(spec, out_dir)

    print(f"\n{len(paths)}장 생성했습니다. 배포 후 캐러셀에 쓸 URL:")
    for url in public_urls(paths):
        print(f"  {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
